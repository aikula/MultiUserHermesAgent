"""Tests: File upload — P0-2 from spec 01."""
import os
import sys
from unittest.mock import patch

import pytest



class TestFileUpload:
    """File validation and safety."""

    def test_rejects_dangerous_extensions(self, tmp_path):
        env = {
            "HERMES_API_URL": "http://test",
            "HERMES_API_KEY": "test",
            "HERMES_MODEL": "test",
        }
        with patch.dict(os.environ, env):
            if "app.relay" in sys.modules:
                del sys.modules["app.relay"]
            from app.relay import _safe_filename, DANGEROUS_EXTENSIONS

            for ext in DANGEROUS_EXTENSIONS:
                with pytest.raises(ValueError, match="dangerous"):
                    _safe_filename(f"malware{ext}", tmp_path)

    def test_rejects_unknown_extensions(self, tmp_path):
        env = {
            "HERMES_API_URL": "http://test",
            "HERMES_API_KEY": "test",
            "HERMES_MODEL": "test",
        }
        with patch.dict(os.environ, env):
            if "app.relay" in sys.modules:
                del sys.modules["app.relay"]
            from app.relay import _safe_filename

            # Only test extensions that are NOT in DANGEROUS_EXTENSIONS
            unknown_exts = ['.jpg', '.png', '.gif', '.xml', '.doc', '.ppt']
            for ext in unknown_exts:
                with pytest.raises(ValueError, match="not in allowed"):
                    _safe_filename(f"unknown{ext}", tmp_path)

    def test_accepts_mvp_allowed_types(self, tmp_path):
        env = {
            "HERMES_API_URL": "http://test",
            "HERMES_API_KEY": "test",
            "HERMES_MODEL": "test",
        }
        with patch.dict(os.environ, env):
            if "app.relay" in sys.modules:
                del sys.modules["app.relay"]
            from app.relay import _safe_filename

            allowed_exts = ['.txt', '.md', '.csv', '.json', '.pdf', '.docx', '.xlsx',
                           '.oga', '.ogg', '.mp3', '.wav', '.m4a', '.opus',
                           '.cer', '.crt', '.pem', '.key', '.log', '.yaml', '.yml', '.toml', '.ini', '.cfg']
            for ext in allowed_exts:
                name = _safe_filename(f"document{ext}", tmp_path)
                assert name.endswith(ext), f"{ext} should be accepted"

    def test_filename_is_uuid_based(self, tmp_path):
        env = {
            "HERMES_API_URL": "http://test",
            "HERMES_API_KEY": "test",
            "HERMES_MODEL": "test",
        }
        with patch.dict(os.environ, env):
            if "app.relay" in sys.modules:
                del sys.modules["app.relay"]
            from app.relay import _safe_filename

            name = _safe_filename("original.pdf", tmp_path)
            assert name.endswith(".pdf")
            stem = name[:-4]
            assert len(stem) == 12
            assert all(c in "0123456789abcdef" for c in stem)

    def test_filename_path_traversal_blocked(self, tmp_path):
        env = {
            "HERMES_API_URL": "http://test",
            "HERMES_API_KEY": "test",
            "HERMES_MODEL": "test",
        }
        with patch.dict(os.environ, env):
            if "app.relay" in sys.modules:
                del sys.modules["app.relay"]
            from app.relay import _safe_filename

            name = _safe_filename("../../evil.txt", tmp_path)
            assert "/" not in name
            assert ".." not in name
            assert name.endswith(".txt")

    def test_dangerous_extensions_set(self):
        env = {
            "HERMES_API_URL": "http://test",
            "HERMES_API_KEY": "test",
            "HERMES_MODEL": "test",
        }
        with patch.dict(os.environ, env):
            if "app.relay" in sys.modules:
                del sys.modules["app.relay"]
            from app.relay import DANGEROUS_EXTENSIONS

            expected_dangerous = {'.exe', '.bat', '.cmd', '.com', '.msi', '.scr', '.pif', '.vbs', '.js', '.ws', '.wsh'}
            assert expected_dangerous.issubset(DANGEROUS_EXTENSIONS)

    def test_empty_filename_gets_default(self, tmp_path):
        env = {
            "HERMES_API_URL": "http://test",
            "HERMES_API_KEY": "test",
            "HERMES_MODEL": "test",
        }
        with patch.dict(os.environ, env):
            if "app.relay" in sys.modules:
                del sys.modules["app.relay"]
            from app.relay import _safe_filename

            name = _safe_filename("", tmp_path)
            assert name
            assert name.endswith(".txt")

    def test_hidden_filename_gets_default(self, tmp_path):
        env = {
            "HERMES_API_URL": "http://test",
            "HERMES_API_KEY": "test",
            "HERMES_MODEL": "test",
        }
        with patch.dict(os.environ, env):
            if "app.relay" in sys.modules:
                del sys.modules["app.relay"]
            from app.relay import _safe_filename

            name = _safe_filename(".hidden", tmp_path)
            assert name
            assert not name.startswith(".")
