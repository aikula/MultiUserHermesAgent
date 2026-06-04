"""Tests: skills library loader (spec 13)."""
from pathlib import Path

import pytest


@pytest.fixture
def loader():
    from app.skills import loader
    return loader


class TestListSkills:
    def test_loads_all_10_skills(self, loader):
        skills = loader.list_skills()
        names = {s.name for s in skills}
        assert "meeting_followup" in names
        assert "task_extraction" in names
        assert "decision_memo" in names
        assert "risk_review" in names
        assert "email_reply" in names
        assert "daily_digest" in names
        assert "delegation_plan" in names
        assert "stakeholder_map" in names
        assert "weekly_status_report" in names
        assert "research_brief" in names
        assert len(skills) == 10

    def test_skill_has_title_and_hint(self, loader):
        skills = loader.list_skills()
        for s in skills:
            assert s.title, f"skill {s.name} has empty title"
            assert s.hint, f"skill {s.name} has empty hint"
            # Hint should be a sensible line, not the whole markdown
            assert len(s.hint) <= 220

    def test_sorted_by_name(self, loader):
        skills = loader.list_skills()
        names = [s.name for s in skills]
        assert names == sorted(names)

    def test_to_dict_round_trip(self, loader):
        s = loader.list_skills()[0]
        d = s.to_dict()
        assert set(d.keys()) == {"name", "title", "hint"}
        assert d["name"] == s.name


class TestGetSkill:
    def test_returns_full_markdown(self, loader):
        md = loader.get_skill("meeting_followup")
        assert md is not None
        assert "# Meeting Follow-up" in md
        assert "## When to use" in md
        assert "## Example prompt" in md

    def test_returns_none_for_missing(self, loader):
        assert loader.get_skill("nonexistent_skill") is None

    def test_rejects_path_traversal(self, loader):
        assert loader.get_skill("../../../etc/passwd") is None
        assert loader.get_skill("..") is None
        assert loader.get_skill("sub/secret") is None
        assert loader.get_skill("a/b") is None

    def test_rejects_empty_name(self, loader):
        assert loader.get_skill("") is None


class TestRenderCompactList:
    def test_renders_non_empty(self, loader):
        block = loader.render_compact_list()
        assert "## Доступные навыки" in block
        assert "meeting_followup" in block
        assert "decision_memo" in block
        # Should mention that full text comes separately
        assert "полный текст" in block or "добавлен отдельно" in block

    def test_compact_format_one_line_per_skill(self, loader):
        block = loader.render_compact_list()
        # Each skill is on its own line starting with "- `"
        skill_lines = [ln for ln in block.splitlines() if ln.startswith("- `")]
        assert len(skill_lines) == 10


class TestRobustParsing:
    def test_handles_missing_h1(self, loader, tmp_path):
        # Create a malformed skill file with no H1 title
        from app.skills.loader import _parse
        s = _parse("weird_name", "## When to use\n\nJust a hint.\n")
        assert s.name == "weird_name"
        # Falls back to title-cased stem
        assert "Weird Name" in s.title
        assert "Just a hint" in s.hint

    def test_hint_capped_at_200(self, loader, tmp_path):
        from app.skills.loader import _parse
        long = "x" * 500
        s = _parse("longtest", f"## When to use\n\n{long}\n")
        assert len(s.hint) <= 201  # 200 + ellipsis
        assert s.hint.endswith("…")

    def test_missing_when_to_use_no_hint(self, loader):
        from app.skills.loader import _parse
        s = _parse("nohint", "# Title Only\n")
        assert s.title == "Title Only"
        assert s.hint == ""
