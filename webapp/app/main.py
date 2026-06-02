"""Hermes multi-user webapp."""
import asyncio
import json
import logging
import os
import secrets
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import httpx
import jwt
from fastapi import Depends, FastAPI, Form, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logging.basicConfig(
    level=os.environ.get("WEBAPP_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from . import chat
from . import summarizer
from .db import HERMES_SHARED_DIR, HERMES_USERS_DIR, SOUL_TEMPLATE_PATH_DEFAULT, USERS_DB, get_db, init_db, now_iso


def _write_auth(telegram_id: int, uid: str) -> None:
    """Пишет {telegram_id: uid} в /opt/hermes-shared/auth.json."""
    path = HERMES_SHARED_DIR / "auth.json"
    try:
        data = json.loads(path.read_text() or "{}") if path.exists() else {}
    except json.JSONDecodeError:
        data = {}
    data[str(telegram_id)] = uid
    path.write_text(json.dumps(data, indent=2, sort_keys=True))

SOUL_TEMPLATE_PATH = Path(os.environ.get("SOUL_TEMPLATE_PATH", str(SOUL_TEMPLATE_PATH_DEFAULT)))
WELCOME_QUOTA = int(os.environ.get("WELCOME_QUOTA", "2000000"))
JWT_SECRET = os.environ.get("JWT_SECRET")
JWT_ALGO = "HS256"
JWT_TTL_HOURS = 24 * 30
INTERNAL_SECRET = os.environ.get("WEBAPP_INTERNAL_SECRET", "")
TELEGRAM_LINK_TTL = int(os.environ.get("TELEGRAM_LINK_TTL", "600"))

if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET env var is required")

APP_DIR = Path(__file__).parent


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


app = FastAPI(title="hermes-webapp", version="0.1.0", root_path="/chat")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


@app.middleware("http")
async def prefix_redirects(request: Request, call_next):
    response = await call_next(request)
    if 300 <= response.status_code < 400:
        loc = response.headers.get("location")
        if loc and loc.startswith("/") and not loc.startswith("//") and not loc.startswith("/chat"):
            response.headers["location"] = "/chat" + loc
    return response


@app.on_event("startup")
async def startup() -> None:
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

    # Start Telegram relay (if configured)
    from .relay import start_relay_task
    await start_relay_task()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _render(request: Request, name: str, **ctx) -> HTMLResponse:
    status_code = ctx.pop("status_code", 200)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, user: str | None = Depends(current_user)):
    if not user:
        return RedirectResponse("/login", status_code=302)
    db = get_db()
    u = db.execute("SELECT login, name FROM users WHERE uid=?", (user,)).fetchone()
    return _render(request, "chat.html", user={"uid": user, **(dict(u) if u else {"login": "?", "name": "?"})})


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
    # active link code (not used, not expired)
    link_row = db.execute(
        "SELECT code, expires_at FROM telegram_links "
        "WHERE uid=? AND used_at IS NULL AND expires_at > ? "
        "ORDER BY expires_at DESC LIMIT 1",
        (user, now_iso()),
    ).fetchone()
    link_code = link_row["code"] if link_row else None
    link_expires = link_row["expires_at"] if link_row else None
    return _render(request, "profile.html", user={"uid": user, **dict(u)}, soul=soul,
                   link_code=link_code, link_expires=link_expires,
                   bot_username=os.environ.get("TELEGRAM_BOT_USERNAME", ""))


@app.post("/api/profile/generate-link")
def api_generate_link(user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")
    db = get_db()
    # expire old codes
    db.execute("DELETE FROM telegram_links WHERE uid=? AND (used_at IS NOT NULL OR expires_at < ?)", (user, now_iso()))
    code = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
    expires = (datetime.now(timezone.utc) + timedelta(seconds=TELEGRAM_LINK_TTL)).isoformat()
    db.execute("INSERT INTO telegram_links (code, uid, expires_at) VALUES (?, ?, ?)", (code, user, expires))
    return JSONResponse({"code": code, "expires_at": expires, "ttl": TELEGRAM_LINK_TTL})


def _check_internal(x_internal_secret: str | None = Header(default=None)):
    if not INTERNAL_SECRET:
        raise HTTPException(503, "internal secret not configured")
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(403, "bad internal secret")
    return True


@app.post("/api/internal/consume-link-code")
async def api_consume_link_code(request: Request, _: bool = Depends(_check_internal)):
    """Relay вызывает: юзер шлёт /start <code> для привязки."""
    body = await request.json()
    code = (body.get("code") or "").strip()
    telegram_id = body.get("telegram_id")
    if not code or not isinstance(telegram_id, int):
        raise HTTPException(400, "code and telegram_id required")
    db = get_db()
    row = db.execute("SELECT uid, expires_at, used_at FROM telegram_links WHERE code=?", (code,)).fetchone()
    if not row or row["used_at"]:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        return JSONResponse({"ok": False, "error": "expired"}, status_code=410)
    uid = row["uid"]
    db.execute("UPDATE telegram_links SET used_at=? WHERE code=?", (now_iso(), code))
    db.execute("UPDATE users SET telegram_id=? WHERE uid=?", (telegram_id, uid))
    _write_auth(telegram_id, uid)
    return JSONResponse({"ok": True, "uid": uid, "kind": "link"})


@app.post("/api/internal/redeem-invite")
async def api_redeem_invite(request: Request, _: bool = Depends(_check_internal)):
    """Relay вызывает: юзер шлёт /start <invite-code> для регистрации."""
    body = await request.json()
    code = (body.get("code") or "").strip()
    telegram_id = body.get("telegram_id")
    name = (body.get("name") or "").strip() or None
    if not code or not isinstance(telegram_id, int):
        raise HTTPException(400, "code and telegram_id required")
    db = get_db()
    inv = db.execute("SELECT 1 FROM invite_codes WHERE code=? AND used_by IS NULL", (code,)).fetchone()
    if not inv:
        return JSONResponse({"ok": False, "error": "invite_not_found"}, status_code=404)
    if db.execute("SELECT 1 FROM users WHERE telegram_id=?", (telegram_id,)).fetchone():
        return JSONResponse({"ok": False, "error": "telegram_already_linked"}, status_code=409)
    uid = secrets.token_urlsafe(9)
    login = f"tg_{telegram_id}"
    n = 0
    while db.execute("SELECT 1 FROM users WHERE login=?", (login,)).fetchone():
        n += 1
        login = f"tg_{telegram_id}_{n}"
    display = name or f"TG_{telegram_id}"
    db.execute(
        "INSERT INTO users (uid, login, name, password_hash, telegram_id, quota_remaining, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (uid, login, display, hash_password(secrets.token_urlsafe(16)), telegram_id, WELCOME_QUOTA, now_iso()),
    )
    db.execute("UPDATE invite_codes SET used_by=? WHERE code=?", (uid, code))
    user_dir = HERMES_USERS_DIR / uid
    user_dir.mkdir(parents=True, exist_ok=True)
    if SOUL_TEMPLATE_PATH.exists():
        (user_dir / "SOUL.md").write_text(SOUL_TEMPLATE_PATH.read_text().format(name=display, login=login))
    else:
        (user_dir / "SOUL.md").write_text(f"# {display}\n\nЛичный помощник.\n")
    (user_dir / "memory.md").touch()
    _write_auth(telegram_id, uid)
    return JSONResponse({"ok": True, "uid": uid, "login": login, "kind": "register"})


@app.get("/api/history")
def api_history(request: Request, user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")
    return JSONResponse(chat.get_history(user))


@app.post("/api/chat")
async def api_chat(request: Request, user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")
    body = await request.json()
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(400, "empty content")
    if len(content) > 8000:
        raise HTTPException(400, "message too long (max 8000 chars)")

    chat.save_message(user, "web", "user", content, 0)
    history = chat.get_history(user)
    messages = [{"role": "system", "content": chat.build_system_prompt(user)}]
    for h in history:
        messages.append({"role": h["role"], "content": h["content"]})

    try:
        result = await chat.call_hermes(messages)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Hermes API error: {e}") from e

    chat.save_message(user, "web", "assistant", result["content"], result["total_tokens"])
    asyncio.create_task(summarizer.maybe_summarize(user))
    return JSONResponse({
        "content": result["content"],
        "usage": {
            "prompt_tokens": result["prompt_tokens"],
            "completion_tokens": result["completion_tokens"],
            "total_tokens": result["total_tokens"],
        },
        "finish_reason": result["finish_reason"],
    })
