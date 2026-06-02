"""Shared DB helpers and constants."""
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

USERS_DB = Path(os.environ.get("USERS_DB_PATH", "/opt/app/data/users.db"))
HERMES_USERS_DIR = Path(os.environ.get("HERMES_USERS_DIR", "/opt/hermes-users"))
HERMES_SHARED_DIR = Path(os.environ.get("HERMES_SHARED_DIR", "/opt/hermes-shared"))
SOUL_TEMPLATE_PATH_DEFAULT = Path("/opt/app/data/templates/SOUL.md")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(USERS_DB, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


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
CREATE INDEX IF NOT EXISTS idx_chat_uid_created ON chat_history(uid, created_at);
"""


def init_db() -> None:
    get_db().executescript(SCHEMA)
