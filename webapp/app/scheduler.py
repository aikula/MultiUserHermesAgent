"""Background scheduler worker (spec 11).

Single-process MVP: an asyncio task in the FastAPI app's event loop that
wakes up every `SCHEDULER_TICK_SECONDS` (default 30), finds due jobs, and
runs them one at a time inside a thread executor (handlers are sync).

For multi-process deployments this would need a DB-backed lock; for now we
rely on a per-process in-memory lock to prevent re-entrancy.
"""
import asyncio
import json
import logging
import secrets
from datetime import datetime, timezone

from .db import get_db, now_iso
from .jobs import store as job_store
from .jobs.handlers import dispatch

log = logging.getLogger("scheduler")

SCHEDULER_TICK_SECONDS = 30
_inflight: bool = False


def _new_run_id() -> str:
    return "run_" + secrets.token_urlsafe(8)


def _record_run_start(job_id: str, uid: str) -> str:
    rid = _new_run_id()
    get_db().execute(
        "INSERT INTO job_runs (id, job_id, uid, started_at, status) VALUES (?, ?, ?, ?, 'running')",
        (rid, job_id, uid, now_iso()),
    )
    return rid


def _record_run_finish(run_id: str, status: str, result: str | None, error: str | None) -> None:
    get_db().execute(
        "UPDATE job_runs SET finished_at=?, status=?, result=?, error=? WHERE id=?",
        (now_iso(), status, result, error, run_id),
    )


def _recompute_after_run(job: dict) -> str | None:
    """Update next_run_at and status based on job's schedule + payload.

    - one_time: disable, clear next_run_at (won't fire again)
    - daily/weekly: advance next_run_at by 1 day / 1 week
    """
    schedule_type = job["schedule_type"]
    weekdays = None
    if job.get("rrule") and job["rrule"].startswith("WEEKLY;"):
        weekdays = [int(d) for d in job["rrule"].split(";")[1].split(",") if d]

    now = datetime.now(timezone.utc)
    if schedule_type == "one_time":
        # Don't fire again
        get_db().execute(
            "UPDATE scheduled_jobs SET status='disabled', next_run_at=NULL, "
            "last_run_at=?, last_result=?, updated_at=? WHERE id=?",
            (now_iso(), "completed", now_iso(), job["id"]),
        )
        return None

    next_run = job_store.compute_next_run_at(
        schedule_type, run_at=None, time_of_day=_extract_time_of_day(job),
        weekdays=weekdays, after=now,
    )
    job_store.update_next_run_at(job["id"], next_run)
    job_store.set_last_run(job["id"], now_iso(), "completed")
    return next_run


def _extract_time_of_day(job: dict) -> str | None:
    """For daily/weekly, the original `time_of_day` was used to set `run_at`
    (the seed) at create time. Recover the HH:MM from that seed.
    Returns HH:MM in UTC, or None if it cannot be derived.
    """
    seed = job.get("run_at")
    if not seed:
        return None
    try:
        dt = datetime.fromisoformat(seed)
    except (TypeError, ValueError):
        return None
    return f"{dt.hour:02d}:{dt.minute:02d}"


def _execute_one(job: dict) -> dict:
    """Run a single job synchronously (callable from a thread executor)."""
    payload = json.loads(job["payload_json"]) if job.get("payload_json") else {}
    run_id = _record_run_start(job["id"], job["uid"])
    try:
        result = dispatch(
            job_kind=job["kind"],
            uid=job["uid"],
            payload=payload,
            channel=job["channel"],
            job_id=job["id"],
        )
        status = result.get("status", "error")
        if status == "skipped_quota":
            _record_run_finish(run_id, "skipped_quota", result.get("message", "")[:500], None)
        elif status == "error":
            _record_run_finish(run_id, "error", None, result.get("message", ""))
        else:
            _record_run_finish(run_id, "success", result.get("message", "")[:500], None)
        _recompute_after_run(job)
        return result
    except Exception as e:
        log.exception("job %s crashed", job.get("id"))
        _record_run_finish(run_id, "error", None, str(e))
        # Still mark one_time as disabled so we don't loop
        if job["schedule_type"] == "one_time":
            get_db().execute(
                "UPDATE scheduled_jobs SET status='disabled', next_run_at=NULL, updated_at=? "
                "WHERE id=?",
                (now_iso(), job["id"]),
            )
        return {"status": "error", "message": str(e)}


def run_due_jobs(now: datetime | None = None) -> list[dict]:
    """Pick due jobs and run them. Returns the list of results.

    Synchronous; safe to call from a thread executor or from tests directly.
    Uses a process-local in-memory lock so the periodic loop doesn't re-enter.
    """
    global _inflight
    if _inflight:
        return []
    _inflight = True
    try:
        due = job_store.list_due_jobs(now=now or datetime.now(timezone.utc), limit=50)
        results = []
        for job in due:
            results.append(_execute_one(job))
        return results
    finally:
        _inflight = False


def run_now(uid: str, job_id: str) -> dict:
    """Run a specific job immediately (bypasses schedule). Demo + tests use this."""
    job = job_store.get_job(uid, job_id)
    if not job:
        return {"status": "not_found", "message": f"job {job_id} not found"}
    return _execute_one(job)


# --- Async loop ---

async def _scheduler_loop(stop_event: asyncio.Event) -> None:
    log.info("scheduler loop started; tick=%ss", SCHEDULER_TICK_SECONDS)
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(run_due_jobs)
        except Exception:
            log.exception("scheduler tick crashed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=SCHEDULER_TICK_SECONDS)
        except asyncio.TimeoutError:
            pass
    log.info("scheduler loop stopped")


_task: asyncio.Task | None = None
_stop: asyncio.Event | None = None


def start_scheduler_task() -> asyncio.Task | None:
    """Start the periodic worker. Idempotent."""
    global _task, _stop
    if _task is not None and not _task.done():
        return _task
    _stop = asyncio.Event()
    _task = asyncio.create_task(_scheduler_loop(_stop), name="hermes-scheduler")
    return _task


async def stop_scheduler_task() -> None:
    global _task, _stop
    if _stop is not None:
        _stop.set()
    if _task is not None:
        try:
            await asyncio.wait_for(_task, timeout=5)
        except asyncio.TimeoutError:
            _task.cancel()
        _task = None
        _stop = None
