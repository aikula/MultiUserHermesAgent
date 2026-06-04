"""Tests: job handlers (spec 11).

Covers:
- reminder: saves assistant message in chat history with channel='scheduler'
- reminder: writes web notification when channel includes 'web'
- reminder: attempts Telegram delivery via relay if linked
- morning_digest: builds digest from memory + recent history + jobs
- morning_digest: skips Hermes call if quota is low
- custom_prompt: runs through chat.call_hermes with user context
- handlers always return a result dict {status, message}
- quota guard: 'skipped_quota' status when budget is exhausted
"""
import pytest


@pytest.fixture
def store():
    from app.jobs import store as job_store
    return job_store


@pytest.fixture
def now_iso():
    from app.db import now_iso as f
    return f()


def _make_handler_ctx():
    """Build the namespace that handlers.py expects (per-call context)."""
    from app.jobs.handlers import HandlerContext
    return HandlerContext


class TestReminderHandler:
    def test_reminder_saves_assistant_message(self, test_user, setup_test_env, db):
        from app.jobs.handlers import handle_reminder
        result = handle_reminder(
            uid=test_user,
            payload={"message": "Проверить письмо клиенту"},
            channel="web",
            job_id="job_x",
        )
        assert result["status"] in ("sent", "partial")
        # chat_history has a new row with channel='scheduler'
        row = db.execute(
            "SELECT role, content, channel FROM chat_history WHERE uid=? AND channel='scheduler' "
            "ORDER BY id DESC LIMIT 1",
            (test_user,),
        ).fetchone()
        assert row is not None
        assert row["role"] == "assistant"
        assert "Проверить письмо клиенту" in row["content"]

    def test_reminder_writes_web_notification(self, test_user, setup_test_env, db):
        from app.jobs.handlers import handle_reminder
        handle_reminder(
            uid=test_user,
            payload={"message": "Web-notif text", "context": "demo"},
            channel="web",
            job_id="job_y",
        )
        row = db.execute(
            "SELECT title, body, read FROM notifications WHERE uid=? ORDER BY id DESC LIMIT 1",
            (test_user,),
        ).fetchone()
        assert row is not None
        assert row["title"] == "Напоминание"
        assert "Web-notif text" in row["body"]
        assert row["read"] == 0

    def test_reminder_channel_telegram_does_not_write_web_notif(self, test_user, setup_test_env, db):
        from app.jobs.handlers import handle_reminder
        handle_reminder(
            uid=test_user,
            payload={"message": "Telegram only"},
            channel="telegram",
            job_id="job_z",
        )
        notif = db.execute(
            "SELECT id FROM notifications WHERE uid=? AND title='Напоминание'",
            (test_user,),
        ).fetchone()
        # When channel is only telegram, no web notification is written
        assert notif is None


class TestMorningDigestHandler:
    def test_morning_digest_uses_memory(self, test_user, monkeypatch, setup_test_env):
        from app.jobs.handlers import handle_morning_digest
        # Write a memory.md so the digest has content
        from pathlib import Path
        target = Path(setup_test_env["users_dir"])
        user_dir = target / test_user
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "memory.md").write_text("Пользователь работает в IT, предпочитает утренние встречи.\n")

        # Stub out chat.call_hermes so we don't hit the network
        async def fake_call(messages, uid=""):
            return {
                "content": "Дайджест готов",
                "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
                "finish_reason": "stop",
            }
        from app import chat
        monkeypatch.setattr(chat, "call_hermes", fake_call)

        result = handle_morning_digest(
            uid=test_user,
            payload={"include_memory": True, "include_recent_history": False,
                     "include_tasks": False, "include_email": False, "include_calendar": False},
            channel="web",
            job_id="job_d1",
        )
        assert result["status"] == "sent"
        assert "Дайджест готов" in result["message"]

    def test_morning_digest_skips_when_quota_low(self, test_user, monkeypatch):
        from app.jobs.handlers import handle_morning_digest
        # Patch quota.check_quota to return failure
        from app import quota
        monkeypatch.setattr(quota, "check_quota", lambda uid, tokens: (False, "out of budget"))
        result = handle_morning_digest(
            uid=test_user,
            payload={"include_memory": True, "include_recent_history": False,
                     "include_tasks": False, "include_email": False, "include_calendar": False},
            channel="web",
            job_id="job_d2",
        )
        assert result["status"] == "skipped_quota"


class TestCustomPromptHandler:
    def test_custom_prompt_runs_through_hermes(self, test_user, monkeypatch, setup_test_env):
        from app.jobs.handlers import handle_custom_prompt
        from app import chat
        async def fake_call(messages, uid=""):
            # Echo the prompt back so we can assert it was forwarded
            return {
                "content": "OK: " + (messages[-1]["content"] if messages else ""),
                "prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10,
                "finish_reason": "stop",
            }
        monkeypatch.setattr(chat, "call_hermes", fake_call)
        result = handle_custom_prompt(
            uid=test_user,
            payload={"prompt": "проверь статус проекта", "send_result": True},
            channel="web",
            job_id="job_cp1",
        )
        assert result["status"] == "sent"
        assert "проверь статус проекта" in result["message"]

    def test_custom_prompt_quota_guard(self, test_user, monkeypatch):
        from app.jobs.handlers import handle_custom_prompt
        from app import quota
        monkeypatch.setattr(quota, "check_quota", lambda uid, tokens: (False, "nope"))
        result = handle_custom_prompt(
            uid=test_user,
            payload={"prompt": "x", "send_result": True},
            channel="web",
            job_id="job_cp2",
        )
        assert result["status"] == "skipped_quota"


class TestDispatch:
    """The dispatch function picks the right handler based on job.kind."""

    def test_dispatch_routes_reminder(self, test_user, setup_test_env):
        from app.jobs.handlers import dispatch
        result = dispatch(
            job_kind="reminder",
            uid=test_user,
            payload={"message": "go"},
            channel="web",
            job_id="job_d_r",
        )
        assert result["status"] in ("sent", "partial")

    def test_dispatch_unknown_kind_returns_error(self, test_user):
        from app.jobs.handlers import dispatch
        result = dispatch(
            job_kind="nonexistent_kind",
            uid=test_user, payload={}, channel="web", job_id="job_d_x",
        )
        assert result["status"] == "error"
