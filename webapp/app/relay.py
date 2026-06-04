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

from .approval import REVIEW_ACTIONS
from .chat import build_system_prompt, get_history, save_message
from .db import HERMES_USERS_DIR, HERMES_SHARED_DIR
from .quota import check_quota, record as quota_record

# Slash-команды, которые relay обрабатывает локально.
# Любая другая команда (начинается с /) НЕ пробрасывается в LLM — иначе
# LLM видит «/foo bar», путается и галлюцинирует «No main session found».
KNOWN_COMMANDS = {"/start", "/login", "/help", "/whoami", "/files", "/unlink", "/new", "/reset"}

# Фразы, которыми LLM иногда галлюцинирует «нет сессии». Ловим и даём
# человеко-понятный ответ вместо загадочной англоязычной строки.
GATEWAY_CONFUSED_PATTERNS = re.compile(
    r"(?i)(no main session|create one via|web ui first|/new or web|main session not found)"
)


def _strip_intent_block(content: str) -> str:
    """Remove action_intent JSON block from user-visible content."""
    import re
    cleaned = re.sub(r'\n?```action_intent\n.*?\n```\n?', '', content, flags=re.DOTALL).strip()
    if not cleaned:
        return "(действие подготовлено — смотри карточку подтверждения)"
    return cleaned


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
from .summarizer import maybe_summarize  # noqa: E402

log = logging.getLogger("relay")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_LINK_TTL = int(os.environ.get("TELEGRAM_LINK_TTL", "600"))
HERMES_API_URL = os.environ.get("HERMES_API_URL", "http://hermes-gateway:8642")
HERMES_API_KEY = os.environ["HERMES_API_KEY"]
HERMES_MODEL = os.environ.get("HERMES_MODEL", "hermes-agent")
MAX_HISTORY = int(os.environ.get("MAX_HISTORY_MESSAGES", "8"))
MAX_TG_MSG = 4000  # Telegram message limit is 4096
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB max file size
LONG_POLL_TIMEOUT = 25  # Telegram best practice; <1 wastes requests, >30 hits client timeout

# MVP allowed file extensions (per spec 01)
ALLOWED_EXTENSIONS = {
    '.txt', '.md', '.csv', '.json', '.pdf', '.docx', '.xlsx',
    '.oga', '.ogg', '.mp3', '.wav', '.m4a', '.opus',
    '.cer', '.crt', '.pem', '.key', '.log', '.yaml', '.yml', '.toml', '.ini', '.cfg',
}

# Dangerous extensions that should NEVER be saved
DANGEROUS_EXTENSIONS = {
    '.exe', '.bat', '.cmd', '.com', '.msi', '.scr', '.pif',
    '.vbs', '.js', '.ws', '.wsh',
    '.sh', '.ps1', '.py',
    '.html', '.htm', '.css',
    '.zip', '.tar', '.gz', '.bz2', '.7z', '.rar',
}


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


async def _aload_auth() -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _load_auth)


async def _asave_auth(auth: dict) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _save_auth, auth)


def _set_link(telegram_id: int, uid: str) -> None:
    auth = _load_auth()
    auth[str(telegram_id)] = uid
    _save_auth(auth)


def _get_user_files_dir(uid: str) -> Path:
    """Get or create user files directory."""
    files_dir = HERMES_USERS_DIR / uid / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    return files_dir


def _list_user_files(files_dir: Path) -> tuple[bool, list[str]]:
    """Returns (exists, formatted list of files). For use in async executor."""
    if not files_dir.exists():
        return False, []
    all_files = [f for f in sorted(files_dir.iterdir()) if f.is_file()]
    if not all_files:
        return True, []
    result = []
    for f in all_files:
        size = f.stat().st_size // 1024
        result.append(f"{f.name} ({size}KB)")
    return True, result


def _safe_filename(filename: str, files_dir: Path) -> str:
    """Generate safe UUID-based filename.
    Rejects dangerous and unknown extensions per spec 01.
    """
    import uuid

    # Remove path components
    name = Path(filename).name
    if not name or name.startswith('.'):
        name = f"file_{int(time.time())}"

    # Check extension — reject dangerous and unknown ones
    ext = Path(name).suffix.lower()
    if ext in DANGEROUS_EXTENSIONS:
        raise ValueError(f"Extension {ext} is dangerous and not allowed")
    if ext and ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Extension {ext} is not in allowed list")
    if not ext:
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

    async def typing(self, chat_id: int) -> None:
        """Best-effort: показать 'печатает...' пока думаем. Тихо глотает ошибки."""
        try:
            await self._tg("sendChatAction", chat_id=chat_id, action="typing")
        except Exception:
            pass

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
            # Pre-check: reject dangerous and unknown extensions before downloading
            ext = Path(original_name).suffix.lower()
            if ext in DANGEROUS_EXTENSIONS:
                return False, f"❌ Файл с расширением {ext} запрещён по соображениям безопасности.", None
            if ext and ext not in ALLOWED_EXTENSIONS:
                return False, f"❌ Расширение {ext} не поддерживается. Допустимые: {', '.join(sorted(ALLOWED_EXTENSIONS))}", None

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
            loop = asyncio.get_running_loop()
            files_dir = await loop.run_in_executor(None, _get_user_files_dir, uid)
            try:
                safe_name = _safe_filename(original_name, files_dir)
            except ValueError as e:
                return False, f"❌ {e}", None
            target = files_dir / safe_name
            await loop.run_in_executor(None, target.write_bytes, r.content)

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
                "http://localhost:9000/api/internal/consume-link-code",
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
                "http://localhost:9000/api/internal/redeem-invite",
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
        loop = asyncio.get_running_loop()
        # Check for pending approval first
        from .approval import get_pending_intent, is_confirmation, is_rejection
        pending = await loop.run_in_executor(None, get_pending_intent, uid)

        if pending and is_confirmation(text):
            from .approval import approve_intent, execute_intent
            if await loop.run_in_executor(None, approve_intent, pending["id"]):
                payload = json.loads(pending["payload_json"])
                try:
                    if pending["action_type"] == "email_send":
                        from .tools.email_tools import send_email
                        result = await loop.run_in_executor(None, send_email, uid, payload["to"], payload["subject"], payload["body"])
                        await loop.run_in_executor(None, execute_intent, pending["id"], json.dumps(result), None)
                        await loop.run_in_executor(None, save_message, uid, "telegram", "assistant", f"✅ Письмо отправлено на {payload['to']}", 0)
                        await self.send(chat_id, f"✅ Письмо отправлено на {payload['to']}")
                        return
                    elif pending["action_type"] == "create_scheduled_job":
                        from .jobs import store as job_store
                        job_payload = payload.get("payload") or {}
                        if not job_payload and payload.get("message"):
                            job_payload = {"message": payload["message"]}
                        if not job_payload and payload.get("prompt"):
                            job_payload = {"prompt": payload["prompt"]}
                        job = await loop.run_in_executor(
                            None, job_store.create_job,
                            uid, payload.get("title", ""), payload.get("kind", "reminder"),
                            payload.get("schedule_type", "one_time"),
                            payload.get("run_at"), payload.get("time_of_day"),
                            payload.get("weekdays"), payload.get("channel", "web"),
                            job_payload,
                        )
                        await loop.run_in_executor(None, execute_intent, pending["id"], json.dumps(job, ensure_ascii=False, default=str), None)
                        title = job.get("title", payload.get("kind", "?"))
                        await loop.run_in_executor(None, save_message, uid, "telegram", "assistant", f"✅ Автоматизация «{title}» создана", 0)
                        await self.send(chat_id, f"✅ Автоматизация «{title}» создана")
                        return
                    else:
                        await loop.run_in_executor(None, execute_intent, pending["id"], None, f"Unknown action: {pending['action_type']}")
                        await self.send(chat_id, "⚠️ Неизвестный тип действия.")
                        return
                except Exception as e:
                    await loop.run_in_executor(None, execute_intent, pending["id"], None, str(e))
                    await self.send(chat_id, f"⚠️ Ошибка выполнения: {e}")
                    return
            else:
                await self.send(chat_id, "⚠️ Заявка истекла или уже обработана.")
                return

        if pending and is_rejection(text):
            from .approval import reject_intent
            await loop.run_in_executor(None, reject_intent, pending["id"])
            await loop.run_in_executor(None, save_message, uid, "telegram", "assistant", "❌ Действие отменено.", 0)
            await self.send(chat_id, "❌ Действие отменено.")
            return

        # Normal chat flow
        await loop.run_in_executor(None, save_message, uid, "telegram", "user", text, 0)

        # Hard quota check — block before Hermes call (with reserve)
        from .quota import MIN_QUOTA_RESERVE_TOKENS, MAX_TOKENS_PER_RESPONSE
        ok, err_msg = await loop.run_in_executor(None, check_quota, uid, MAX_TOKENS_PER_RESPONSE + MIN_QUOTA_RESERVE_TOKENS)
        if not ok:
            await loop.run_in_executor(None, save_message, uid, "telegram", "assistant", err_msg, 0)
            await self.send(chat_id, f"⚠️ {err_msg}")
            return

        history = await loop.run_in_executor(None, get_history, uid)
        system_prompt = await loop.run_in_executor(None, build_system_prompt, uid)
        messages = [{"role": "system", "content": system_prompt}]
        for h in history:
            messages.append({"role": h["role"], "content": h["content"]})
        await self.typing(chat_id)
        try:
            content, total = await self._call_hermes_and_record(uid, messages)
            await self._deliver_response(chat_id, uid, content, total)
        except httpx.ConnectError:
            log.exception("process_chat_message: gateway connection error")
            await self.send(chat_id, "⚠️ Сервис временно недоступен. Попробуй позже.")
        except httpx.TimeoutException:
            log.exception("process_chat_message: gateway timeout")
            await self.send(chat_id, "⏳ Запрос всё ещё выполняется. Я пришлю ответ, когда он будет готов.")
            try:
                content, total = await self._call_hermes_and_record(uid, messages)
                await self._deliver_response(chat_id, uid, content, total)
            except httpx.TimeoutException:
                await self.send(chat_id, "⏳ Запрос всё ещё обрабатывается. Придёт ответом, как только будет готов.")
            except Exception as e:
                log.exception("process_chat_message: retry error")
                await self.send(chat_id, f"⚠️ Не удалось получить ответ: {e}")
        except Exception as e:
            log.exception("process_chat_message error")
            await self.send(chat_id, f"⚠️ Ошибка: {e}")

    async def _call_hermes_and_record(self, uid: str, messages: list[dict]) -> tuple[str, int]:
        """POST to gateway, save assistant reply, record quota. Returns (content, total_tokens)."""
        async with httpx.AsyncClient(timeout=600) as client:
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
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, save_message, uid, "telegram", "assistant", content, total)
        await loop.run_in_executor(None, quota_record, uid, "telegram", total)
        return content, total

    async def _deliver_response(self, chat_id: int, uid: str, content: str, total: int) -> None:
        """Send LLM response to Telegram. Handle approval cards, gateway-confused hallucinations, plain text."""
        intent_data = _extract_intent_from_response(content)
        if intent_data and intent_data["action_type"] in REVIEW_ACTIONS:
            from .approval import create_intent, format_intent_payload
            clean = _strip_intent_block(content)
            loop = asyncio.get_running_loop()
            intent = await loop.run_in_executor(
                None, create_intent, uid, intent_data["action_type"], intent_data["payload"]
            )
            display = format_intent_payload(intent)
            await self.send(
                chat_id,
                f"{clean}\n\n{display}\n\n❓ Подтверди или отмень (ответь «подтверждаю» или «отмена»)",
            )
        elif GATEWAY_CONFUSED_PATTERNS.search(content):
            await self.send(
                chat_id,
                "🤔 Hermes не смог обработать запрос (похоже на сбой сессии на стороне gateway).\n\n"
                "Попробуй:\n"
                "• Перефразировать запрос\n"
                "• Отправить /start и заново привязать аккаунт\n"
                "• Зайти в web-интерфейс https://hermes.kulinich.ru/chat/ и попробовать там",
            )
        else:
            await self.send(chat_id, content)
        asyncio.create_task(maybe_summarize(uid))

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

        # Handle /start command (also accept /login as alias for compatibility)
        if text and (text == "/start" or text.startswith("/start ") or text == "/login" or text.startswith("/login ")):
            # Normalize: rewrite /login to /start logic
            if text.startswith("/login"):
                text = "/start" + text[len("/login"):]
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
                "/start CODE — привязать или зарегистрироваться (/login — алиас)\n"
                "/whoami — твой UID\n"
                "/files — список файлов\n"
                "/unlink — отвязать аккаунт\n"
                "/help — эта справка\n\n"
                "Также ты можешь отправлять мне файлы (документы, текст, CSV, PDF) —\n"
                "они сохранятся и я смогу с ними работать.",
            )
            return

        # Pre-check: любая неизвестная slash-команда НЕ должна уходить в LLM.
        # Без этого LLM видит «/foo bar», путается и галлюцинирует
        # «No main session found. Create one via /new or Web UI first.»
        if text and text.startswith("/"):
            cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
            if cmd not in KNOWN_COMMANDS:
                await self.send(
                    chat_id,
                    f"❓ Не знаю команду <code>{cmd}</code>.\n\n"
                    "Доступные: /start, /login, /help, /whoami, /files, /unlink.\n"
                    "Просто напиши вопрос обычным сообщением — я отвечу.",
                    parse_mode="HTML",
                )
                return

        # Handle /whoami command
        if text == "/whoami":
            auth = await _aload_auth()
            uid = auth.get(str(tg_id))
            if uid:
                await self.send(chat_id, f"🆔 Твой UID: <code>{uid}</code>", parse_mode="HTML")
            else:
                await self.send(chat_id, "❌ Ты не привязан. Отправь /start КОД")
            return

        # Handle /files command
        if text == "/files":
            auth = await _aload_auth()
            uid = auth.get(str(tg_id))
            if not uid:
                await self.send(chat_id, "❌ Ты не привязан. Отправь /start КОД")
                return
            files_dir = HERMES_USERS_DIR / uid / "files"
            exists, files = await asyncio.to_thread(_list_user_files, files_dir)
            if not exists:
                await self.send(chat_id, "📁 У тебя нет файлов.")
                return
            if not files:
                await self.send(chat_id, "📁 У тебя нет файлов.")
                return
            file_list = "\n".join(f"• {f}" for f in files)
            await self.send(chat_id, f"📁 Твои файлы:\n{file_list}")
            return

        # Handle /unlink command
        if text == "/unlink":
            auth = await _aload_auth()
            if str(tg_id) in auth:
                del auth[str(tg_id)]
                await _asave_auth(auth)
            from .db import get_db
            db = await asyncio.to_thread(get_db)
            await asyncio.to_thread(db.execute, "UPDATE users SET telegram_id=NULL WHERE telegram_id=?", (tg_id,))
            await self.send(chat_id, "🔓 Аккаунт отвязан. Ты можешь привязать другой аккаунт через /start КОД")
            return

        # Handle file (document)
        document = message.get("document")
        if document:
            auth = await _aload_auth()
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
            auth = await _aload_auth()
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

        # Handle voice message
        voice = message.get("voice")
        if voice:
            auth = await _aload_auth()
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

            file_id = voice.get("file_id")
            duration = voice.get("duration", 0)
            await self.send(chat_id, f"🎤 Распознаю голосовое ({duration}с)...")

            try:
                file_info = await self._tg("getFile", file_id=file_id)
                tg_file_path = file_info.get("file_path")
                if not tg_file_path:
                    await self.send(chat_id, "❌ Не удалось получить файл.")
                    return

                url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{tg_file_path}"
                r = await self._client.get(url)
                r.raise_for_status()
                audio_bytes = r.content

                from .stt import transcribe
                text = await transcribe(audio_bytes)
                if not text:
                    await self.send(chat_id, "❌ Не удалось распознать голосовое сообщение.")
                    return

                await self.send(chat_id, f"🎤 Распознано: {text}")
                await self.send(chat_id, "🤖 Принял запрос в обработку. Как только будет готов ответ — я его пришлю.")
                asyncio.create_task(self.process_chat_message(uid, chat_id, text))
            except httpx.HTTPError as e:
                log.exception(f"voice download error: {e}")
                await self.send(chat_id, f"❌ Ошибка скачивания: {e}")
            except Exception as e:
                log.exception(f"voice processing error: {e}")
                await self.send(chat_id, f"❌ Ошибка: {e}")
            return

        # Handle text message
        if text:
            auth = await _aload_auth()
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
                    params={"timeout": LONG_POLL_TIMEOUT, "offset": self.offset, "allowed_updates": '["message"]'},
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


# Module-level relay instance for API access
_relay_instance: TelegramRelay | None = None


async def send_message(chat_id: int, text: str, parse_mode: str | None = None) -> dict:
    """Send a Telegram message via the active relay.
    Called from API endpoints (e.g. gateway bridge).
    Returns {"ok": True} or raises RuntimeError.
    """
    if not _relay_instance:
        raise RuntimeError("Telegram relay not started (TELEGRAM_BOT_TOKEN not set?)")
    await _relay_instance.send(chat_id, text, parse_mode=parse_mode)
    return {"ok": True}


async def start_relay_task() -> asyncio.Task | None:
    global _relay_instance
    if not TELEGRAM_BOT_TOKEN:
        log.info("TELEGRAM_BOT_TOKEN not set, relay disabled")
        return None
    relay = TelegramRelay()
    _relay_instance = relay
    task = asyncio.create_task(relay.run(), name="telegram-relay")
    log.info("telegram relay started")
    return task
