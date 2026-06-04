"""Tests: profile endpoints — Google disconnect, invite-code expiry."""
import asyncio
import json
import secrets
from pathlib import Path


class TestGoogleDisconnect:
    """POST /api/profile/google/disconnect (the missing endpoint that 404'd)."""

    def _make_user_with_google(self, db):
        import bcrypt
        from app.db import now_iso
        uid = "goog_" + secrets.token_urlsafe(6)
        login = f"goog_{secrets.token_urlsafe(4)}"
        pw_hash = bcrypt.hashpw(b"testpassword123", bcrypt.gensalt()).decode()
        db.execute(
            "INSERT INTO users (uid, login, name, password_hash, quota_remaining, "
            "google_connected, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
            (uid, login, "G Test", pw_hash, 2000000, now_iso()),
        )
        db.commit()
        return uid, login

    def test_disconnect_requires_auth(self, client):
        async def _test():
            async with client as c:
                r = await c.post("/api/profile/google/disconnect", json={})
                assert r.status_code == 401

        asyncio.run(_test())

    def test_disconnect_removes_token_and_clears_flag(self, client, db, setup_test_env, monkeypatch):
        async def _test():
            from app.main import make_token, generate_csrf_token

            uid, _ = self._make_user_with_google(db)
            # The module-level HERMES_USERS_DIR is captured at first import. Re-bind it
            # to THIS test's tmp_path so the API and the assertion share one filesystem.
            target_users_dir = Path(setup_test_env["users_dir"])
            from app import main as m
            monkeypatch.setattr(m, "HERMES_USERS_DIR", target_users_dir)

            token_path = target_users_dir / uid / "google_token.json"
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(json.dumps({"access_token": "fake"}))

            session_token = make_token(uid)
            csrf_token = generate_csrf_token(session_token)

            async with client as c:
                c.cookies.set("session", session_token)
                r = await c.post(
                    "/api/profile/google/disconnect",
                    json={},
                    headers={"X-CSRF-Token": csrf_token},
                )
                assert r.status_code == 200, f"got {r.status_code}: {r.text}"
                assert not token_path.exists(), f"file still at {token_path}"
                row = db.execute("SELECT google_connected FROM users WHERE uid=?", (uid,)).fetchone()
                assert row["google_connected"] == 0

        asyncio.run(_test())

    def test_disconnect_idempotent_when_no_token(self, client, db, setup_test_env, monkeypatch):
        async def _test():
            from app.main import make_token, generate_csrf_token
            uid, _ = self._make_user_with_google(db)
            monkeypatch.setattr("app.main.HERMES_USERS_DIR", Path(setup_test_env["users_dir"]))
            session_token = make_token(uid)
            csrf_token = generate_csrf_token(session_token)

            async with client as c:
                c.cookies.set("session", session_token)
                r = await c.post(
                    "/api/profile/google/disconnect",
                    json={},
                    headers={"X-CSRF-Token": csrf_token},
                )
                assert r.status_code == 200
                assert r.json()["ok"] is True

        asyncio.run(_test())

    def test_disconnect_requires_csrf(self, client, db, setup_test_env, monkeypatch):
        async def _test():
            from app.main import make_token
            uid, _ = self._make_user_with_google(db)
            monkeypatch.setattr("app.main.HERMES_USERS_DIR", Path(setup_test_env["users_dir"]))
            session_token = make_token(uid)

            async with client as c:
                c.cookies.set("session", session_token)
                r = await c.post("/api/profile/google/disconnect", json={})
                assert r.status_code == 403

        asyncio.run(_test())


class TestInviteCodeExpiry:
    """invite_codes.expires_at must be honored on /register and /api/internal/redeem-invite."""

    def _seed_invite(self, db, code: str, expires_at: str | None):
        from app.db import now_iso
        db.execute(
            "INSERT INTO invite_codes (code, created_at, expires_at) VALUES (?, ?, ?)",
            (code, now_iso(), expires_at),
        )
        db.commit()

    def test_register_rejects_expired_invite(self, client, db):
        async def _test():
            expired = "expired-" + secrets.token_urlsafe(4)
            self._seed_invite(db, expired, "2020-01-01T00:00:00+00:00")

            async with client as c:
                r = await c.post("/register", data={
                    "name": "Late",
                    "login": f"late_{secrets.token_urlsafe(4)}",
                    "password": "validpassword123",
                    "invite_code": expired,
                }, follow_redirects=False)
                assert r.status_code == 400
                body = r.text.lower()
                assert "invite" in body or "неверн" in body

        asyncio.run(_test())

    def test_register_accepts_unexpiring_invite(self, client, db):
        async def _test():
            no_exp = "forever-" + secrets.token_urlsafe(4)
            self._seed_invite(db, no_exp, None)

            async with client as c:
                r = await c.post("/register", data={
                    "name": "Forever",
                    "login": f"forever_{secrets.token_urlsafe(4)}",
                    "password": "validpassword123",
                    "invite_code": no_exp,
                }, follow_redirects=False)
                assert r.status_code == 303, f"got {r.status_code}: {r.text[:200]}"

        asyncio.run(_test())

    def test_register_accepts_future_invite(self, client, db):
        async def _test():
            from datetime import datetime, timezone, timedelta
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            code = "future-" + secrets.token_urlsafe(4)
            self._seed_invite(db, code, future)

            async with client as c:
                r = await c.post("/register", data={
                    "name": "Early",
                    "login": f"early_{secrets.token_urlsafe(4)}",
                    "password": "validpassword123",
                    "invite_code": code,
                }, follow_redirects=False)
                assert r.status_code == 303, f"got {r.status_code}: {r.text[:200]}"

        asyncio.run(_test())

    def test_internal_redeem_rejects_expired_invite(self, client, db):
        async def _test():
            code = "expired-int-" + secrets.token_urlsafe(4)
            self._seed_invite(db, code, "2020-01-01T00:00:00+00:00")
            # Verify the seed is visible via a fresh connection
            from app.db import get_db
            fresh = get_db()
            check = fresh.execute(
                "SELECT 1 FROM invite_codes WHERE code=? AND used_by IS NULL "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (code, fresh.execute("SELECT 1").fetchone() and "2099-01-01T00:00:00+00:00" or "2026-06-04T00:00:00+00:00")
            ).fetchone()
            print("DEBUG check from fresh conn:", check)

            async with client as c:
                r = await c.post(
                    "/api/internal/redeem-invite",
                    json={"code": code, "telegram_id": 999000111, "name": "X"},
                    headers={"X-Internal-Secret": "test-internal-secret-key-12345"},
                )
                assert r.status_code == 404, f"got {r.status_code}: {r.text}"
                assert r.json()["error"] == "invite_not_found"

        asyncio.run(_test())


class TestEmailPortValidation:
    """POST /api/profile/email — port type validation (500→400 fix)."""

    def _make_authed_session(self, client, db):
        """Create a user, return (session_token, csrf_token)."""
        import bcrypt
        from app.db import now_iso
        from app.main import make_token, generate_csrf_token
        uid = "port_" + secrets.token_urlsafe(6)
        login = f"port_{secrets.token_urlsafe(4)}"
        pw_hash = bcrypt.hashpw(b"testpassword123", bcrypt.gensalt()).decode()
        db.execute(
            "INSERT INTO users (uid, login, name, password_hash, quota_remaining, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uid, login, "Port Test", pw_hash, 2000000, now_iso()),
        )
        db.commit()
        session_token = make_token(uid)
        csrf_token = generate_csrf_token(session_token)
        client.cookies.set("session", session_token)
        return session_token, csrf_token

    def test_string_port_returns_400(self, client, db):
        async def _test():
            async with client as c:
                _, csrf = self._make_authed_session(c, db)
                r = await c.post(
                    "/api/profile/email",
                    json={
                        "imap_host": "imap.test.com", "imap_port": "abc",
                        "smtp_host": "smtp.test.com", "smtp_port": 587,
                        "email_login": "x@y.com", "email_password": "pwd",
                    },
                    headers={"X-CSRF-Token": csrf},
                )
                assert r.status_code == 400, f"got {r.status_code}: {r.text}"
                assert "port" in r.text.lower()

        asyncio.run(_test())

    def test_negative_port_returns_400(self, client, db):
        async def _test():
            async with client as c:
                _, csrf = self._make_authed_session(c, db)
                r = await c.post(
                    "/api/profile/email",
                    json={
                        "imap_host": "imap.test.com", "imap_port": -1,
                        "smtp_host": "smtp.test.com", "smtp_port": 587,
                        "email_login": "x@y.com", "email_password": "pwd",
                    },
                    headers={"X-CSRF-Token": csrf},
                )
                assert r.status_code == 400, f"got {r.status_code}: {r.text}"

        asyncio.run(_test())

    def test_port_above_65535_returns_400(self, client, db):
        async def _test():
            async with client as c:
                _, csrf = self._make_authed_session(c, db)
                r = await c.post(
                    "/api/profile/email",
                    json={
                        "imap_host": "imap.test.com", "imap_port": 993,
                        "smtp_host": "smtp.test.com", "smtp_port": 70000,
                        "email_login": "x@y.com", "email_password": "pwd",
                    },
                    headers={"X-CSRF-Token": csrf},
                )
                assert r.status_code == 400, f"got {r.status_code}: {r.text}"

        asyncio.run(_test())

    def test_valid_ports_succeed(self, client, db):
        async def _test():
            async with client as c:
                _, csrf = self._make_authed_session(c, db)
                r = await c.post(
                    "/api/profile/email",
                    json={
                        "imap_host": "imap.test.com", "imap_port": 993,
                        "smtp_host": "smtp.test.com", "smtp_port": 587,
                        "email_login": "x@y.com", "email_password": "pwd",
                    },
                    headers={"X-CSRF-Token": csrf},
                )
                assert r.status_code == 200, f"got {r.status_code}: {r.text}"
                assert r.json()["ok"] is True

        asyncio.run(_test())


class TestReviewActionsSync:
    """REVIEW_ACTIONS must be a single source of truth (approval.py)."""

    def test_relay_imports_from_approval(self):
        """relay.REVIEW_ACTIONS IS approval.REVIEW_ACTIONS (same object after import)."""
        from app.approval import REVIEW_ACTIONS as APPROVAL_ACTIONS
        from app.relay import REVIEW_ACTIONS as RELAY_ACTIONS
        assert RELAY_ACTIONS is APPROVAL_ACTIONS, "relay uses its own copy!"

    def test_same_set(self):
        """Values match even if identity doesn't."""
        from app.approval import REVIEW_ACTIONS as APPROVAL_ACTIONS
        from app.relay import REVIEW_ACTIONS as RELAY_ACTIONS
        assert RELAY_ACTIONS == APPROVAL_ACTIONS
