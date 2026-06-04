"""Tests: /api/files/* endpoints (spec 10: Files UI).

Covers:
- GET /files (HTML page renders, requires auth)
- GET /api/files (list, scoped per user)
- POST /api/files/mkdir (folder creation, validation, CSRF)
- POST /api/files/upload (allowlist, quota, sanitization, CSRF)
- GET /api/files/download (streams file, rejects dirs)
- POST /api/files/delete (file/empty folder, CSRF)
- POST /api/files/write-text (text artifact creation, CSRF)
- User scoping (cannot access other user's files)
"""
from pathlib import Path

import pytest


# --- helpers ---

async def _login(client, db):
    """Create a user, set the session cookie directly (bypasses rate limiter)."""
    import bcrypt
    import secrets
    from app.db import now_iso
    from app.main import make_token
    uid = "fsuser_" + secrets.token_urlsafe(6)
    login = f"fs_{secrets.token_urlsafe(4)}"
    pw_hash = bcrypt.hashpw(b"testpass1234", bcrypt.gensalt()).decode()
    db.execute(
        "INSERT INTO users (uid, login, name, password_hash, quota_remaining, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (uid, login, "Test", pw_hash, 2000000, now_iso()),
    )
    db.commit()
    # Set the session cookie directly. Avoids hitting /login (which is rate-limited).
    token = make_token(uid)
    client.cookies.set("session", token)
    return uid, login


def _csrf(client):
    """Read the CSRF token derived from the session cookie."""
    from app.main import generate_csrf_token
    session = client.cookies.get("session")
    return generate_csrf_token(session) if session else ""


# --- GET /files (HTML) ---

class TestFilesPage:
    @pytest.mark.asyncio
    async def test_requires_auth(self, client):
        r = await client.get("/files")
        assert r.status_code == 302
        assert "/login" in r.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_renders_for_authed_user(self, client, db):
        _uid, _login_name = await _login(client, db)
        r = await client.get("/files")
        assert r.status_code == 200
        assert "Файлы" in r.text


# --- GET /api/files ---

class TestApiList:
    @pytest.mark.asyncio
    async def test_empty_listing(self, client, db):
        _uid, _ = await _login(client, db)
        r = await client.get("/api/files")
        assert r.status_code == 200
        data = r.json()
        assert data["directories"] == []
        assert data["files"] == []
        assert data["current_path"] == ""

    @pytest.mark.asyncio
    async def test_requires_auth(self, client):
        r = await client.get("/api/files")
        assert r.status_code == 401


# --- POST /api/files/mkdir ---

class TestApiMkdir:
    @pytest.mark.asyncio
    async def test_creates_folder(self, client, db, setup_test_env, monkeypatch):
        from app import db as appdb
        monkeypatch.setattr(appdb, "HERMES_USERS_DIR", Path(setup_test_env["users_dir"]))
        uid, _ = await _login(client, db)
        csrf = _csrf(client)
        r = await client.post("/api/files/mkdir",
                              json={"path": "", "name": "Test Folder"},
                              headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200, r.text
        assert (Path(setup_test_env["users_dir"]) / uid / "files" / "Test Folder").is_dir()

    @pytest.mark.asyncio
    async def test_csrf_required(self, client, db):
        await _login(client, db)
        r = await client.post("/api/files/mkdir", json={"path": "", "name": "x"})
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_invalid_name_returns_400(self, client, db):
        await _login(client, db)
        csrf = _csrf(client)
        r = await client.post("/api/files/mkdir",
                              json={"path": "", "name": "../bad"},
                              headers={"X-CSRF-Token": csrf})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_duplicate_returns_409(self, client, db, setup_test_env, monkeypatch):
        from app import db as appdb
        monkeypatch.setattr(appdb, "HERMES_USERS_DIR", Path(setup_test_env["users_dir"]))
        await _login(client, db)
        csrf = _csrf(client)
        await client.post("/api/files/mkdir", json={"path": "", "name": "dup"},
                          headers={"X-CSRF-Token": csrf})
        r = await client.post("/api/files/mkdir", json={"path": "", "name": "dup"},
                              headers={"X-CSRF-Token": csrf})
        assert r.status_code == 409


# --- POST /api/files/upload ---

class TestApiUpload:
    @pytest.mark.asyncio
    async def test_uploads_file(self, client, db, setup_test_env, monkeypatch):
        from app import db as appdb
        monkeypatch.setattr(appdb, "HERMES_USERS_DIR", Path(setup_test_env["users_dir"]))
        uid, _ = await _login(client, db)
        csrf = _csrf(client)
        files = {"file": ("notes.txt", b"hello", "text/plain")}
        r = await client.post("/api/files/upload",
                              data={"path": ""},
                              files=files,
                              headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200, r.text
        assert (Path(setup_test_env["users_dir"]) / uid / "files" / "notes.txt").exists()

    @pytest.mark.asyncio
    async def test_rejects_dangerous_extension(self, client, db):
        await _login(client, db)
        csrf = _csrf(client)
        files = {"file": ("evil.exe", b"MZ", "application/octet-stream")}
        r = await client.post("/api/files/upload",
                              data={"path": ""},
                              files=files,
                              headers={"X-CSRF-Token": csrf})
        assert r.status_code == 400
        assert "dangerous" in r.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_csrf_required(self, client, db):
        await _login(client, db)
        files = {"file": ("notes.txt", b"hi", "text/plain")}
        r = await client.post("/api/files/upload",
                              data={"path": ""},
                              files=files)
        assert r.status_code == 403


# --- GET /api/files/download ---

class TestApiDownload:
    @pytest.mark.asyncio
    async def test_downloads_existing_file(self, client, db, setup_test_env, monkeypatch):
        from app import db as appdb
        monkeypatch.setattr(appdb, "HERMES_USERS_DIR", Path(setup_test_env["users_dir"]))
        await _login(client, db)
        csrf = _csrf(client)
        files = {"file": ("a.txt", b"hello world", "text/plain")}
        r = await client.post("/api/files/upload",
                              data={"path": ""},
                              files=files,
                              headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200
        r = await client.get("/api/files/download?path=a.txt")
        assert r.status_code == 200
        assert r.content == b"hello world"

    @pytest.mark.asyncio
    async def test_missing_file_404(self, client, db):
        await _login(client, db)
        r = await client.get("/api/files/download?path=nope.txt")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_traversal_rejected_403(self, client, db):
        await _login(client, db)
        r = await client.get("/api/files/download?path=../../../etc/passwd")
        assert r.status_code == 403


# --- POST /api/files/delete ---

class TestApiDelete:
    @pytest.mark.asyncio
    async def test_deletes_file(self, client, db, setup_test_env, monkeypatch):
        from app import db as appdb
        monkeypatch.setattr(appdb, "HERMES_USERS_DIR", Path(setup_test_env["users_dir"]))
        uid, _ = await _login(client, db)
        csrf = _csrf(client)
        files = {"file": ("del.txt", b"x", "text/plain")}
        await client.post("/api/files/upload",
                          data={"path": ""},
                          files=files,
                          headers={"X-CSRF-Token": csrf})
        r = await client.post("/api/files/delete", json={"path": "del.txt"},
                              headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200
        assert not (Path(setup_test_env["users_dir"]) / uid / "files" / "del.txt").exists()

    @pytest.mark.asyncio
    async def test_csrf_required(self, client, db):
        await _login(client, db)
        r = await client.post("/api/files/delete", json={"path": "x.txt"})
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_missing_returns_404(self, client, db):
        await _login(client, db)
        csrf = _csrf(client)
        r = await client.post("/api/files/delete", json={"path": "nope.txt"},
                              headers={"X-CSRF-Token": csrf})
        assert r.status_code == 404


# --- POST /api/files/write-text ---

class TestApiWriteText:
    @pytest.mark.asyncio
    async def test_writes_text_file(self, client, db, setup_test_env, monkeypatch):
        from app import db as appdb
        monkeypatch.setattr(appdb, "HERMES_USERS_DIR", Path(setup_test_env["users_dir"]))
        uid, _ = await _login(client, db)
        csrf = _csrf(client)
        r = await client.post("/api/files/write-text",
                              json={"path": "", "name": "tasks.md", "content": "# Tasks"},
                              headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200, r.text
        f = Path(setup_test_env["users_dir"]) / uid / "files" / "tasks.md"
        assert f.exists()
        assert f.read_text() == "# Tasks"

    @pytest.mark.asyncio
    async def test_rejects_path_in_name(self, client, db):
        await _login(client, db)
        csrf = _csrf(client)
        r = await client.post("/api/files/write-text",
                              json={"path": "", "name": "sub/x.md", "content": "x"},
                              headers={"X-CSRF-Token": csrf})
        assert r.status_code == 400


# --- User scoping ---

class TestUserScopingApi:
    @pytest.mark.asyncio
    async def test_user_cannot_download_other_users_file(self, client, db, setup_test_env, monkeypatch):
        from app import db as appdb
        monkeypatch.setattr(appdb, "HERMES_USERS_DIR", Path(setup_test_env["users_dir"]))
        import bcrypt
        import secrets
        from app.db import now_iso
        from app.main import make_token
        # User A
        uid_a = "scope_a_" + secrets.token_urlsafe(4)
        login_a = f"sa_{secrets.token_urlsafe(4)}"
        pw_hash = bcrypt.hashpw(b"testpass1234", bcrypt.gensalt()).decode()
        db.execute("INSERT INTO users (uid, login, name, password_hash, quota_remaining, created_at) "
                   "VALUES (?, ?, ?, ?, ?, ?)",
                   (uid_a, login_a, "A", pw_hash, 2000000, now_iso()))
        db.commit()
        client.cookies.set("session", make_token(uid_a))
        csrf = _csrf(client)
        await client.post("/api/files/upload",
                          data={"path": ""},
                          files={"file": ("secret.txt", b"private", "text/plain")},
                          headers={"X-CSRF-Token": csrf})
        # Switch to a different user
        await _login(client, db)
        # B tries to download A's file (path doesn't exist under B's root)
        r = await client.get("/api/files/download?path=secret.txt")
        assert r.status_code == 404
