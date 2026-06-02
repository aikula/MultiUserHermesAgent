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
9d8e7f6c5b4a39281706f5e4d3c2b1a098f7e6d5c4b3a29180706f5e4d3c2b1a0
```

**Telegram:** `TELEGRAM_BOT_TOKEN` пуст — бот не запущен, TG-флоу проверяется через прямые вызовы internal API.

---

## 1. Базовый web-флоу (alice)

- [ ] Открыть https://hermes.kulinich.ru/chat/ → редирект на `/chat/login`
- [ ] Войти как `alice` / `secret123` → `/chat/` (чат)
- [ ] В шапке справа: ссылки «Профиль», «Выйти»
- [ ] Отправить «Привет, как дела?»
- [ ] Внизу появилось `промпт N → ответ M (всего K)`
- [ ] Открыть `/chat/profile` — progress bar `0%`, без badge

Заметки:
```


```

## 2. Квоты

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

Заметки:
```


```

## 3. Telegram-привязка (alice, link-code `12yAB0Kj`)

- [ ] `/chat/profile` → блок «Telegram» → код `12yAB0Kj` виден
- [ ] Ссылка «Открыть бота с этим кодом» (если задан `TELEGRAM_BOT_USERNAME`)

Имитация relay (новый TG-юзер нажал `/start 12yAB0Kj`):

```bash
curl -X POST https://hermes.kulinich.ru/chat/api/internal/consume-link-code \
  -H "Content-Type: application/json" \
  -H "X-Internal-Secret: 9d8e7f6c5b4a39281706f5e4d3c2b1a098f7e6d5c4b3a29180706f5e4d3c2b1a0" \
  -d '{"code":"12yAB0Kj","telegram_id":123456789}'
```

- [ ] Ответ: `{"ok":true,"uid":"EDRR3qt7dOOJ","kind":"link"}`
- [ ] `cat /root/.hermes-shared/auth.json` → `{"123456789": "EDRR3qt7dOOJ"}`
- [ ] `/chat/profile` → «✅ привязан (123456789)»

Заметки:
```


```

## 4. Регистрация через invite-code (имитация TG-юзера)

Создать invite:
```bash
docker exec hermes-webapp python3 -c "
import sqlite3
c=sqlite3.connect('/opt/app/data/users.db', isolation_level=None)
c.execute(\"INSERT INTO invite_codes (code, created_at) VALUES ('test-tg-2026', '2026-06-02T20:00:00+00:00')\")"
```

Имитация `/start test-tg-2026` от нового TG-юзера:
```bash
curl -X POST https://hermes.kulinich.ru/chat/api/internal/redeem-invite \
  -H "Content-Type: application/json" \
  -H "X-Internal-Secret: 9d8e7f6c5b4a39281706f5e4d3c2b1a098f7e6d5c4b3a29180706f5e4d3c2b1a0" \
  -d '{"code":"test-tg-2026","telegram_id":987654321}'
```

- [ ] Ответ: `{"ok":true,"uid":"<new>","login":"tg_987654321","kind":"register"}`
- [ ] `/root/.hermes/users/<uid>/SOUL.md` создан
- [ ] `auth.json`: `{"123456789":"EDRR3qt7dOOJ","987654321":"<uid>"}`

Заметки:
```


```

## 5. Память (memory summarizer)

- [ ] Войти как `bob` (пустой чат)
- [ ] Отправить 20+ сообщений (например «факт N: ...»)
- [ ] Подождать 10 секунд
- [ ] В логах webapp:
  ```bash
  docker logs --tail 30 hermes-webapp | grep -i summariz
  # INFO root: summarized uid=...: +N msgs, memory=M bytes
  ```
- [ ] `cat /root/.hermes/users/kfVto-kwrZCz/memory.md` — структурированный Markdown
- [ ] Следующее сообщение в чате → агент «помнит» факты

Заметки / примеры фактов:
```


```

## 6. Alert 80%

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

Заметки:
```


```

## 7. Бэкапы

- [ ] `crontab -l | grep backup` → `30 3 * * * /root/Agents/Hermes/cron/backup.sh`
- [ ] Запустить вручную:
  ```bash
  /root/Agents/Hermes/cron/backup.sh
  cat /root/.hermes-app/logs/backup-$(date -u +%Y-%m-%d).log
  ```
- [ ] `ls /root/.hermes-app/backups/` → `users-YYYY-MM-DD.db` + `users-YYYY-MM-DD/`

Заметки:
```


```

## 8. Выход

- [ ] `/chat/logout` → редирект на `/chat/login`, cookie удалена
- [ ] `/chat/` → редирект на `/chat/login` (без auth)

Заметки:
```


```

---

## Общие баги / находки

```
[ ] 
[ ] 
[ ] 
```

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

# Состояние контейнеров
docker ps --format "table {{.Names}}\t{{.Status}}" | grep hermes
docker stats hermes-webapp hermes-gateway --no-stream

# БД напрямую
docker exec hermes-webapp python3 -c "
import sqlite3
c=sqlite3.connect('/opt/app/data/users.db')
for r in c.execute('SELECT login, quota_remaining, quota_used, last_alert_pct FROM users'): print(r)"

# Daily JSON квоты
cat /root/.hermes-app/quotas/<uid>/$(date -u +%Y-%m-%d).json | python3 -m json.tool

# TG-маппинг
cat /root/.hermes-shared/auth.json

# Per-user файлы
ls /root/.hermes/users/<uid>/
cat /root/.hermes/users/<uid>/memory.md
```
