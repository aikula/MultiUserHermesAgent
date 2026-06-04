"""Tests: Approval flow — spec 02."""

import pytest


class TestConfirmationParser:
    """is_confirmation / is_rejection parsers."""

    @pytest.mark.parametrize("phrase", [
        "да", "подтверждаю", "подтверждаю отправку", "отправляй",
        "можно отправлять", "согласен", "approve", "send it", "yes",
        "confirm", "ok", "go", "поехали", "вперёд", "сделай", "выполни",
    ])
    def test_confirmation_positive(self, phrase):
        from app.approval import is_confirmation
        assert is_confirmation(phrase) is True

    @pytest.mark.parametrize("phrase", [
        "нет", "не отправляй", "отмена", "стоп", "подожди",
        "измени текст", "cancel", "stop", "wait", "no", "reject", "abort",
    ])
    def test_confirmation_negative(self, phrase):
        from app.approval import is_confirmation
        assert is_confirmation(phrase) is False

    @pytest.mark.parametrize("phrase", [
        "нет", "не отправляй", "отмена", "стоп", "подожди",
        "измени текст", "cancel", "stop", "wait", "no", "reject", "abort",
    ])
    def test_rejection_positive(self, phrase):
        from app.approval import is_rejection
        assert is_rejection(phrase) is True

    @pytest.mark.parametrize("phrase", [
        "да", "подтверждаю", "отправляй", "согласен", "approve",
    ])
    def test_rejection_negative(self, phrase):
        from app.approval import is_rejection
        assert is_rejection(phrase) is False


class TestApprovalFlow:
    """Intent creation, approval, execution flow."""

    def test_payload_hash_changes_when_payload_changes(self):
        from app.approval import _payload_hash
        p1 = {"to": "a@test.com", "subject": "Hello"}
        p2 = {"to": "b@test.com", "subject": "Hello"}
        assert _payload_hash(p1) != _payload_hash(p2)

    def test_create_intent_stores_in_db(self, test_user):
        from app.approval import create_intent
        payload = {"to": "test@test.com", "subject": "Test", "body": "Hello"}
        intent = create_intent(test_user, "email_send", payload)
        assert intent["status"] == "pending_approval"
        assert intent["action_type"] == "email_send"
        assert "intent_" in intent["id"]

    def test_approve_intent_transitions_to_approved(self, test_user):
        from app.approval import create_intent, approve_intent, get_pending_intent
        payload = {"to": "test@test.com", "subject": "Test", "body": "Hello"}
        intent = create_intent(test_user, "email_send", payload)
        assert approve_intent(intent["id"]) is True
        pending = get_pending_intent(test_user)
        assert pending is None  # No more pending

    def test_expired_intent_cannot_execute(self, test_user):
        from app.approval import create_intent, approve_intent
        from app.db import get_db
        payload = {"to": "test@test.com", "subject": "Test", "body": "Hello"}
        intent = create_intent(test_user, "email_send", payload)

        # Manually expire the intent
        db = get_db()
        from datetime import datetime, timezone, timedelta
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        db.execute("UPDATE action_intents SET expires_at=? WHERE id=?", (past, intent["id"]))
        db.commit()

        # Approval should fail
        assert approve_intent(intent["id"]) is False

    def test_repeated_confirmation_does_not_duplicate(self, test_user):
        from app.approval import create_intent, approve_intent, execute_intent
        payload = {"to": "test@test.com", "subject": "Test", "body": "Hello"}
        intent = create_intent(test_user, "email_send", payload)
        approve_intent(intent["id"])
        execute_intent(intent["id"], result_json='{"ok": true}')

        # Second confirmation on same intent should not work
        # (intent is already executed, get_pending_intent returns None)
        from app.approval import get_pending_intent
        assert get_pending_intent(test_user) is None


class TestEmailToolImport:
    """P0-1: Email tool import must work."""

    def test_email_tools_import(self):
        from app.tools.email_tools import send_email, check_connection
        assert callable(send_email)
        assert callable(check_connection)

    def test_approve_non_pending_intent_returns_controlled_error(self, test_user):
        from app.approval import create_intent, approve_intent, execute_intent, get_intent_by_id_for_user
        payload = {"to": "test@test.com", "subject": "Test", "body": "Hello"}
        intent = create_intent(test_user, "email_send", payload)
        approve_intent(intent["id"])
        execute_intent(intent["id"], result_json='{"ok": true}')
        # Can't approve again
        assert approve_intent(intent["id"]) is False
        # get_intent_by_id_for_user should return the executed intent
        found = get_intent_by_id_for_user(intent["id"], test_user)
        assert found is not None
        assert found["status"] == "executed"
