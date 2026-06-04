"""Web tools for the agent (spec 12, Phase 1).

Engine layer: pure functions + httpx calls. No agent integration here.

Phase 1 stack (SearxNG + HTTP fetch + trafilatura + beautifulsoup):
- search_web(query, limit) via SearxNG
- fetch_url_async(url) with timeout + size cap + safety guards
- parse_html_to_text(html, url) — clean article text + source attribution
- extract_links(html, base_url, pattern, allowed_domains) — anchor parser
- download_files(uid, urls, target_folder, max_count) — fetch + persist
  to HERMES_USERS_DIR/<uid>/files/<folder>/

Safety:
- block local/private IP ranges (toggleable via WEB_BLOCK_PRIVATE_IPS)
- block file://, ftp://, javascript:, data:
- allow only http(s)
- max response size (WEB_FETCH_MAX_BYTES)
- file extension allowlist for downloads
"""
import ipaddress
import os
import re
import socket
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
from bs4 import BeautifulSoup

# ---- Config ----

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://searxng:8888").rstrip("/")
SEARXNG_USER = os.environ.get("SEARXNG_USER") or None
SEARXNG_PASS = os.environ.get("SEARXNG_PASS") or None
WEB_FETCH_TIMEOUT_SECONDS = float(os.environ.get("WEB_FETCH_TIMEOUT_SECONDS", "20"))
WEB_FETCH_MAX_BYTES = int(os.environ.get("WEB_FETCH_MAX_BYTES", "5000000"))
WEB_DOWNLOAD_MAX_FILES = int(os.environ.get("WEB_DOWNLOAD_MAX_FILES", "10"))
WEB_DOWNLOAD_ALLOWED_EXTENSIONS = (
    os.environ.get(
        "WEB_DOWNLOAD_ALLOWED_EXTENSIONS",
        ".pdf,.txt,.md,.csv,.json,.docx,.xlsx",
    )
    .lower().split(",")
)
WEB_ALLOWED_DOMAINS: set[str] = {
    d.strip().lower() for d in os.environ.get("WEB_ALLOWED_DOMAINS", "").split(",") if d.strip()
}
WEB_BLOCK_PRIVATE_IPS = os.environ.get("WEB_BLOCK_PRIVATE_IPS", "true").lower() in ("1", "true", "yes")

ALLOWED_DOWNLOAD_MIME = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "application/json": ".json",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/zip": ".zip",
}

# ---- URL validation ----

_UNSAFE_SCHEMES = {"file", "ftp", "javascript", "data", "vbscript"}


def _is_private_ip(host: str) -> bool:
    """Return True if the host is a loopback/RFC1918/link-local address."""
    if not host:
        return False
    # Try IPv4 / IPv6 literal
    try:
        ip = ipaddress.ip_address(host)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        )
    except ValueError:
        pass
    # Try DNS resolution and check ALL returned IPs
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        sockaddr = info[4]
        addr = sockaddr[0]
        try:
            ip = ipaddress.ip_address(addr)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
            ):
                return True
        except ValueError:
            continue
    return False


def _validate_url(url: str) -> str:
    """Validate URL scheme + private-IP policy. Returns the URL on success."""
    if not url:
        raise ValueError("empty url")
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"unsafe scheme: {parsed.scheme!r}")
    if not parsed.netloc:
        raise ValueError("url has no host")
    if WEB_BLOCK_PRIVATE_IPS and _is_private_ip(parsed.hostname or ""):
        raise ValueError(f"refusing to fetch private/loopback IP: {parsed.hostname!r}")
    if WEB_ALLOWED_DOMAINS:
        host = (parsed.hostname or "").lower()
        if not any(host == d or host.endswith("." + d) for d in WEB_ALLOWED_DOMAINS):
            raise ValueError(f"host {host!r} not in WEB_ALLOWED_DOMAINS")
    return url


# ---- HTTP fetch ----

@dataclass
class FetchResult:
    url: str
    status: int
    content_type: str
    body: bytes
    truncated: bool
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "status": self.status,
            "content_type": self.content_type,
            "size": len(self.body),
            "truncated": self.truncated,
            "error": self.error,
        }


async def fetch_url_async(url: str) -> dict:
    """Fetch a single URL. Returns a dict with body bytes, content-type, etc.

    On size-cap hit: returns the truncated body with `truncated=True` so the
    caller can still extract text from what was downloaded.
    On error: returns a dict with `error` set and `body=b""`.
    """
    _validate_url(url)
    headers = {"User-Agent": "hermes-webapp/0.1 (+browsing)"}
    try:
        async with httpx.AsyncClient(
            timeout=WEB_FETCH_TIMEOUT_SECONDS, follow_redirects=True, headers=headers,
        ) as client:
            r = await client.get(url)
            r.raise_for_status()
            content_type = r.headers.get("content-type", "").split(";")[0].strip()
            body = r.content
            truncated = False
            if len(body) > WEB_FETCH_MAX_BYTES:
                body = body[:WEB_FETCH_MAX_BYTES]
                truncated = True
            result = FetchResult(
                url=url, status=r.status_code, content_type=content_type,
                body=body, truncated=truncated,
            )
            text = parse_html_to_text(body.decode("utf-8", errors="ignore"), url=url)["text"] \
                if content_type.startswith("text/html") else body.decode("utf-8", errors="ignore")
            d = result.to_dict()
            d["text"] = text[:50_000]  # cap text to keep response reasonable
            return d
    except (httpx.HTTPError, ValueError) as e:
        return {"url": url, "status": 0, "content_type": "", "size": 0,
                "truncated": False, "error": str(e), "text": ""}


# ---- Parse helpers ----

def parse_html_to_text(html: str, url: str | None = None) -> dict:
    """Extract clean article text via trafilatura; fallback to BeautifulSoup."""
    if not html:
        return {"text": "", "source_url": url, "method": "empty"}
    text = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
    method = "trafilatura"
    if not text or len(text) < 80:
        # Fallback: BS4 visible text
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        method = "beautifulsoup"
    return {"text": text.strip(), "source_url": url, "method": method}


_LINK_RE = re.compile(r"^https?://", re.IGNORECASE)


def extract_links(
    html: str,
    base_url: str,
    pattern: str | None = None,
    allowed_domains: Iterable[str] | None = None,
    limit: int = 100,
) -> dict:
    """Parse anchors. Resolve relative URLs, skip non-http schemes, optionally
    filter by regex pattern and allowed_domains.
    """
    if not html:
        return {"links": [], "total": 0, "filtered": 0}
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    out: list[dict] = []
    allowed = {d.lower() for d in (allowed_domains or [])}
    pattern_re = re.compile(pattern) if pattern else None
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href)
        if not _LINK_RE.match(absolute):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        if allowed:
            host = (urlparse(absolute).hostname or "").lower()
            if not any(host == d or host.endswith("." + d) for d in allowed):
                continue
        if pattern_re and not pattern_re.search(absolute):
            continue
        out.append({"url": absolute, "text": a.get_text(" ", strip=True)[:200]})
        if len(out) >= limit:
            break
    return {"links": out, "total": len(seen), "filtered": len(out)}


# ---- Download ----

async def download_files(
    uid: str,
    urls: list[str],
    target_folder: str = "downloads",
    max_count: int | None = None,
) -> dict:
    """Fetch a list of URLs and save allowed ones under the user's files dir.

    The folder is created if it doesn't exist. Per-file result is recorded
    in `saved` or `skipped` so the agent can show a summary.
    """
    if not urls:
        return {"saved": [], "skipped": [], "saved_count": 0}
    max_count = min(max_count or WEB_DOWNLOAD_MAX_FILES, WEB_DOWNLOAD_MAX_FILES)
    selected = urls[:max_count]

    saved: list[dict] = []
    skipped: list[dict] = []
    async with httpx.AsyncClient(
        timeout=WEB_FETCH_TIMEOUT_SECONDS, follow_redirects=True,
        headers={"User-Agent": "hermes-webapp/0.1 (+browsing)"},
    ) as client:
        for url in selected:
            try:
                _validate_url(url)
            except ValueError as e:
                skipped.append({"url": url, "reason": f"unsafe: {e}"})
                continue
            parsed = urlparse(url)
            # Extension policy: derive from URL path, fall back to content-type
            path = parsed.path.lower()
            ext = next((e for e in WEB_DOWNLOAD_ALLOWED_EXTENSIONS if path.endswith(e)), None)
            try:
                r = await client.get(url)
                r.raise_for_status()
            except httpx.HTTPError as e:
                skipped.append({"url": url, "reason": f"http error: {e}"})
                continue
            if not ext:
                ct = r.headers.get("content-type", "").split(";")[0].strip().lower()
                ext = ALLOWED_DOWNLOAD_MIME.get(ct)
            if not ext:
                skipped.append({"url": url, "reason": "extension not in allowlist"})
                continue
            body = r.content
            if len(body) > WEB_FETCH_MAX_BYTES:
                body = body[:WEB_FETCH_MAX_BYTES]
            filename = (path.rsplit("/", 1)[-1] or "download") + (ext if not path.endswith(ext) else "")
            # Sanitize filename: keep simple chars
            safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:120] or f"file{ext}"
            try:
                file_info = await _save_download(uid, target_folder, safe_name, body)
                saved.append({"url": url, "file": file_info})
            except Exception as e:
                skipped.append({"url": url, "reason": f"save failed: {e}"})
    return {"saved": saved, "skipped": skipped, "saved_count": len(saved)}


async def _save_download(uid: str, folder: str, name: str, body: bytes) -> dict:
    """Save bytes into <HERMES_USERS_DIR>/<uid>/files/<folder>/<name>."""
    import asyncio
    from .. import db as db_mod
    loop = asyncio.get_running_loop()

    def _write():
        # Folder is allowed if it doesn't try to escape via ..
        clean_folder = re.sub(r"[^A-Za-z0-9._/-]", "", folder).strip("/")
        if ".." in clean_folder.split("/"):
            raise ValueError("folder path traversal")
        target_dir = db_mod.HERMES_USERS_DIR / uid / "files" / clean_folder
        target_dir.mkdir(parents=True, exist_ok=True)
        # If the name already exists, append a counter
        path = target_dir / name
        counter = 1
        while path.exists():
            stem, suffix = path.stem, path.suffix
            path = target_dir / f"{stem}.{counter}{suffix}"
            counter += 1
        path.write_bytes(body)
        return {
            "name": path.name,
            "path": f"{clean_folder}/{path.name}" if clean_folder else path.name,
            "size": len(body),
        }
    return await loop.run_in_executor(None, _write)


# ---- Search ----

async def search_web(
    query: str,
    limit: int = 10,
    categories: str | None = None,
    engines: str | None = None,
    time_range: str | None = None,
    pageno: int = 1,
    language: str = "ru",
    safesearch: int = 1,
) -> list[dict]:
    """Call SearxNG JSON API. Returns up to `limit` results.

    Parameters:
        query: search query string
        limit: max results to return
        categories: comma-separated category names (general, news, images, videos, etc.)
        engines: comma-separated engine names (google, bing, wikipedia, etc.)
        time_range: day, month, year (if engine supports it)
        pageno: page number (default 1)
        language: language code (default ru)
        safesearch: 0=off, 1=moderate, 2=strict (default 1)
    """
    if not SEARXNG_URL:
        return []
    params: dict[str, str | int] = {
        "q": query, "format": "json",
        "language": language, "safesearch": safesearch,
        "pageno": pageno,
    }
    if categories:
        params["categories"] = categories
    if engines:
        params["engines"] = engines
    if time_range:
        params["time_range"] = time_range

    headers = {"User-Agent": "hermes-webapp/0.1 (+search)"}
    auth = httpx.BasicAuth(SEARXNG_USER, SEARXNG_PASS) if SEARXNG_USER and SEARXNG_PASS else None
    try:
        async with httpx.AsyncClient(timeout=WEB_FETCH_TIMEOUT_SECONDS, headers=headers) as client:
            r = await client.get(f"{SEARXNG_URL}/search", params=params, auth=auth)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError:
        return []
    results: list[dict] = []
    for item in (data.get("results") or [])[:limit]:
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
            "engine": item.get("engine", ""),
            "category": item.get("category", ""),
        })
    return results
