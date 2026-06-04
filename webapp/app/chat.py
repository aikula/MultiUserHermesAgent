"""Chat: per-user context, Hermes API calls, history."""
import asyncio
import os
import re
from datetime import datetime, timezone

import httpx

from .db import HERMES_USERS_DIR, get_db, now_iso
from .skills.loader import get_skill, render_compact_list
from .skills.manager_templates import get_manager_templates_block

# Marker for explicit skill activation (set by the Skills tab).
# The full skill text is injected into the current turn's messages
# (not into the cached system prompt) — keeps the prompt compact.
SKILL_MARKER_RE = re.compile(r"^\s*\[Используй навык:\s*([a-z0-9_]+)\]\s*\n?", re.IGNORECASE)

HERMES_API_URL = os.environ.get("HERMES_API_URL", "http://hermes-gateway:8642")
HERMES_API_KEY = os.environ["HERMES_API_KEY"]
HERMES_MODEL = os.environ.get("HERMES_MODEL", "hermes-agent")
MAX_HISTORY = int(os.environ.get("MAX_HISTORY_MESSAGES", "8"))


def _get_user_files(uid: str) -> list[dict]:
    """Get list of user's files with metadata."""
    files_dir = HERMES_USERS_DIR / uid / "files"
    if not files_dir.exists():
        return []
    
    files = []
    for f in sorted(files_dir.iterdir()):
        if f.is_file():
            stat = f.stat()
            files.append({
                "name": f.name,
                "size": stat.st_size,
                "size_human": _human_size(stat.st_size),
                "ext": f.suffix.lower(),
            })
    return files


def _human_size(size: int) -> str:
    """Convert bytes to human readable format."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def build_system_prompt(uid: str) -> str:
    """Читает SOUL.md + memory.md + credentials + files, формирует system-промпт."""
    user_dir = HERMES_USERS_DIR / uid
    soul = (user_dir / "SOUL.md").read_text(encoding="utf-8") if (user_dir / "SOUL.md").exists() else ""
    memory = (user_dir / "memory.md").read_text(encoding="utf-8").strip() if (user_dir / "memory.md").exists() else ""
    parts = [soul.strip()] if soul.strip() else ["# Ассистент\n\nПолезный помощник для пользователя."]
    if memory:
        parts.append("\n## Твоя память о юзере\n\n" + memory)

    now_utc = datetime.now(timezone.utc)
    parts.append(
        f"\n## Текущее время (серверное)\n"
        f"UTC: {now_utc.strftime('%Y-%m-%d %H:%M:%S %A')}\n"
        f"ISO: {now_utc.isoformat()}\n"
        "Используй это, когда юзер спрашивает про время/дату/дедлайны. "
        "Не выдумывай — бери из этого блока."
    )

    # User's files
    files = _get_user_files(uid)
    if files:
        file_list = ", ".join(f"{f['name']}({f['size_human']})" for f in files)
        parts.append(
            f"\n## Файлы\n{file_list}\n"
            f"Путь: /opt/hermes-users/{uid}/files/. Для чтения: open('...').read()"
        )

    # Email capability (NEVER expose credentials to LLM)
    db = get_db()
    user = db.execute(
        "SELECT email_login, email_imap_host, google_connected FROM users WHERE uid=?",
        (uid,),
    ).fetchone()
    if user:
        if user["email_login"] and user["email_imap_host"]:
            parts.append(
                "\n## Доступ к почте\n"
                f"У пользователя подключена почта ({user['email_login']}).\n"
                "Для работы с email ИСПОЛЬЗУЙ backend-инструмент email_tools — НЕ пиши скрипты с imaplib/smtplib.\n"
                "Доступные действия:\n"
                "- email_check_connection — проверить подключение\n"
                "- email_list_folders — список папок\n"
                "- email_search — поиск писем\n"
                "- email_read — прочитать письмо по ID\n"
                "- email_send — отправить письмо (требует подтверждения)\n"
            )
        if user["google_connected"]:
            parts.append(
                "\n## Доступ к Google Workspace\n"
                "У пользователя подключён Google. Для работы с Gmail/Calendar/Drive используй google_api.py.\n"
            )

    # Manager skill templates (routing + 6 demo formats)
    parts.append("\n" + get_manager_templates_block())

    # Compact skills library (spec 13) — names + 1-line hints only.
    # Full text is injected per-turn via detect_skill_request().
    skills_block = render_compact_list()
    if skills_block:
        parts.append("\n" + skills_block)

    return "\n\n".join(parts).strip()


def detect_skill_request(content: str) -> tuple[str | None, str]:
    """Detect explicit skill activation in a user message.

    Returns (skill_name_or_None, cleaned_content).
    If the user message starts with `[Используй навык: name]`, the marker is
    stripped and the skill name is returned. The full skill text should be
    fetched via `get_skill(name)` and injected into THIS turn's messages.
    """
    if not content:
        return None, content
    m = SKILL_MARKER_RE.match(content)
    if not m:
        return None, content
    name = m.group(1).strip().lower()
    cleaned = content[m.end():]
    return name, cleaned


def build_skill_user_message(skill_name: str, original_content: str) -> dict:
    """Build a single user-role message that injects the full skill text.

    The skill content is wrapped in a system-style block; the user's actual
    request is included as a quoted 'Request' section so the model knows
    what to do with the skill.
    """
    skill_md = get_skill(skill_name) or ""
    body = (
        f"## Активный навык: {skill_name}\n\n"
        f"{skill_md}\n\n"
        f"---\n\n"
        f"## Запрос пользователя\n\n"
        f"{original_content.strip()}"
    )
    return {"role": "user", "content": body}


def get_history(uid: str, limit: int = MAX_HISTORY) -> list[dict]:
    """Последние N сообщений юзера (user/assistant) из БД."""
    db = get_db()
    rows = db.execute(
        "SELECT role, content, created_at FROM chat_history "
        "WHERE uid=? AND role IN ('user','assistant') "
        "ORDER BY id DESC LIMIT ?",
        (uid, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def save_message(uid: str, channel: str, role: str, content: str, tokens: int = 0) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO chat_history (uid, channel, role, content, tokens, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (uid, channel, role, content, tokens, now_iso()),
    )


async def call_hermes(messages: list[dict], uid: str = "") -> dict:
    """POST к :8642/v1/chat/completions. Возвращает dict {content, prompt_tokens, completion_tokens, total_tokens}."""
    payload = {
        "model": HERMES_MODEL,
        "messages": messages,
        "max_tokens": 1024,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {HERMES_API_KEY}",
        "Content-Type": "application/json",
    }
    if uid:
        headers["X-Hermes-Session-Key"] = uid
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{HERMES_API_URL}/v1/chat/completions", json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
    choice = data["choices"][0]
    usage = data.get("usage", {})
    return {
        "content": choice["message"]["content"],
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "finish_reason": choice.get("finish_reason"),
    }


# --- Async wrappers for sync functions ---

async def asave_message(uid: str, channel: str, role: str, content: str, tokens: int = 0) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, save_message, uid, channel, role, content, tokens)


async def aget_history(uid: str, limit: int = MAX_HISTORY) -> list[dict]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_history, uid, limit)


async def abuild_system_prompt(uid: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, build_system_prompt, uid)
