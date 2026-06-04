"""Tests: skills API and page (spec 13, Sprint 2 step 2).

- /api/skills/list returns all skills (names + titles + hints)
- /api/skills/{name} returns full markdown
- /api/skills/{name} returns 404 for unknown names
- /api/skills/{name} rejects path-traversal attempts
- /skills page is rendered for an authed user
- /skills page is protected (redirects to /login for anon)
"""
import pytest


class TestSkillsApi:
    """The skills API mirrors the loader module."""

    @pytest.fixture
    def authed_client(self, client, test_user):
        from app.main import make_token
        client.cookies.set("session", make_token(test_user))
        return client

    @pytest.mark.asyncio
    async def test_list_returns_all_skills(self, authed_client):
        from app.skills.loader import list_skills
        r = await authed_client.get("/api/skills/list")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        skills = data["skills"]
        assert len(skills) == len(list_skills())
        for s in skills:
            assert {"name", "title", "hint"} <= set(s.keys())

    @pytest.mark.asyncio
    async def test_list_requires_auth(self, client):
        r = await client.get("/api/skills/list")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_get_returns_full_markdown(self, authed_client):
        r = await authed_client.get("/api/skills/meeting_followup")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["name"] == "meeting_followup"
        assert "## When to use" in data["content"]
        assert "## Output format" in data["content"]

    @pytest.mark.asyncio
    async def test_get_unknown_returns_404(self, authed_client):
        r = await authed_client.get("/api/skills/no_such_skill_xyz")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_get_blocks_traversal(self, authed_client):
        """Path-traversal attempts must be rejected with 404, not 200."""
        r = await authed_client.get("/api/skills/..%2F..%2Fconfig")
        assert r.status_code == 404
        # And the literal "../etc/passwd" form too
        r2 = await authed_client.get("/api/skills/../etc/passwd")
        # FastAPI normalises paths; the request may be 307-redirected or 404.
        # The key invariant: no file content is leaked.
        assert r2.status_code in (307, 404, 400)


class TestSkillsPage:
    """The /skills page is the UI surface of the library."""

    @pytest.fixture
    def authed_client(self, client, test_user):
        from app.main import make_token
        client.cookies.set("session", make_token(test_user))
        return client

    @pytest.mark.asyncio
    async def test_skills_page_renders(self, authed_client):
        r = await authed_client.get("/skills")
        assert r.status_code == 200
        body = r.text
        # All 10 skill names appear on the page
        from app.skills.loader import list_skills
        for s in list_skills():
            assert s.name in body, f"Skill {s.name} missing from /skills page"
        # At least one "use in chat" button
        assert "Использовать в чате" in body
        # Compact list is shown alongside the full template
        assert "## Активный навык" not in body  # the page itself doesn't trigger it

    @pytest.mark.asyncio
    async def test_skills_page_protected(self, client):
        r = await client.get("/skills", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"].endswith("/login")
