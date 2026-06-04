"""Tests: Manager skills — spec 03."""

from app.skills.manager_templates import get_manager_templates_block


class TestManagerTemplates:
    """Manager skill templates content validation."""

    def test_routing_block_exists(self):
        block = get_manager_templates_block()
        assert "Управленческие режимы" in block
        assert "письмо" in block.lower()
        assert "встреча" in block.lower()
        assert "документ" in block.lower()
        assert "задачи" in block.lower()
        assert "решение" in block.lower()
        assert "дайджест" in block.lower()

    def test_action_intent_format_in_routing(self):
        block = get_manager_templates_block()
        assert "action_intent" in block
        assert "email_send" in block
        assert "calendar_create" in block

    def test_block_is_compact(self):
        block = get_manager_templates_block()
        assert len(block) < 1500  # compact format should be under 1.5KB
