"""Per-user quota tracking.

Tracks token usage per turn, decrements users.quota_remaining, and maintains
a daily JSON snapshot per user for human inspection.

HARD CAP: requests are blocked when quota_remaining <= 0.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from .db import QUOTAS_DIR, get_db, now_iso

WELCOME_QUOTA = int(os.environ.get("WELCOME_QUOTA", "2000000"))
ALERT_THRESHOLD_PCT = int(os.environ.get("ALERT_THRESHOLD_PCT", "80"))
MIN_QUOTA_RESERVE_TOKENS = int(os.environ.get("MIN_QUOTA_RESERVE_TOKENS", "2048"))
MAX_TOKENS_PER_RESPONSE = int(os.environ.get("MAX_TOKENS_PER_RESPONSE", "1024"))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_ADMIN_BOT_TOKEN = os.environ.get("TELEGRAM_ADMIN_BOT_TOKEN", "").strip() or TELEGRAM_BOT_TOKEN
TELEGRAM_ADMIN_CHAT_ID = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "").strip()


def check_quota(uid: str, estimated_tokens: int | None = None) -> tuple[bool, str]:
    """Preflight quota check. Returns (ok, error_message).
    Call BEFORE making Hermes API request.
    If estimated_tokens is provided, also checks against reserve.
    """
    remaining = _quota_remaining(uid)
    if remaining <= 0:
        return False, (
            f"⚠️ Квота исчерпана. Использовано {WELCOME_QUOTA:,} из {WELCOME_QUOTA:,} токенов.\n"
            "Обратитесь к администратору для продления квоты."
        )
    reserve = estimated_tokens or MIN_QUOTA_RESERVE_TOKENS
    if remaining < reserve:
        return False, (
            f"⚠️ Недостаточно квоты (осталось {remaining:,}, нужно ~{reserve:,}). "
            "Попробуйте позже или обратитесь к администратору."
        )
    return True, ""


def _daily_path(uid: str, date: str) -> Path:
    return QUOTAS_DIR / uid / f"{date}.json"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def record(uid: str, channel: str, tokens: int) -> int:
    """Декремент quota_remaining, инкремент quota_used, обновление daily JSON.
    Clamp-ит остаток: quota не может стать ниже 0.
    Возвращает новое значение quota_remaining.
    """
    if tokens <= 0:
        return _quota_remaining(uid)
    db = get_db()
    current = _quota_remaining(uid)
    actual = min(tokens, current)
    db.execute(
        "UPDATE users SET quota_remaining = COALESCE(quota_remaining, 0) - ?, "
        "quota_used = COALESCE(quota_used, 0) + ? "
        "WHERE uid=?",
        (actual, actual, uid),
    )
    _update_daily(uid, channel, actual)
    remaining = _quota_remaining(uid)
    _maybe_alert(uid, remaining)
    return remaining


def _quota_remaining(uid: str) -> int:
    row = get_db().execute(
        "SELECT COALESCE(quota_remaining, 0) FROM users WHERE uid=?", (uid,)
    ).fetchone()
    return (row[0] or 0) if row else 0


def _quota_used(uid: str) -> int:
    row = get_db().execute(
        "SELECT COALESCE(quota_used, 0) FROM users WHERE uid=?", (uid,)
    ).fetchone()
    return (row[0] or 0) if row else 0


def _login(uid: str) -> str:
    row = get_db().execute("SELECT login FROM users WHERE uid=?", (uid,)).fetchone()
    return row[0] if row else uid


def _update_daily(uid: str, channel: str, tokens: int) -> None:
    """Атомарно обновляет /opt/app/data/quotas/<uid>/<YYYY-MM-DD>.json."""
    path = _daily_path(uid, _today())
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {}

    data.setdefault("date", _today())
    data["uid"] = uid
    data["login"] = _login(uid)
    data["tokens_total"] = data.get("tokens_total", 0) + tokens
    data["calls_total"] = data.get("calls_total", 0) + 1
    by_ch_t = data.setdefault("tokens_by_channel", {})
    by_ch_t[channel] = by_ch_t.get(channel, 0) + tokens
    by_ch_c = data.setdefault("calls_by_channel", {})
    by_ch_c[channel] = by_ch_c.get(channel, 0) + 1
    data["last_updated"] = now_iso()

    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.rename(path)


def _maybe_alert(uid: str, remaining: int) -> None:
    """Оповещение админу при пересечении ALERT_THRESHOLD_PCT, не чаще раза в сутки."""
    if WELCOME_QUOTA <= 0:
        return
    used = WELCOME_QUOTA - remaining
    pct = int(used * 100 / WELCOME_QUOTA)
    if pct < ALERT_THRESHOLD_PCT:
        return
    db = get_db()
    last_pct = db.execute(
        "SELECT COALESCE(last_alert_pct, 0) FROM users WHERE uid=?", (uid,)
    ).fetchone()[0] or 0
    if pct < last_pct + 5 and last_pct >= ALERT_THRESHOLD_PCT:
        return
    db.execute("UPDATE users SET last_alert_pct=? WHERE uid=?", (pct, uid))

    msg = (
        f"⚠️ Hermes: юзер {_login(uid)} (uid={uid}) использовал {pct}% квоты "
        f"({used:,} / {WELCOME_QUOTA:,} токенов)"
    )
    logging.warning(msg)
    if TELEGRAM_ADMIN_BOT_TOKEN and TELEGRAM_ADMIN_CHAT_ID:
        try:
            import httpx
            with httpx.Client(timeout=10) as c:
                c.post(
                    f"https://api.telegram.org/bot{TELEGRAM_ADMIN_BOT_TOKEN}/sendMessage",
                    json={"chat_id": TELEGRAM_ADMIN_CHAT_ID, "text": msg},
                )
        except Exception:
            logging.exception("admin alert telegram send failed")


def get_usage(uid: str) -> dict:
    """Breakdown для /api/usage и /profile."""
    db = get_db()
    user = db.execute(
        "SELECT login, name, quota_remaining, quota_used, created_at FROM users WHERE uid=?",
        (uid,),
    ).fetchone()
    if not user:
        return {}

    today = _today()
    today_path = _daily_path(uid, today)
    today_used = 0
    today_calls = 0
    if today_path.exists():
        try:
            d = json.loads(today_path.read_text(encoding="utf-8"))
            today_used = d.get("tokens_total", 0)
            today_calls = d.get("calls_total", 0)
        except (json.JSONDecodeError, OSError):
            pass

    monthly_row = db.execute(
        "SELECT COALESCE(SUM(tokens), 0), COUNT(*) FROM chat_history "
        "WHERE uid=? AND role='assistant' AND created_at >= ?",
        (uid, f"{today[:7]}-01T00:00:00+00:00"),
    ).fetchone()
    monthly_used = monthly_row[0] or 0
    monthly_calls = monthly_row[1] or 0

    remaining = max(0, user["quota_remaining"] or 0)
    used = user["quota_used"] or 0
    pct = int(used * 100 / WELCOME_QUOTA) if WELCOME_QUOTA > 0 else 0
    return {
        "login": user["login"],
        "name": user["name"],
        "welcome_quota": WELCOME_QUOTA,
        "used": used,
        "remaining": remaining,
        "pct": min(pct, 100),
        "today_tokens": today_used,
        "today_calls": today_calls,
        "month_tokens": monthly_used,
        "month_calls": monthly_calls,
        "alert_threshold_pct": ALERT_THRESHOLD_PCT,
    }


# --- Async wrappers ---

async def acheck_quota(uid: str, estimated_tokens: int | None = None) -> tuple[bool, str]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, check_quota, uid, estimated_tokens)


async def arecord(uid: str, channel: str, tokens: int) -> int:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, record, uid, channel, tokens)


async def aget_usage(uid: str) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_usage, uid)
