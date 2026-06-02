"""Hermes multi-user webapp (Phase 1a)."""
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import jwt
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).parent
USERS_DB = Path(os.environ.get("USERS_DB_PATH", "/opt/app/data/users.db"))
HERMES_USERS_DIR = Path(os.environ.get("HERMES_USERS_DIR", "/opt/hermes-users"))
HERMES_SHARED_DIR = Path(os.environ.get("HERMES_SHARED_DIR", "/opt/hermes-shared"))
SOUL_TEMPLATE_PATH = Path(os.environ.get("SOUL_TEMPLATE_PATH", "/opt/app/data/templates/SOUL.md"))

WELCOME_QUOTA = int(os.environ.get("WELCOME_QUOTA", "2000000"))
JWT_SECRET = os.environ.get("JWT_SECRET")
JWT_ALGO = "HS256"
JWT_TTL_HOURS = 24 * 30

if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET env var is required")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(USERS_DB, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


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
    db = get_db()
    db.executescript(SCHEMA)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    return bcrypt.checkpw(pw.encode(), hashed.encode())


def make_token(uid: str) -> str:
    payload = {
        "uid": uid,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_TTL_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def read_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        return payload.get("uid")
    except jwt.PyJWTError:
        return None


def current_user(request: Request) -> str | None:
    token = request.cookies.get("session")
    return read_token(token) if token else None


app = FastAPI(title="hermes-webapp", version="0.1.0")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


@app.on_event("startup")
def startup() -> None:
    USERS_DB.parent.mkdir(parents=True, exist_ok=True)
    HERMES_USERS_DIR.mkdir(parents=True, exist_ok=True)
    HERMES_SHARED_DIR.mkdir(parents=True, exist_ok=True)
    auth_path = HERMES_SHARED_DIR / "auth.json"
    auth_path.touch(exist_ok=True)
    init_db()

    bootstrap = os.environ.get("INVITE_CODE_BOOTSTRAP")
    if bootstrap:
        db = get_db()
        if not db.execute("SELECT 1 FROM invite_codes WHERE code=?", (bootstrap,)).fetchone():
            db.execute("INSERT INTO invite_codes (code, created_at) VALUES (?, ?)", (bootstrap, now_iso()))
            print(f"[bootstrap] invite code created: {bootstrap}", flush=True)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _render(request: Request, name: str, **ctx) -> HTMLResponse:
    status_code = ctx.pop("status_code", 200)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, user: str | None = Depends(current_user)):
    return RedirectResponse("/profile" if user else "/login", status_code=302)


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, user: str | None = Depends(current_user)):
    if user:
        return RedirectResponse("/profile", status_code=302)
    return _render(request, "register.html", error=None, login="")


@app.post("/register")
def register_submit(
    request: Request,
    name: str = Form(...),
    login: str = Form(...),
    password: str = Form(...),
    invite_code: str = Form(...),
):
    login = login.strip().lower()
    if not login or len(password) < 6:
        return _render(request, "register.html", error="Логин пустой или пароль < 6 символов", login=login, status_code=400)

    db = get_db()
    inv = db.execute("SELECT 1 FROM invite_codes WHERE code=? AND used_by IS NULL", (invite_code,)).fetchone()
    if not inv:
        return _render(request, "register.html", error="Неверный или использованный invite-code", login=login, status_code=400)
    if db.execute("SELECT 1 FROM users WHERE login=?", (login,)).fetchone():
        return _render(request, "register.html", error="Логин уже занят", login=login, status_code=400)

    uid = secrets.token_urlsafe(9)
    db.execute(
        "INSERT INTO users (uid, login, name, password_hash, quota_remaining, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (uid, login, name.strip(), hash_password(password), WELCOME_QUOTA, now_iso()),
    )
    db.execute("UPDATE invite_codes SET used_by=? WHERE code=?", (uid, invite_code))

    user_dir = HERMES_USERS_DIR / uid
    user_dir.mkdir(parents=True, exist_ok=True)
    if SOUL_TEMPLATE_PATH.exists():
        (user_dir / "SOUL.md").write_text(SOUL_TEMPLATE_PATH.read_text().format(name=name.strip(), login=login))
    else:
        (user_dir / "SOUL.md").write_text(f"# {name.strip()}\n\nЛичный помощник для @{login}.\n")
    (user_dir / "memory.md").touch()

    resp = RedirectResponse("/profile", status_code=303)
    resp.set_cookie("session", make_token(uid), httponly=True, samesite="lax", max_age=JWT_TTL_HOURS * 3600)
    return resp


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user: str | None = Depends(current_user)):
    if user:
        return RedirectResponse("/profile", status_code=302)
    return _render(request, "login.html", error=None, login="")


@app.post("/login")
def login_submit(request: Request, login: str = Form(...), password: str = Form(...)):
    login = login.strip().lower()
    db = get_db()
    row = db.execute("SELECT uid, password_hash FROM users WHERE login=?", (login,)).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return _render(request, "login.html", error="Неверный логин или пароль", login=login, status_code=401)
    resp = RedirectResponse("/profile", status_code=303)
    resp.set_cookie("session", make_token(row["uid"]), httponly=True, samesite="lax", max_age=JWT_TTL_HOURS * 3600)
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("session")
    return resp


@app.get("/profile", response_class=HTMLResponse)
def profile(request: Request, user: str | None = Depends(current_user)):
    if not user:
        return RedirectResponse("/login", status_code=302)
    db = get_db()
    u = db.execute("SELECT login, name, telegram_id, quota_remaining FROM users WHERE uid=?", (user,)).fetchone()
    if not u:
        return RedirectResponse("/logout", status_code=302)
    soul_path = HERMES_USERS_DIR / user / "SOUL.md"
    soul = soul_path.read_text() if soul_path.exists() else ""
    return _render(request, "profile.html", user={"uid": user, **dict(u)}, soul=soul)
