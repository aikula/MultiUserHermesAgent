"""Tests: scheduled jobs store (spec 11).

Covers engine layer:
- create / list / get / disable / enable / delete
- next_run_at computation: one_time, daily, weekly
- list is scoped per-user (no cross-user leak)
- delete is soft (status -> 'deleted')
- payload_json round-trips correctly
"""
import json
from datetime import datetime, timezone, timedelta

import pytest


@pytest.fixture
def store():
    # Imported inside the fixture so conftest env-setup is active.
    from app.jobs import store as job_store
    return job_store


class TestCreateJob:
    def test_create_one_time_returns_full_row(self, store, test_user):
        run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        job = store.create_job(
            uid=test_user,
            title="Позвонить клиенту",
            kind="reminder",
            schedule_type="one_time",
            run_at=run_at,
            channel="telegram",
            payload={"message": "Проверить статус заказа"},
        )
        assert job["uid"] == test_user
        assert job["title"] == "Позвонить клиенту"
        assert job["kind"] == "reminder"
        assert job["status"] == "enabled"
        assert job["schedule_type"] == "one_time"
        assert job["channel"] == "telegram"
        assert job["next_run_at"] == run_at
        assert json.loads(job["payload_json"]) == {"message": "Проверить статус заказа"}
        assert job["id"]
        assert job["created_at"]
        assert job["updated_at"]

    def test_create_requires_title(self, store, test_user):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with pytest.raises(ValueError, match="title"):
            store.create_job(
                uid=test_user, title="", kind="reminder",
                schedule_type="one_time", run_at=future,
                channel="web", payload={},
            )

    def test_create_rejects_unknown_kind(self, store, test_user):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with pytest.raises(ValueError, match="kind"):
            store.create_job(
                uid=test_user, title="x", kind="unknown_kind",
                schedule_type="one_time", run_at=future,
                channel="web", payload={},
            )

    def test_create_rejects_unknown_schedule_type(self, store, test_user):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with pytest.raises(ValueError, match="schedule_type"):
            store.create_job(
                uid=test_user, title="x", kind="reminder",
                schedule_type="every_blue_moon", run_at=future,
                channel="web", payload={},
            )

    def test_create_rejects_unknown_channel(self, store, test_user):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with pytest.raises(ValueError, match="channel"):
            store.create_job(
                uid=test_user, title="x", kind="reminder",
                schedule_type="one_time", run_at=future,
                channel="fax", payload={},
            )

    def test_create_one_time_rejects_missing_run_at(self, store, test_user):
        with pytest.raises(ValueError, match="future"):
            store.create_job(
                uid=test_user, title="x", kind="reminder",
                schedule_type="one_time", run_at=None,
                channel="web", payload={},
            )

    def test_create_one_time_rejects_past_run_at(self, store, test_user):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with pytest.raises(ValueError, match="future"):
            store.create_job(
                uid=test_user, title="x", kind="reminder",
                schedule_type="one_time", run_at=past,
                channel="web", payload={},
            )

    def test_create_daily_computes_next_run_at(self, store, test_user):
        # No run_at given — should default to "next 9am" from now
        job = store.create_job(
            uid=test_user, title="Дайджест", kind="morning_digest",
            schedule_type="daily",
            time_of_day="09:00",
            channel="telegram",
            payload={"include_memory": True, "include_recent_history": True,
                     "include_tasks": True, "include_email": False, "include_calendar": False},
        )
        next_run = datetime.fromisoformat(job["next_run_at"])
        assert next_run.tzinfo is not None
        assert next_run.hour == 9
        assert next_run.minute == 0
        # next_run is in the future
        assert next_run > datetime.now(timezone.utc)


class TestListAndGet:
    def test_list_returns_only_user_jobs(self, store, test_user, db):
        # Create a second user
        import secrets
        import bcrypt
        from app.db import now_iso
        other = "test_user_" + secrets.token_urlsafe(6)
        db.execute(
            "INSERT INTO users (uid, login, name, password_hash, quota_remaining, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (other, f"other_{secrets.token_urlsafe(4)}", "Other", bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
             1000000, now_iso()),
        )
        db.commit()
        run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        a = store.create_job(uid=test_user, title="mine", kind="reminder",
                             schedule_type="one_time", run_at=run_at, channel="web",
                             payload={"message": "x"})
        b = store.create_job(uid=other, title="theirs", kind="reminder",
                             schedule_type="one_time", run_at=run_at, channel="web",
                             payload={"message": "y"})

        mine = store.list_jobs(test_user)
        theirs = store.list_jobs(other)
        assert len(mine) == 1 and mine[0]["id"] == a["id"]
        assert len(theirs) == 1 and theirs[0]["id"] == b["id"]

    def test_list_excludes_deleted_by_default(self, store, test_user):
        run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        job = store.create_job(uid=test_user, title="t", kind="reminder",
                               schedule_type="one_time", run_at=run_at, channel="web",
                               payload={"message": "m"})
        store.delete_job(test_user, job["id"])
        assert store.list_jobs(test_user) == []

    def test_list_with_include_deleted(self, store, test_user):
        run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        job = store.create_job(uid=test_user, title="t", kind="reminder",
                               schedule_type="one_time", run_at=run_at, channel="web",
                               payload={"message": "m"})
        store.delete_job(test_user, job["id"])
        assert len(store.list_jobs(test_user, include_deleted=True)) == 1

    def test_get_returns_none_for_wrong_owner(self, store, test_user, db):
        import secrets
        import bcrypt
        from app.db import now_iso
        other = "test_user_" + secrets.token_urlsafe(6)
        db.execute(
            "INSERT INTO users (uid, login, name, password_hash, quota_remaining, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (other, f"other_{secrets.token_urlsafe(4)}", "Other", bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
             1000000, now_iso()),
        )
        db.commit()
        run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        job = store.create_job(uid=other, title="t", kind="reminder",
                               schedule_type="one_time", run_at=run_at, channel="web",
                               payload={"message": "m"})
        # test_user tries to read other_user's job
        assert store.get_job(test_user, job["id"]) is None
        # the actual owner can read it
        assert store.get_job(other, job["id"]) is not None


class TestLifecycle:
    def test_disable_and_enable(self, store, test_user):
        run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        job = store.create_job(uid=test_user, title="t", kind="reminder",
                               schedule_type="one_time", run_at=run_at, channel="web",
                               payload={"message": "m"})
        assert store.set_status(test_user, job["id"], "disabled") is True
        assert store.get_job(test_user, job["id"])["status"] == "disabled"
        assert store.set_status(test_user, job["id"], "enabled") is True
        assert store.get_job(test_user, job["id"])["status"] == "enabled"

    def test_set_status_wrong_owner_returns_false(self, store, test_user, db):
        import secrets
        import bcrypt
        from app.db import now_iso
        other = "test_user_" + secrets.token_urlsafe(6)
        db.execute(
            "INSERT INTO users (uid, login, name, password_hash, quota_remaining, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (other, f"other_{secrets.token_urlsafe(4)}", "Other", bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
             1000000, now_iso()),
        )
        db.commit()
        run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        job = store.create_job(uid=other, title="t", kind="reminder",
                               schedule_type="one_time", run_at=run_at, channel="web",
                               payload={"message": "m"})
        assert store.set_status(test_user, job["id"], "disabled") is False
        assert store.get_job(other, job["id"])["status"] == "enabled"  # unchanged

    def test_delete_is_soft(self, store, test_user):
        run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        job = store.create_job(uid=test_user, title="t", kind="reminder",
                               schedule_type="one_time", run_at=run_at, channel="web",
                               payload={"message": "m"})
        assert store.delete_job(test_user, job["id"]) is True
        # Public get_job filters out deleted; include_deleted surfaces the row.
        assert store.get_job(test_user, job["id"]) is None
        listed = store.list_jobs(test_user, include_deleted=True)
        assert listed[0]["id"] == job["id"]
        assert listed[0]["status"] == "deleted"
        assert store.delete_job(test_user, "no-such-id") is False


class TestNextRunAt:
    """Pure-function tests for next_run_at computation."""

    def test_one_time_in_future(self, store):
        run_at = "2099-01-15T09:00:00+00:00"
        assert store.compute_next_run_at(
            "one_time", run_at=run_at, time_of_day=None, weekdays=None,
            after=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ) == run_at

    def test_one_time_in_past_returns_none(self, store):
        # Past one-time job won't run again
        run_at = "2020-01-01T00:00:00+00:00"
        assert store.compute_next_run_at(
            "one_time", run_at=run_at, time_of_day=None, weekdays=None,
            after=datetime(2026, 6, 1, tzinfo=timezone.utc),
        ) is None

    def test_daily_no_time_defaults_to_09_00(self, store):
        nxt = store.compute_next_run_at(
            "daily", run_at=None, time_of_day=None, weekdays=None,
            after=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )
        dt = datetime.fromisoformat(nxt)
        assert dt.hour == 9 and dt.minute == 0
        # It's tomorrow morning
        assert dt.date() == datetime(2026, 6, 2).date()

    def test_daily_time_today_if_future(self, store):
        nxt = store.compute_next_run_at(
            "daily", run_at=None, time_of_day="15:00", weekdays=None,
            after=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
        )
        dt = datetime.fromisoformat(nxt)
        assert dt.date() == datetime(2026, 6, 1).date()
        assert dt.hour == 15

    def test_daily_time_today_if_past_rolls_to_tomorrow(self, store):
        nxt = store.compute_next_run_at(
            "daily", run_at=None, time_of_day="08:00", weekdays=None,
            after=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
        )
        dt = datetime.fromisoformat(nxt)
        assert dt.date() == datetime(2026, 6, 2).date()

    def test_weekly_specific_weekday(self, store):
        # 2026-06-01 is a Monday. Pick Wednesday.
        nxt = store.compute_next_run_at(
            "weekly", run_at=None, time_of_day="10:00", weekdays=[2],
            after=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
        )
        dt = datetime.fromisoformat(nxt)
        assert dt.weekday() == 2  # Wednesday
        assert dt.hour == 10
        # Should be 2 days later
        assert dt.date() == datetime(2026, 6, 3).date()

    def test_weekly_weekday_today_future(self, store):
        nxt = store.compute_next_run_at(
            "weekly", run_at=None, time_of_day="15:00", weekdays=[0],  # Monday
            after=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
        )
        dt = datetime.fromisoformat(nxt)
        # After is Monday 09:00, so today's 15:00 is still future
        assert dt.date() == datetime(2026, 6, 1).date()
        assert dt.hour == 15

    def test_weekly_no_weekday_defaults_to_daily_behavior(self, store):
        # No weekdays → behave like daily
        nxt = store.compute_next_run_at(
            "weekly", run_at=None, time_of_day="10:00", weekdays=None,
            after=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
        )
        dt = datetime.fromisoformat(nxt)
        assert dt.date() == datetime(2026, 6, 1).date()
        assert dt.hour == 10

    def test_invalid_time_of_day_raises(self, store):
        with pytest.raises(ValueError):
            store.compute_next_run_at(
                "daily", run_at=None, time_of_day="bad", weekdays=None,
                after=datetime(2026, 6, 1, tzinfo=timezone.utc),
            )


class TestDueJobs:
    """The worker uses list_due_jobs to find what to run."""

    def test_list_due_returns_only_enabled_and_due(self, store, test_user, db):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        j1 = store.create_job(uid=test_user, title="due", kind="reminder",
                              schedule_type="one_time", run_at=future, channel="web",
                              payload={"message": "x"})
        j2 = store.create_job(uid=test_user, title="not-due", kind="reminder",
                              schedule_type="one_time", run_at=future, channel="web",
                              payload={"message": "x"})
        # Backdate j1 to be due now; leave j2 in the future.
        past_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        db.execute("UPDATE scheduled_jobs SET next_run_at=? WHERE id=?", (past_iso, j1["id"]))
        db.commit()
        due = store.list_due_jobs(now=datetime.now(timezone.utc))
        ids = [j["id"] for j in due]
        assert j1["id"] in ids
        assert j2["id"] not in ids
        # Disabled due job is excluded
        store.set_status(test_user, j1["id"], "disabled")
        due2 = store.list_due_jobs(now=datetime.now(timezone.utc))
        assert j1["id"] not in [j["id"] for j in due2]
