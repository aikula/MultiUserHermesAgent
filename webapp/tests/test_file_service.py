"""Tests: file_service — per-user file storage (spec 10).

Covers the helper module. API-endpoint tests live in test_files_api.py.
"""
import pytest


@pytest.fixture
def file_service_mod():
    """Import file_service inside the autouse fixture (which sets HERMES_API_KEY)."""
    from app import file_service as fsm
    return fsm


class TestResolveUserPath:
    """The resolver must reject path traversal and absolute paths."""

    def test_root_when_empty(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        monkeypatch.setattr(db, "HERMES_USERS_DIR", Path(fs_test_setup(test_user, monkeypatch)["users_dir"]))
        # Reset fs to the redirected root
        p = file_service_mod._resolve_user_path(test_user, "")
        assert p == file_service_mod.user_files_root(test_user)

    def test_subdir_resolves(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target_dir = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target_dir)
        (target_dir / test_user / "files" / "sub").mkdir(parents=True)
        p = file_service_mod._resolve_user_path(test_user, "sub")
        assert p.name == "sub"

    def test_traversal_blocked(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target_dir = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target_dir)
        with pytest.raises(file_service_mod.FileServiceError) as e:
            file_service_mod._resolve_user_path(test_user, "../../../etc/passwd")
        assert e.value.code == 403
        assert "traversal" in e.value.message.lower()

    def test_absolute_path_rejected_as_traversal(self, test_user, monkeypatch, file_service_mod):
        """An absolute path that escapes the user root must be rejected as traversal.

        Python's pathlib treats `Path('/x') / '/etc/passwd'` as the second path
        (an escape), not a join. The resolver must catch this and raise 403.
        """
        from pathlib import Path
        from app import db
        target_dir = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target_dir)
        with pytest.raises(file_service_mod.FileServiceError) as e:
            file_service_mod._resolve_user_path(test_user, "/etc/passwd")
        assert e.value.code == 403


def fs_test_setup(test_user, monkeypatch):
    """Helper: redirect HERMES_USERS_DIR to the conftest tmp path."""
    # The conftest's autouse fixture has already created users_dir.
    # Read the env var to find it.
    import os
    return {"users_dir": os.environ["HERMES_USERS_DIR"]}


class TestCreateFolder:
    """Folder creation rules from spec 10."""

    def test_creates_folder(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        result = file_service_mod.create_folder(test_user, "", "Занятие 1")
        assert result["name"] == "Занятие 1"
        assert (target / test_user / "files" / "Занятие 1").is_dir()

    def test_rejects_empty_name(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        with pytest.raises(file_service_mod.FileServiceError) as e:
            file_service_mod.create_folder(test_user, "", "   ")
        assert e.value.code == 400

    def test_rejects_dot_prefix(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        with pytest.raises(file_service_mod.FileServiceError) as e:
            file_service_mod.create_folder(test_user, "", ".hidden")
        assert e.value.code == 400

    def test_rejects_invalid_chars(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        with pytest.raises(file_service_mod.FileServiceError) as e:
            file_service_mod.create_folder(test_user, "", "bad/name")
        assert e.value.code == 400

    def test_rejects_too_long_name(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        with pytest.raises(file_service_mod.FileServiceError) as e:
            file_service_mod.create_folder(test_user, "", "a" * 100)
        assert e.value.code == 400

    def test_duplicate_folder_409(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        file_service_mod.create_folder(test_user, "", "dup")
        with pytest.raises(file_service_mod.FileServiceError) as e:
            file_service_mod.create_folder(test_user, "", "dup")
        assert e.value.code == 409


class TestSaveUpload:
    """File upload rules: allowlist, quota, sanitization."""

    def test_saves_text_file(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        result = file_service_mod.save_upload(test_user, "", "notes.txt", b"hello")
        assert (target / test_user / "files" / "notes.txt").is_file()
        assert result["size"] == 5

    def test_rejects_dangerous_extension(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        with pytest.raises(file_service_mod.FileServiceError) as e:
            file_service_mod.save_upload(test_user, "", "evil.exe", b"x" * 10)
        assert e.value.code == 400
        assert "dangerous" in e.value.message

    def test_rejects_unknown_extension(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        with pytest.raises(file_service_mod.FileServiceError) as e:
            file_service_mod.save_upload(test_user, "", "photo.jpg", b"x" * 10)
        assert e.value.code == 400
        assert "not in allowed" in e.value.message

    def test_strips_path_components_from_name(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        result = file_service_mod.save_upload(test_user, "", "../../../etc/passwd.txt", b"x")
        # Final name is just 'passwd.txt' — Path(filename).name strips traversal.
        assert (target / test_user / "files" / "passwd.txt").is_file()
        assert ".." not in result["name"]

    def test_disambiguates_duplicate_name(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        file_service_mod.save_upload(test_user, "", "dup.txt", b"first")
        result = file_service_mod.save_upload(test_user, "", "dup.txt", b"second")
        assert result["name"] != "dup.txt"
        assert result["name"].startswith("dup-")

    def test_oversize_file_rejected(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        # Set a tiny per-file limit for this test
        monkeypatch.setattr(file_service_mod, "MAX_FILE_SIZE_BYTES", 10)
        with pytest.raises(file_service_mod.FileServiceError) as e:
            file_service_mod.save_upload(test_user, "", "big.txt", b"x" * 100)
        assert e.value.code == 413

    def test_quota_enforced(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        # Set a tiny quota
        monkeypatch.setattr(file_service_mod, "USER_STORAGE_QUOTA_BYTES", 50)
        monkeypatch.setattr(file_service_mod, "MAX_FILE_SIZE_BYTES", 100)
        file_service_mod.save_upload(test_user, "", "a.txt", b"x" * 30)
        with pytest.raises(file_service_mod.FileServiceError) as e:
            file_service_mod.save_upload(test_user, "", "b.txt", b"x" * 30)
        assert e.value.code == 413
        assert "quota" in e.value.message.lower()

    def test_empty_file_rejected(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        with pytest.raises(file_service_mod.FileServiceError) as e:
            file_service_mod.save_upload(test_user, "", "empty.txt", b"")
        assert e.value.code == 400


class TestDelete:
    """Delete a file or empty folder."""

    def test_delete_file(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        file_service_mod.save_upload(test_user, "", "del.txt", b"x")
        result = file_service_mod.delete_path(test_user, "del.txt")
        assert result["deleted"] == "file"
        assert not (target / test_user / "files" / "del.txt").exists()

    def test_delete_empty_folder(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        file_service_mod.create_folder(test_user, "", "empty")
        result = file_service_mod.delete_path(test_user, "empty")
        assert result["deleted"] == "folder"
        assert not (target / test_user / "files" / "empty").exists()

    def test_delete_nonempty_folder_rejected(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        file_service_mod.create_folder(test_user, "", "full")
        file_service_mod.save_upload(test_user, "full", "inside.txt", b"x")
        with pytest.raises(file_service_mod.FileServiceError) as e:
            file_service_mod.delete_path(test_user, "full")
        assert e.value.code == 400
        assert "not empty" in e.value.message.lower()

    def test_delete_missing_404(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        with pytest.raises(file_service_mod.FileServiceError) as e:
            file_service_mod.delete_path(test_user, "nope.txt")
        assert e.value.code == 404

    def test_cannot_delete_root(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        with pytest.raises(file_service_mod.FileServiceError) as e:
            file_service_mod.delete_path(test_user, "")
        assert e.value.code == 400


class TestListFiles:
    """List directory contents and storage usage."""

    def test_list_empty_root(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        result = file_service_mod.list_files(test_user, "")
        assert result["directories"] == []
        assert result["files"] == []
        assert result["current_path"] == ""
        assert result["total_size"] == 0
        assert result["storage_limit"] == file_service_mod.USER_STORAGE_QUOTA_BYTES

    def test_list_with_files_and_dirs(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        file_service_mod.create_folder(test_user, "", "sub")
        file_service_mod.save_upload(test_user, "", "a.txt", b"hello")
        file_service_mod.save_upload(test_user, "sub", "b.md", b"x" * 100)
        result = file_service_mod.list_files(test_user, "")
        names = [d["name"] for d in result["directories"]]
        file_names = [f["name"] for f in result["files"]]
        assert "sub" in names
        assert "a.txt" in file_names
        assert result["total_size"] == 105

    def test_breadcrumbs_for_nested(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        file_service_mod.create_folder(test_user, "", "a")
        file_service_mod.create_folder(test_user, "a", "b")
        result = file_service_mod.list_files(test_user, "a/b")
        assert [c["name"] for c in result["breadcrumbs"]] == ["files", "a", "b"]


class TestWriteTextFile:
    """Agent creates markdown/text artifacts."""

    def test_writes_text(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        result = file_service_mod.write_text_file(test_user, "", "tasks.md", "# Tasks\n- [ ] thing")
        assert (target / test_user / "files" / "tasks.md").read_text() == "# Tasks\n- [ ] thing"
        assert result["name"] == "tasks.md"

    def test_rejects_path_in_name(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        with pytest.raises(file_service_mod.FileServiceError):
            file_service_mod.write_text_file(test_user, "", "sub/tasks.md", "x")


class TestUserScoping:
    """A user must never see/touch another user's files."""

    def test_other_user_files_invisible(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        # Save a file as test_user
        file_service_mod.save_upload(test_user, "", "private.txt", b"x")
        # List as a different uid
        other_uid = "other_" + test_user.split("_", 1)[-1] + "_v2"
        result = file_service_mod.list_files(other_uid, "")
        assert result["files"] == []
        assert not (target / other_uid / "files" / "private.txt").exists()

    def test_cannot_delete_other_user_file(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        file_service_mod.save_upload(test_user, "", "private.txt", b"x")
        # Even if attacker knows the path, they can't escape their own root.
        with pytest.raises(file_service_mod.FileServiceError):
            # Path "../<test_user>/private.txt" would resolve to outside other_uid's root
            file_service_mod.delete_path("other_user_xyz", f"../{test_user}/private.txt")


class TestStorageUsage:
    """storage_usage must sum every file under the user root."""

    def test_zero_when_empty(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        assert file_service_mod.storage_usage(test_user) == 0

    def test_sums_nested_files(self, test_user, monkeypatch, file_service_mod):
        from pathlib import Path
        from app import db
        target = Path(fs_test_setup(test_user, monkeypatch)["users_dir"])
        monkeypatch.setattr(db, "HERMES_USERS_DIR", target)
        file_service_mod.save_upload(test_user, "", "a.txt", b"x" * 100)
        file_service_mod.create_folder(test_user, "", "sub")
        file_service_mod.save_upload(test_user, "sub", "b.txt", b"x" * 50)
        assert file_service_mod.storage_usage(test_user) == 150
