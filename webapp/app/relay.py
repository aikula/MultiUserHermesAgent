"""Telegram relay: long-poll, /start, chat."""
import asyncio
import json
import logging
import os
import string
from pathlib import Path

import httpx

from .chat import build_system_prompt, get_history, save_message
from .db import HERMES_SHARED_DIR, get_db, now_iso
from .quota import record as quota_record
from .summarizer import maybe_summarize

log = logging.getLogger("relay")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_LINK_TTL = int(os.environ.get("TELEGRAM_LINK_TTL", "600"))
HERMES_API_URL = os.environ.get("HERMES_API_URL", "http://hermes-gateway:8642")
HERMES_API_KEY = os.environ["HERMES_API_KEY"]
HERMES_MODEL = os.environ.get("HERMES_MODEL", "hermes-agent")
MAX_HISTORY = int(os.environ.get("MAX_HISTORY_MESSAGES", "20"))
MAX_TG_MSG = 4000  # Telegram message limit is 4096


def _gen_code(n: int = 6) -> str:
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(n))


def _auth_path() -> Path:
    return HERMES_SHARED_DIR / "auth.json"


def _load_auth() -> dict:
    p = _auth_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text() or "{}")
    except json.JSONDecodeError:
        return {}


def _save_auth(auth: dict) -> None:
    p = _auth_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(auth, indent=2, sort_keys=True))


def _set_link(telegram_id: int, uid: str) -> None:
    auth = _load_auth()
    auth[str(telegram_id)] = uid
    _save_auth(auth)


class TelegramRelay:
    def __init__(self):
        if not TELEGRAM_BOT_TOKEN:
            raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
        self.api_base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
        self.offset = 0
        self.bot_username: str | None = None
        self._client = httpx.AsyncClient(timeout=60)

    async def close(self):
        await self._client.aclose()

    async def _tg(self, method: str, **params):
        r = await self._client.post(f"{self.api_base}/{method}", json=params)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram {method}: {data.get('description')}")
        return data["result"]

    async def send(self, chat_id: int, text: str, parse_mode: str | None = None):
        # Telegram limit is 4096; chunk if needed
        chunks = [text[i:i + MAX_TG_MSG] for i in range(0, len(text), MAX_TG_MSG)]
        for chunk in chunks:
            try:
                await self._tg("sendMessage", chat_id=chat_id, text=chunk, parse_mode=parse_mode)
            except RuntimeError as e:
                if parse_mode and "parse_mode" in str(e):
                    await self._tg("sendMessage", chat_id=chat_id, text=chunk)
                else:
                    raise

    async def get_me(self):
        me = await self._tg("getMe")
        self.bot_username = me.get("username")
        log.info(f"bot @{self.bot_username} ready")
        return me

    async def consume_link_code(self, code: str, telegram_id: int) -> tuple[bool, str, str | None]:
        """Привязать существующий web-юзер через link-code. Возвращает (ok, message, uid)."""
        try:
            r = await self._client.post(
                f"http://localhost:9000/api/internal/consume-link-code",
                json={"code": code, "telegram_id": telegram_id},
                headers={"X-Internal-Secret": os.environ.get("WEBAPP_INTERNAL_SECRET", "")},
            )
        except httpx.HTTPError as e:
            return False, f"Сервер недоступен: {e}", None
        if r.status_code == 200:
            data = r.json()
            return True, "ok", data.get("uid")
        if r.status_code == 404:
            return False, "not_link", None
        if r.status_code == 410:
            return False, "expired", None
        return False, f"Ошибка: {r.status_code}", None

    async def redeem_invite(self, code: str, telegram_id: int) -> tuple[bool, str, str | None]:
        """Регистрация нового юзера через Telegram с invite-code."""
        try:
            r = await self._client.post(
                f"http://localhost:9000/api/internal/redeem-invite",
                json={"code": code, "telegram_id": telegram_id},
                headers={"X-Internal-Secret": os.environ.get("WEBAPP_INTERNAL_SECRET", "")},
            )
        except httpx.HTTPError as e:
            return False, f"Сервер недоступен: {e}", None
        if r.status_code == 200:
            data = r.json()
            return True, "ok", data.get("uid")
        if r.status_code == 404:
            return False, "not_invite", None
        if r.status_code == 409:
            return False, "telegram_already_linked", None
        return False, f"Ошибка: {r.status_code}", None

    async def process_chat_message(self, uid: str, chat_id: int, text: str):
        save_message(uid, "telegram", "user", text, 0)
        history = get_history(uid)
        messages = [{"role": "system", "content": build_system_prompt(uid)}]
        for h in history:
            messages.append({"role": h["role"], "content": h["content"]})
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(
                    f"{HERMES_API_URL}/v1/chat/completions",
                    json={"model": HERMES_MODEL, "messages": messages, "max_tokens": 1024, "stream": False},
                    headers={"Authorization": f"Bearer {HERMES_API_KEY}", "Content-Type": "application/json"},
                )
                r.raise_for_status()
                data = r.json()
            content = data["choices"][0]["message"]["content"]
            total = data.get("usage", {}).get("total_tokens", 0)
            save_message(uid, "telegram", "assistant", content, total)
            quota_record(uid, "telegram", total)
            await self.send(chat_id, content)
            asyncio.create_task(maybe_summarize(uid))
        except Exception as e:
            log.exception("process_chat_message error")
            await self.send(chat_id, f"⚠️ Ошибка: {e}")

    async def handle_update(self, update: dict):
        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        text = (message.get("text") or "").strip()
        chat_id = message["chat"]["id"]
        from_user = message.get("from") or message.get("chat") or {}
        tg_id = from_user.get("id")
        if not tg_id or not text:
            return

        if text == "/start" or text.startswith("/start "):
            code = text.split(maxsplit=1)[1].strip() if " " in text else ""
            if not code:
                deep = f"https://{self.bot_username}.t.me" if self.bot_username else "https://t.me"
                await self.send(
                    chat_id,
                    "Привет! Чтобы зарегистрироваться:\n"
                    "1. Зайди на https://hermes.kulinich.ru/chat/ и получи invite-code\n"
                    "2. Вернись сюда и отправь: <code>/start INVITE-CODE</code>",
                    parse_mode="HTML",
                )
                return
            # Try link-code first (existing user)
            ok, info, uid = await self.consume_link_code(code, tg_id)
            if ok:
                await self.send(chat_id, f"✅ Аккаунт привязан. Можешь общаться прямо здесь.")
                return
            if info not in ("not_link", "expired"):
                await self.send(chat_id, f"❌ {info}")
                return
            # Then try invite-code (new user)
            ok, info, uid = await self.redeem_invite(code, tg_id)
            if ok:
                await self.send(
                    chat_id,
                    f"✅ Регистрация успешна!\n"
                    f"Твой логин: <code>tg_{tg_id}</code>\n"
                    f"Зайди на https://hermes.kulinich.ru/chat/profile чтобы задать пароль.",
                    parse_mode="HTML",
                )
                return
            if info == "not_invite":
                await self.send(chat_id, "❌ Код не распознан (ни link-code, ни invite-code).")
            else:
                await self.send(chat_id, f"❌ {info}")
            return

        if text == "/help":
            await self.send(chat_id, "/start CODE — привязать/зарегистрироваться\n/whoami — твой UID\n/unlink — отвязать")
            return

        if text == "/whoami":
            auth = _load_auth()
            uid = auth.get(str(tg_id))
            await self.send(chat_id, f"UID: <code>{uid or '—'}</code>", parse_mode="HTML")
            return

        if text == "/unlink":
            auth = _load_auth()
            if str(tg_id) in auth:
                del auth[str(tg_id)]
                _save_auth(auth)
            from .db import get_db
            db = get_db()
            db.execute("UPDATE users SET telegram_id=NULL WHERE telegram_id=?", (tg_id,))
            await self.send(chat_id, "Отвязано.")
            return

        # Regular message: look up user
        auth = _load_auth()
        uid = auth.get(str(tg_id))
        if not uid:
            await self.send(
                chat_id,
                "Ты не зарегистрирован. Зайди на https://hermes.kulinich.ru/chat/ → /profile,\n"
                "получи invite-code, потом /start <code>",
            )
            return
        await self.process_chat_message(uid, chat_id, text)

    async def run(self):
        await self.get_me()
        backoff = 1
        while True:
            try:
                r = await self._client.get(
                    f"{self.api_base}/getUpdates",
                    params={"timeout": 30, "offset": self.offset, "allowed_updates": '["message"]'},
                )
                r.raise_for_status()
                data = r.json()
                if not data.get("ok"):
                    log.warning(f"getUpdates not ok: {data}")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)
                    continue
                backoff = 1
                for upd in data["result"]:
                    self.offset = max(self.offset, upd["update_id"] + 1)
                    try:
                        await self.handle_update(upd)
                    except Exception as e:
                        log.exception(f"handle_update error: {e}")
            except (httpx.HTTPError, asyncio.TimeoutError) as e:
                log.warning(f"getUpdates error: {e}; backoff={backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            except Exception:
                log.exception("relay loop error")
                await asyncio.sleep(5)


async def start_relay_task() -> asyncio.Task | None:
    if not TELEGRAM_BOT_TOKEN:
        log.info("TELEGRAM_BOT_TOKEN not set, relay disabled")
        return None
    relay = TelegramRelay()
    task = asyncio.create_task(relay.run(), name="telegram-relay")
    log.info("telegram relay started")
    return task
