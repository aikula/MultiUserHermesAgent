"""Tests: web tools API endpoints (spec 12).

Covers:
- POST /api/web/search → SearxNG results
- POST /api/web/fetch → cleaned text + source attribution
- POST /api/web/parse → server-side HTML→text
- POST /api/web/links → filtered anchor list
- POST /api/web/download → bulk download to user folder + approval flow
  (action_intent with status pending_approval; web_download_files in REVIEW_ACTIONS)
- All endpoints require auth
- Read-only endpoints don't need CSRF; /api/web/download does
- The download endpoint is *both* a read-fetch (per-URL GET is free) and a
  state-changing operation (writes files) — the spec says bulk download
  needs approval, so we always require CSRF on /api/web/download
"""
import pytest
from unittest.mock import AsyncMock


class TestSearchEndpoint:
    @pytest.fixture
    def authed(self, client, test_user):
        from app.main import make_token
        client.cookies.set("session", make_token(test_user))
        return client

    @pytest.mark.asyncio
    async def test_search_returns_results(self, authed, monkeypatch):
        from app.tools import web_tools
        async def fake_search(query, limit=10):
            return [
                {"title": "X", "url": "https://example.com/x", "snippet": "..."},
            ]
        monkeypatch.setattr(web_tools, "search_web", fake_search)
        r = await authed.post("/api/web/search", json={"query": "AI agents", "limit": 5})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert len(data["results"]) == 1
        assert data["results"][0]["url"] == "https://example.com/x"

    @pytest.mark.asyncio
    async def test_search_requires_auth(self, client):
        r = await client.post("/api/web/search", json={"query": "x"})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_search_validates_query(self, authed):
        r = await authed.post("/api/web/search", json={"query": ""})
        assert r.status_code == 400


class TestFetchEndpoint:
    @pytest.fixture
    def authed(self, client, test_user):
        from app.main import make_token
        client.cookies.set("session", make_token(test_user))
        return client

    @pytest.mark.asyncio
    async def test_fetch_returns_text(self, authed, monkeypatch):
        from app.tools import web_tools
        async def fake_fetch(url):
            return {
                "url": url, "status": 200, "content_type": "text/html",
                "size": 100, "truncated": False, "error": None,
                "text": "<h1>Title</h1> Body...",
            }
        monkeypatch.setattr(web_tools, "fetch_url_async", fake_fetch)
        r = await authed.post("/api/web/fetch", json={"url": "https://example.com/x"})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["text"]
        assert data["url"] == "https://example.com/x"

    @pytest.mark.asyncio
    async def test_fetch_rejects_unsafe_url(self, authed):
        r = await authed.post("/api/web/fetch", json={"url": "file:///etc/passwd"})
        # Either 400 (validation) or 500 with error — we accept any non-2xx
        assert r.status_code in (400, 500)


class TestParseEndpoint:
    @pytest.fixture
    def authed(self, client, test_user):
        from app.main import make_token
        client.cookies.set("session", make_token(test_user))
        return client

    @pytest.mark.asyncio
    async def test_parse_cleans_html(self, authed):
        html = "<html><body><h1>Title</h1><p>Body 1</p><script>var x=1</script></body></html>"
        r = await authed.post("/api/web/parse", json={"html": html, "url": "https://example.com"})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert "Title" in data["text"]
        assert "var x=1" not in data["text"]


class TestLinksEndpoint:
    @pytest.fixture
    def authed(self, client, test_user):
        from app.main import make_token
        client.cookies.set("session", make_token(test_user))
        return client

    @pytest.mark.asyncio
    async def test_extract_links_filtered(self, authed):
        html = """
        <a href="/page.pdf">P1</a>
        <a href="/page.html">P2</a>
        """
        r = await authed.post("/api/web/links", json={
            "html": html, "base_url": "https://example.com", "pattern": r"\.pdf$",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert len(data["links"]) == 1
        assert data["links"][0]["url"].endswith("/page.pdf")


class TestDownloadEndpoint:
    @pytest.fixture
    def authed(self, client, test_user):
        from app.main import make_token, generate_csrf_token
        client.cookies.set("session", make_token(test_user))
        return client, generate_csrf_token(make_token(test_user))

    @pytest.mark.asyncio
    async def test_download_creates_intent_for_approval(self, authed, monkeypatch):
        """Bulk download should create an action_intent that needs approval."""
        from app.tools import web_tools
        async def fake_download(uid, urls, target_folder="downloads", max_count=None):
            return {"saved": [{"url": urls[0], "file": {"name": "x.pdf", "path": "downloads/x.pdf", "size": 10}}],
                    "skipped": [], "saved_count": 1}
        monkeypatch.setattr(web_tools, "download_files", fake_download)

        client, token = authed
        r = await client.post(
            "/api/web/download",
            json={"urls": ["https://example.com/x.pdf"], "target_folder": "downloads", "max_count": 5},
            headers={"X-CSRF-Token": token},
        )
        # Endpoint should create a pending intent, NOT save yet
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert data["approval_required"] is True
        assert data["intent_id"]
        # File is NOT yet in user's folder
        from app import db as db_mod
        target = db_mod.HERMES_USERS_DIR / data["uid"] / "files" / "downloads"
        assert not target.exists() or not list(target.glob("*.pdf"))

    @pytest.mark.asyncio
    async def test_download_requires_csrf(self, authed, monkeypatch):
        from app.tools import web_tools
        monkeypatch.setattr(web_tools, "download_files", AsyncMock(return_value={"saved": [], "skipped": [], "saved_count": 0}))
        client, _ = authed
        r = await client.post(
            "/api/web/download",
            json={"urls": ["https://example.com/x.pdf"]},
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_download_rejects_too_many_urls(self, authed):
        client, token = authed
        urls = [f"https://example.com/{i}.pdf" for i in range(50)]
        r = await client.post(
            "/api/web/download",
            json={"urls": urls},
            headers={"X-CSRF-Token": token},
        )
        assert r.status_code == 400


class TestDownloadActionInReviewActions:
    """The web_download_files action_type must be in REVIEW_ACTIONS so the
    existing approval flow can handle it."""

    def test_web_download_in_review_actions(self):
        from app.relay import REVIEW_ACTIONS
        assert "web_download_files" in REVIEW_ACTIONS
