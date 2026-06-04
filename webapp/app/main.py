"""Hermes multi-user webapp."""
import asyncio
import hmac
import hashlib
import json
import logging
import os
import secrets
import string
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import httpx
import jwt
from fastapi import Depends, FastAPI, File, Form, HTTPException, Header, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .secrets_store import encrypt

# --- Rate limiter (in-memory, per IP) ---
_login_attempts: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_WINDOW = 300  # 5 minutes
_RATE_LIMIT_MAX = 10  # max attempts per window


def _check_rate_limit(ip: str) -> bool:
    """Return True if request is allowed, False if rate-limited."""
    now = time.time()
    cutoff = now - _RATE_LIMIT_WINDOW
    _login_attempts[ip] = [t for t in _login_attempts[ip] if t > cutoff]
    if len(_login_attempts[ip]) >= _RATE_LIMIT_MAX:
        return False
    _login_attempts[ip].append(now)
    return True


# --- CSRF protection ---
def generate_csrf_token(session_token: str) -> str:
    """Generate CSRF token from session token."""
    secret = os.environ.get("JWT_SECRET", "")
    return hmac.new(secret.encode(), session_token.encode(), hashlib.sha256).hexdigest()[:32]


def validate_csrf_token(session_token: str, csrf_token: str) -> bool:
    """Validate CSRF token matches session."""
    if not session_token or not csrf_token:
        return False
    expected = generate_csrf_token(session_token)
    return hmac.compare_digest(expected, csrf_token)

logging.basicConfig(
    level=os.environ.get("WEBAPP_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from . import chat  # noqa: E402
from . import quota  # noqa: E402
from . import summarizer  # noqa: E402
from .db import HERMES_SHARED_DIR, HERMES_USERS_DIR, SOUL_TEMPLATE_PATH_DEFAULT, USERS_DB, get_db, ainit_db, now_iso  # noqa: E402


def _write_auth(telegram_id: int, uid: str) -> None:
    """Пишет {telegram_id: uid} в /opt/hermes-shared/auth.json."""
    path = HERMES_SHARED_DIR / "auth.json"
    try:
        data = json.loads(path.read_text() or "{}") if path.exists() else {}
    except json.JSONDecodeError:
        data = {}
    data[str(telegram_id)] = uid
    path.write_text(json.dumps(data, indent=2, sort_keys=True))


async def _awrite_auth(telegram_id: int, uid: str) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _write_auth, telegram_id, uid)

SOUL_TEMPLATE_PATH = Path(os.environ.get("SOUL_TEMPLATE_PATH", str(SOUL_TEMPLATE_PATH_DEFAULT)))
WELCOME_QUOTA = int(os.environ.get("WELCOME_QUOTA", "2000000"))
JWT_SECRET = os.environ.get("JWT_SECRET")
JWT_ALGO = "HS256"
JWT_TTL_HOURS = 24 * 30
INTERNAL_SECRET = os.environ.get("WEBAPP_INTERNAL_SECRET", "")
TELEGRAM_LINK_TTL = int(os.environ.get("TELEGRAM_LINK_TTL", "600"))
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() == "true"
COOKIE_SAMESITE = os.environ.get("COOKIE_SAMESITE", "lax").lower()

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


def require_csrf(request: Request) -> None:
    """Validate CSRF token for browser POST requests.
    Internal endpoints with X-Internal-Secret are exempt.
    """
    # Internal endpoints are exempt from CSRF
    if request.headers.get("X-Internal-Secret"):
        return

    session_token = request.cookies.get("session")
    if not session_token:
        raise HTTPException(401, "not authenticated")

    # Get CSRF token from form data or header
    csrf_token = request.headers.get("X-CSRF-Token")
    if not csrf_token:
        # For form submissions, we'll need to read the body
        # But since we're using JSON for API, check header first
        raise HTTPException(403, "CSRF token missing")

    if not validate_csrf_token(session_token, csrf_token):
        raise HTTPException(403, "CSRF token invalid")


app = FastAPI(title="hermes-webapp", version="0.1.0", root_path="/chat")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
templates.env.globals["chat_prefix"] = "/chat"


def _get_csrf_token(request: Request) -> str:
    """Get CSRF token for current session."""
    session_token = request.cookies.get("session", "")
    if session_token:
        return generate_csrf_token(session_token)
    return ""


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
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: USERS_DB.parent.mkdir(parents=True, exist_ok=True))
    await loop.run_in_executor(None, lambda: HERMES_USERS_DIR.mkdir(parents=True, exist_ok=True))
    await loop.run_in_executor(None, lambda: HERMES_SHARED_DIR.mkdir(parents=True, exist_ok=True))
    auth_path = HERMES_SHARED_DIR / "auth.json"
    await loop.run_in_executor(None, lambda: auth_path.touch(exist_ok=True))
    await ainit_db()

    bootstrap = os.environ.get("INVITE_CODE_BOOTSTRAP")
    if bootstrap:
        db = await loop.run_in_executor(None, get_db)
        found = await loop.run_in_executor(None, lambda: db.execute("SELECT 1 FROM invite_codes WHERE code=?", (bootstrap,)).fetchone())
        if not found:
            await loop.run_in_executor(None, lambda: db.execute("INSERT INTO invite_codes (code, created_at) VALUES (?, ?)", (bootstrap, now_iso())))
            print(f"[bootstrap] invite code created: {bootstrap}", flush=True)

    # Start Telegram relay (if configured)
    from .relay import start_relay_task
    await start_relay_task()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _render(request: Request, name: str, **ctx) -> HTMLResponse:
    status_code = ctx.pop("status_code", 200)
    ctx["csrf_token"] = _get_csrf_token(request)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, user: str | None = Depends(current_user)):
    if not user:
        return RedirectResponse("/login", status_code=302)
    u = await asyncio.to_thread(
        lambda: get_db().execute("SELECT login, name FROM users WHERE uid=?", (user,)).fetchone()
    )
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
    if not login or len(password) < 10:
        return _render(request, "register.html", error="Логин пустой или пароль < 10 символов", login=login, status_code=400)

    db = get_db()
    inv = db.execute(
        "SELECT 1 FROM invite_codes WHERE code=? AND used_by IS NULL "
        "AND (expires_at IS NULL OR expires_at > ?)",
        (invite_code, now_iso()),
    ).fetchone()
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
    resp.set_cookie("session", make_token(uid), httponly=True, samesite=COOKIE_SAMESITE, secure=COOKIE_SECURE, max_age=JWT_TTL_HOURS * 3600)
    return resp


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user: str | None = Depends(current_user)):
    if user:
        return RedirectResponse("/profile", status_code=302)
    return _render(request, "login.html", error=None, login="")


@app.post("/login")
def login_submit(request: Request, login: str = Form(...), password: str = Form(...)):
    # Rate limit check
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        return _render(request, "login.html", error="Слишком много попыток. Подождите 5 минут.", login=login, status_code=429)

    login = login.strip().lower()
    db = get_db()
    row = db.execute("SELECT uid, password_hash FROM users WHERE login=?", (login,)).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return _render(request, "login.html", error="Неверный логин или пароль", login=login, status_code=200)
    resp = RedirectResponse("/profile", status_code=303)
    resp.set_cookie("session", make_token(row["uid"]), httponly=True, samesite=COOKIE_SAMESITE, secure=COOKIE_SECURE, max_age=JWT_TTL_HOURS * 3600)
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
    u = db.execute(
        "SELECT login, name, telegram_id, quota_remaining, "
        "email_imap_host, email_imap_port, email_smtp_host, email_smtp_port, "
        "email_login, email_password, google_connected FROM users WHERE uid=?",
        (user,),
    ).fetchone()
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
                   bot_username=os.environ.get("TELEGRAM_BOT_USERNAME", ""),
                   usage=quota.get_usage(user))


@app.post("/api/profile/update")
async def api_profile_update(request: Request, user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")
    require_csrf(request)
    body = await request.json()
    name = (body.get("name") or "").strip()
    current_password = (body.get("current_password") or "").strip()
    new_password = (body.get("new_password") or "").strip()
    soul_md = body.get("soul_md")

    loop = asyncio.get_running_loop()
    row = await loop.run_in_executor(None, lambda: get_db().execute("SELECT password_hash FROM users WHERE uid=?", (user,)).fetchone())

    changed = ""

    if new_password:
        if not current_password:
            raise HTTPException(400, "current_password required to change password")
        if len(new_password) < 10:
            raise HTTPException(400, "new password too short (min 10 chars)")
        if not verify_password(current_password, row["password_hash"]):
            raise HTTPException(403, "current password is wrong")
        pwhash = await loop.run_in_executor(None, hash_password, new_password)
        await loop.run_in_executor(None, lambda: get_db().execute("UPDATE users SET password_hash=? WHERE uid=?", (pwhash, user)))
        changed = "password"

    if name:
        await loop.run_in_executor(None, lambda: get_db().execute("UPDATE users SET name=? WHERE uid=?", (name, user)))
        changed = (changed + ", " if changed else "") + "name"

    if soul_md is not None:
        soul_path = HERMES_USERS_DIR / user / "SOUL.md"
        await loop.run_in_executor(None, lambda: soul_path.write_text(soul_md))
        changed = (changed + ", " if changed else "") + "SOUL.md"

    if not changed:
        raise HTTPException(400, "nothing to update")

    return JSONResponse({"ok": True, "changed": changed})


@app.post("/api/profile/email")
async def api_profile_email(request: Request, user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")
    require_csrf(request)
    body = await request.json()
    imap_host = (body.get("imap_host") or "").strip()
    imap_port = body.get("imap_port") or 993
    smtp_host = (body.get("smtp_host") or "").strip()
    smtp_port = body.get("smtp_port") or 587
    email_login = (body.get("email_login") or "").strip()
    email_password = (body.get("email_password") or "").strip()

    if not all([imap_host, smtp_host, email_login, email_password]):
        raise HTTPException(400, "all fields required")

    # Validate ports are valid integers
    try:
        imap_port = int(imap_port)
        smtp_port = int(smtp_port)
        if imap_port < 1 or imap_port > 65535 or smtp_port < 1 or smtp_port > 65535:
            raise HTTPException(400, "port must be 1-65535")
    except (ValueError, TypeError):
        raise HTTPException(400, "port must be a number")

    # Encrypt password before storing (CPU-bound PBKDF2)
    loop = asyncio.get_running_loop()
    encrypted_password = await loop.run_in_executor(None, encrypt, email_password, user)

    await loop.run_in_executor(None, lambda: get_db().execute(
        "UPDATE users SET email_imap_host=?, email_imap_port=?, email_smtp_host=?, "
        "email_smtp_port=?, email_login=?, email_password=? WHERE uid=?",
        (imap_host, imap_port, smtp_host, smtp_port, email_login, encrypted_password, user),
    ))
    return JSONResponse({"ok": True})


@app.post("/api/profile/email/clear")
async def api_profile_email_clear(request: Request, user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")
    require_csrf(request)
    await asyncio.to_thread(lambda: get_db().execute(
        "UPDATE users SET email_imap_host=NULL, email_imap_port=993, email_smtp_host=NULL, "
        "email_smtp_port=587, email_login=NULL, email_password=NULL WHERE uid=?",
        (user,),
    ))
    return JSONResponse({"ok": True})


@app.get("/api/profile/google/status")
def api_google_status(user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")
    token_path = HERMES_USERS_DIR / user / "google_token.json"
    connected = token_path.exists()
    return JSONResponse({"connected": connected})


@app.post("/api/profile/google/disconnect")
async def api_google_disconnect(request: Request, user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")
    require_csrf(request)
    token_path = HERMES_USERS_DIR / user / "google_token.json"
    if token_path.exists():
        await asyncio.to_thread(token_path.unlink)
    await asyncio.to_thread(
        lambda: get_db().execute(
            "UPDATE users SET google_connected=0 WHERE uid=?", (user,)
        )
    )
    return JSONResponse({"ok": True})


@app.post("/api/profile/generate-link")
def api_generate_link(request: Request, user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")
    require_csrf(request)
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
    if not x_internal_secret or not hmac.compare_digest(x_internal_secret, INTERNAL_SECRET):
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
    loop = asyncio.get_running_loop()

    def _consume():
        db = get_db()
        row = db.execute("SELECT uid, expires_at, used_at FROM telegram_links WHERE code=?", (code,)).fetchone()
        if not row or row["used_at"]:
            return None, "not_found"
        if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
            return None, "expired"
        uid = row["uid"]
        db.execute("UPDATE telegram_links SET used_at=? WHERE code=?", (now_iso(), code))
        db.execute("UPDATE users SET telegram_id=? WHERE uid=?", (telegram_id, uid))
        return uid, None

    uid, err = await loop.run_in_executor(None, _consume)
    if err == "not_found":
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    if err == "expired":
        return JSONResponse({"ok": False, "error": "expired"}, status_code=410)
    await _awrite_auth(telegram_id, uid)
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
    loop = asyncio.get_running_loop()

    def _redeem():
        db = get_db()
        inv = db.execute(
            "SELECT 1 FROM invite_codes WHERE code=? AND used_by IS NULL "
            "AND (expires_at IS NULL OR expires_at > ?)",
            (code, now_iso()),
        ).fetchone()
        if not inv:
            return "invite_not_found"
        if db.execute("SELECT 1 FROM users WHERE telegram_id=?", (telegram_id,)).fetchone():
            return "telegram_already_linked"
        uid = secrets.token_urlsafe(9)
        login = f"tg_{telegram_id}"
        n = 0
        while db.execute("SELECT 1 FROM users WHERE login=?", (login,)).fetchone():
            n += 1
            login = f"tg_{telegram_id}_{n}"
        display = name or f"TG_{telegram_id}"
        pwhash = hash_password(secrets.token_urlsafe(16))
        db.execute(
            "INSERT INTO users (uid, login, name, password_hash, telegram_id, quota_remaining, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uid, login, display, pwhash, telegram_id, WELCOME_QUOTA, now_iso()),
        )
        db.execute("UPDATE invite_codes SET used_by=? WHERE code=?", (uid, code))
        user_dir = HERMES_USERS_DIR / uid
        user_dir.mkdir(parents=True, exist_ok=True)
        if SOUL_TEMPLATE_PATH.exists():
            (user_dir / "SOUL.md").write_text(SOUL_TEMPLATE_PATH.read_text().format(name=display, login=login))
        else:
            (user_dir / "SOUL.md").write_text(f"# {display}\n\nЛичный помощник.\n")
        (user_dir / "memory.md").touch()
        return ("ok", uid, login)

    result = await loop.run_in_executor(None, _redeem)
    if isinstance(result, str):
        if result == "invite_not_found":
            return JSONResponse({"ok": False, "error": "invite_not_found"}, status_code=404)
        if result == "telegram_already_linked":
            return JSONResponse({"ok": False, "error": "telegram_already_linked"}, status_code=409)
    _, uid, login = result
    await _awrite_auth(telegram_id, uid)
    return JSONResponse({"ok": True, "uid": uid, "login": login, "kind": "register"})


@app.post("/api/telegram/send")
async def api_telegram_send(request: Request, _: bool = Depends(_check_internal)):
    """Gateway вызывает для отправки сообщений через webapp relay.
    Тело: {"chat_id": int, "text": str, "parse_mode": str?}
    """
    body = await request.json()
    chat_id = body.get("chat_id")
    text = (body.get("text") or "").strip()
    parse_mode = body.get("parse_mode")
    if not isinstance(chat_id, int) or not text:
        raise HTTPException(400, "chat_id (int) and text (str) required")
    try:
        from .relay import send_message
        await send_message(chat_id, text, parse_mode=parse_mode)
        return JSONResponse({"ok": True})
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@app.get("/api/history")
def api_history(request: Request, user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")
    return JSONResponse(chat.get_history(user))


@app.get("/api/usage")
def api_usage(request: Request, user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")
    return JSONResponse(quota.get_usage(user))


# --- Files UI (spec 10) ---

def _file_service_error_response(e: "Exception") -> JSONResponse:
    """Convert FileServiceError into a JSON response with the right HTTP code."""
    from .file_service import FileServiceError
    assert isinstance(e, FileServiceError)
    return JSONResponse({"ok": False, "error": e.message}, status_code=e.code)


@app.get("/files", response_class=HTMLResponse)
async def files_page(request: Request, path: str = "", user: str | None = Depends(current_user)):
    if not user:
        return RedirectResponse("/login", status_code=302)
    from . import file_service
    try:
        listing = await asyncio.to_thread(file_service.list_files, user, path)
    except file_service.FileServiceError as e:
        # Render an empty listing with the error so the user can navigate back.
        listing = {"current_path": path, "breadcrumbs": [{"name": "files", "path": ""}],
                   "directories": [], "files": [], "total_size": 0,
                   "total_size_human": "0 B", "storage_limit": file_service.USER_STORAGE_QUOTA_BYTES,
                   "storage_limit_human": file_service._human_size(file_service.USER_STORAGE_QUOTA_BYTES),
                   "error": e.message}
    u = await asyncio.to_thread(
        lambda: get_db().execute("SELECT login, name FROM users WHERE uid=?", (user,)).fetchone()
    )
    return _render(request, "files.html", user={"uid": user, **(dict(u) if u else {"login": "?", "name": "?"})},
                   listing=listing)


@app.get("/skills", response_class=HTMLResponse)
async def skills_page(request: Request, user: str | None = Depends(current_user)):
    if not user:
        return RedirectResponse("/login", status_code=302)
    from .skills.loader import get_skill, list_skills
    skills = await asyncio.to_thread(list_skills)
    # Eagerly include full text so the page renders without an extra fetch.
    enriched = []
    for s in skills:
        enriched.append({
            "name": s.name,
            "title": s.title,
            "hint": s.hint,
            "full": get_skill(s.name) or "",
        })
    u = await asyncio.to_thread(
        lambda: get_db().execute("SELECT login, name FROM users WHERE uid=?", (user,)).fetchone()
    )
    return _render(request, "skills.html", user={"uid": user, **(dict(u) if u else {"login": "?", "name": "?"})},
                   skills=enriched)


@app.get("/api/skills/list")
async def api_skills_list(user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")
    from .skills.loader import list_skills
    skills = await asyncio.to_thread(list_skills)
    return JSONResponse({"ok": True, "skills": [s.to_dict() for s in skills]})


@app.get("/api/skills/{name}")
async def api_skill_get(name: str, user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")
    from .skills.loader import get_skill
    md = await asyncio.to_thread(get_skill, name)
    if md is None:
        return JSONResponse({"ok": False, "error": "Skill not found"}, status_code=404)
    return JSONResponse({"ok": True, "name": name, "content": md})


@app.get("/api/files")
async def api_files_list(path: str = "", user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")
    from . import file_service
    try:
        listing = await asyncio.to_thread(file_service.list_files, user, path)
    except file_service.FileServiceError as e:
        return _file_service_error_response(e)
    return JSONResponse(listing)


@app.post("/api/files/mkdir")
async def api_files_mkdir(request: Request, user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")
    require_csrf(request)
    body = await request.json()
    path = (body.get("path") or "").strip()
    name = (body.get("name") or "").strip()
    from . import file_service
    try:
        result = await asyncio.to_thread(file_service.create_folder, user, path, name)
    except file_service.FileServiceError as e:
        return _file_service_error_response(e)
    return JSONResponse({"ok": True, "folder": result})


@app.post("/api/files/upload")
async def api_files_upload(
    request: Request,
    path: str = Form(""),
    file: UploadFile = File(...),
    user: str | None = Depends(current_user),
):
    if not user:
        raise HTTPException(401, "not authenticated")
    require_csrf(request)
    content = await file.read()
    from . import file_service
    try:
        result = await asyncio.to_thread(
            file_service.save_upload, user, path, file.filename or "upload", content
        )
    except file_service.FileServiceError as e:
        return _file_service_error_response(e)
    return JSONResponse({"ok": True, "file": result})


@app.get("/api/files/download")
async def api_files_download(path: str, user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")
    from . import file_service
    try:
        target = await asyncio.to_thread(file_service.resolve_for_download, user, path)
    except file_service.FileServiceError as e:
        return _file_service_error_response(e)
    # FileResponse streams the file with a sane default filename.
    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type="application/octet-stream",
    )


@app.post("/api/files/delete")
async def api_files_delete(request: Request, user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")
    require_csrf(request)
    body = await request.json()
    path = (body.get("path") or "").strip()
    from . import file_service
    try:
        result = await asyncio.to_thread(file_service.delete_path, user, path)
    except file_service.FileServiceError as e:
        return _file_service_error_response(e)
    return JSONResponse({"ok": True, **result})


@app.post("/api/files/write-text")
async def api_files_write_text(request: Request, user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")
    require_csrf(request)
    body = await request.json()
    path = (body.get("path") or "").strip()
    name = (body.get("name") or "").strip()
    content = body.get("content") or ""
    from . import file_service
    try:
        result = await asyncio.to_thread(file_service.write_text_file, user, path, name, content)
    except file_service.FileServiceError as e:
        return _file_service_error_response(e)
    return JSONResponse({"ok": True, "file": result})


@app.post("/api/chat")
async def api_chat(request: Request, user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")

    # CSRF validation
    require_csrf(request)

    body = await request.json()
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(400, "empty content")
    if len(content) > 8000:
        raise HTTPException(400, "message too long (max 8000 chars)")

    loop = asyncio.get_running_loop()

    # P1-1: Web chat confirmation pre-check — handle pending intent before Hermes
    from .approval import (
        get_pending_intent, is_confirmation, is_rejection,
        approve_intent, execute_intent, reject_intent,
        create_intent, REVIEW_ACTIONS,
    )
    pending = await loop.run_in_executor(None, get_pending_intent, user)
    if pending:
        if is_confirmation(content):
            if await loop.run_in_executor(None, approve_intent, pending["id"]):
                payload = json.loads(pending["payload_json"])
                try:
                    if pending["action_type"] == "email_send":
                        from .tools.email_tools import send_email
                        result = await loop.run_in_executor(
                            None, send_email,
                            user, payload["to"], payload["subject"], payload["body"],
                        )
                        await loop.run_in_executor(None, execute_intent, pending["id"], json.dumps(result), None)
                        await loop.run_in_executor(None, chat.save_message, user, "web", "assistant",
                                          f"✅ Письмо отправлено на {payload['to']}", 0)
                        return JSONResponse({"ok": True, "intent_id": pending["id"], "result": result})
                    else:
                        await loop.run_in_executor(None, execute_intent, pending["id"], None, f"Unknown action: {pending['action_type']}")
                        return JSONResponse({"ok": False, "error": f"Unknown action: {pending['action_type']}"})
                except Exception as e:
                    await loop.run_in_executor(None, execute_intent, pending["id"], None, str(e))
                    return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
            else:
                return JSONResponse({"ok": False, "error": "Intent expired or already processed"}, status_code=410)
        elif is_rejection(content):
            await loop.run_in_executor(None, reject_intent, pending["id"])
            await loop.run_in_executor(None, chat.save_message, user, "web", "assistant", "❌ Действие отменено.", 0)
            return JSONResponse({"ok": True, "rejected": True})

    # Hard quota check — block before Hermes call (with reserve)
    ok, err_msg = await loop.run_in_executor(None, quota.check_quota, user, quota.MAX_TOKENS_PER_RESPONSE + quota.MIN_QUOTA_RESERVE_TOKENS)
    if not ok:
        raise HTTPException(429, err_msg)

    # Skill activation (spec 13): user message may start with `[Используй навык: name]`.
    # The full skill text is injected into THIS turn's user message, while the raw
    # content (with marker) is preserved in history for audit.
    skill_name, cleaned_content = chat.detect_skill_request(content)
    if skill_name and chat.get_skill(skill_name) is None:
        # Unknown skill — treat as if marker wasn't there, but tell the client
        skill_name = None
    effective_user_text = cleaned_content if (skill_name and cleaned_content) else content
    if not effective_user_text:
        raise HTTPException(400, "empty content after skill marker")

    await loop.run_in_executor(None, chat.save_message, user, "web", "user", content, 0)
    history = await loop.run_in_executor(None, chat.get_history, user)
    system_prompt = await loop.run_in_executor(None, chat.build_system_prompt, user)
    messages = [{"role": "system", "content": system_prompt}]
    for h in history:
        messages.append({"role": h["role"], "content": h["content"]})
    # Replace the last user turn (just-saved raw content) with the cleaned version
    # + skill-injected wrapper when a skill is active.
    if skill_name and cleaned_content:
        messages.pop()  # the raw `[...]\n`+cleaned entry
        messages.append(chat.build_skill_user_message(skill_name, cleaned_content))

    try:
        result = await chat.call_hermes(messages, uid=user)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Hermes API error: {e}") from e

    await loop.run_in_executor(None, chat.save_message, user, "web", "assistant", result["content"], result["total_tokens"])
    await loop.run_in_executor(None, quota.record, user, "web", result["total_tokens"])
    asyncio.create_task(summarizer.maybe_summarize(user))

    # Check if response contains an action intent that needs approval
    intent_data = _extract_intent_from_response(result["content"])
    if intent_data and intent_data["action_type"] in REVIEW_ACTIONS:
        # P1-3: Strip action_intent JSON from user-visible content
        clean_content = _strip_intent_block(result["content"])
        intent = await loop.run_in_executor(None, create_intent, user, intent_data["action_type"], intent_data["payload"])
        return JSONResponse({
            "content": clean_content,
            "usage": {
                "prompt_tokens": result["prompt_tokens"],
                "completion_tokens": result["completion_tokens"],
                "total_tokens": result["total_tokens"],
            },
            "finish_reason": result["finish_reason"],
            "skill": skill_name,
            "approval": {
                "intent_id": intent["id"],
                "action_type": intent["action_type"],
                "payload": intent_data["payload"],
            },
        })

    return JSONResponse({
        "content": result["content"],
        "usage": {
            "prompt_tokens": result["prompt_tokens"],
            "completion_tokens": result["completion_tokens"],
            "total_tokens": result["total_tokens"],
        },
        "finish_reason": result["finish_reason"],
        "skill": skill_name,
    })


def _extract_intent_from_response(content: str) -> dict | None:
    """Extract action intent from agent response if it follows the format."""
    import re
    import json

    # Look for ACTION_INTENT block in response
    match = re.search(r'```action_intent\n(.*?)\n```', content, re.DOTALL)
    if not match:
        return None

    try:
        data = json.loads(match.group(1))
        if "action_type" in data and "payload" in data:
            return data
    except json.JSONDecodeError:
        pass
    return None


def _strip_intent_block(content: str) -> str:
    """Remove action_intent JSON block from user-visible content."""
    import re
    cleaned = re.sub(r'\n?```action_intent\n.*?\n```\n?', '', content, flags=re.DOTALL).strip()
    if not cleaned:
        return "(действие подготовлено — смотри карточку подтверждения)"
    return cleaned


@app.post("/api/approve")
async def api_approve(request: Request, user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")

    # CSRF validation
    require_csrf(request)

    body = await request.json()
    intent_id = body.get("intent_id")
    if not intent_id:
        raise HTTPException(400, "intent_id required")

    loop = asyncio.get_running_loop()
    from .approval import approve_intent, get_intent_by_id_for_user, execute_intent
    from .tools.email_tools import send_email

    intent = await loop.run_in_executor(None, get_intent_by_id_for_user, intent_id, user)
    if not intent:
        raise HTTPException(404, "intent not found")
    if intent["status"] != "pending_approval":
        return JSONResponse({"ok": False, "error": f"Intent already {intent['status']}"}, status_code=200)

    if not await loop.run_in_executor(None, approve_intent, intent_id):
        raise HTTPException(410, "intent expired or already processed")

    # Execute the action
    payload = json.loads(intent["payload_json"])
    try:
        if intent["action_type"] == "email_send":
            result = await loop.run_in_executor(None, send_email, user, payload["to"], payload["subject"], payload["body"])
            await loop.run_in_executor(None, execute_intent, intent_id, json.dumps(result), None)
            await loop.run_in_executor(None, chat.save_message, user, "web", "assistant",
                              f"✅ Письмо отправлено на {payload['to']}", 0)
            return JSONResponse({"ok": True, "result": result})
        else:
            await loop.run_in_executor(None, execute_intent, intent_id, None, f"Unknown action type: {intent['action_type']}")
            raise HTTPException(400, f"Unknown action type: {intent['action_type']}")
    except Exception as e:
        await loop.run_in_executor(None, execute_intent, intent_id, None, str(e))
        raise HTTPException(500, f"Execution failed: {e}") from e


@app.post("/api/reject")
async def api_reject(request: Request, user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")

    # CSRF validation
    require_csrf(request)

    body = await request.json()
    intent_id = body.get("intent_id")
    if not intent_id:
        raise HTTPException(400, "intent_id required")

    loop = asyncio.get_running_loop()
    from .approval import reject_intent, get_intent_by_id_for_user
    intent = await loop.run_in_executor(None, get_intent_by_id_for_user, intent_id, user)
    if not intent:
        raise HTTPException(404, "intent not found")
    if intent["status"] != "pending_approval":
        return JSONResponse({"ok": False, "error": f"Intent already {intent['status']}"}, status_code=200)
    await loop.run_in_executor(None, reject_intent, intent_id)
    await loop.run_in_executor(None, chat.save_message, user, "web", "assistant", "❌ Действие отменено пользователем.", 0)
    return JSONResponse({"ok": True})


@app.get("/api/pending-intents")
async def api_pending_intents(user: str | None = Depends(current_user)):
    if not user:
        raise HTTPException(401, "not authenticated")
    from .approval import get_pending_intent, format_intent_payload
    intent = await asyncio.to_thread(get_pending_intent, user)
    if not intent:
        return JSONResponse({"intent": None})
    return JSONResponse({
        "intent": {
            "id": intent["id"],
            "action_type": intent["action_type"],
            "display": format_intent_payload(intent),
            "created_at": intent["created_at"],
            "expires_at": intent["expires_at"],
        }
    })
