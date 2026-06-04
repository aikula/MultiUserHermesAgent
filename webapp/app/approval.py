"""Action approval model.

Single-confirmation UX for external actions (email, calendar, etc.).
Implements the approval policy from docs/specs/02_action_approval_policy.md.
"""
import asyncio
import hashlib
import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

from .db import get_db, now_iso

log = logging.getLogger(__name__)

# Intent TTL in minutes
INTENT_TTL_MINUTES = 15

# Action types that require approval
REVIEW_ACTIONS = {"email_send", "calendar_create", "calendar_update", "telegram_send_external", "file_share_external", "web_download_files", "create_scheduled_job"}

# --- Confirmation / rejection parsers ---

_CONFIRMATION_PATTERNS = re.compile(
    r"^(да|подтверждаю|подтверждаю отправку|отправляй|можно отправлять|согласен|approve|send it|yes|confirm|ok|go|поехали|вперёд|сделай|выполни)\s*[!.]?\s*$",
    re.IGNORECASE,
)

_REJECTION_PATTERNS = re.compile(
    r"^(не отправляй|отмена|подожди|измени текст|нет|стоп|cancel|stop|wait|no|reject|abort)\s*[!.]?\s*$",
    re.IGNORECASE,
)


def is_confirmation(text: str) -> bool:
    """Check if user text is a confirmation of a pending action."""
    return bool(_CONFIRMATION_PATTERNS.match(text.strip()))


def is_rejection(text: str) -> bool:
    """Check if user text is a rejection/cancellation of a pending action."""
    return bool(_REJECTION_PATTERNS.match(text.strip()))


def _payload_hash(payload: dict) -> str:
    """Deterministic hash of action payload for change detection."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _gen_intent_id() -> str:
    return f"intent_{secrets.token_urlsafe(12)}"


# --- DB operations ---

def create_intent(uid: str, action_type: str, payload: dict) -> dict:
    """Create a new action intent. Returns the intent dict.
    If there's an existing pending intent for the same action_type and same payload,
    return it instead of creating a duplicate.
    """
    db = get_db()
    ph = _payload_hash(payload)

    # Check for existing pending intent with same payload
    existing = db.execute(
        "SELECT * FROM action_intents WHERE uid=? AND action_type=? AND status='pending_approval' AND payload_hash=?",
        (uid, action_type, ph),
    ).fetchone()
    if existing:
        return dict(existing)

    # Cancel any existing pending intents for this action_type (payload changed)
    db.execute(
        "UPDATE action_intents SET status='expired' WHERE uid=? AND action_type=? AND status='pending_approval'",
        (uid, action_type),
    )

    intent_id = _gen_intent_id()
    created = now_iso()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=INTENT_TTL_MINUTES)).isoformat()

    db.execute(
        "INSERT INTO action_intents (id, uid, action_type, status, payload_json, payload_hash, created_at, expires_at) "
        "VALUES (?, ?, ?, 'pending_approval', ?, ?, ?, ?)",
        (intent_id, uid, action_type, json.dumps(payload, ensure_ascii=False), ph, created, expires),
    )
    db.commit()

    log.info(f"Intent created: {intent_id} type={action_type} uid={uid}")
    return {"id": intent_id, "uid": uid, "action_type": action_type, "status": "pending_approval",
            "payload_json": json.dumps(payload, ensure_ascii=False), "payload_hash": ph,
            "created_at": created, "expires_at": expires}


def get_intent_by_id_for_user(intent_id: str, uid: str) -> dict | None:
    """Get a specific intent by id, verifying it belongs to the user."""
    db = get_db()
    row = db.execute(
        "SELECT * FROM action_intents WHERE id=? AND uid=?",
        (intent_id, uid),
    ).fetchone()
    return dict(row) if row else None


def get_pending_intent(uid: str, action_type: str | None = None) -> dict | None:
    """Get the latest pending intent for a user, optionally filtered by action_type."""
    db = get_db()
    if action_type:
        row = db.execute(
            "SELECT * FROM action_intents WHERE uid=? AND action_type=? AND status='pending_approval' "
            "ORDER BY created_at DESC LIMIT 1",
            (uid, action_type),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT * FROM action_intents WHERE uid=? AND status='pending_approval' "
            "ORDER BY created_at DESC LIMIT 1",
            (uid,),
        ).fetchone()
    return dict(row) if row else None


def approve_intent(intent_id: str) -> bool:
    """Approve a pending intent. Returns True if successful."""
    db = get_db()
    row = db.execute("SELECT * FROM action_intents WHERE id=? AND status='pending_approval'", (intent_id,)).fetchone()
    if not row:
        return False

    # Check TTL
    expires = datetime.fromisoformat(row["expires_at"])
    if datetime.now(timezone.utc) > expires:
        db.execute("UPDATE action_intents SET status='expired' WHERE id=?", (intent_id,))
        db.commit()
        return False

    db.execute(
        "UPDATE action_intents SET status='approved', approved_at=? WHERE id=? AND status='pending_approval'",
        (now_iso(), intent_id),
    )
    db.commit()
    log.info(f"Intent approved: {intent_id}")
    return True


def execute_intent(intent_id: str, result_json: str | None = None, error: str | None = None) -> None:
    """Mark intent as executed or failed."""
    db = get_db()
    status = "executed" if error is None else "failed"
    db.execute(
        "UPDATE action_intents SET status=?, executed_at=?, result_json=?, error=? WHERE id=?",
        (status, now_iso(), result_json, error, intent_id),
    )
    db.commit()
    log.info(f"Intent {status}: {intent_id}")


def reject_intent(intent_id: str) -> bool:
    """Reject a pending intent."""
    db = get_db()
    db.execute("UPDATE action_intents SET status='rejected', approved_at=? WHERE id=? AND status='pending_approval'",
               (now_iso(), intent_id))
    db.commit()
    return True


# --- Async wrappers ---

async def acreate_intent(uid: str, action_type: str, payload: dict) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, create_intent, uid, action_type, payload)


async def aget_intent_by_id_for_user(intent_id: str, uid: str) -> dict | None:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_intent_by_id_for_user, intent_id, uid)


async def aget_pending_intent(uid: str, action_type: str | None = None) -> dict | None:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_pending_intent, uid, action_type)


async def aapprove_intent(intent_id: str) -> bool:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, approve_intent, intent_id)


async def aexecute_intent(intent_id: str, result_json: str | None = None, error: str | None = None) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, execute_intent, intent_id, result_json, error)


async def areject_intent(intent_id: str) -> bool:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, reject_intent, intent_id)


def format_intent_payload(intent: dict) -> str:
    """Format intent payload for user display (Telegram or web)."""
    payload = json.loads(intent["payload_json"])
    action = intent["action_type"]

    if action == "email_send":
        to = payload.get("to", "?")
        subject = payload.get("subject", "(без темы)")
        body_preview = (payload.get("body") or "")[:200]
        return (
            f"📧 **Отправка письма**\n"
            f"Кому: {to}\n"
            f"Тема: {subject}\n"
            f"Текст: {body_preview}{'...' if len(payload.get('body', '')) > 200 else ''}"
        )
    elif action == "calendar_create":
        summary = payload.get("summary", "?")
        dt = payload.get("dt", "?")
        return f"📅 **Событие**: {summary} @ {dt}"
    elif action == "web_download_files":
        urls = payload.get("urls") or []
        folder = payload.get("target_folder") or "downloads"
        max_count = payload.get("max_count") or len(urls)
        preview = "\n".join(f"  - {u}" for u in urls[:5])
        if len(urls) > 5:
            preview += f"\n  … и ещё {len(urls) - 5}"
        return (
            f"🌐 **Скачивание файлов из веба**\n"
            f"Папка: {folder}\n"
            f"Лимит: {max_count} файлов\n"
            f"Источники:\n{preview}"
        )
    elif action == "create_scheduled_job":
        kind = payload.get("kind", "?")
        schedule = payload.get("schedule_type", "?")
        channel = payload.get("channel", "web")
        title = payload.get("title", "")
        message = (payload.get("payload") or {}).get("message") or (payload.get("payload") or {}).get("prompt") or ""
        preview = f": {title}" if title else ""
        return (
            f"📋 **Создание автоматизации**{preview}\n"
            f"Тип: {kind} | Расписание: {schedule} | Канал: {channel}\n"
            f"{'Текст: ' + message[:150] if message else ''}"
        )
    else:
        return f"⚡ Действие: {action}\nДанные: {json.dumps(payload, ensure_ascii=False)[:300]}"
