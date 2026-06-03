"""Telegram relay: long-poll, /start, chat, file handling."""
import asyncio
import json
import logging
import os
import re
import secrets
import string
import time
from pathlib import Path

import httpx

from .chat import build_system_prompt, get_history, save_message
from .db import HERMES_USERS_DIR, HERMES_SHARED_DIR, get_db, now_iso
from .quota import record as quota_record

# Action types that require approval before execution
REVIEW_ACTIONS = {"email_send", "calendar_create", "calendar_update", "telegram_send_external", "file_share_external"}


def _extract_intent_from_response(content: str) -> dict | None:
    """Extract action intent from agent response if it follows the format."""
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
from .summarizer import maybe_summarize

log = logging.getLogger("relay")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_LINK_TTL = int(os.environ.get("TELEGRAM_LINK_TTL", "600"))
HERMES_API_URL = os.environ.get("HERMES_API_URL", "http://hermes-gateway:8642")
HERMES_API_KEY = os.environ["HERMES_API_KEY"]
HERMES_MODEL = os.environ.get("HERMES_MODEL", "hermes-agent")
MAX_HISTORY = int(os.environ.get("MAX_HISTORY_MESSAGES", "20"))
MAX_TG_MSG = 4000  # Telegram message limit is 4096
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB max file size

# Allowed file extensions
ALLOWED_EXTENSIONS = {
    # Documents
    '.txt', '.md', '.csv', '.json', '.xml', '.yaml', '.yml', '.toml',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.html', '.htm', '.css', '.js', '.py', '.java', '.c', '.cpp', '.h',
    '.sql', '.sh', '.bat', '.ps1',
    # Data
    '.zip', '.tar', '.gz', '.bz2', '.7z', '.rar',
    '.log', '.conf', '.cfg', '.ini',
    # Images (will be saved but agent can't process them directly)
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg',
}

# Dangerous extensions that should NEVER be saved (even renamed)
DANGEROUS_EXTENSIONS = {'.exe', '.bat', '.cmd', '.com', '.msi', '.scr', '.pif', '.vbs', '.js', '.ws', '.wsh'}


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


def _get_user_files_dir(uid: str) -> Path:
    """Get or create user files directory."""
    files_dir = HERMES_USERS_DIR / uid / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    return files_dir


def _safe_filename(filename: str, files_dir: Path) -> str:
    """Generate safe UUID-based filename, avoiding conflicts and dangerous extensions."""
    import uuid

    # Remove path components
    name = Path(filename).name
    if not name or name.startswith('.'):
        name = f"file_{int(time.time())}"

    # Check extension — reject dangerous ones
    ext = Path(name).suffix.lower()
    if ext in DANGEROUS_EXTENSIONS:
        ext = ".txt"
    elif ext not in ALLOWED_EXTENSIONS:
        ext = ".txt"

    # Use UUID to avoid conflicts and path traversal
    safe_name = f"{uuid.uuid4().hex[:12]}{ext}"
    return safe_name


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
                params = {"chat_id": chat_id, "text": chunk}
                if parse_mode:
                    params["parse_mode"] = parse_mode
                await self._tg("sendMessage", **params)
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

    async def download_file(self, file_id: str, original_name: str, uid: str) -> tuple[bool, str, str | None]:
        """Download file from Telegram and save to user's folder.
        Returns (ok, message, saved_filename).
        """
        try:
            # Pre-check: reject dangerous extensions before downloading
            ext = Path(original_name).suffix.lower()
            if ext in DANGEROUS_EXTENSIONS:
                return False, f"❌ Файл с расширением {ext} запрещён по соображениям безопасности.", None

            # Get file info from Telegram
            file_info = await self._tg("getFile", file_id=file_id)
            file_path = file_info.get("file_path")
            file_size = file_info.get("file_size", 0)

            if not file_path:
                return False, "Не удалось получить информацию о файле", None

            if file_size > MAX_FILE_SIZE:
                return False, f"Файл слишком большой ({file_size // 1024 // 1024}MB). Максимум: {MAX_FILE_SIZE // 1024 // 1024}MB", None

            # Download file
            url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
            r = await self._client.get(url)
            r.raise_for_status()

            # Save to user's folder (UUID-based safe filename)
            files_dir = _get_user_files_dir(uid)
            safe_name = _safe_filename(original_name, files_dir)
            target = files_dir / safe_name
            target.write_bytes(r.content)

            log.info(f"File saved: {target} ({file_size} bytes)")
            return True, f"Файл сохранён: {safe_name}", safe_name

        except httpx.HTTPError as e:
            log.exception(f"download_file error: {e}")
            return False, f"Ошибка скачивания: {e}", None
        except Exception as e:
            log.exception(f"download_file error: {e}")
            return False, f"Ошибка сохранения: {e}", None

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
        # Check for pending approval first
        from .approval import get_pending_intent, is_confirmation, is_rejection, approve_intent, execute_intent, format_intent_payload
        pending = get_pending_intent(uid)

        if pending and is_confirmation(text):
            # Approve and execute
            if approve_intent(pending["id"]):
                payload = json.loads(pending["payload_json"])
                try:
                    if pending["action_type"] == "email_send":
                        from .tools.email_tools import send_email
                        result = send_email(uid=uid, to=payload["to"], subject=payload["subject"], body=payload["body"])
                        execute_intent(pending["id"], result_json=json.dumps(result))
                        save_message(uid, "telegram", "assistant", f"✅ Письмо отправлено на {payload['to']}", 0)
                        await self.send(chat_id, f"✅ Письмо отправлено на {payload['to']}")
                        return
                    else:
                        execute_intent(pending["id"], error=f"Unknown action: {pending['action_type']}")
                        await self.send(chat_id, "⚠️ Неизвестный тип действия.")
                        return
                except Exception as e:
                    execute_intent(pending["id"], error=str(e))
                    await self.send(chat_id, f"⚠️ Ошибка выполнения: {e}")
                    return
            else:
                await self.send(chat_id, "⚠️ Заявка истекла или уже обработана.")
                return

        if pending and is_rejection(text):
            from .approval import reject_intent
            reject_intent(pending["id"])
            save_message(uid, "telegram", "assistant", "❌ Действие отменено.", 0)
            await self.send(chat_id, "❌ Действие отменено.")
            return

        # Normal chat flow
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
                    headers={
                        "Authorization": f"Bearer {HERMES_API_KEY}",
                        "Content-Type": "application/json",
                        "X-Hermes-Session-Key": uid,
                    },
                )
                r.raise_for_status()
                data = r.json()
            content = data["choices"][0]["message"]["content"]
            total = data.get("usage", {}).get("total_tokens", 0)
            save_message(uid, "telegram", "assistant", content, total)
            quota_record(uid, "telegram", total)

            # Check if response contains action intent
            intent_data = _extract_intent_from_response(content)
            if intent_data and intent_data["action_type"] in REVIEW_ACTIONS:
                from .approval import create_intent
                intent = create_intent(uid, intent_data["action_type"], intent_data["payload"])
                display = format_intent_payload(intent)
                await self.send(chat_id, f"{display}\n\n❓ Подтверди или отмень (ответь «подтверждаю» или «отмена»)")
            else:
                await self.send(chat_id, content)

            asyncio.create_task(maybe_summarize(uid))
        except httpx.ConnectError:
            log.exception("process_chat_message: gateway connection error")
            await self.send(chat_id, "⚠️ Сервис временно недоступен. Попробуй позже.")
        except httpx.TimeoutException:
            log.exception("process_chat_message: gateway timeout")
            await self.send(chat_id, "⚠️ Превышено время ожидания. Попробуй позже.")
        except Exception as e:
            log.exception("process_chat_message error")
            await self.send(chat_id, f"⚠️ Ошибка: {e}")

    async def handle_update(self, update: dict):
        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        chat_id = message["chat"]["id"]
        from_user = message.get("from") or message.get("chat") or {}
        tg_id = from_user.get("id")
        text = (message.get("text") or "").strip()

        if not tg_id:
            return

        # Handle /start command
        if text and (text == "/start" or text.startswith("/start ")):
            code = text.split(maxsplit=1)[1].strip() if " " in text else ""
            if not code:
                await self.send(
                    chat_id,
                    "👋 Привет! Чтобы начать:\n\n"
                    "1. Зайди на https://hermes.kulinich.ru/chat/\n"
                    "2. Зарегистрируйся или войди\n"
                    "3. Открой Профиль → Telegram → Получи код\n"
                    "4. Вернись сюда и отправь: <code>/start ТВОЙ-КОД</code>",
                    parse_mode="HTML",
                )
                return
            # Try link-code first (existing user)
            ok, info, uid = await self.consume_link_code(code, tg_id)
            if ok:
                await self.send(chat_id, "✅ Аккаунт привязан! Можешь общаться прямо здесь.\n\nОтправь любое сообщение, чтобы начать.")
                return
            if info == "expired":
                await self.send(
                    chat_id,
                    "⏰ Код истёк. Получи новый код:\n"
                    "1. Зайди на https://hermes.kulinich.ru/chat/profile\n"
                    "2. Нажми «Привязать Telegram»\n"
                    "3. Отправь новый код здесь: <code>/start НОВЫЙ-КОД</code>",
                    parse_mode="HTML",
                )
                return
            if info == "not_link":
                # Try invite-code (new user)
                ok, info, uid = await self.redeem_invite(code, tg_id)
                if ok:
                    await self.send(
                        chat_id,
                        f"✅ Регистрация успешна!\n\n"
                        f"Твой логин: <code>tg_{tg_id}</code>\n\n"
                        f"Зайди на https://hermes.kulinich.ru/chat/profile чтобы задать пароль и имя.",
                        parse_mode="HTML",
                    )
                    return
                if info == "not_invite":
                    await self.send(
                        chat_id,
                        "❌ Код не распознан.\n\n"
                        "Проверь код и попробуй снова:\n"
                        "<code>/start ТВОЙ-КОД</code>\n\n"
                        "Или получи новый код на https://hermes.kulinich.ru/chat/profile",
                        parse_mode="HTML",
                    )
                elif info == "telegram_already_linked":
                    await self.send(
                        chat_id,
                        "⚠️ Этот Telegram аккаунт уже привязан к другому пользователю.\n\n"
                        "Если это ты — зайди на https://hermes.kulinich.ru/chat/profile\n"
                        "Или отправь /unlink чтобы отвязать.",
                    )
                else:
                    await self.send(chat_id, f"❌ {info}")
                return
            # Some other error
            await self.send(chat_id, f"❌ {info}")
            return

        # Handle /help command
        if text == "/help":
            await self.send(
                chat_id,
                "📖 Доступные команды:\n\n"
                "/start CODE — привязать или зарегистрироваться\n"
                "/whoami — твой UID\n"
                "/files — список файлов\n"
                "/unlink — отвязать аккаунт\n"
                "/help — эта справка\n\n"
                "Также ты можешь отправлять мне файлы (документы, текст, CSV, PDF) —\n"
                "они сохранятся и я смогу с ними работать.",
            )
            return

        # Handle /whoami command
        if text == "/whoami":
            auth = _load_auth()
            uid = auth.get(str(tg_id))
            if uid:
                await self.send(chat_id, f"🆔 Твой UID: <code>{uid}</code>", parse_mode="HTML")
            else:
                await self.send(chat_id, "❌ Ты не привязан. Отправь /start КОД")
            return

        # Handle /files command
        if text == "/files":
            auth = _load_auth()
            uid = auth.get(str(tg_id))
            if not uid:
                await self.send(chat_id, "❌ Ты не привязан. Отправь /start КОД")
                return
            files_dir = HERMES_USERS_DIR / uid / "files"
            if not files_dir.exists():
                await self.send(chat_id, "📁 У тебя нет файлов.")
                return
            files = list(files_dir.iterdir())
            files = [f for f in files if f.is_file()]
            if not files:
                await self.send(chat_id, "📁 У тебя нет файлов.")
                return
            file_list = "\n".join(f"• {f.name} ({f.stat().st_size // 1024}KB)" for f in sorted(files))
            await self.send(chat_id, f"📁 Твои файлы:\n{file_list}")
            return

        # Handle /unlink command
        if text == "/unlink":
            auth = _load_auth()
            if str(tg_id) in auth:
                del auth[str(tg_id)]
                _save_auth(auth)
            from .db import get_db
            db = get_db()
            db.execute("UPDATE users SET telegram_id=NULL WHERE telegram_id=?", (tg_id,))
            await self.send(chat_id, "🔓 Аккаунт отвязан. Ты можешь привязать другой аккаунт через /start КОД")
            return

        # Handle file (document)
        document = message.get("document")
        if document:
            auth = _load_auth()
            uid = auth.get(str(tg_id))
            if not uid:
                await self.send(
                    chat_id,
                    "❌ Ты не зарегистрирован.\n\n"
                    "Зайди на https://hermes.kulinich.ru/chat/ → Профиль,\n"
                    "получи invite-code, потом /start <code>INVITE-CODE</code>",
                    parse_mode="HTML",
                )
                return

            file_id = document.get("file_id")
            file_name = document.get("file_name") or f"file_{int(time.time())}"
            file_size = document.get("file_size", 0)

            if file_size > MAX_FILE_SIZE:
                await self.send(chat_id, f"❌ Файл слишком большой ({file_size // 1024 // 1024}MB). Максимум: {MAX_FILE_SIZE // 1024 // 1024}MB")
                return

            await self.send(chat_id, f"📥 Скачиваю {file_name}...")
            ok, msg, saved_name = await self.download_file(file_id, file_name, uid)
            if ok:
                await self.send(
                    chat_id,
                    f"✅ {msg}\n\n"
                    f"Теперь я могу работать с этим файлом. Просто напиши, что с ним сделать.",
                )
            else:
                await self.send(chat_id, f"❌ {msg}")
            return

        # Handle photo
        photo = message.get("photo")
        if photo:
            auth = _load_auth()
            uid = auth.get(str(tg_id))
            if not uid:
                await self.send(
                    chat_id,
                    "❌ Ты не зарегистрирован.\n\n"
                    "Зайди на https://hermes.kulinich.ru/chat/ → Профиль,\n"
                    "получи invite-code, потом /start <code>INVITE-CODE</code>",
                    parse_mode="HTML",
                )
                return

            # Get largest photo
            largest = max(photo, key=lambda p: p.get("file_size", 0))
            file_id = largest.get("file_id")
            file_size = largest.get("file_size", 0)

            if file_size > MAX_FILE_SIZE:
                await self.send(chat_id, f"❌ Фото слишком большое ({file_size // 1024 // 1024}MB). Максимум: {MAX_FILE_SIZE // 1024 // 1024}MB")
                return

            filename = f"photo_{int(time.time())}.jpg"
            await self.send(chat_id, "📥 Скачиваю фото...")
            ok, msg, saved_name = await self.download_file(file_id, filename, uid)
            if ok:
                await self.send(
                    chat_id,
                    f"✅ {msg}\n\n"
                    f"Теперь я могу работать с этим фото. Просто напиши, что с ним сделать.",
                )
            else:
                await self.send(chat_id, f"❌ {msg}")
            return

        # Handle text message
        if text:
            auth = _load_auth()
            uid = auth.get(str(tg_id))
            if not uid:
                await self.send(
                    chat_id,
                    "❌ Ты не зарегистрирован.\n\n"
                    "Зайди на https://hermes.kulinich.ru/chat/ → Профиль,\n"
                    "получи invite-code, потом /start <code>INVITE-CODE</code>",
                    parse_mode="HTML",
                )
                return
            await self.process_chat_message(uid, chat_id, text)
            return

        # Unknown message type
        await self.send(
            chat_id,
            "🤔 Я не понимаю этот тип сообщения.\n\n"
            "Отправь мне текст или файл, или используй команды:\n"
            "/help — справка по командам",
        )

    async def run(self):
        await self.get_me()
        backoff = 1
        while True:
            try:
                r = await self._client.get(
                    f"{self.api_base}/getUpdates",
                    params={"timeout": 1, "offset": self.offset, "allowed_updates": '["message"]'},
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
