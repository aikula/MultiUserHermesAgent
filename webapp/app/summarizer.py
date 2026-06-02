"""Per-user memory summarizer.

After every N (default 20) new chat_history rows for a user, calls Hermes
to synthesize an updated users/<uid>/memory.md from existing memory + new
conversation. Injectable as system-prompt context by chat.build_system_prompt.
"""
import asyncio
import logging
import os
from pathlib import Path

import httpx

from .chat import HERMES_API_KEY, HERMES_API_URL, HERMES_MODEL
from .db import HERMES_USERS_DIR, get_db

SUMMARY_THRESHOLD = int(os.environ.get("SUMMARY_THRESHOLD", "20"))
SUMMARY_MAX_HISTORY = int(os.environ.get("SUMMARY_MAX_HISTORY", "100"))

_locks: dict[str, asyncio.Lock] = {}


def _user_memory_path(uid: str) -> Path:
    return HERMES_USERS_DIR / uid / "memory.md"


def _unsummarized_count(uid: str) -> int:
    db = get_db()
    row = db.execute(
        "SELECT COALESCE(last_summarized_id, 0) FROM users WHERE uid=?", (uid,)
    ).fetchone()
    if not row:
        return 0
    last_id = row[0] or 0
    row = db.execute("SELECT COALESCE(MAX(id), 0) FROM chat_history WHERE uid=?", (uid,)).fetchone()
    max_id = row[0] or 0
    return max(0, max_id - last_id)


def _should_summarize(uid: str) -> bool:
    return _unsummarized_count(uid) >= SUMMARY_THRESHOLD


SUMMARY_PROMPT = """\
Ты — синтезатор памяти ассистента. Твоя задача — поддерживать СТРУКТУРИРОВАННУЮ ПАМЯТЬ \
о пользователе {login} на основе истории диалогов.

ПРАВИЛА:
- Пиши КРАТКО. Целься в 500-1500 слов суммарно. Если память разрастается — сжимай старые пункты.
- Используй bullet points, не прозу.
- Обновляй существующие категории на месте. Не создавай новые категории без причины.
- Включай ТОЛЬКО ФАКТЫ, полезные ассистенту при следующих сессиях: имя, работа, проекты, \
интересы, предпочтения, договорённости, открытые задачи.
- НЕ включай мелкую болтовню, приветствия, разовые вопросы.
- НЕ дублируй то, что уже есть в ТЕКУЩЕЙ ПАМЯТИ — обновляй или дополняй.
- Пиши на русском, если юзер общается на русском.
- ВЫВОД: ТОЛЬКО содержимое memory.md (Markdown). Без преамбул и пояснений.

ФОРМАТ (Markdown):
# Память: {login}
## Имя и контекст
- Имя: ...
- Род занятий / роль: ...
- Язык общения: ...

## Текущие задачи и проекты
- ...

## Интересы и экспертиза
- ...

## Факты и предпочтения
- ...

## Стиль общения
- ...

## Договорённости и обещания
- ...
"""


async def _call_hermes_summary(messages: list[dict]) -> str:
    payload = {
        "model": HERMES_MODEL,
        "messages": messages,
        "max_tokens": 2048,
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {HERMES_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(f"{HERMES_API_URL}/v1/chat/completions", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()


async def _do_summarize(uid: str) -> bool:
    db = get_db()
    user = db.execute("SELECT login, name FROM users WHERE uid=?", (uid,)).fetchone()
    if not user:
        return False

    row = db.execute(
        "SELECT COALESCE(last_summarized_id, 0) FROM users WHERE uid=?", (uid,)
    ).fetchone()
    last_id = (row[0] or 0) if row else 0

    rows = db.execute(
        "SELECT id, role, content, created_at FROM chat_history "
        "WHERE uid=? AND id > ? AND role IN ('user', 'assistant') "
        "ORDER BY id ASC LIMIT ?",
        (uid, last_id, SUMMARY_MAX_HISTORY),
    ).fetchall()
    if not rows:
        return False

    mem_path = _user_memory_path(uid)
    current_memory = mem_path.read_text(encoding="utf-8") if mem_path.exists() else ""

    conv_lines: list[str] = []
    for r in rows:
        content = (r["content"] or "").strip()
        if len(content) > 1500:
            content = content[:1500] + "…"
        conv_lines.append(f"[{r['role']}] {content}")
    conv_text = "\n".join(conv_lines)

    login = user["login"] or "user"
    name = user["name"] or login
    header = SUMMARY_PROMPT.format(login=login, name=name)
    user_msg = (
        f"=== ТЕКУЩАЯ ПАМЯТЬ ===\n{current_memory or '(пусто)'}\n\n"
        f"=== НОВЫЕ СООБЩЕНИЯ ({len(rows)} шт., {rows[0]['id']}…{rows[-1]['id']}) ===\n{conv_text}\n\n"
        f"=== ЗАДАЧА ===\nВерни обновлённую память (только Markdown-тело)."
    )

    try:
        new_memory = await _call_hermes_summary([
            {"role": "system", "content": header},
            {"role": "user", "content": user_msg},
        ])
    except Exception:
        logging.exception("summary LLM call failed for uid=%s", uid)
        return False

    if not new_memory:
        return False

    mem_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = mem_path.with_suffix(".md.tmp")
    tmp.write_text(new_memory, encoding="utf-8")
    tmp.rename(mem_path)

    max_id = rows[-1]["id"]
    db.execute("UPDATE users SET last_summarized_id=? WHERE uid=?", (max_id, uid))

    logging.info(
        "summarized uid=%s: +%d msgs, memory=%d bytes",
        uid, len(rows), len(new_memory),
    )
    return True


async def maybe_summarize(uid: str) -> None:
    """Fire-and-forget. Schedule from anywhere after a chat turn."""
    lock = _locks.setdefault(uid, asyncio.Lock())
    if lock.locked():
        return
    async with lock:
        if not _should_summarize(uid):
            return
        try:
            await _do_summarize(uid)
        except Exception:
            logging.exception("summarize failed for uid=%s", uid)
        if _should_summarize(uid):
            asyncio.create_task(maybe_summarize(uid))


def force_summarize(uid: str) -> None:
    """Sync wrapper: schedule summarize_now regardless of threshold (used by admin tools)."""
    async def _go():
        lock = _locks.setdefault(uid, asyncio.Lock())
        async with lock:
            await _do_summarize(uid)
    asyncio.create_task(_go())
