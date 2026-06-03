# AGENTS.md

Инструкции для AI-агентов и контрибьюторов, работающих с этим репозиторием.

## Назначение

Этот репозиторий содержит **multi-user веб-приложение** и **оркестрацию развёртывания** [Hermes Agent](https://github.com/NousResearch/hermes-agent) от Nous Research.

### Что есть в репозитории

- `webapp/` — FastAPI multi-user веб-приложение (чат, профиль, email, Telegram relay)
- `docker-compose.yml` — описание сервисов (gateway, webapp, dashboard)
- `.env.hermes` — секреты (НЕ в git)
- `.env.example` — шаблон секретов
- `.github/workflows/ci.yml` — CI pipeline (ruff, pytest, bandit)
- `docs/specs/` — спецификации функциональности
- `cron/` — скрипты бэкапов

### Чего НЕТ в репозитории

- Исходный код Hermes Agent (он в `nousresearch/hermes-agent`)
- Конфигурация агента (`config.yaml`, скиллы, память) — живёт в `/root/.hermes/`

## Архитектура

```
Internet ──► Traefik (:443, tghub-network)
              ├─► hermes-dashboard :9119  (admin UI, basic-auth)
              ├─► hermes-webapp   :9000   (multi-user chat + Telegram relay)
              └─► hermes-gateway  :8642   (Hermes API, OpenAI-compatible)
                   │
                   └─► /opt/data → /root/.hermes (bind-mount)
                          ├─ config.yaml
                          ├─ SOUL.md
                          ├─ skills/, memories/, sessions/
                          ├─ kanban.db, response_store.db
                          └─ logs/, cron/
```

Внешние сервисы: `tghub-traefik`, Fireworks AI API, прокси `107.173.19.16:3128` (для Telegram из РФ).

## Файлы и их роль

| Путь | Назначение | В git? |
|---|---|---|
| `docker-compose.yml` | Описание gateway + dashboard | Да |
| `.env.example` | Шаблон env-переменных | Да |
| `.env.hermes` | Боевые секреты | Нет (chmod 600) |
| `.gitignore` | Исключает секреты и мусор | Да |
| `/root/.hermes/config.yaml` | Главный конфиг агента | Нет (вне репо) |
| `/root/.hermes/SOUL.md` | Персона агента | Нет (вне репо) |

## Первичная настройка нового окружения

1. Скопировать шаблон:
   ```bash
   cp .env.example .env.hermes
   chmod 600 .env.hermes
   ```
2. Заполнить `.env.hermes` реальными значениями (см. комментарии в файле).
3. Убедиться, что внешняя сеть `tghub-network` существует:
   ```bash
   docker network ls | grep tghub-network
   ```
4. Запустить:
   ```bash
   docker compose --env-file .env.hermes up -d
   ```
5. Проверить:
   ```bash
   docker ps --filter name=hermes
   docker exec hermes-gateway hermes doctor
   ```

## Частые операции

### Рестарт
```bash
docker compose --env-file .env.hermes restart
# или
docker compose --env-file .env.hermes up -d
```

### Логи
```bash
docker logs -f hermes-gateway
docker logs -f hermes-dashboard
# с ротацией (json-file, см. compose):
docker logs --tail 200 hermes-gateway
```

### Диагностика
```bash
docker exec hermes-gateway hermes doctor    # внутри контейнера
docker inspect hermes-gateway --format '{{.State.Health.Status}}'  # healthcheck
```

### Доступ к конфигурации агента
```bash
docker exec -it hermes-gateway sh
# внутри:
vi /opt/data/config.yaml
# или с хоста:
$EDITOR /root/.hermes/config.yaml
```
После правки `config.yaml` — рестарт: `docker compose --env-file .env.hermes restart`.

### Прокси и Telegram
Прокси уже настроен через `HTTP_PROXY`/`HTTPS_PROXY` в `.env.hermes`. Если прокси меняется — обновить `.env.hermes` и перезапустить gateway.

## Соглашения

### Коммиты
- Сообщения на русском, в повелительном наклонении.
- Один логический блок изменений = один коммит.
- Упоминать блок/направление в заголовке (напр. "Безопасность:", "docker-compose:", "config:").

### Секреты
- **Никогда** не коммитить `.env.hermes` — он в `.gitignore`.
- При добавлении новой переменной в compose: сначала добавить в `.env.example` с placeholder, затем в `.env.hermes` с реальным значением.
- Хэш basic-auth для Traefik хранится **инлайн** в `docker-compose.yml` (с `$$`-экранированием) — в `.env` docker-compose **не экранирует** `$$`.

### Параметризация
- Все домены, порты, секреты, прокси — через `${VAR:-default}` в compose.
- `HERMES_UID/GID` подставляется в `user:` директиву compose.
- `/root/.hermes` должен быть **owned by `HERMES_UID:HERMES_GID`**:
  ```bash
  chown -R 1000:1000 /root/.hermes
  ```

### Обновление образа
```bash
docker compose --env-file .env.hermes pull
docker compose --env-file .env.hermes up -d
docker exec hermes-gateway hermes doctor
```

## Точки расширения

- **Добавить мессенджер** (Telegram, Discord, Slack): `hermes gateway setup` внутри контейнера или ручная правка `/root/.hermes/config.yaml` секции `telegram:` / `discord:` / `slack:`.
- **Сменить модель**: `hermes model` или `hermes config set model.default <name>`.
- **Раскомментировать `fallback_model`** в `~/.hermes/config.yaml:510` — указать провайдера (openrouter, openai-codex, nous, kimi-coding и др., см. комментарий).
- **Traefik для gateway API**: добавить router/service labels в `docker-compose.yml` (сейчас API доступен только внутри сети).

## Известные ограничения

- `command_allowlist: []` при `approvals.mode: manual` — для cron-автоматизации нужно либо добавить команды в allowlist, либо переключить режим.
- Костыль `dashboard.command` отключает s6-сервис `gateway-default` из-за lock-конфликта на общем volume `/opt/data`. Полное решение — развести volume логов (требует миграции).
- `fallback_model` закомментирован — при недоступности Fireworks агент встанет.

## Cron / бэкапы (multi-user)

Хост-cron для multi-user webapp:

- `30 3 * * * /root/Agents/Hermes/cron/backup.sh` — ежедневный бэкап `users.db` + `users/<uid>/` с ротацией 7 дней
- Скрипт: `cron/backup.sh` (симлинк из `/root/.hermes-app/cron/`)
- Логи: `/root/.hermes-app/logs/backup-YYYY-MM-DD.log`

Quota tracking НЕ использует cron — webapp инкрементально обновляет `/root/.hermes-app/quotas/<uid>/<YYYY-MM-DD>.json` при каждом вызове (через `app/quota.py`).

## Ссылки

- Агент: https://github.com/NousResearch/hermes-agent
- Документация: https://hermes-agent.nousresearch.com/docs/
- Конфигурация: https://hermes-agent.nousresearch.com/docs/user-guide/configuration
- Безопасность: https://hermes-agent.nousresearch.com/docs/user-guide/security
