#!/usr/bin/env bash
# Hermes multi-user: ежедневный бэкап users.db + users/<uid>/.
# Ротация: 7 последних дней.
# Запуск: crontab 30 3 * * *  (после ispmanager archive-cleanup в 0 3).
set -euo pipefail

SRC_DB="/root/.hermes-app/users.db"
SRC_USERS="/root/.hermes/users"
BACKUPS="/root/.hermes-app/backups"
LOGDIR="/root/.hermes-app/logs"
KEEP=7
DATE="$(date -u +%Y-%m-%d)"
TS="$(date -u +%Y-%m-%dT%H-%M-%S)"
LOG="$LOGDIR/backup-$DATE.log"

mkdir -p "$LOGDIR"

log() { printf '[%s] %s\n' "$TS" "$*" | tee -a "$LOG" >&2 ; }

if [[ ! -f "$SRC_DB" ]]; then
    log "ERROR: $SRC_DB not found"
    exit 1
fi

mkdir -p "$BACKUPS"

# 1) SQLite .backup (атомарно, безопасно для live WAL)
DB_DEST="$BACKUPS/users-$DATE.db"
log "backing up users.db → $DB_DEST"
python3 - "$SRC_DB" "$DB_DEST" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
dst.close()
src.close()
PY

# 2) Per-user files: SOUL.md, memory.md
USERS_DEST="$BACKUPS/users-$DATE"
if [[ -d "$SRC_USERS" ]] && [[ -n "$(ls -A "$SRC_USERS" 2>/dev/null)" ]]; then
    log "backing up users/ → $USERS_DEST"
    rm -rf "$USERS_DEST"
    cp -a "$SRC_USERS" "$USERS_DEST"
else
    log "users/ is empty or missing — skipping"
fi

# 3) Ротация: удалить всё старше KEEP дней
DELETED=$(find "$BACKUPS" -maxdepth 1 -name 'users-*.db' -mtime +$KEEP -print -delete | wc -l)
DELETED_USERS=$(find "$BACKUPS" -maxdepth 1 -name 'users-*' -type d -mtime +$KEEP -print -exec rm -rf {} + 2>/dev/null | wc -l)
log "rotation: deleted $DELETED db files, $DELETED_USERS user-dirs older than $KEEP days"

# 4) Сводка
TOTAL_DB=$(du -sh "$BACKUPS" 2>/dev/null | cut -f1)
COUNT_DB=$(find "$BACKUPS" -maxdepth 1 -name 'users-*.db' | wc -l)
log "done: $COUNT_DB db backups, total $TOTAL_DB in $BACKUPS"
