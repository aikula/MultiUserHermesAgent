# Hermes cron jobs

Хост-cron jobs для multi-user webapp. Все скрипты должны быть идемпотентными и безопасными для параллельного выполнения.

## backup.sh

Ежедневный бэкап:
- `users.db` → `backups/users-YYYY-MM-DD.db` через SQLite `.backup()` (атомарно, безопасно для live WAL)
- `users/<uid>/` (SOUL.md, memory.md) → `backups/users-YYYY-MM-DD/`
- Ротация: хранить 7 последних дней

Установка в crontab:
```
30 3 * * * /root/.hermes-app/cron/backup.sh
```

Логи: `/root/.hermes-app/logs/backup-YYYY-MM-DD.log`

## quota-observer.sh

(Не используется в v1 — quota tracking делает webapp в `app/quota.py` при каждом вызове. Спецификация v1 предполагала парсить логи gateway, но это невозможно: gateway не знает про наших юзеров.)

## Восстановление

```bash
# Из бэкапа
cp /root/.hermes-app/backups/users-YYYY-MM-DD.db /root/.hermes-app/users.db
# (если webapp запущен — рестартнуть: docker compose --env-file .env.hermes restart webapp)
cp -a /root/.hermes-app/backups/users-YYYY-MM-DD/* /root/.hermes/users/
chown -R 1000:1000 /root/.hermes/users
```
