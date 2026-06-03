"""Tests: File upload — P0-5 from spec 01."""
import os
import sys
from unittest.mock import patch



class TestFileUpload:
    """File validation and safety."""

    def test_rejects_dangerous_extensions(self, tmp_path):
        # Mock env vars needed by relay import
        env = {
            "HERMES_API_URL": "http://test",
            "HERMES_API_KEY": "test",
            "HERMES_MODEL": "test",
        }
        with patch.dict(os.environ, env):
            # Reimport to avoid stale module cache
            if "app.relay" in sys.modules:
                del sys.modules["app.relay"]
            from app.relay import _safe_filename, DANGEROUS_EXTENSIONS

            for ext in DANGEROUS_EXTENSIONS:
                name = _safe_filename(f"malware{ext}", tmp_path)
                # Should be renamed to .txt
                assert not name.endswith(ext), f"{ext} should be rejected"
                assert name.endswith(".txt"), f"{ext} should become .txt"

    def test_accepts_safe_document_types(self, tmp_path):
        env = {
            "HERMES_API_URL": "http://test",
            "HERMES_API_KEY": "test",
            "HERMES_MODEL": "test",
        }
        with patch.dict(os.environ, env):
            if "app.relay" in sys.modules:
                del sys.modules["app.relay"]
            from app.relay import _safe_filename

            safe_exts = ['.txt', '.md', '.csv', '.json', '.pdf', '.docx', '.xlsx', '.jpg', '.png']
            for ext in safe_exts:
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
