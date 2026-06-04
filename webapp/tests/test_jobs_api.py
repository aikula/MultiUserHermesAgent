"""Tests: scheduled jobs API + page (spec 11, Sprint 3).

Covers:
- POST /api/jobs: create one-time / daily / weekly
- POST /api/jobs: validation (400 on bad input)
- GET  /api/jobs: lists user's jobs
- POST /api/jobs/{id}/disable, /enable, /delete, /run-now
- All state-changing endpoints require CSRF
- /automations page renders for authed user; redirects anon
"""
from datetime import datetime, timezone, timedelta

import pytest


class TestJobsApi:
    @pytest.fixture
    def authed(self, client, test_user):
        from app.main import make_token, generate_csrf_token
        client.cookies.set("session", make_token(test_user))
        token = generate_csrf_token(client.cookies.get("session"))
        return client, token

    @pytest.mark.asyncio
    async def test_create_one_time_reminder(self, authed):
        client, token = authed
        run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        r = await client.post(
            "/api/jobs",
            json={
                "title": "Позвонить",
                "kind": "reminder",
                "schedule_type": "one_time",
                "run_at": run_at,
                "channel": "telegram",
                "payload": {"message": "проверить статус заказа"},
            },
            headers={"X-CSRF-Token": token},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert data["job"]["title"] == "Позвонить"
        assert data["job"]["kind"] == "reminder"
        assert data["job"]["channel"] == "telegram"

    @pytest.mark.asyncio
    async def test_create_daily_reminder(self, authed):
        client, token = authed
        r = await client.post(
            "/api/jobs",
            json={
                "title": "Дайджест",
                "kind": "morning_digest",
                "schedule_type": "daily",
                "time_of_day": "09:00",
                "channel": "web",
                "payload": {"include_memory": True, "include_recent_history": True,
                            "include_tasks": True, "include_email": False, "include_calendar": False},
            },
            headers={"X-CSRF-Token": token},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert data["job"]["schedule_type"] == "daily"

    @pytest.mark.asyncio
    async def test_create_weekly(self, authed):
        client, token = authed
        r = await client.post(
            "/api/jobs",
            json={
                "title": "Weekly status",
                "kind": "custom_prompt",
                "schedule_type": "weekly",
                "time_of_day": "10:00",
                "weekdays": [0, 2, 4],
                "channel": "telegram",
                "payload": {"prompt": "Сделай weekly status report", "send_result": True},
            },
            headers={"X-CSRF-Token": token},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert data["job"]["schedule_type"] == "weekly"

    @pytest.mark.asyncio
    async def test_create_validation_error(self, authed):
        client, token = authed
        r = await client.post(
            "/api/jobs",
            json={"title": "", "kind": "reminder", "schedule_type": "one_time",
                  "run_at": datetime.now(timezone.utc).isoformat(),
                  "channel": "web", "payload": {}},
            headers={"X-CSRF-Token": token},
        )
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_create_requires_csrf(self, client, test_user):
        from app.main import make_token
        client.cookies.set("session", make_token(test_user))
        r = await client.post(
            "/api/jobs",
            json={"title": "x", "kind": "reminder", "schedule_type": "one_time",
                  "run_at": datetime.now(timezone.utc).isoformat(),
                  "channel": "web", "payload": {"message": "m"}},
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_list_jobs(self, authed):
        client, token = authed
        # Create one
        run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        await client.post(
            "/api/jobs",
            json={"title": "x", "kind": "reminder", "schedule_type": "one_time",
                  "run_at": run_at, "channel": "web", "payload": {"message": "m"}},
            headers={"X-CSRF-Token": token},
        )
        r = await client.get("/api/jobs")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["title"] == "x"

    @pytest.mark.asyncio
    async def test_disable_enable_delete(self, authed):
        client, token = authed
        run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        cr = await client.post(
            "/api/jobs",
            json={"title": "x", "kind": "reminder", "schedule_type": "one_time",
                  "run_at": run_at, "channel": "web", "payload": {"message": "m"}},
            headers={"X-CSRF-Token": token},
        )
        jid = cr.json()["job"]["id"]

        r = await client.post(f"/api/jobs/{jid}/disable",
                              headers={"X-CSRF-Token": token})
        assert r.status_code == 200
        # Verify status in list
        lst = (await client.get("/api/jobs")).json()["jobs"]
        assert lst[0]["status"] == "disabled"

        r = await client.post(f"/api/jobs/{jid}/enable",
                              headers={"X-CSRF-Token": token})
        assert r.status_code == 200

        r = await client.post(f"/api/jobs/{jid}/delete",
                              headers={"X-CSRF-Token": token})
        assert r.status_code == 200
        # List excludes deleted by default
        lst = (await client.get("/api/jobs")).json()["jobs"]
        assert lst == []

    @pytest.mark.asyncio
    async def test_run_now_requires_csrf(self, authed):
        client, token = authed
        run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        cr = await client.post(
            "/api/jobs",
            json={"title": "x", "kind": "reminder", "schedule_type": "one_time",
                  "run_at": run_at, "channel": "web", "payload": {"message": "m"}},
            headers={"X-CSRF-Token": token},
        )
        jid = cr.json()["job"]["id"]
        r = await client.post(f"/api/jobs/{jid}/run-now")  # no CSRF
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_run_now_executes(self, authed):
        client, token = authed
        run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        cr = await client.post(
            "/api/jobs",
            json={"title": "x", "kind": "reminder", "schedule_type": "one_time",
                  "run_at": run_at, "channel": "web", "payload": {"message": "Запусти сейчас"}},
            headers={"X-CSRF-Token": token},
        )
        jid = cr.json()["job"]["id"]
        r = await client.post(f"/api/jobs/{jid}/run-now",
                              headers={"X-CSRF-Token": token})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["result"]["status"] in ("sent", "partial", "error")


class TestAutomationsPage:
    @pytest.fixture
    def authed_client(self, client, test_user):
        from app.main import make_token
        client.cookies.set("session", make_token(test_user))
        return client

    @pytest.mark.asyncio
    async def test_page_renders(self, authed_client):
        r = await authed_client.get("/automations")
        assert r.status_code == 200
        body = r.text
        # Form + section headers
        assert "Создать напоминание" in body
        assert "Утренний дайджест" in body
        assert "Запланированные задачи" in body

    @pytest.mark.asyncio
    async def test_page_protected(self, client):
        r = await client.get("/automations", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"].endswith("/login")
