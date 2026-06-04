"""Shared DB helpers and constants."""
import asyncio
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

USERS_DB = Path(os.environ.get("USERS_DB_PATH", "/opt/app/data/users.db"))
HERMES_USERS_DIR = Path(os.environ.get("HERMES_USERS_DIR", "/opt/hermes-users"))
HERMES_SHARED_DIR = Path(os.environ.get("HERMES_SHARED_DIR", "/opt/hermes-shared"))
QUOTAS_DIR = Path(os.environ.get("QUOTAS_DIR", "/opt/app/data/quotas"))
SOUL_TEMPLATE_PATH_DEFAULT = Path("/opt/app/data/templates/SOUL.md")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(USERS_DB, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


async def aget_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(str(USERS_DB))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def ainit_db() -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, init_db)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    uid TEXT PRIMARY KEY,
    login TEXT UNIQUE NOT NULL,
    name TEXT,
    password_hash TEXT,
    telegram_id INTEGER UNIQUE,
    status TEXT DEFAULT 'active',
    quota_remaining INTEGER,
    quota_used INTEGER DEFAULT 0,
    last_alert_pct INTEGER DEFAULT 0,
    last_summarized_id INTEGER DEFAULT 0,
    email_imap_host TEXT,
    email_imap_port INTEGER DEFAULT 993,
    email_smtp_host TEXT,
    email_smtp_port INTEGER DEFAULT 587,
    email_login TEXT,
    email_password TEXT,
    google_connected INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invite_codes (
    code TEXT PRIMARY KEY,
    used_by TEXT REFERENCES users(uid),
    created_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE TABLE IF NOT EXISTS telegram_links (
    code TEXT PRIMARY KEY,
    uid TEXT REFERENCES users(uid) NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT REFERENCES users(uid) NOT NULL,
    channel TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tokens INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quotas (
    uid TEXT PRIMARY KEY REFERENCES users(uid),
    month TEXT,
    tokens_used INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS action_intents (
    id TEXT PRIMARY KEY,
    uid TEXT NOT NULL REFERENCES users(uid),
    action_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_approval',
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    approved_at TEXT,
    executed_at TEXT,
    result_json TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_chat_uid_created ON chat_history(uid, created_at);
CREATE INDEX IF NOT EXISTS idx_intents_uid_status ON action_intents(uid, status);

-- Scheduled automations (spec 11)
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id TEXT PRIMARY KEY,
    uid TEXT NOT NULL REFERENCES users(uid),
    title TEXT NOT NULL,
    kind TEXT NOT NULL,                    -- 'reminder' | 'morning_digest' | 'custom_prompt'
    status TEXT NOT NULL DEFAULT 'enabled',-- 'enabled' | 'disabled' | 'deleted'
    schedule_type TEXT NOT NULL,           -- 'one_time' | 'daily' | 'weekly'
    run_at TEXT,                           -- ISO UTC; used for one_time and as the seed for daily/weekly
    time_of_day TEXT,                      -- 'HH:MM' for daily/weekly
    weekdays TEXT,                         -- JSON list of ints 0..6
    rrule TEXT,                            -- optional RFC5545/RRULE-ish; for MVP we just use it as a hint
    next_run_at TEXT,                      -- ISO UTC; the worker uses this
    channel TEXT NOT NULL DEFAULT 'web',   -- 'telegram' | 'web' | 'both'
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_run_at TEXT,
    last_result TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_uid_status ON scheduled_jobs(uid, status);
CREATE INDEX IF NOT EXISTS idx_jobs_due ON scheduled_jobs(status, next_run_at);

CREATE TABLE IF NOT EXISTS job_runs (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES scheduled_jobs(id),
    uid TEXT NOT NULL REFERENCES users(uid),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,                  -- 'running' | 'success' | 'error' | 'skipped_quota'
    result TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_job_started ON job_runs(job_id, started_at);

-- Web notification center (spec 11 nice-to-have)
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL REFERENCES users(uid),
    title TEXT NOT NULL,
    body TEXT,
    link TEXT,
    read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notif_uid_created ON notifications(uid, created_at);
"""


def init_db() -> None:
    conn = get_db()
    conn.executescript(SCHEMA)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "last_summarized_id" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN last_summarized_id INTEGER DEFAULT 0")
    if "quota_used" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN quota_used INTEGER DEFAULT 0")
    if "last_alert_pct" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN last_alert_pct INTEGER DEFAULT 0")
    if "email_imap_host" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN email_imap_host TEXT")
    if "email_imap_port" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN email_imap_port INTEGER DEFAULT 993")
    if "email_smtp_host" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN email_smtp_host TEXT")
    if "email_smtp_port" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN email_smtp_port INTEGER DEFAULT 587")
    if "email_login" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN email_login TEXT")
    if "email_password" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN email_password TEXT")
    if "google_connected" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN google_connected INTEGER DEFAULT 0")

    # Migration: add scheduling fields to scheduled_jobs (idempotent).
    _cols_jobs = [r[1] for r in conn.execute("PRAGMA table_info(scheduled_jobs)").fetchall()]
    if "time_of_day" not in _cols_jobs:
        conn.execute("ALTER TABLE scheduled_jobs ADD COLUMN time_of_day TEXT")
    if "weekdays" not in _cols_jobs:
        conn.execute("ALTER TABLE scheduled_jobs ADD COLUMN weekdays TEXT")

    # Migration: encrypt plaintext email passwords
    _migrate_plaintext_passwords(conn)


def _migrate_plaintext_passwords(conn: sqlite3.Connection) -> None:
    """Encrypt any plaintext email passwords that haven't been migrated yet."""
    try:
        from .secrets_store import is_encrypted, encrypt
    except ImportError:
        return  # secrets_store not available yet

    rows = conn.execute(
        "SELECT uid, email_password FROM users WHERE email_password IS NOT NULL AND email_password != ''"
    ).fetchall()
    for row in rows:
        uid, pwd = row["uid"], row["email_password"]
        if not is_encrypted(pwd):
            encrypted = encrypt(pwd, uid)
            conn.execute("UPDATE users SET email_password=? WHERE uid=?", (encrypted, uid))
