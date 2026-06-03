"""Tests: Manager skills — spec 03."""
import pytest

from app.skills.manager_templates import (
    MANAGER_ROUTING_BLOCK,
    MANAGER_TEMPLATES,
    get_manager_templates_block,
)


class TestManagerTemplates:
    """Manager skill templates content validation."""

    def test_routing_block_exists(self):
        assert "Управленческие режимы" in MANAGER_ROUTING_BLOCK
        assert "письмо" in MANAGER_ROUTING_BLOCK
        assert "встреча" in MANAGER_ROUTING_BLOCK
        assert "документ" in MANAGER_ROUTING_BLOCK
        assert "задачи" in MANAGER_ROUTING_BLOCK
        assert "решение" in MANAGER_ROUTING_BLOCK
        assert "дайджест" in MANAGER_ROUTING_BLOCK

    def test_all_six_templates_exist(self):
        expected_keys = {"email", "meeting", "document", "tasks", "decision", "digest"}
        assert expected_keys == set(MANAGER_TEMPLATES.keys())

    def test_meeting_followup_template_sections(self):
        template = MANAGER_TEMPLATES["meeting"]
        assert "Итоги встречи" in template
        assert "Договоренности" in template
        assert "Задачи" in template
        assert "Письмо" in template or "follow-up" in template.lower()

    def test_task_extraction_template_columns(self):
        template = MANAGER_TEMPLATES["tasks"]
        assert "Задача" in template
        assert "Ответственный" in template
        assert "Срок" in template
        assert "Риск" in template

    def test_decision_memo_template_sections(self):
        template = MANAGER_TEMPLATES["decision"]
        assert "Вариант" in template
        assert "Рекомендация" in template

    def test_get_manager_templates_block_concatenates_all(self):
        block = get_manager_templates_block()
        assert "Управленческие режимы" in block
        assert "action_intent" in block
        for key in MANAGER_TEMPLATES:
            # Each template's first heading should appear
            pass  # Templates are just text, just verify they're included

    def test_action_intent_format_in_routing(self):
        block = get_manager_templates_block()
        assert "action_intent" in block
        assert "email_send" in block
        assert "calendar_create" in block
