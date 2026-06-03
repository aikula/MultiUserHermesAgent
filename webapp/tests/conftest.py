"""Shared test fixtures for webapp tests."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure app module is importable
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path):
    """Set up isolated test environment with temp DB and env vars."""
    db_path = tmp_path / "test_users.db"
    hermes_dir = tmp_path / "hermes"
    hermes_dir.mkdir()
    users_dir = hermes_dir / "users"
    users_dir.mkdir()
    shared_dir = hermes_dir / "shared"
    shared_dir.mkdir()

    env = {
        "WEBAPP_INTERNAL_SECRET": "test-internal-secret-key-12345",
        "JWT_SECRET": "test-jwt-secret-key-12345",
        "HERMES_API_URL": "http://localhost:8642",
        "HERMES_API_KEY": "test-api-key",
        "HERMES_MODEL": "test-model",
        "HERMES_HOME": str(hermes_dir),
        "HERMES_USERS_DIR": str(users_dir),
        "HERMES_SHARED_DIR": str(shared_dir),
        "USERS_DB_PATH": str(db_path),
        "WELCOME_QUOTA": "2000000",
        "ENCRYPTION_KEY": "test-encryption-key-12345",
    }

    with patch.dict(os.environ, env):
        # Initialize DB
        from app.db import init_db
        init_db()
        yield {
            "db_path": db_path,
            "hermes_dir": hermes_dir,
            "users_dir": users_dir,
            "shared_dir": shared_dir,
            "env": env,
        }


@pytest.fixture
def client(setup_test_env):
    """Create FastAPI test client."""
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def db():
    """Get test database connection."""
    from app.db import get_db
    return get_db()


@pytest.fixture
def test_user(db):
    """Create a test user and return uid."""
    import secrets
    import bcrypt
    from app.db import now_iso
    uid = "test_user_" + secrets.token_urlsafe(6)
    login = f"test_{secrets.token_urlsafe(4)}"
    password_hash = bcrypt.hashpw(b"testpass1234", bcrypt.gensalt()).decode()
    db.execute(
        "INSERT INTO users (uid, login, name, password_hash, quota_remaining, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (uid, login, "Test User", password_hash, 2000000, now_iso()),
    )
    db.commit()
    return uid


@pytest.fixture
def user_with_email(db, test_user):
    """Create a test user with encrypted email credentials."""
    from app.secrets_store import encrypt
    encrypted_pw = encrypt("test-email-password", test_user)
    db.execute(
        "UPDATE users SET email_imap_host='imap.test.com', email_imap_port=993, "
        "email_smtp_host='smtp.test.com', email_smtp_port=587, "
        "email_login='test@test.com', email_password=? WHERE uid=?",
        (encrypted_pw, test_user),
    )
    db.commit()
    return test_user
