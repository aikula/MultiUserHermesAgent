"""Tests: web_tools module (spec 12).

Covers:
- URL validation: scheme/file/private-IP blocking
- fetch_url: size cap, timeout, content-type check
- extract_links: filters by pattern and allowed_domains
- parse_html_to_text: returns clean text + source attribution
- download_files: max_count, extension allowlist, saves under user files dir
- search_web: calls SearxNG, returns results
"""
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest


@pytest.fixture
def web():
    # Imported inside the fixture so conftest env-setup is active.
    from app.tools import web_tools
    return web_tools


class TestValidateUrl:
    @pytest.mark.parametrize("url,reason", [
        ("file:///etc/passwd", "scheme"),
        ("ftp://example.com/x", "scheme"),
        ("javascript:alert(1)", "scheme"),
        ("data:text/plain,xxx", "scheme"),
    ])
    def test_rejects_unsafe_scheme(self, web, url, reason):
        with pytest.raises(ValueError):
            web._validate_url(url)

    def test_accepts_http_and_https(self, web):
        web._validate_url("http://example.com/path")
        web._validate_url("https://example.com/path?x=1")

    def test_rejects_private_ip_when_enabled(self, web, monkeypatch):
        monkeypatch.setattr(web, "WEB_BLOCK_PRIVATE_IPS", True)
        with pytest.raises(ValueError, match="[Pp]rivate|IP"):
            web._validate_url("http://127.0.0.1/x")
        with pytest.raises(ValueError, match="[Pp]rivate|IP"):
            web._validate_url("http://10.0.0.5/x")
        with pytest.raises(ValueError, match="[Pp]rivate|IP"):
            web._validate_url("http://192.168.1.1/x")
        with pytest.raises(ValueError, match="[Pp]rivate|IP"):
            web._validate_url("http://169.254.169.254/latest/meta-data/")

    def test_allows_private_ip_when_disabled(self, web, monkeypatch):
        monkeypatch.setattr(web, "WEB_BLOCK_PRIVATE_IPS", False)
        # Should not raise
        web._validate_url("http://127.0.0.1/x")


class TestFetchUrl:
    @pytest.mark.asyncio
    async def test_size_limit_truncates(self, web, monkeypatch):
        # 10 MB body, limit 1 MB → truncated or refused
        big_body = b"x" * (web.WEB_FETCH_MAX_BYTES + 1)
        async def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {"content-type": "text/html"}
            resp.content = big_body
            resp.raise_for_status = MagicMock()
            return resp
        monkeypatch.setattr(web.httpx, "AsyncClient", lambda **kw: _FakeAsyncCtx(fake_get))
        result = await web.fetch_url_async("https://example.com/")
        # Either truncated with a flag, or refused with an error string
        assert result["truncated"] is True or "error" in result

    @pytest.mark.asyncio
    async def test_returns_html_and_text(self, web, monkeypatch):
        async def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {"content-type": "text/html; charset=utf-8"}
            resp.content = b"<html><body><h1>Hello</h1><p>World</p></body></html>"
            resp.raise_for_status = MagicMock()
            return resp
        monkeypatch.setattr(web.httpx, "AsyncClient", lambda **kw: _FakeAsyncCtx(fake_get))
        result = await web.fetch_url_async("https://example.com/")
        assert "Hello" in result["text"]
        assert result["url"] == "https://example.com/"
        assert result["truncated"] is False

    @pytest.mark.asyncio
    async def test_rejects_file_scheme(self, web):
        with pytest.raises(ValueError):
            await web.fetch_url_async("file:///etc/passwd")


class TestParseHtmlToText:
    def test_returns_clean_text(self, web):
        html = """
        <html><head><title>Article</title><script>var x=1;</script>
        <style>body { color: red; }</style></head>
        <body><h1>Title</h1><p>Body text 1.</p>
        <p>Body text 2.</p><footer>footer noise</footer></body></html>
        """
        result = web.parse_html_to_text(html, url="https://example.com/article")
        assert "Title" in result["text"]
        assert "Body text 1." in result["text"]
        # The script content must NOT appear in the cleaned text
        assert "var x=1" not in result["text"]
        assert result["source_url"] == "https://example.com/article"

    def test_handles_empty(self, web):
        result = web.parse_html_to_text("", url="https://example.com")
        assert result["text"] == "" or len(result["text"]) < 50


class TestExtractLinks:
    def test_filters_by_pattern(self, web):
        html = """
        <a href="/page1">P1</a>
        <a href="/page2.pdf">P2</a>
        <a href="https://other.com/x">Other</a>
        <a href="https://example.com/y.pdf">Y</a>
        """
        result = web.extract_links(html, base_url="https://example.com", pattern=r"\.pdf$")
        urls = [r["url"] for r in result["links"]]
        assert "https://example.com/page2.pdf" in urls
        assert "https://example.com/y.pdf" in urls
        assert "https://example.com/page1" not in urls

    def test_filters_by_allowed_domains(self, web):
        html = '<a href="https://allowed.com/x">A</a><a href="https://blocked.com/y">B</a>'
        result = web.extract_links(html, base_url="https://example.com", allowed_domains={"allowed.com"})
        urls = [r["url"] for r in result["links"]]
        assert "https://allowed.com/x" in urls
        assert "https://blocked.com/y" not in urls

    def test_skips_anchors_and_javascript(self, web):
        html = """
        <a href="#section">jump</a>
        <a href="javascript:void(0)">js</a>
        <a href="mailto:x@y.com">mail</a>
        <a href="https://example.com/real">real</a>
        """
        result = web.extract_links(html, base_url="https://example.com")
        urls = [r["url"] for r in result["links"]]
        assert urls == ["https://example.com/real"]


class TestDownloadFiles:
    @pytest.mark.asyncio
    async def test_respects_max_count(self, web, monkeypatch, test_user, setup_test_env, tmp_path):
        # Bind users_dir to a writable path
        from app import db as db_mod
        target = Path(setup_test_env["users_dir"])
        monkeypatch.setattr(db_mod, "HERMES_USERS_DIR", target)
        # 3 URLs but max_count=2
        urls = [
            "https://example.com/a.pdf",
            "https://example.com/b.pdf",
            "https://example.com/c.pdf",
        ]
        async def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {"content-type": "application/pdf"}
            resp.content = b"%PDF-1.4 fake"
            resp.raise_for_status = MagicMock()
            return resp
        monkeypatch.setattr(web.httpx, "AsyncClient", lambda **kw: _FakeAsyncCtx(fake_get))
        result = await web.download_files(test_user, urls, target_folder="downloads", max_count=2)
        assert result["saved_count"] == 2
        # The 3rd URL should appear in skipped or in the saved_count == 2
        assert result["saved_count"] <= 2

    @pytest.mark.asyncio
    async def test_rejects_unsafe_extension(self, web, monkeypatch, test_user, setup_test_env, tmp_path):
        from app import db as db_mod
        target = Path(setup_test_env["users_dir"])
        monkeypatch.setattr(db_mod, "HERMES_USERS_DIR", target)
        result = await web.download_files(
            test_user, ["https://example.com/x.exe"], target_folder="downloads", max_count=10,
        )
        assert result["saved_count"] == 0
        assert any(".exe" in s.get("reason", "") or "extension" in s.get("reason", "").lower()
                   for s in result.get("skipped", []))

    @pytest.mark.asyncio
    async def test_saves_to_user_folder(self, web, monkeypatch, test_user, setup_test_env):
        from app import db as db_mod
        target = Path(setup_test_env["users_dir"])
        monkeypatch.setattr(db_mod, "HERMES_USERS_DIR", target)
        async def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {"content-type": "application/pdf"}
            resp.content = b"%PDF-1.4 fake"
            resp.raise_for_status = MagicMock()
            return resp
        monkeypatch.setattr(web.httpx, "AsyncClient", lambda **kw: _FakeAsyncCtx(fake_get))
        await web.download_files(test_user, ["https://example.com/report.pdf"],
                                  target_folder="downloads", max_count=10)
        user_dir = target / test_user / "files" / "downloads"
        assert user_dir.exists()
        saved = list(user_dir.glob("*.pdf"))
        assert len(saved) == 1
        assert saved[0].read_bytes() == b"%PDF-1.4 fake"

    @pytest.mark.asyncio
    async def test_rejects_private_ip(self, web, monkeypatch, test_user, setup_test_env):
        from app import db as db_mod
        target = Path(setup_test_env["users_dir"])
        monkeypatch.setattr(db_mod, "HERMES_USERS_DIR", target)
        result = await web.download_files(
            test_user, ["http://127.0.0.1/x.pdf"], target_folder="downloads", max_count=10,
        )
        assert result["saved_count"] == 0


class TestSearchWeb:
    @pytest.mark.asyncio
    async def test_calls_searxng(self, web, monkeypatch):
        async def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "results": [
                    {"title": "First", "url": "https://example.com/1", "content": "Snippet 1"},
                    {"title": "Second", "url": "https://example.com/2", "content": "Snippet 2"},
                ]
            }
            return resp
        monkeypatch.setattr(web.httpx, "AsyncClient", lambda **kw: _FakeAsyncCtx(fake_get))
        results = await web.search_web("test query", limit=5)
        assert len(results) == 2
        assert results[0]["title"] == "First"
        assert results[0]["url"] == "https://example.com/1"
        assert results[0]["snippet"] == "Snippet 1"

    @pytest.mark.asyncio
    async def test_handles_empty_results(self, web, monkeypatch):
        async def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"results": []}
            return resp
        monkeypatch.setattr(web.httpx, "AsyncClient", lambda **kw: _FakeAsyncCtx(fake_get))
        results = await web.search_web("test", limit=5)
        assert results == []


class TestIsPrivateIp:
    def test_loopback(self, web, monkeypatch):
        assert web._is_private_ip("127.0.0.1") is True

    def test_rfc1918(self, web):
        assert web._is_private_ip("10.0.0.5") is True
        assert web._is_private_ip("172.16.0.1") is True
        assert web._is_private_ip("192.168.1.1") is True

    def test_link_local(self, web):
        assert web._is_private_ip("169.254.169.254") is True

    def test_public_ip(self, web):
        assert web._is_private_ip("8.8.8.8") is False
        assert web._is_private_ip("1.1.1.1") is False

    def test_ipv6_loopback(self, web):
        assert web._is_private_ip("::1") is True


# ---- helper for httpx.AsyncClient mocking ----

class _FakeAsyncCtx:
    def __init__(self, get_fn):
        self._get_fn = get_fn

    async def __aenter__(self):
        client = MagicMock()
        client.get = self._get_fn
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        return client

    async def __aexit__(self, *args):
        return None
