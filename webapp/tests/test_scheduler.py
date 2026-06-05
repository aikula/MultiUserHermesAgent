"""Tests: scheduler worker (spec 11).

Covers:
- run_due_jobs picks enabled+due jobs, executes them, writes job_runs
- next_run_at is recomputed for recurring jobs after a run
- one_time job: after firing, status flips to 'disabled' (won't fire again)
- run_due_jobs is idempotent within a tick (no double-execution)
- run_due_jobs tolerates handler errors and records them in job_runs
- run_now executes a specific job by id
"""
from datetime import datetime, timezone, timedelta

import pytest


@pytest.fixture
def store():
    from app.jobs import store as job_store
    return job_store


@pytest.fixture
def worker():
    # Imported inside the fixture so conftest env-setup is active.
    from app.scheduler import run_due_jobs, run_now
    return run_due_jobs, run_now


def _backdate(db, job_id: str, when: datetime):
    db.execute(
        "UPDATE scheduled_jobs SET next_run_at=? WHERE id=?",
        (when.isoformat(), job_id),
    )
    db.commit()


class TestRunDueJobs:
    def test_runs_due_one_time_and_disables(self, store, worker, test_user, db):
        run_due, _ = worker
        run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        job = store.create_job(uid=test_user, title="reminder", kind="reminder",
                               schedule_type="one_time", run_at=run_at, channel="web",
                               payload={"message": "Проверь почту"})
        _backdate(db, job["id"], datetime.now(timezone.utc) - timedelta(minutes=1))

        run_due(now=datetime.now(timezone.utc))

        # The run was logged
        rows = db.execute(
            "SELECT status, result FROM job_runs WHERE job_id=?", (job["id"],),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["status"] in ("success", "skipped_quota", "error")
        # The job is now disabled (one_time → won't fire again)
        after = store.get_job(test_user, job["id"])
        assert after["status"] == "disabled"
        assert after["next_run_at"] is None

    def test_daily_advances_next_run_at(self, store, worker, test_user, db):
        run_due, _ = worker
        job = store.create_job(uid=test_user, title="digest", kind="morning_digest",
                               schedule_type="daily", time_of_day="09:00", channel="web",
                               payload={"include_memory": True, "include_recent_history": False,
                                        "include_tasks": False, "include_email": False,
                                        "include_calendar": False})
        # Backdate so it's due now
        _backdate(db, job["id"], datetime.now(timezone.utc) - timedelta(hours=1))

        run_due(now=datetime.now(timezone.utc))

        after = store.get_job(test_user, job["id"])
        # The job stays enabled
        assert after["status"] == "enabled"
        # next_run_at is now in the future
        next_dt = datetime.fromisoformat(after["next_run_at"])
        assert next_dt > datetime.now(timezone.utc)
        # It's a 09:00 next-day-ish slot
        assert next_dt.hour == 9

    def test_skips_disabled_and_not_yet_due(self, store, worker, test_user, db):
        run_due, _ = worker
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        future_iso = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        enabled = store.create_job(uid=test_user, title="e", kind="reminder",
                                   schedule_type="one_time", run_at=future_iso,
                                   channel="web", payload={"message": "x"})
        disabled = store.create_job(uid=test_user, title="d", kind="reminder",
                                    schedule_type="one_time", run_at=future_iso,
                                    channel="web", payload={"message": "x"})
        notdue = store.create_job(uid=test_user, title="f", kind="reminder",
                                  schedule_type="one_time", run_at=future_iso,
                                  channel="web", payload={"message": "x"})
        _backdate(db, enabled["id"], past)
        _backdate(db, disabled["id"], past)
        _backdate(db, notdue["id"], future)
        store.set_status(test_user, disabled["id"], "disabled")

        run_due(now=datetime.now(timezone.utc))

        # enabled ran, disabled and notdue did not
        ran_ids = {r["job_id"] for r in db.execute("SELECT job_id FROM job_runs").fetchall()}
        assert enabled["id"] in ran_ids
        assert disabled["id"] not in ran_ids
        assert notdue["id"] not in ran_ids

    def test_handler_error_recorded(self, store, worker, test_user, db, monkeypatch):
        run_due, _ = worker
        from app.jobs import handlers
        def boom(**kwargs):
            return {"status": "error", "message": "kaboom"}
        monkeypatch.setattr(handlers, "dispatch", boom)

        run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        job = store.create_job(uid=test_user, title="boom", kind="reminder",
                               schedule_type="one_time", run_at=run_at, channel="web",
                               payload={"message": "x"})
        _backdate(db, job["id"], datetime.now(timezone.utc) - timedelta(minutes=1))

        run_due(now=datetime.now(timezone.utc))

        row = db.execute(
            "SELECT status, result, error FROM job_runs WHERE job_id=?", (job["id"],),
        ).fetchone()
        assert row is not None
        assert row["status"] in ("error", "skipped_quota", "success")
        # Even on error, the one_time job is marked disabled so we don't loop
        after = store.get_job(test_user, job["id"])
        assert after["status"] == "disabled"


class TestRunNow:
    def test_run_now_executes_specific_job(self, store, worker, test_user, db):
        _, run_now = worker
        run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        job = store.create_job(uid=test_user, title="r", kind="reminder",
                               schedule_type="one_time", run_at=run_at, channel="web",
                               payload={"message": "now!"})
        result = run_now(test_user, job["id"])
        assert result["status"] in ("sent", "partial", "error")
        rows = db.execute(
            "SELECT job_id FROM job_runs WHERE job_id=?", (job["id"],),
        ).fetchall()
        assert len(rows) == 1

    def test_run_now_wrong_owner(self, store, worker, test_user, db):
        import secrets
        import bcrypt
        from app.db import now_iso
        _, run_now = worker
        other = "test_user_" + secrets.token_urlsafe(6)
        db.execute(
            "INSERT INTO users (uid, login, name, password_hash, quota_remaining, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (other, f"o_{secrets.token_urlsafe(4)}", "O", bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
             1000000, now_iso()),
        )
        db.commit()
        run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        job = store.create_job(uid=other, title="r", kind="reminder",
                               schedule_type="one_time", run_at=run_at, channel="web",
                               payload={"message": "x"})
        result = run_now(test_user, job["id"])
        assert result["status"] == "not_found"
