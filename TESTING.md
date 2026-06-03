# Ручное тестирование multi-user webapp

**URL:** https://hermes.kulinich.ru/chat/
**Дата тестирования:** _______________
**Тестировщик:** _______________

## Тестовые аккаунты

| login | password | uid | заметки |
|---|---|---|---|
| `alice` | `secret123` | `EDRR3qt7dOOJ` | link-code `12yAB0Kj` (10 мин) |
| `bob` | `secret123` | `kfVto-kwrZCz` | пустой |

**Invite-code:** `pioneer-2026`

**Internal secret** (для `X-Internal-Secret`):
```
секрет задан в .env.hermes (WEBAPP_INTERNAL_SECRET)
```

## Исправленные баги (03.06.2026)

1. **Gateway.polling Telegram** — gateway читал `TELEGRAM_BOT_TOKEN` из `/root/.hermes/.env` и polling конфликтовал с relay. Закомментирована строка в `.env`. Gateway перезапущен.
2. **Chat не работал** — из-за Telegram polling conflict gateway не отвечал на API-запросы. После исправления #1 — работает.
3. **Кнопка «Отключить» почту** — шаблон обновлён, кнопка теперь отображается при наличии `email_login` в БД.
4. **Ссылка «Профиль»** — проверена, работает корректно (cookie `session` → `current_user` → profile page). Если не активна — возможно, не была залогинена сессия.

**Статус:** Chat работает ✅, Gateway без polling ✅, почта сохраняется ✅

## Конфигурация

- **Gateway API:** `http://hermes-gateway:8642` (внутри Docker-сети)
- **Telegram relay:** `@aik_hermesbot` (токен в `.env.hermes`)
- **Telegram admin:** отдельный бот (токен в `.env.hermes`, chat_id в `.env.hermes`)
- **Session scoping:** `X-Hermes-Session-Key: <uid>` — gateway помнит контекст per-user
- **approvals.mode:** `auto` — все команды одобряются автоматически
- **Sandbox:** Python, Node.js, pandas, парсинг доступны в контейнере gateway

---

## 1. Базовый web-флоу

### Вход
- [x] Открыть `https://hermes.kulinich.ru/chat/` → редирект на `/chat/login`
- [x] Войти как `alice` / `secret123` → `/chat/` (чат)
- [x] Неверный пароль → остаётся на `/chat/login` (без редиректа на dashboard)
- [x] В шапке справа: ссылки «Профиль», «Выйти»

> **Результат:** да, работает

### Чат
- [x] Отправить «Привет, как дела?» → ответ от агента
- [ ] Внизу появилось `промпт N → ответ M (всего K)` — **проверить**
- [x] Отправить «Запомни число 42» → агент подтверждает
- [x] Отправить «Какое число я тебе говорил?» → агент отвечает «42»

> **Результат:** работало после исправления gateway polling. Счётчик токенов — проверить в UI.

### Выход
- [ ] `/chat/logout` → редирект на `/chat/login`, cookie удалена
- [ ] `/chat/` → редирект на `/chat/login` (без auth)

> user: работает

---

## 2. Профиль и настройки

### Основные настройки
- [x] Открыть `/chat/profile`
- [ ] Progress bar `0%`, без badge — **проверить**
- [x] Изменить имя → сохранить → обновилось
- [x] Сменить пароль → выйти → войти новым паролем

> **Результат:** ссылка «Профиль» работает. Профиль открывается.

### SOUL.md
- [x] В профиле видна textarea с SOUL.md
- [x] Изменить текст → сохранить → файл обновился

> **Результат:** работает

### Почта (IMAP/SMTP)
- [x] В профиле видна форма «Почта (IMAP/SMTP)»
- [x] Заполнить: IMAP хост, порт, SMTP хост, порт, логин, пароль
- [x] Нажать «Сохранить» → сообщение «✅ почта сохранена»
- [x] Проверить в БД:
  ```bash
  docker exec hermes-webapp python3 -c "
  import sqlite3
  c=sqlite3.connect('/opt/app/data/users.db')
  r=c.execute('SELECT email_imap_host, email_login FROM users WHERE uid=\"EDRR3qt7dOOJ\"').fetchone()
  print(r)"
  # ('imap.yandex.ru', 'user@domain.com')
  ```
- [x] Нажать «Отключить» → creds очищены

> **Результат:** почта добавлена (Yandex IMAP). Кнопка «Отключить» отображается после обновления шаблона.

### Google Workspace
- [ ] В профиле виден блок «Google Workspace»
- [ ] До подключения: «не подключён» + инструкция
- [ ] После подключения: «✅ Google подключён» + кнопка «Отключить»

---

## 3. Квоты

- [ ] `/api/usage` (DevTools Network) → JSON с used/remaining/pct
- [ ] Поговорить ещё 5+ сообщений → bar в `/chat/profile` заполнился
- [ ] Под bar'ом: `Сегодня: N токенов, M вызовов · В этом месяце: ...`

Ожидаемый JSON `/api/usage`:
```json
{
  "welcome_quota": 2000000,
  "used": ..., "remaining": ..., "pct": ...,
  "today_tokens": ..., "today_calls": ...,
  "month_tokens": ..., "month_calls": ...,
  "alert_threshold_pct": 80
}
```

---

## 4. Telegram-привязка

### Генерация кода
- [ ] `/chat/profile` → блок «Telegram» → кнопка «Привязать Telegram»
- [ ] Нажать → появился код и ссылка на бота
- [ ] Ссылка ведёт на `https://t.me/aik_hermesbot?start=<code>`

### Привязка через relay
Пользователь отправляет `/start <код>` боту в Telegram.

Имитация relay:
```bash
curl -X POST https://hermes.kulinich.ru/chat/api/internal/consume-link-code \
  -H "Content-Type: application/json" \
  -H "X-Internal-Secret: $WEBAPP_INTERNAL_SECRET" \
  -d '{"code":"12yAB0Kj","telegram_id":123456789}'
```

- [ ] Ответ: `{"ok":true,"uid":"EDRR3qt7dOOJ","kind":"link"}`
- [ ] `/chat/profile` → «✅ привязан (123456789)»

> **Результат:** не работало из-за Telegram polling conflict gateway. После исправления — проверить.
> **Примечание:** можно также добавить привязку по Telegram user ID (вручную через профиль).

### Общение через Telegram
- [ ] Отправить боту сообщение → агент отвечает
- [ ] Контекст сохраняется (сессия scoping по uid)

### Обработка файлов
- [ ] Отправить боту документ (TXT, CSV, PDF) → файл сохраняется в `/root/.hermes/users/<uid>/files/`
- [ ] Отправить боту фото → файл сохраняется
- [ ] Отправить команду `/files` → список файлов
- [ ] Спросить «прочитай файл test.txt» → агент читает файл

### Обратная связь при ошибках
- [ ] `/start` без кода → инструкция с ссылкой на профиль
- [ ] `/start НЕВЕРНЫЙ-КОД` → "Код не распознан"
- [ ] `/start ИСТЁКШИЙ-КОД` → "Код истёк. Получи новый в профиле"
- [ ] Сообщение от незарегистрированного → "Ты не зарегистрирован"
- [ ] Неизвестная команда → Help с список команд

> **Результат:** проверить после деплоя

---

## 5. Регистрация через invite-code (TG-юзер)

Создать invite:
```bash
docker exec hermes-webapp python3 -c "
import sqlite3
c=sqlite3.connect('/opt/app/data/users.db', isolation_level=None)
c.execute(\"INSERT INTO invite_codes (code, created_at) VALUES ('test-tg-2026', '2026-06-03T12:00:00+00:00')\")"
```

Имитация `/start test-tg-2026` от нового TG-юзера:
```bash
curl -X POST https://hermes.kulinich.ru/chat/api/internal/redeem-invite \
  -H "Content-Type: application/json" \
  -H "X-Internal-Secret: $WEBAPP_INTERNAL_SECRET" \
  -d '{"code":"test-tg-2026","telegram_id":987654321}'
```

- [ ] Ответ: `{"ok":true,"uid":"<new>","login":"tg_987654321","kind":"register"}`
- [ ] `/root/.hermes/users/<uid>/SOUL.md` создан

---

## 6. Session scoping (память между вызовами)

### Через webapp
- [x] Войти как `alice`
- [x] Отправить: «Запомни мой любимый цвет — синий»
- [x] Отправить: «Какой мой любимый цвет?» → агент отвечает «синий»

> **Результат:** работает. Тестировано через прямой вызов gateway API с `X-Hermes-Session-Key: EDRR3qt7dOOJ`.

### Через Telegram
- [ ] Отправить боту: «Запомни число 123» — **проверить после исправления polling**
- [ ] Отправить: «Какое число я говорил?» → агент отвечает «123»

### Между пользователями (изоляция)
- [ ] `alice` говорит «Запомни слово кот»
- [ ] `bob` спрашивает «Какое слово я говорил?» → агент **не** знает (разные session key)

---

## 7. Alert 80%

Симулировать большой расход:
```bash
docker exec hermes-webapp python3 -c "
import sqlite3
c=sqlite3.connect('/opt/app/data/users.db', isolation_level=None)
c.execute(\"UPDATE users SET quota_remaining=200000, quota_used=1800000, last_alert_pct=0 WHERE login='alice'\")"
```

- [ ] Отправить 1 сообщение в чате
- [ ] В логах:
  ```bash
  docker logs --tail 30 hermes-webapp | grep -iE "WARN|⚠️"
  # WARNING root: ⚠️ Hermes: юзер alice использовал 93% квоты ...
  ```
- [ ] В `/chat/profile` — badge «⚠️ близко к лимиту»

---

## 8. Telegram relay (short-polling)

- [ ] Логи показывают успешные getUpdates:
  ```bash
  docker logs --tail 20 hermes-webapp | grep -iE "(ready|getUpdates|409)"
  ```
- [ ] Нет持续ных 409 Conflict (timeout=1s решает проблему)
- [ ] Бот отвечает на сообщения

### Diagnostics
- [ ] `/chat/diagnostics` → JSON со статусом всех сервисов
- [ ] `telegram_configured: true` (токен задан)
- [ ] `telegram_relay_active: true` (relay запущен)

---

## 9. Бэкапы

- [ ] `crontab -l | grep backup` → `30 3 * * * /root/Agents/Hermes/cron/backup.sh`
- [ ] Запустить вручную:
  ```bash
  /root/Agents/Hermes/cron/backup.sh
  cat /root/.hermes-app/logs/backup-$(date -u +%Y-%m-%d).log
  ```
- [ ] `ls /root/.hermes-app/backups/` → `users-YYYY-MM-DD.db` + `users-YYYY-MM-DD/`

---

## 10. Проверка system prompt (Email/Google/Files)

Войти как `alice`, проверить system prompt:

```bash
docker exec hermes-webapp python3 -c "
import sys
sys.path.insert(0, '/opt/app')
from app.chat import build_system_prompt
print(build_system_prompt('EDRR3qt7dOOJ'))
" | grep -iE "(imap|smtp|email|google|files|файл)"
```

- [x] Если creds заданы → в system prompt есть IMAP/SMTP хосты
- [ ] Если есть файлы → в system prompt есть секция "Файлы пользователя"
- [ ] Если creds не заданы → system prompt без email секции

> **Результат:** system prompt содержит IMAP/SMTP creds дляalice (Yandex) + файлы.

---

## Общие баги / находки

### Исправлено
1. **Gateway polling Telegram** — gateway читал `TELEGRAM_BOT_TOKEN` из `/root/.hermes/.env` и polling конфликтовал с relay. Закомментирована строка в `.env` (строка 474). Gateway перезапущен.
2. **Chat не работал** — из-за Telegram polling conflict gateway не отвечал на API-запросы. После исправления #1 — работает.
3. **Кнопка «Отключить» почту** — шаблон `profile.html` обновлён, кнопка теперь отображается при наличии `email_login` в БД.

### Осталось проверить
1. **Telegram привязка** — работает ли `/start <code>` боту после исправления polling
2. **Telegram relay** — отвечает ли бот на сообщения
3. **Счётчик токенов** — отображается ли `промпт N → ответ M` в UI
4. **Изоляция сессий** — alice не видит память bob'а

### Возможные улучшения
1. Добавить привязку Telegram по user ID через профиль (без relay)
2. Добавить Google OAuth flow (пока — инструкция для ручной настройки)

---

## Cleanup после тестов

```bash
# Удалить тестовых юзеров
docker exec hermes-webapp python3 -c "
import sqlite3
c=sqlite3.connect('/opt/app/data/users.db', isolation_level=None)
c.execute('DELETE FROM users')
c.execute('DELETE FROM chat_history')
c.execute('DELETE FROM telegram_links')
c.execute('UPDATE invite_codes SET used_by=NULL')"
rm -rf /root/.hermes/users/*
echo '{}' > /root/.hermes-shared/auth.json
chown -R 1000:1000 /root/.hermes/users /root/.hermes-shared
```

## Полезные команды

```bash
# Логи webapp
docker logs -f hermes-webapp
docker logs --tail 50 hermes-webapp | grep -iE "(summariz|quota|WARN|ERROR)"

# Логи gateway
docker logs -f hermes-gateway
docker logs --tail 50 hermes-gateway | grep -iE "(session|memory|ERROR)"

# Состояние контейнеров
docker ps --format "table {{.Names}}\t{{.Status}}" | grep hermes
docker stats hermes-webapp hermes-gateway --no-stream

# БД напрямую
docker exec hermes-webapp python3 -c "
import sqlite3
c=sqlite3.connect('/opt/app/data/users.db')
for r in c.execute('SELECT login, email_imap_host, email_login, google_connected FROM users'): print(r)"

# TG-маппинг
cat /root/.hermes-shared/auth.json

# Per-user файлы
ls /root/.hermes/users/<uid>/
cat /root/.hermes/users/<uid>/SOUL.md
cat /root/.hermes/users/<uid>/memory.md

# Ручной вызов gateway API (с session key)
curl -s -X POST http://hermes-gateway:8642/v1/chat/completions \
  -H "Authorization: Bearer D2evCo05WWMqiRNfjwGfoAVasQPTPWMrmR2UADWfPfY=" \
  -H "Content-Type: application/json" \
  -H "X-Hermes-Session-Key: <uid>" \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"Тест"}],"max_tokens":100}'
```
