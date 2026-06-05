"""Scheduled job handlers (spec 11).

Each handler is a sync function: `handle_X(uid, payload, channel, job_id)`
returning a dict {status, message}. The worker (app/scheduler.py) wraps the
call in job_runs logging + quota guard + next_run_at recomputation.

Quotas:
- reminder: no LLM call → no quota check
- morning_digest / custom_prompt: LLM call → quota.check_quota before call
"""

from ..db import get_db, now_iso


# --- helpers ---

def _save_assistant_message(uid: str, content: str) -> None:
    get_db().execute(
        "INSERT INTO chat_history (uid, channel, role, content, tokens, created_at) "
        "VALUES (?, 'scheduler', 'assistant', ?, 0, ?)",
        (uid, content, now_iso()),
    )


def _add_notification(uid: str, title: str, body: str, link: str | None = None) -> None:
    get_db().execute(
        "INSERT INTO notifications (uid, title, body, link, read, created_at) "
        "VALUES (?, ?, ?, ?, 0, ?)",
        (uid, title, body, link, now_iso()),
    )


def _get_telegram_id(uid: str) -> int | None:
    """Lookup telegram_id for a user. Falls back to auth.json reverse lookup."""
    row = get_db().execute(
        "SELECT telegram_id FROM users WHERE uid=?", (uid,),
    ).fetchone()
    if row and row["telegram_id"]:
        return row["telegram_id"]
    import json
    from ..db import HERMES_SHARED_DIR
    auth_path = HERMES_SHARED_DIR / "auth.json"
    if not auth_path.exists():
        return None
    try:
        auth = json.loads(auth_path.read_text() or "{}")
        for tg_id, linked_uid in auth.items():
            if linked_uid == uid:
                return int(tg_id)
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _send_telegram_sync(chat_id: int, text: str) -> None:
    """Send a Telegram message synchronously via Bot API."""
    import httpx
    import os
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    with httpx.Client(timeout=15) as client:
        r = client.post(url, json={"chat_id": chat_id, "text": text})
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram sendMessage: {data.get('description')}")


def _try_telegram(uid: str, text: str) -> tuple[bool, str | None]:
    """Best-effort Telegram delivery; returns (delivered, error).

    Looks up the user's telegram_id (with auth.json fallback).
    Uses a direct sync HTTP call to Telegram Bot API — no async relay dependency.
    """
    chat_id = _get_telegram_id(uid)
    if not chat_id:
        return False, None
    try:
        _send_telegram_sync(chat_id, text)
        return True, None
    except Exception as e:  # pragma: no cover — best-effort
        return False, str(e)


# --- reminder ---

def handle_reminder(*, uid: str, payload: dict, channel: str, job_id: str) -> dict:
    message = (payload.get("message") or "").strip()
    if not message:
        return {"status": "error", "message": "reminder payload is empty"}
    context = (payload.get("context") or "").strip()
    full = message if not context else f"{message}\n\nКонтекст: {context}"

    channels = [channel] if channel != "both" else ["telegram", "web"]
    delivered_tg = False
    for ch in channels:
        if ch == "telegram":
            ok, _ = _try_telegram(uid, f"⏰ {full}")
            delivered_tg = ok
        if ch == "web":
            _add_notification(uid, "Напоминание", full, link=None)
    _save_assistant_message(uid, f"⏰ {full}")

    status = "sent" if (delivered_tg or "web" in channels) else "partial"
    return {"status": status, "message": full[:500]}


# --- morning_digest ---

def _read_memory(uid: str) -> str:
    from .. import db as db_mod
    md_path = db_mod.HERMES_USERS_DIR / uid / "memory.md"
    if md_path.exists():
        return md_path.read_text(encoding="utf-8").strip()
    return ""


def _recent_history(uid: str, limit: int = 6) -> list[dict]:
    rows = get_db().execute(
        "SELECT role, content, created_at FROM chat_history "
        "WHERE uid=? AND role IN ('user','assistant') "
        "ORDER BY id DESC LIMIT ?",
        (uid, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def _pending_jobs(uid: str) -> list[dict]:
    rows = get_db().execute(
        "SELECT title, kind, next_run_at, channel FROM scheduled_jobs "
        "WHERE uid=? AND status='enabled' AND next_run_at IS NOT NULL "
        "ORDER BY next_run_at ASC LIMIT 10",
        (uid,),
    ).fetchall()
    return [dict(r) for r in rows]


def handle_morning_digest(*, uid: str, payload: dict, channel: str, job_id: str) -> dict:
    from .. import quota

    ok, err = quota.check_quota(uid, quota.MAX_TOKENS_PER_RESPONSE + quota.MIN_QUOTA_RESERVE_TOKENS)
    if not ok:
        return {"status": "skipped_quota", "message": err}

    # Build compact context
    parts: list[str] = []
    if payload.get("include_memory"):
        mem = _read_memory(uid)
        if mem:
            parts.append("## Память о пользователе\n" + mem)
    if payload.get("include_recent_history"):
        hist = _recent_history(uid, limit=6)
        if hist:
            lines = [f"[{h['created_at']}] {h['role']}: {h['content'][:200]}" for h in hist]
            parts.append("## Последние сообщения\n" + "\n".join(lines))
    if payload.get("include_tasks"):
        jobs = _pending_jobs(uid)
        if jobs:
            lines = [f"- {j['title']} ({j['kind']}, {j['next_run_at']}, {j['channel']})" for j in jobs]
            parts.append("## Запланированные задачи\n" + "\n".join(lines))

    if not parts:
        body = "Утренний дайджест: пока нечего показать (нет памяти, истории или задач)."
    else:
        body_text = "\n\n".join(parts)
        body = (
            "Подготовь короткий утренний дайджест для пользователя "
            "(3-6 пунктов, деловой тон, на русском). "
            "Вот компактный контекст:\n\n" + body_text
        )

    # Run through Hermes if quota is OK (already checked above)
    import asyncio
    from .. import chat
    try:
        result = asyncio.run(_call_hermes(chat, body, uid))
        content = result["content"]
    except Exception as e:
        return {"status": "error", "message": f"hermes call failed: {e}"}

    # Persist + deliver
    _save_assistant_message(uid, f"☀️ Утренний дайджест\n\n{content}")
    if channel in ("web", "both"):
        _add_notification(uid, "Утренний дайджест", content[:1000])
    if channel in ("telegram", "both"):
        _try_telegram(uid, f"☀️ Утренний дайджест\n\n{content[:3500]}")

    return {"status": "sent", "message": content[:500]}


async def _call_hermes(chat_mod, body: str, uid: str) -> dict:
    """Async wrapper around the sync call_hermes for use inside asyncio.run."""
    return await chat_mod.call_hermes(
        [{"role": "user", "content": body}], uid=uid,
    )


# --- custom_prompt ---

def handle_custom_prompt(*, uid: str, payload: dict, channel: str, job_id: str) -> dict:
    from .. import quota
    ok, err = quota.check_quota(uid, quota.MAX_TOKENS_PER_RESPONSE + quota.MIN_QUOTA_RESERVE_TOKENS)
    if not ok:
        return {"status": "skipped_quota", "message": err}

    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return {"status": "error", "message": "custom_prompt payload.prompt is empty"}
    send_result = bool(payload.get("send_result", True))

    import asyncio
    from .. import chat
    try:
        result = asyncio.run(_call_hermes(chat, prompt, uid))
        content = result["content"]
    except Exception as e:
        return {"status": "error", "message": f"hermes call failed: {e}"}

    _save_assistant_message(uid, f"📋 {prompt}\n\n{content}")
    if send_result and channel in ("web", "both"):
        _add_notification(uid, "Автоматизация", content[:1000])
    if send_result and channel in ("telegram", "both"):
        _try_telegram(uid, f"📋 {prompt}\n\n{content[:3500]}")
    return {"status": "sent", "message": content[:500]}


# --- dispatch ---

def dispatch(*, job_kind: str, uid: str, payload: dict, channel: str, job_id: str) -> dict:
    if job_kind == "reminder":
        return handle_reminder(uid=uid, payload=payload, channel=channel, job_id=job_id)
    if job_kind == "morning_digest":
        return handle_morning_digest(uid=uid, payload=payload, channel=channel, job_id=job_id)
    if job_kind == "custom_prompt":
        return handle_custom_prompt(uid=uid, payload=payload, channel=channel, job_id=job_id)
    return {"status": "error", "message": f"unknown job kind: {job_kind!r}"}
