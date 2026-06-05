"""Scheduled jobs storage (spec 11).

Engine layer — pure data access + pure-function next_run_at computation.
No I/O beyond the shared SQLite DB. Handlers and the worker live elsewhere.
"""
import json
import secrets
from datetime import datetime, timezone, timedelta

from ..db import get_db, now_iso

VALID_KINDS = {"reminder", "morning_digest", "custom_prompt"}
VALID_SCHEDULES = {"one_time", "daily", "weekly"}
VALID_CHANNELS = {"telegram", "web", "both"}
VALID_STATUSES = {"enabled", "disabled", "deleted"}


# ---- Validation helpers ----

def _validate(uid, title, kind, schedule_type, channel):
    if not uid or not isinstance(uid, str):
        raise ValueError("uid is required")
    if not title or not isinstance(title, str):
        raise ValueError("title is required")
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown kind: {kind!r}; expected one of {sorted(VALID_KINDS)}")
    if schedule_type not in VALID_SCHEDULES:
        raise ValueError(f"unknown schedule_type: {schedule_type!r}; expected one of {sorted(VALID_SCHEDULES)}")
    if channel not in VALID_CHANNELS:
        raise ValueError(f"unknown channel: {channel!r}; expected one of {sorted(VALID_CHANNELS)}")


# ---- Pure: next_run_at ----

def _parse_time_of_day(tod: str | None) -> tuple[int, int]:
    """Return (hour, minute) from 'HH:MM' string, or default to (9, 0)."""
    if not tod:
        return 9, 0
    parts = tod.split(":")
    if len(parts) != 2:
        raise ValueError(f"time_of_day must be 'HH:MM', got {tod!r}")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"time_of_day out of range: {tod!r}")
    return h, m


def compute_next_run_at(
    schedule_type: str,
    run_at: str | None,
    time_of_day: str | None,
    weekdays: list[int] | None,
    after: datetime,
) -> str | None:
    """Compute the next ISO UTC run time for a job, given the current time `after`.

    - one_time: returns `run_at` as-is; None if `run_at` is in the past
    - daily:    next occurrence of HH:MM (>= after)
    - weekly:   next occurrence of one of the weekdays at HH:MM (>= after);
                if `weekdays` is empty, behaves like daily
    """
    if schedule_type == "one_time":
        if not run_at:
            return None
        dt = datetime.fromisoformat(run_at)
        if dt <= after:
            return None
        return dt.isoformat()

    h, m = _parse_time_of_day(time_of_day)
    if schedule_type == "daily":
        candidate = after.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate <= after:
            candidate = candidate + timedelta(days=1)
        return candidate.isoformat()

    if schedule_type == "weekly":
        if not weekdays:
            # No specific day → behave like daily
            candidate = after.replace(hour=h, minute=m, second=0, microsecond=0)
            if candidate <= after:
                candidate = candidate + timedelta(days=1)
            return candidate.isoformat()
        # Find the next day matching one of the weekdays (0=Mon..6=Sun)
        for offset in range(0, 8):
            candidate = (after + timedelta(days=offset)).replace(
                hour=h, minute=m, second=0, microsecond=0
            )
            if candidate.weekday() in weekdays and candidate > after:
                return candidate.isoformat()
        return None

    raise ValueError(f"unknown schedule_type: {schedule_type!r}")


# ---- CRUD ----

def _row_to_dict(row) -> dict:
    return dict(row) if row else None


def create_job(
    *,
    uid: str,
    title: str,
    kind: str,
    schedule_type: str,
    run_at: str | None = None,
    time_of_day: str | None = None,
    weekdays: list[int] | None = None,
    channel: str = "web",
    payload: dict | None = None,
) -> dict:
    _validate(uid, title, kind, schedule_type, channel)
    if schedule_type == "weekly" and weekdays is not None:
        for d in weekdays:
            if not isinstance(d, int) or not (0 <= d <= 6):
                raise ValueError(f"weekdays must be ints 0..6, got {weekdays!r}")
    if weekdays is not None and not isinstance(weekdays, list):
        raise ValueError("weekdays must be a list of ints 0..6")

    # If run_at wasn't given, compute a seed (used as `next_run_at`).
    now = datetime.now(timezone.utc)
    if not run_at and schedule_type != "one_time":
        # For daily/weekly, `run_at` is treated as a seed timestamp; next_run_at
        # is computed from `time_of_day` and `weekdays`.
        seed_iso = now.isoformat()
    else:
        seed_iso = run_at or now.isoformat()

    next_run = compute_next_run_at(
        schedule_type, seed_iso, time_of_day, weekdays, after=now,
    )

    # Reject one-time jobs with past run_at (allow sub-second timing tolerance)
    if schedule_type == "one_time" and next_run is None and not run_at:
        raise ValueError("one_time jobs require run_at in the future")
    if schedule_type == "one_time" and next_run is None and run_at:
        raise ValueError("run_at must be in the future for one_time jobs")

    job_id = "job_" + secrets.token_urlsafe(8)
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    rrule = None
    weekdays_json = None
    if weekdays:
        rrule = "WEEKLY;" + ",".join(str(d) for d in weekdays)
        weekdays_json = json.dumps(weekdays)

    db = get_db()
    db.execute(
        "INSERT INTO scheduled_jobs (id, uid, title, kind, status, schedule_type, "
        "run_at, time_of_day, weekdays, rrule, next_run_at, channel, payload_json, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'enabled', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, uid, title, kind, schedule_type, seed_iso, time_of_day, weekdays_json,
         rrule, next_run, channel, payload_json, now_iso(), now_iso()),
    )
    return get_job(uid, job_id) or {}


def get_job(uid: str, job_id: str) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM scheduled_jobs WHERE id=? AND uid=? AND status != 'deleted'",
        (job_id, uid),
    ).fetchone()
    return _row_to_dict(row)


def list_jobs(uid: str, include_deleted: bool = False) -> list[dict]:
    if include_deleted:
        rows = get_db().execute(
            "SELECT * FROM scheduled_jobs WHERE uid=? ORDER BY created_at DESC",
            (uid,),
        ).fetchall()
    else:
        rows = get_db().execute(
            "SELECT * FROM scheduled_jobs WHERE uid=? AND status != 'deleted' "
            "ORDER BY created_at DESC",
            (uid,),
        ).fetchall()
    return [dict(r) for r in rows]


def set_status(uid: str, job_id: str, status: str) -> bool:
    if status not in VALID_STATUSES:
        raise ValueError(f"unknown status: {status!r}")
    db = get_db()
    cur = db.execute(
        "UPDATE scheduled_jobs SET status=?, updated_at=? WHERE id=? AND uid=? AND status != 'deleted'",
        (status, now_iso(), job_id, uid),
    )
    return cur.rowcount > 0


def delete_job(uid: str, job_id: str) -> bool:
    cur = get_db().execute(
        "UPDATE scheduled_jobs SET status='deleted', updated_at=? "
        "WHERE id=? AND uid=? AND status != 'deleted'",
        (now_iso(), job_id, uid),
    )
    return cur.rowcount > 0


def list_due_jobs(now: datetime | None = None, limit: int = 50) -> list[dict]:
    """Return enabled jobs whose `next_run_at` is <= now, in chronological order."""
    now = now or datetime.now(timezone.utc)
    rows = get_db().execute(
        "SELECT * FROM scheduled_jobs "
        "WHERE status='enabled' AND next_run_at IS NOT NULL AND next_run_at <= ? "
        "ORDER BY next_run_at ASC LIMIT ?",
        (now.isoformat(), limit),
    ).fetchall()
    return [dict(r) for r in rows]


def update_next_run_at(job_id: str, next_run: str | None) -> None:
    get_db().execute(
        "UPDATE scheduled_jobs SET next_run_at=?, updated_at=? WHERE id=?",
        (next_run, now_iso(), job_id),
    )


def set_last_run(job_id: str, last_run_at: str, last_result: str) -> None:
    get_db().execute(
        "UPDATE scheduled_jobs SET last_run_at=?, last_result=?, updated_at=? WHERE id=?",
        (last_run_at, last_result, now_iso(), job_id),
    )
