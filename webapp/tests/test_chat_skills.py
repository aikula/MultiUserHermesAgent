"""Tests: chat skill integration (spec 13).

Covers Sprint 2 step 2:
- `detect_skill_request` extracts the marker, normalises name, strips it
- `detect_skill_request` returns (None, original) when marker is absent/malformed
- `build_skill_user_message` includes full skill text + user's request
- `build_system_prompt` includes a compact skills list (names + hints)
- A `[Используй навык: meeting_followup]` marker in user content is consumed
  (the cleaned content is sent to Hermes; the raw content is preserved in
  history)
"""
from pathlib import Path

import pytest


# ----- detect_skill_request -----

class TestDetectSkillRequest:
    """The skill marker must be detected, normalised, and stripped."""

    def test_detects_marker(self):
        from app.chat import detect_skill_request
        name, cleaned = detect_skill_request("[Используй навык: meeting_followup]\nНапиши follow-up")
        assert name == "meeting_followup"
        assert cleaned == "Напиши follow-up"

    def test_marker_case_insensitive(self):
        from app.chat import detect_skill_request
        name, cleaned = detect_skill_request("[используй навык: Email_Reply]\nReply please")
        assert name == "email_reply"
        assert cleaned == "Reply please"

    def test_no_marker_returns_none(self):
        from app.chat import detect_skill_request
        name, cleaned = detect_skill_request("Просто обычное сообщение")
        assert name is None
        assert cleaned == "Просто обычное сообщение"

    def test_empty_content(self):
        from app.chat import detect_skill_request
        name, cleaned = detect_skill_request("")
        assert name is None
        assert cleaned == ""

    def test_malformed_marker_not_consumed(self):
        """`[Используй навык]` without a name must not match."""
        from app.chat import detect_skill_request
        name, cleaned = detect_skill_request("[Используй навык] текст")
        assert name is None
        assert cleaned == "[Используй навык] текст"

    def test_marker_with_leading_whitespace(self):
        from app.chat import detect_skill_request
        name, cleaned = detect_skill_request("   [Используй навык: risk_review]\nТема: ...")
        assert name == "risk_review"
        assert cleaned == "Тема: ..."

    def test_english_marker(self):
        from app.chat import detect_skill_request
        name, cleaned = detect_skill_request("[Use skill: daily_digest]\nSummarize today")
        assert name == "daily_digest"
        assert cleaned == "Summarize today"

    def test_english_marker_case_insensitive(self):
        from app.chat import detect_skill_request
        name, cleaned = detect_skill_request("[use SKILL: Email_Reply]\nReply please")
        assert name == "email_reply"
        assert cleaned == "Reply please"


# ----- build_skill_user_message -----

class TestBuildSkillUserMessage:
    """The wrapper must include the full skill markdown and the user's request."""

    def test_includes_skill_name(self):
        from app.chat import build_skill_user_message
        msg = build_skill_user_message("meeting_followup", "Конспект встречи...")
        assert msg["role"] == "user"
        assert "## Активный навык: meeting_followup" in msg["content"]

    def test_includes_full_skill_markdown(self):
        from app.chat import build_skill_user_message
        msg = build_skill_user_message("meeting_followup", "Конспект")
        # Full template must include the "When to use" section header.
        assert "## When to use" in msg["content"]
        # And the user request
        assert "Конспект" in msg["content"]

    def test_unknown_skill_renders_without_full_text(self):
        """If the skill name doesn't exist, the wrapper is built with empty body
        so the marker is consumed but the model still sees the user's request."""
        from app.chat import build_skill_user_message
        msg = build_skill_user_message("definitely_not_a_skill", "Привет")
        assert "Привет" in msg["content"]
        assert "## Активный навык: definitely_not_a_skill" in msg["content"]


# ----- build_system_prompt integration -----

class TestSystemPromptSkillsBlock:
    """The system prompt must include a compact skills list block."""

    def test_skills_block_present(self, test_user):
        from app.chat import build_system_prompt
        prompt = build_system_prompt(test_user)
        assert "## Доступные навыки" in prompt

    def test_skills_block_lists_all_known_skills(self, test_user):
        from app.chat import build_system_prompt
        from app.skills.loader import list_skills
        prompt = build_system_prompt(test_user)
        skills = list_skills()
        # Every known skill name must appear in the prompt (at least once).
        for s in skills:
            assert s.name in prompt, f"Skill {s.name} missing from prompt"
            # The title is included too
            assert s.title in prompt, f"Skill title for {s.name} missing"

    def test_skills_block_does_not_include_full_markdown(self, test_user):
        """Full skill text is per-turn only — the system prompt stays compact."""
        from app.chat import build_system_prompt
        prompt = build_system_prompt(test_user)
        # '## Quality checklist' is part of the full template, not the compact list
        assert "## Quality checklist" not in prompt


# ----- end-to-end: chat API consumes marker -----

class TestChatApiSkillIntegration:
    """The /api/chat endpoint must:
    - detect the marker in `content`
    - send the cleaned content + full skill text to Hermes
    - save the raw (with marker) content to history
    - return the activated skill name in the response
    """

    @pytest.fixture
    def chat_setup(self, client, test_user, monkeypatch, setup_test_env):
        """Common setup: authed client, patched call_hermes, fresh user files."""
        from app import chat
        from app.main import make_token
        # Auth bypass via session cookie (rate limiter is in /login only)
        client.cookies.set("session", make_token(test_user))
        captured: dict = {}

        async def fake_call(messages, uid=""):
            captured["messages"] = messages
            return {
                "content": "ok",
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
                "finish_reason": "stop",
            }
        monkeypatch.setattr(chat, "call_hermes", fake_call)
        return captured, client

    @pytest.mark.asyncio
    async def test_skill_marker_consumed(self, chat_setup, test_user):
        from app.main import generate_csrf_token
        captured, client = chat_setup
        token = generate_csrf_token(client.cookies.get("session"))
        r = await client.post(
            "/api/chat",
            json={"content": "[Используй навык: meeting_followup]\nТема: roadmap"},
            headers={"X-CSRF-Token": token},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["skill"] == "meeting_followup"
        # Last user message sent to Hermes must include the full skill text.
        last = captured["messages"][-1]
        assert last["role"] == "user"
        assert "## Активный навык: meeting_followup" in last["content"]
        assert "Тема: roadmap" in last["content"]
        # Raw marker should NOT appear in the message sent to Hermes.
        assert "[Используй навык:" not in last["content"]

    @pytest.mark.asyncio
    async def test_skill_raw_preserved_in_history(self, chat_setup, test_user, monkeypatch, setup_test_env):
        """The raw `[...]\n...` content is what we save in chat_history (audit)."""
        from app.main import generate_csrf_token
        from app import db
        captured, client = chat_setup
        # Make the in-memory DB writable for the test
        from app import db as db_mod
        target = Path(setup_test_env["users_dir"])
        monkeypatch.setattr(db_mod, "HERMES_USERS_DIR", target)
        (target / test_user).mkdir(parents=True, exist_ok=True)

        token = generate_csrf_token(client.cookies.get("session"))
        r = await client.post(
            "/api/chat",
            json={"content": "[Используй навык: meeting_followup]\nКонспект"},
            headers={"X-CSRF-Token": token},
        )
        assert r.status_code == 200, r.text
        # Read the latest chat_history row and confirm raw text is saved.
        row = db.get_db().execute(
            "SELECT role, content FROM chat_history WHERE uid=? AND role='user' "
            "ORDER BY id DESC LIMIT 1",
            (test_user,),
        ).fetchone()
        assert row is not None
        assert row["content"].startswith("[Используй навык: meeting_followup]")

    @pytest.mark.asyncio
    async def test_no_marker_passes_through(self, chat_setup, test_user):
        """Without a marker, `skill` is None and the content is unchanged."""
        from app.main import generate_csrf_token
        captured, client = chat_setup
        token = generate_csrf_token(client.cookies.get("session"))
        r = await client.post(
            "/api/chat",
            json={"content": "Обычный вопрос"},
            headers={"X-CSRF-Token": token},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["skill"] is None
        # Last user message is the original (history row).
        last = captured["messages"][-1]
        assert last["content"] == "Обычный вопрос"

    @pytest.mark.asyncio
    async def test_unknown_skill_falls_back(self, chat_setup, test_user):
        """Unknown skill name is treated as if no marker was given."""
        from app.main import generate_csrf_token
        captured, client = chat_setup
        token = generate_csrf_token(client.cookies.get("session"))
        r = await client.post(
            "/api/chat",
            json={"content": "[Используй навык: no_such_skill]\nВопрос"},
            headers={"X-CSRF-Token": token},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["skill"] is None
        # The raw text is still saved as-is.
        last = captured["messages"][-1]
        assert "Вопрос" in last["content"]
