"""Tests: build_system_prompt — UTC time injection.

Covers Commit 3 (Sprint 0 stabilization):
- System prompt includes a 'Текущее время (серверное)' block
- The block contains both human-readable UTC date and ISO 8601
- The time is a recent UTC moment (within a few seconds of now)
- The block survives regardless of SOUL/memory state
"""
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path


class TestSystemPromptTime:
    """The system prompt must include a server-time block so the agent never invents dates."""

    def test_time_block_present(self, test_user):
        from app.chat import build_system_prompt
        prompt = build_system_prompt(test_user)
        assert "## Текущее время (серверное)" in prompt

    def test_time_block_has_utc_and_iso(self, test_user):
        from app.chat import build_system_prompt
        prompt = build_system_prompt(test_user)
        # Human-readable form: "UTC: YYYY-MM-DD HH:MM:SS Day"
        utc_match = re.search(
            r"UTC: (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (\w+)", prompt
        )
        assert utc_match, f"UTC: line not found in:\n{prompt}"
        # ISO form: optional fractional seconds, mandatory timezone offset
        iso_match = re.search(
            r"ISO: (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?[+\-]\d{2}:\d{2})", prompt
        )
        assert iso_match, f"ISO: line not found or not timezone-aware in:\n{prompt}"
        assert iso_match.group(1).endswith("+00:00"), "ISO must be in UTC"

    def test_time_is_recent_utc(self, test_user):
        """The injected time must be within a few seconds of now() in UTC."""
        from app.chat import build_system_prompt
        before = datetime.now(timezone.utc)
        prompt = build_system_prompt(test_user)
        after = datetime.now(timezone.utc)

        iso_match = re.search(
            r"ISO: (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?[+\-]\d{2}:\d{2})", prompt
        )
        assert iso_match
        injected = datetime.fromisoformat(iso_match.group(1))
        # Allow up to 5s drift for slow CI
        assert before - timedelta(seconds=5) <= injected <= after + timedelta(seconds=5)

    def test_time_block_independent_of_soul(self, test_user, monkeypatch, setup_test_env):
        """Even without SOUL/memory, the time block must be present."""
        # app.db.HERMES_USERS_DIR is captured at module load from the env at
        # import time. The conftest sets the env per-test, but the module
        # constant is already bound. Redirect it for this test only — both in
        # app.db (source of truth) and in app.chat (where the import is cached).
        from app import db, chat
        target = Path(setup_test_env["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        monkeypatch.setattr(chat, "HERMES_USERS_DIR", target)
        # The user_dir must exist; the conftest's tmp_path is fresh per test.
        (target / test_user).mkdir(parents=True, exist_ok=True)
        from app.chat import build_system_prompt
        prompt = build_system_prompt(test_user)
        assert "## Текущее время (серверное)" in prompt

    def test_time_block_present_with_soul(self, test_user, monkeypatch, setup_test_env):
        """With a SOUL.md, time block still present and SOUL content is included."""
        from app import db, chat
        target = Path(setup_test_env["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        monkeypatch.setattr(chat, "HERMES_USERS_DIR", target)
        from app.chat import build_system_prompt
        user_dir = target / test_user
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "SOUL.md").write_text("# Мой помощник\n\nТы краткий и точный.")
        prompt = build_system_prompt(test_user)
        assert "## Текущее время (серверное)" in prompt
        assert "краткий и точный" in prompt
