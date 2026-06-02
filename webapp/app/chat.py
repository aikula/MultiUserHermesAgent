"""Chat: per-user context, Hermes API calls, history."""
import os

import httpx

from .db import HERMES_USERS_DIR, get_db, now_iso

HERMES_API_URL = os.environ.get("HERMES_API_URL", "http://hermes-gateway:8642")
HERMES_API_KEY = os.environ["HERMES_API_KEY"]
HERMES_MODEL = os.environ.get("HERMES_MODEL", "hermes-agent")
MAX_HISTORY = int(os.environ.get("MAX_HISTORY_MESSAGES", "20"))


def build_system_prompt(uid: str) -> str:
    """Читает SOUL.md + memory.md, формирует system-промпт."""
    user_dir = HERMES_USERS_DIR / uid
    soul = (user_dir / "SOUL.md").read_text(encoding="utf-8") if (user_dir / "SOUL.md").exists() else ""
    memory = (user_dir / "memory.md").read_text(encoding="utf-8").strip() if (user_dir / "memory.md").exists() else ""
    parts = [soul.strip()] if soul.strip() else [f"# Ассистент\n\nПолезный помощник для пользователя."]
    if memory:
        parts.append("\n## Твоя память о юзере\n\n" + memory)
    return "\n\n".join(parts).strip()


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


async def call_hermes(messages: list[dict]) -> dict:
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
