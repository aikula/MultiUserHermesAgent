"""Tests: relay robustness — slash-command guard, typing indicator, gateway-confused fallback.

Covers Commit 1 (Sprint 0 stabilization):
- KNOWN_COMMANDS whitelist blocks unknown slash commands
- typing() is best-effort and never raises
- _deliver_response detects gateway-confused hallucinations and sends a friendly fallback
- _deliver_response still routes approval intents to the approval card
- _deliver_response sends plain content for normal replies
- Long-poll uses LONG_POLL_TIMEOUT constant
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_relay(monkeypatch):
    """Build a TelegramRelay with TELEGRAM_BOT_TOKEN set, _client stubbed."""
    from app import relay
    monkeypatch.setattr(relay, "TELEGRAM_BOT_TOKEN", "test-bot-token", raising=False)
    instance = relay.TelegramRelay()
    instance._client = MagicMock()
    instance.bot_username = "test_bot"
    return relay, instance


def _update_with_text(text: str, tg_id: int = 111, chat_id: int = 222) -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": tg_id, "is_bot": False, "first_name": "T"},
            "text": text,
        },
    }


class TestKnownCommands:
    """Slash-command whitelist."""

    def test_known_commands_set_is_exact(self):
        from app import relay
        assert relay.KNOWN_COMMANDS == {
            "/start", "/login", "/help", "/whoami", "/files", "/unlink", "/new", "/reset",
        }

    def test_long_poll_timeout_is_reasonable(self):
        from app import relay
        # Telegram best practice: <1 wastes requests, >30 hits client timeout
        assert 1 < relay.LONG_POLL_TIMEOUT <= 30

    @pytest.mark.asyncio
    async def test_unknown_slash_command_blocked(self, monkeypatch):
        """`/foo bar` should NOT reach LLM — relay responds locally and returns."""
        _, instance = _make_relay(monkeypatch)
        sent: list[dict] = []

        async def fake_send(chat_id, text, parse_mode=None):
            sent.append({"chat_id": chat_id, "text": text, "parse_mode": parse_mode})

        instance.send = fake_send
        instance.process_chat_message = AsyncMock()

        await instance.handle_update(_update_with_text("/foo bar"))

        instance.process_chat_message.assert_not_called()
        assert len(sent) == 1
        assert "Не знаю команду" in sent[0]["text"]
        assert "/foo" in sent[0]["text"]
        assert sent[0]["parse_mode"] == "HTML"

    @pytest.mark.asyncio
    async def test_unknown_command_with_at_suffix_blocked(self, monkeypatch):
        """`/foo@somebot` should be normalized and blocked."""
        _, instance = _make_relay(monkeypatch)
        sent: list[dict] = []

        async def fake_send(chat_id, text, parse_mode=None):
            sent.append({"text": text})

        instance.send = fake_send
        instance.process_chat_message = AsyncMock()

        await instance.handle_update(_update_with_text("/foo@otherbot arg"))

        instance.process_chat_message.assert_not_called()
        assert any("Не знаю команду" in m["text"] for m in sent)

    @pytest.mark.asyncio
    async def test_known_command_help_still_works(self, monkeypatch):
        """`/help` is a known command → handle locally, not blocked."""
        _, instance = _make_relay(monkeypatch)
        sent: list[dict] = []

        async def fake_send(chat_id, text, parse_mode=None):
            sent.append({"text": text})

        instance.send = fake_send
        instance.process_chat_message = AsyncMock()

        await instance.handle_update(_update_with_text("/help"))

        instance.process_chat_message.assert_not_called()
        assert any("/start" in m["text"] and "команды" in m["text"] for m in sent)


class TestTypingIndicator:
    """typing() must never raise — used to show 'печатает...' while LLM works."""

    @pytest.mark.asyncio
    async def test_typing_swallows_http_error(self, monkeypatch):
        _, instance = _make_relay(monkeypatch)

        async def boom(*a, **kw):
            raise RuntimeError("telegram sendChatAction: chat not found")

        instance._tg = boom
        # Must not raise
        await instance.typing(222)

    @pytest.mark.asyncio
    async def test_typing_swallows_connection_error(self, monkeypatch):
        _, instance = _make_relay(monkeypatch)

        async def boom(*a, **kw):
            raise ConnectionError("network down")

        instance._tg = boom
        await instance.typing(222)  # must not raise


class TestConfusedPatterns:
    """GATEWAY_CONFUSED_PATTERNS must catch the well-known hallucination phrasings."""

    def test_matches_known_phrases(self):
        from app import relay
        assert relay.GATEWAY_CONFUSED_PATTERNS.search("No main session found")
        assert relay.GATEWAY_CONFUSED_PATTERNS.search("Create one via /new or Web UI first.")
        assert relay.GATEWAY_CONFUSED_PATTERNS.search("main session not found, please init")

    def test_case_insensitive(self):
        from app import relay
        assert relay.GATEWAY_CONFUSED_PATTERNS.search("NO MAIN SESSION")
        assert relay.GATEWAY_CONFUSED_PATTERNS.search("Main Session Not Found")

    def test_does_not_match_normal_reply(self):
        from app import relay
        assert not relay.GATEWAY_CONFUSED_PATTERNS.search("Привет, как дела?")
        assert not relay.GATEWAY_CONFUSED_PATTERNS.search("Готово, отправил письмо.")


class TestDeliverResponse:
    """_deliver_response routing: approval, confused, plain."""

    @pytest.mark.asyncio
    async def test_confused_content_sends_friendly_fallback(self, monkeypatch):
        _, instance = _make_relay(monkeypatch)
        sent: list[dict] = []

        async def fake_send(chat_id, text, parse_mode=None):
            sent.append({"text": text})

        instance.send = fake_send

        with patch("app.summarizer.maybe_summarize", new=AsyncMock()):
            await instance._deliver_response(
                222, "u1",
                "No main session found. Create one via /new or Web UI first.",
                total=10,
            )

        assert len(sent) == 1
        msg = sent[0]["text"]
        assert "Hermes не смог обработать запрос" in msg
        assert "/start" in msg

    @pytest.mark.asyncio
    async def test_plain_content_passes_through(self, monkeypatch):
        _, instance = _make_relay(monkeypatch)
        sent: list[dict] = []

        async def fake_send(chat_id, text, parse_mode=None):
            sent.append({"text": text})

        instance.send = fake_send

        with patch("app.summarizer.maybe_summarize", new=AsyncMock()):
            await instance._deliver_response(222, "u1", "Привет! Как сам?", total=5)

        assert len(sent) == 1
        assert sent[0]["text"] == "Привет! Как сам?"

    @pytest.mark.asyncio
    async def test_approval_intent_still_routes_to_approval_card(self, monkeypatch):
        """The refactor must not break the existing approval path."""
        _, instance = _make_relay(monkeypatch)
        sent: list[dict] = []

        async def fake_send(chat_id, text, parse_mode=None):
            sent.append({"text": text})

        instance.send = fake_send

        content = (
            "Отправляю письмо.\n\n"
            "```action_intent\n"
            + json.dumps({
                "action_type": "email_send",
                "payload": {"to": "a@b.com", "subject": "Hi", "body": "Test"},
            })
            + "\n```"
        )
        fake_intent = {
            "id": "intent-1",
            "action_type": "email_send",
            "payload_json": json.dumps({"to": "a@b.com", "subject": "Hi", "body": "Test"}),
        }

        with patch("app.summarizer.maybe_summarize", new=AsyncMock()), \
             patch("app.approval.create_intent", return_value=fake_intent) as ci:
            await instance._deliver_response(222, "u1", content, total=20)

        ci.assert_called_once()
        assert len(sent) == 1
        msg = sent[0]["text"]
        assert "Подтверди или отмень" in msg
        # The action_intent block should be stripped from the body
        assert "```action_intent" not in msg


class TestGetUpdatesTimeout:
    """Long-poll uses LONG_POLL_TIMEOUT, not the old hard-coded 1s."""

    def test_run_loop_uses_long_poll_constant(self):
        """The getUpdates call in run() must reference LONG_POLL_TIMEOUT, not a hard-coded value."""
        import inspect
        from app import relay
        source = inspect.getsource(relay.TelegramRelay.run)
        assert "LONG_POLL_TIMEOUT" in source, (
            "TelegramRelay.run must use the LONG_POLL_TIMEOUT constant; "
            "hard-coding a value there wastes requests or hits client timeout."
        )
        # Old code had `params={"timeout": 1, ...}` — make sure that's gone.
        assert '"timeout": 1' not in source
        assert "'timeout': 1" not in source
