"""Tests: Secret safety — P0-1 from spec 01."""
import os
from unittest.mock import patch

import pytest


class TestSecretsNotInPrompt:
    """build_system_prompt must never contain secrets."""

    def test_prompt_does_not_include_email_password(self, user_with_email):
        from app.chat import build_system_prompt
        prompt = build_system_prompt(user_with_email)
        # Email password should NOT be in prompt
        assert "test-email-password" not in prompt
        # Capability text mentioning imaplib/smtplib is ok, but credentials are not
        assert "test@test.com" in prompt  # email login is ok (not a secret)

    def test_prompt_does_not_include_env_secrets(self, test_user):
        from app.chat import build_system_prompt
        prompt = build_system_prompt(test_user)
        # env secrets should never appear
        assert "test-internal-secret-key" not in prompt
        assert "test-jwt-secret-key" not in prompt
        assert "test-api-key" not in prompt
        assert "test-encryption-key" not in prompt

    def test_prompt_contains_only_capability_text(self, user_with_email):
        from app.chat import build_system_prompt
        prompt = build_system_prompt(user_with_email)
        # Should contain capability description, not credentials
        assert "подключена почта" in prompt or "email" in prompt.lower()
        assert "backend" in prompt.lower() or "инструмент" in prompt.lower()


class TestProfileNotRenderingPassword:
    """Profile HTML must not expose saved password."""

    def test_profile_does_not_render_password(self, client, user_with_email):
        import asyncio

        async def _test():
            # Login first
            from app.main import make_token
            token = make_token(user_with_email)

            async with client as c:
                c.cookies.set("session", token)
                r = await c.get("/profile")
                assert r.status_code == 200
                html = r.text
                # Password should NOT appear in HTML
                assert "test-email-password" not in html
                # Email login should appear (it's not a secret)
                assert "test@test.com" in html

        asyncio.run(_test())


class TestEmailToolDecryption:
    """Email tools decrypt only inside backend, never expose."""

    def test_email_tool_decrypts_only_inside_backend(self, user_with_email):
        from app.secrets_store import decrypt
        from app.db import get_db
        db = get_db()
        row = db.execute("SELECT email_password FROM users WHERE uid=?", (user_with_email,)).fetchone()
        encrypted = row["email_password"]

        # Encrypted value should start with enc:v1:
        assert encrypted.startswith("enc:v1:")

        # Decrypt should return original
        decrypted = decrypt(encrypted, user_with_email)
        assert decrypted == "test-email-password"

        # Encrypted value should NOT contain plaintext
        assert "test-email-password" not in encrypted
