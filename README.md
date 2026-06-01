# Hermes Agent — развёртывание

Оркестрация [Hermes Agent](https://github.com/NousResearch/hermes-agent) (Nous Research) в Docker-контейнерах за Traefik reverse proxy.

## Что это

Самообучающийся AI-агент с TUI, CLI, и gateway для мессенджеров (Telegram, Discord, Slack, WhatsApp). В этом репозитории — только **deployment** (compose, env, secrets), сам агент работает из образа `nousresearch/hermes-agent`.

## Состав

- **Gateway** (`hermes-gateway`) — главный сервис: CLI, шлюз мессенджеров, OpenAI-совместимый API на порту 8642.
- **Dashboard** (`hermes-dashboard`) — web-интерфейс на порту 9119, доступен по https://hermes.kulinich.ru.

Оба сервиса работают за Traefik (внешняя сеть `tghub-network`) с TLS и basic-auth.

## Доступы

| Сервис | URL | Авторизация |
|---|---|---|
| Dashboard | https://hermes.kulinich.ru | basic-auth (логин/пароль в `.env.hermes`) |
| OpenAI API | http://hermes-gateway:8642/v1/ (внутри docker-сети) | Bearer `API_SERVER_KEY` из `.env.hermes` |

Данные авторизации и ключи **не хранятся в репозитории** — только в `.env.hermes` (chmod 600, в `.gitignore`).

## Запуск

```bash
# одноразовая настройка
cp .env.example .env.hermes
chmod 600 .env.hermes
$EDITOR .env.hermes   # заполнить реальные значения

# поднять
docker compose --env-file .env.hermes up -d

# проверить
docker ps --filter name=hermes
docker exec hermes-gateway hermes doctor
```

## Конфигурация

- `docker-compose.yml` — описание сервисов, healthcheck, ресурсы, Traefik-метки
- `.env.hermes` — секреты, домен, прокси, порты
- `/root/.hermes/config.yaml` — главный конфиг агента (вне репо)

Подробнее см. [AGENTS.md](AGENTS.md).

## Обслуживание

```bash
# рестарт
docker compose --env-file .env.hermes restart

# логи
docker logs -f hermes-gateway

# обновить образ
docker compose --env-file .env.hermes pull
docker compose --env-file .env.hermes up -d

# диагностика
docker exec hermes-gateway hermes doctor
```

## Безопасность

- Контейнеры работают под `uid=1000` (не root)
- `tirith_fail_open: false` — URL-сканер не пропускает при сбое
- `redact_pii: true` — PII в логах вырезается
- `redact_secrets: true` — секреты в логах вырезаются
- `gateway.strict: true` — строгая валидация входящих
- Dashboard за basic-auth, весь трафик через TLS

Полная политика безопасности: https://hermes-agent.nousresearch.com/docs/user-guide/security

## Структура данных

Данные агента (не в репозитории):

```
/root/.hermes/
├── config.yaml          # главный конфиг
├── SOUL.md              # персона
├── skills/              # 23 скилла
├── memories/            # долговременная память
├── sessions/            # история сессий
├── kanban.db            # SQLite: задачи
├── response_store.db    # SQLite: кэш ответов
├── logs/                # логи
├── cron/                # запланированные задачи
└── audio_cache/, image_cache/  # медиа-кэш
```

Bind-mount `/root/.hermes:/opt/data` в обоих контейнерах.

## Лицензия

Код оркестрации — MIT. Сам агент — MIT (Nous Research).

## Ссылки

- Hermes Agent: https://github.com/NousResearch/hermes-agent
- Документация: https://hermes-agent.nousresearch.com/docs/
- Discord Nous Research: https://discord.gg/NousResearch
