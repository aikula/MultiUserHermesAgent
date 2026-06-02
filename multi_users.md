# Мультиюзер в одном контейнере Hermes (вариант A)

## Цель

Дать 20-30 пользователям доступ к Hermes-агенту через web-интерфейс и Telegram, в **одном Hermes-процессе**. Multi-user логика — на стороне FastAPI-webapp (per-user auth, per-user chat history, per-user SOUL.md как system prompt). Один Fireworks API-ключ, один Telegram-бот, изоляция per-user — на уровне webapp и файловой системы.

## Почему A, а не B

VPS: 8 CPU, 11 GB RAM, ~7.5 GB свободно. Другие процессы占用 ~4 GB. Вариант B (N Hermes-процессов) требует ~8 GB — впритык. Вариант A — ~1.5 GB, запас ×4.

## Архитектура

```
[Telegram]                                            [Browser]
   │                                                      │
   │ long-poll                                            │ HTTPS
   ▼                                                      ▼
┌──────────────────────────────────────────────────────────────────┐
│ hermes-webapp (1 контейнер, 2 процесса)                          │
│                                                                  │
│  ┌─────────────────────────┐    ┌──────────────────────────────┐  │
│  │ telegram-relay.py       │    │ FastAPI webapp               │  │
│  │ (asyncio task)          │    │ :9000                        │  │
│  │                         │    │                              │  │
│  │ • /start <code>         │───►│ • /api/chat                  │  │
│  │   (регистрация/линк)    │    │ • /api/usage                 │  │
│  │ • при message:          │    │ • /register, /login, /profile│  │
│  │   1. auth.json lookup   │    │                              │  │
│  │   2. SOUL.md + memory   │───►│ При message:                 │  │
│  │   3. POST :8642         │    │ 1. /opt/hermes-users/<uid>/  │  │
│  │   4. send Telegram      │    │ 2. last 20 из users.db       │  │
│  │                         │    │ 3. POST :8642                │  │
│  │                         │    │ 4. save в users.db           │  │
│  └─────────────────────────┘    └──────────────────────────────┘  │
│                                                                  │
│  Volumes:                                                        │
│    /opt/app          = /root/.hermes-app (users.db, quotas)     │
│    /opt/hermes-users = /root/.hermes/users:ro (SOUL/memory)      │
│    /opt/hermes-shared= /root/.hermes-shared:rw (auth.json)       │
└──────────────────────────────────────────────────────────────────┘
                                │
                                │ HTTP POST /v1/chat/completions
                                │ Authorization: Bearer <API_SERVER_KEY>
                                │ system: <per-user SOUL.md + memory>
                                ▼
                    ┌──────────────────────────┐
                    │ hermes-gateway           │
                    │ Hermes (1 процесс)       │
                    │ :8642                    │
                    │ Telegram platform: OFF   │
                    │                          │
                    │ /opt/data/               │
                    │   config.yaml            │
                    │   skills/ (общие)        │
                    │   sessions/              │
                    │   logs/                  │
                    └──────────────────────────┘
```

**Ключевая идея**: один Hermes делает всю работу. FastAPI-webapp держит per-user state (auth, history, SOUL.md, preferences) и подмешивает per-user контекст в каждый запрос. Telegram обрабатывается **отдельным relay-процессом внутри `hermes-webapp`** (E1) — встроенная Telegram-платформа Hermes **выключена**, чтобы не дублировать routing и не терять per-user персонализацию.

**Shared auth.json**: пишется webapp/relay-ом, читается relay-ом для маршрутизации `telegram_user_id → uid`. Хранится в `/root/.hermes-shared/` — отдельном каталоге, смонтированном в `hermes-webapp` (master-volume `/root/.hermes/` webapp-у не отдан целиком из соображений безопасности).

## Изоляция per-user

| Что | Где | Как обеспечивается |
|---|---|---|
| Auth (web) | `users.db` (SQLite) | bcrypt пароль + JWT в HttpOnly cookie |
| Chat history | `users.db` | webapp хранит messages per user, шлёт в Hermes с каждым запросом |
| SOUL.md | `/root/.hermes/users/<uid>/SOUL.md` | webapp читает, инжектит как `system` message |
| Memory | `/root/.hermes/users/<uid>/memory.md` | webapp читает/пишет, инжектит как контекст (вручную, без Hermes memory system в v1) |
| Telegram routing | `/root/.hermes-shared/auth.json` | `{"telegram_user_id": "uid"}` — relay читает, webapp пишет |
| Telegram access | relay-уровень (в webapp) | relay проверяет `telegram_user_id ∈ auth.json`; **Hermes Telegram-платформа выключена** |
| Per-user ключи к Hermes | Один `API_SERVER_KEY` | Все юзеры шарят (упрощение для варианта A) |
| Kanban/response_store | Пока общий (один `kanban.db`) | В v1 шарится; в v2 — per-user DB |

## Пользовательские потоки

### 1. Регистрация (web)
1. `/register` → имя, логин, пароль, **invite-code**
2. FastAPI валидирует код (таблица `invite_codes` в `users.db`)
3. Создаётся запись в `users.db` (bcrypt пароля, `status=active`, `quota_remaining=2_000_000`)
4. FastAPI создаёт `users/<uid>/` каталог с шаблоном `SOUL.md`, пустым `memory.md`
5. (Опционально) юзер сразу указывает `telegram_user_id` — FastAPI пишет в `/opt/hermes-shared/auth.json`

### 2. Регистрация (Telegram)
1. Юзер пишет `/start <invite-code>` боту
2. **telegram-relay** получает сообщение, валидирует код через `POST http://webapp:9000/api/internal/validate-invite`
3. Webapp создаёт запись в `users.db` (без пароля, `telegram_id` = primary, `status=active`)
4. Webapp создаёт `users/<uid>/` с шаблоном `SOUL.md` и пустым `memory.md`
5. Webapp пишет `auth.json: {<telegram_user_id>: <uid>}` в `/opt/hermes-shared/`
6. Relay отвечает: «Готово. Зайди на https://hermes.kulinich.ru/ → /profile, задай пароль»

### 3. Линковка Telegram ↔ web (если юзер зарегался через web, потом хочет Telegram)
- `/profile` → «Привязать Telegram» → генерация 6-символьного link-code (TTL 10 мин) в `users.db`
- Юзер пишет боту `/start <link-code>` → **relay** вызывает `POST /api/internal/consume-link-code` → webapp помечает код использованным и пишет `auth.json: {<tg_id>: <uid>}`
- Следующее сообщение в Telegram уже идёт через relay с per-user контекстом
- **Hermes не перезапускается** — relay сам читает обновлённый `auth.json` (in-memory cache + mtime check)

### 4. Чат
- **Web**: `/chat` — JS fetch к FastAPI `/api/chat`
  - FastAPI читает `users/<uid>/SOUL.md` и `memory.md`
  - Берёт последние N сообщений из `users.db` (chat history)
  - Шлёт в Hermes: `messages: [{role:system, content: SOUL.md+memory}, ..., история, новое]`
  - Получает ответ, сохраняет в `users.db`, рендерит
- **Telegram**: **telegram-relay** получает сообщение
  - Проверяет `telegram_user_id` в `auth.json` (если нет — отвечает «не зарегистрирован, пришли /start <invite-code>»)
  - Читает `users/<uid>/SOUL.md` и `memory.md`
  - Шлёт в `:8642/v1/chat/completions` с тем же `system` промптом
  - Сохраняет в `users.db` (общая история web+telegram)
  - Отвечает в Telegram
  - **Важно**: per-user персонализация держится на стороне relay, не встроенной Telegram-платформе Hermes

### 5. Квоты
- **Cron-наблюдатель** каждые 5 мин парсит `/opt/data/logs/`
- Суммирует `total_tokens` per `telegram_user_id`/per session_id → per uid (через `auth.json`)
- Записывает в `/root/.hermes-app/quotas/<uid>/<YYYY-MM-DD>.json`
- FastAPI `/usage` показывает breakdown
- При превышении 2М welcome или 300К/мес — alert админу (через того же Telegram-бота, admin chat_id)
- **Не блокирует** в v1

## Фазы внедрения

### Фаза 0 — Подготовка ✅ Done (2026-06-02)
- ✅ Снизить `hermes-gateway` лимит памяти: `memory: 4G → 3G` (cpus остались 2.0)
- ✅ Создать `/root/.hermes-app/{backups,quotas,templates,logs,cron}`, chown 1000:1000, chmod 700
- ✅ Проверить `api_mode: chat_completions` с длинным system-промптом: 16K символов = 13 720 токенов → 200 OK
- ✅ `telegram.allowed_chats: ''` (пусто; Hermes Telegram-платформа будет выключена в Фазе 3)
- **Verification:** `docker inspect hermes-gateway` → 3 GB / 2 cores; контейнер healthy

### Фаза 1 — FastAPI webapp каркас (3-5 дней)
- Новый сервис `hermes-webapp` в compose: `python:3.11-slim` + uvicorn + jinja2 + httpx
- Volumes (compose):
  - `/root/.hermes-app:/opt/app` (users.db, quotas/, templates/, backups/)
  - `/root/.hermes/users:/opt/hermes-users:ro` (чтение SOUL.md/memory.md)
  - `/root/.hermes-shared:/opt/hermes-shared:rw` (auth.json — пишется webapp/relay-ом)
- `mkdir -p /root/.hermes-shared && chown 1000:1000 /root/.hermes-shared`
- Traefik-роут `hermes.kulinich.ru/` → webapp
- Реализовать:
  - `/register` (имя, логин, пароль, invite-code)
  - `/login`, `/logout`
  - `/profile` (имя, SOUL.md, telegram linking UI)
  - `POST /api/internal/validate-invite` (вызывается relay-ом при `/start <code>`)
  - `POST /api/internal/consume-link-code` (для линковки)
- `users.db` (SQLite): таблицы `users`, `invite_codes`, `telegram_links`, `chat_history`, `quotas` (всё в одной БД, без отдельной `chat_history.db`)
- bcrypt для паролей, JWT в HttpOnly cookies
- При регистрации: `mkdir -p /opt/hermes-users/<uid>/` + шаблон `SOUL.md`
- **Done when:** я регистрируюсь, логинюсь, вижу профиль

### Фаза 2 — Web-чат (2-3 дня)
- `/chat` — минимальный UI (HTML+JS), textarea + история, fetch к FastAPI `/api/chat`
- FastAPI: читает `users/<uid>/SOUL.md` + `memory.md`, берёт последние 20 сообщений из `users.db`, шлёт в Hermes
- После ответа: сохраняет в `chat_history`, обновляет счётчик токенов
- **Done when:** я могу общаться с агентом через web, история сохраняется, разные юзеры не видят чужие чаты

### Фаза 3 — Telegram relay + линковка (3-5 дней)
- ✅ **Внутри `hermes-webapp`** запускается `telegram-relay.py` (asyncio task в одном процессе с FastAPI, либо отдельный entrypoint в compose)
- ✅ Hermes встроенная Telegram-платформа **выключена** (config.yaml: `telegram.enabled: false`), чтобы не было двойного handling
- `/profile` → «Привязать Telegram» → генерация link-code в `users.db` (TTL 10 мин)
- **Relay** получает `/start <link-code>` → `POST /api/internal/consume-link-code` → webapp помечает код использованным и пишет в `/opt/hermes-shared/auth.json: {<tg_id>: <uid>}`
- Relay при входящем сообщении:
  - Проверяет `telegram_user_id` в `auth.json` (in-memory cache с mtime-проверкой файла)
  - Если нет → отвечает «не зарегистрирован, /start <invite-code>»
  - Если есть → читает `users/<uid>/SOUL.md` + `memory.md`, шлёт `:8642/v1/chat/completions` с system-промптом
  - Сохраняет в `users.db.chat_history` (общая история с web)
- `telegram-relay.py` ≈ 100-150 строк: long-poll loop, message handler, httpx POST к webapp/:8642, sendMessage обратно
- **Done when:** юзер с Telegram привязкой общается в обе стороны, история общая с web, per-user persona работает

### Фаза 4 — Per-user memory (2-3 дня)
- Webapp пишет в `users/<uid>/memory.md` после каждого N сообщений (через summarizer LLM)
- При запросе webapp подмешивает `memory.md` в system prompt (после SOUL.md)
- Telegram-релей делает то же самое
- **Done when:** агент помнит факты о юзере между сессиями

### Фаза 5 — Квоты (1-2 дня)
- Хост-крон (C2) `*/5 * * * * /root/.hermes-app/cron/quota-observer.sh`:
  - `find /root/.hermes/logs/ -name '*.log' -mmin -10`
  - Парсит строки с `total_tokens=`
  - Группирует по session_id, потом session_id → uid через `/root/.hermes-shared/auth.json`
  - Суммирует в `/root/.hermes-app/quotas/<uid>/<YYYY-MM-DD>.json`
- FastAPI `/usage` показывает breakdown (used / 2M welcome / 300K monthly)
- Alert-скрипт: при превышении 80% — admin notification через Telegram-бота (admin chat_id в `users.db` или .env)
- **Не блокирует** в v1 — только наблюдение
- **Done when:** вижу расход по юзерам, alert при приближении к лимиту

### Фаза 6 — Бэкапы и операционка (1 день)
- Cron `/opt/cron/backup.sh` ежедневно:
  - `cp /root/.hermes-app/users.db /root/.hermes-app/backups/users-$(date +%F).db`
  - `cp -r /root/.hermes/users /root/.hermes-app/backups/users-$(date +%F)/`
  - Ротация: хранить 7 последних

## Ресурсы (вариант A, 20-30 юзеров)

| Компонент | CPU | RAM |
|---|---|---|
| Hermes (1 процесс) | 0.5–1.5 (шарят все) | 500 MB – 1 GB |
| FastAPI webapp + telegram-relay (1 контейнер) | 0.3–0.5 | 200–300 MB |
| SQLite, логи, бэкапы | — | 100–200 MB |
| **ИТОГО новое** | **0.8–2.0** | **0.8–1.5 GB** |
| Доступно на VPS | 8 | 7.5 GB |
| **Запас** | **×4-10** | **×5-9** |

**Снимаем нагрузку** с текущего `hermes-gateway`:
- `cpus: '2.0', memory: 3G` (применено в Фазе 0)
- Новый `hermes-webapp`: `cpus: '0.5', memory: 512M`

## Решения (зафиксировано)

| # | Решение | Значение |
|---|---|---|
| 1 | Web-UI | FastAPI (свой, контейнер `hermes-webapp`) |
| 2 | Регистрация | Гибрид (web + Telegram), профиль в web |
| 3 | Invite-code | В `users.db`, таблица `invite_codes` с TTL |
| 4 | Telegram-линковка | Через бота: `/start <link-code>` |
| 5 | Per-user memory | Per-user файл `users/<uid>/memory.md`, инжектится как system промпт |
| 6 | Per-user SOUL.md | Файл `users/<uid>/SOUL.md`, инжектится как system промпт |
| 7 | Email | Нет в v1 |
| 8 | Telegram без web-аккаунта | Нет |
| 9 | Квоты | Cron-парсер логов + FastAPI `/usage` |
| 10 | Лимиты | 2М welcome, 300К/мес; только наблюдение |
| 11 | Архитектура | **A: один Hermes + FastAPI-webapp** |
| 12 | Auth webapp → Hermes | Один `API_SERVER_KEY` (все юзеры шарят) |
| 13 | users.db | `/root/.hermes-app/users.db` (отдельный volume) |
| 14 | Cron квот-наблюдателя | **C2: хост-крон** (`/root/.hermes-app/cron/`) |
| 15 | Telegram routing | **E1: telegram-relay.py** внутри `hermes-webapp` |
| 16 | Лимиты ресурсов | gateway `2.0 CPU / 3G RAM`, webapp `0.5 CPU / 512M RAM` |
| 17 | Hermes Telegram-платформа | **Выключена** — только relay |
| 18 | Shared `auth.json` | `/root/.hermes-shared/auth.json` (отдельный volume, rw) |

## Структура на диске

```
/root/
├── .hermes/                          # bind-mount в hermes-gateway (полный)
│   ├── config.yaml                   # master Hermes (Telegram platform: OFF)
│   ├── skills/                       # общие builtin
│   ├── sessions/                     # глобальные сессии (master)
│   ├── logs/                         # логи для quota-observer
│   └── users/                        # shared SOUL/memory (bind-mount в webapp :ro)
│       ├── alice/
│       │   ├── SOUL.md
│       │   └── memory.md
│       ├── bob/
│       │   ├── SOUL.md
│       │   └── memory.md
│       └── ...
│
├── .hermes-shared/                   # bind-mount rw в hermes-webapp
│   └── auth.json                     # {"<telegram_user_id>": "<uid>"}
│
└── .hermes-app/                      # bind-mount в hermes-webapp
    ├── users.db                      # SQLite: users, invite_codes, telegram_links,
    │                                 #         chat_history, quotas
    ├── templates/                    # шаблоны SOUL.md, register.html
    ├── backups/
    │   ├── users-2026-06-01.db
    │   └── users-2026-06-01/
    ├── quotas/
    │   ├── alice/2026-06-01.json
    │   └── ...
    └── cron/
        └── quota-observer.sh         # запускается хост-кроном (C2)
```

## История решений (что обсуждалось и как решено)

**Q-B. Лимиты ресурсов** — gateway 2.0/3G, webapp 0.5/512M. ✅ Применено в Фазе 0.

**Q-C. Где cron-наблюдатель квот** — C2 (хост-крон). Альтернативы C1 (s6-сервис в gateway — лишняя зависимость), C3 (apscheduler в webapp — теряем при рестарте webapp).

**Q-D. Где живёт `users.db`** — `/root/.hermes-app/` (отдельный volume). Альтернатива шарить с `/root/.hermes/` отвергнута: смешение конфигов Hermes и per-user данных, разные владельцы, разные бэкап-политики.

**Q-E. Telegram-relay отдельным скриптом или в Hermes** — E1 (relay в webapp). Альтернатива E2 (патчить Hermes) отвергнута: Hermes чужой код, апстрим может сломать патч при обновлении; relay = наш код, 100-150 строк, полный контроль.

**Q-New. Где живёт `auth.json`** — `/root/.hermes-shared/` (отдельный volume rw в webapp). Альтернативы:
- A. В `/root/.hermes-app/` (шарит с users.db) — допустимо, но `auth.json` это shared-state между webapp и relay, лучше отделить
- B. Монтировать весь `/root/.hermes/` в webapp — отвергнуто (webapp получает доступ к `config.yaml` Hermes'а, нежелательно)
- **C. `/root/.hermes-shared/` ✅** — минимальный shared volume, чёткая граница

---

Спецификация финализирована. См. таблицу «Решения (зафиксировано)» в строках выше.
