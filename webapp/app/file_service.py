"""Per-user file storage service (spec 10: Files UI).

Centralizes all file operations for a user:
- list / mkdir / upload / download / delete / write-text / storage_usage
- path-traversal protection (resolve + containment check)
- file extension allowlist (shared with Telegram relay)
- per-user storage quota

All public functions are synchronous — call from asyncio.to_thread or
run_in_executor when invoked from async endpoints.
"""
import os
import re
import uuid
from pathlib import Path

from . import db
from .relay import ALLOWED_EXTENSIONS, DANGEROUS_EXTENSIONS

# Re-exported for backwards-compat in case anything imports the symbol name.
HERMES_USERS_DIR = db.HERMES_USERS_DIR  # may be patched via monkeypatch

# Storage cap per user (MB). Files upload beyond this fails with 413.
USER_STORAGE_QUOTA_MB = int(os.environ.get("USER_STORAGE_QUOTA_MB", "100"))
USER_STORAGE_QUOTA_BYTES = USER_STORAGE_QUOTA_MB * 1024 * 1024

# Max single-file size (MB). Independent of quota to keep error messages clear.
MAX_FILE_SIZE_MB = int(os.environ.get("MAX_FILE_SIZE_MB", "50"))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# Folder name: letters (incl. Cyrillic), digits, space, dash, underscore, dot.
# Length 1..80. No leading dot, no traversal chars.
FOLDER_NAME_RE = re.compile(r"^[A-Za-zА-Яа-я0-9 _\-\.]{1,80}$")


class FileServiceError(Exception):
    """Domain error. The .code attribute maps to HTTP status (400/403/404/409/413)."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def user_files_root(uid: str) -> Path:
    """Per-user root directory. Created on first access."""
    # Use db.HERMES_USERS_DIR dynamically so monkeypatch.setattr works in tests.
    root = db.HERMES_USERS_DIR / uid / "files"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_user_path(uid: str, relative_path: str) -> Path:
    """Resolve a user-supplied path and ensure it stays inside the user's root.

    Raises FileServiceError(403) on traversal attempts.
    Returns the absolute path (which may not yet exist).
    """
    root = user_files_root(uid).resolve()
    rel = (relative_path or "").strip()
    if not rel or rel in (".", "/"):
        return root
    # Treat the relative path as relative to the root and resolve.
    # If the user passes an absolute path, anchor it to the root anyway.
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise FileServiceError(403, "path traversal detected")
    return candidate


def list_files(uid: str, path: str = "") -> dict:
    """List directory contents under user's root.

    Returns: {current_path, breadcrumbs, directories, files, total_size, storage_limit}

    `total_size` is the user's TOTAL storage usage (recursive), matching the
    spec's "storage limit" semantics. Per-directory browsing only filters what
    is shown in the table.
    """
    target = _resolve_user_path(uid, path)
    if target.exists() and not target.is_dir():
        raise FileServiceError(400, "not a directory")

    directories: list[dict] = []
    files: list[dict] = []

    if target.exists():
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            try:
                if child.is_dir():
                    directories.append({
                        "name": child.name,
                        "type": "dir",
                        "updated_at": child.stat().st_mtime,
                    })
                else:
                    stat = child.stat()
                    files.append({
                        "name": child.name,
                        "type": "file",
                        "ext": child.suffix.lower(),
                        "size": stat.st_size,
                        "size_human": _human_size(stat.st_size),
                        "updated_at": stat.st_mtime,
                    })
            except OSError:
                # Skip unreadable entries (broken symlinks, permission issues)
                continue

    return {
        "current_path": _relative(target, user_files_root(uid)),
        "breadcrumbs": _breadcrumbs(target, user_files_root(uid)),
        "directories": directories,
        "files": files,
        "total_size": storage_usage(uid),
        "total_size_human": _human_size(storage_usage(uid)),
        "storage_limit": USER_STORAGE_QUOTA_BYTES,
        "storage_limit_human": _human_size(USER_STORAGE_QUOTA_BYTES),
    }


def create_folder(uid: str, path: str, name: str) -> dict:
    """Create a new folder under the given path. Returns the new folder's metadata."""
    name = (name or "").strip()
    if not name:
        raise FileServiceError(400, "name is required")
    if name.startswith("."):
        raise FileServiceError(400, "name must not start with a dot")
    if not FOLDER_NAME_RE.match(name):
        raise FileServiceError(400, "name has invalid characters")
    if len(name) > 80:
        raise FileServiceError(400, "name too long (max 80)")

    target_dir = _resolve_user_path(uid, path)
    if not target_dir.exists() or not target_dir.is_dir():
        raise FileServiceError(404, "parent directory not found")

    new_dir = target_dir / name
    if new_dir.exists():
        raise FileServiceError(409, "folder already exists")
    new_dir.mkdir()
    return {
        "name": name,
        "path": _relative(new_dir, user_files_root(uid)),
    }


def save_upload(uid: str, path: str, filename: str, content: bytes) -> dict:
    """Save an uploaded file under path/. Returns metadata of the saved file.

    Validates:
    - extension is in allowlist (and not dangerous)
    - size within per-file and per-user quota
    - sanitized name (path traversal stripped, dangerous prefixes blocked)
    """
    if not content:
        raise FileServiceError(400, "empty file")

    # Validate size BEFORE writing to disk
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise FileServiceError(413, f"file too large (max {MAX_FILE_SIZE_MB} MB)")

    # Quota check (current + new file must fit)
    used = storage_usage(uid)
    if used + len(content) > USER_STORAGE_QUOTA_BYTES:
        raise FileServiceError(
            413,
            f"storage quota exceeded ({_human_size(used)} used + "
            f"{_human_size(len(content))} new > {_human_size(USER_STORAGE_QUOTA_BYTES)} limit)"
        )

    # Sanitize the name
    safe_name = _safe_upload_name(filename, content)

    target_dir = _resolve_user_path(uid, path)
    if not target_dir.exists() or not target_dir.is_dir():
        raise FileServiceError(404, "upload directory not found")

    final_path = target_dir / safe_name
    if final_path.exists():
        # Disambiguate by appending -<short uuid>
        stem = final_path.stem
        ext = final_path.suffix
        final_path = target_dir / f"{stem}-{uuid.uuid4().hex[:6]}{ext}"

    # Write atomically: write to a temp file in the same dir then rename
    tmp = target_dir / f".{uuid.uuid4().hex}.tmp"
    try:
        tmp.write_bytes(content)
        tmp.rename(final_path)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise

    stat = final_path.stat()
    return {
        "name": final_path.name,
        "path": _relative(final_path, user_files_root(uid)),
        "size": stat.st_size,
        "size_human": _human_size(stat.st_size),
    }


def delete_path(uid: str, path: str) -> dict:
    """Delete a file or empty folder. Recursive deletion is NOT supported in demo."""
    target = _resolve_user_path(uid, path)
    root = user_files_root(uid)
    if target == root:
        raise FileServiceError(400, "cannot delete the root files directory")

    if not target.exists():
        raise FileServiceError(404, "path not found")

    if target.is_dir():
        # Reject non-empty folders (demo keeps things explicit)
        if any(target.iterdir()):
            raise FileServiceError(400, "folder is not empty; recursive delete not supported")
        target.rmdir()
        return {"deleted": "folder", "path": path}

    target.unlink()
    return {"deleted": "file", "path": path}


def write_text_file(uid: str, path: str, name: str, content: str) -> dict:
    """Write a text file (used for agent-generated artifacts like tasks.md)."""
    name = (name or "").strip()
    if not name:
        raise FileServiceError(400, "name is required")
    if "/" in name or "\\" in name:
        raise FileServiceError(400, "name must not contain path separators")
    safe_name = _safe_upload_name(name, content.encode("utf-8"))
    return save_upload(uid, path, safe_name, content.encode("utf-8"))


def storage_usage(uid: str) -> int:
    """Total bytes used by this user under their files root."""
    root = user_files_root(uid)
    total = 0
    for p in root.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def resolve_for_download(uid: str, path: str) -> Path:
    """Resolve a user-supplied path for download. Returns absolute path on disk.

    Raises FileServiceError if path is missing, is a directory, or escapes root.
    """
    target = _resolve_user_path(uid, path)
    if not target.exists():
        raise FileServiceError(404, "file not found")
    if target.is_dir():
        raise FileServiceError(400, "directory download not supported")
    return target


# --- helpers ---

def _safe_upload_name(filename: str, content: bytes) -> str:
    """Sanitize a user-supplied filename. Use original name when safe; UUID otherwise.

    Rules:
    - Strip directory components (Path(filename).name)
    - Reject empty or dotfile-only names
    - Reject dangerous extensions
    - Reject extensions not in the allowlist
    - Reject control characters
    - Limit length to 200 chars
    """
    if not filename:
        raise FileServiceError(400, "filename is required")
    name = Path(filename).name
    if not name or name.startswith("."):
        raise FileServiceError(400, "filename is invalid")
    # Reject control characters
    if any(ord(c) < 0x20 for c in name):
        raise FileServiceError(400, "filename contains control characters")

    ext = Path(name).suffix.lower()
    if ext in DANGEROUS_EXTENSIONS:
        raise FileServiceError(400, f"extension {ext} is dangerous and not allowed")
    if ext and ext not in ALLOWED_EXTENSIONS:
        raise FileServiceError(400, f"extension {ext} is not in allowed list")
    if not ext:
        ext = ".txt"

    # Truncate stem if needed; keep extension intact
    stem = Path(name).stem[:200]
    safe = f"{stem}{ext}"
    return safe


def _human_size(size: int) -> str:
    """Bytes → human string. 0 → '0 B'."""
    size = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def _relative(path: Path, root: Path) -> str:
    """Path → POSIX-style string relative to root. Empty for root itself."""
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return ""
    return "" if rel == Path(".") else rel.as_posix()


def _breadcrumbs(path: Path, root: Path) -> list[dict]:
    """Build a breadcrumb trail from root to the given path."""
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return [{"name": "files", "path": ""}]
    crumbs = [{"name": "files", "path": ""}]
    if not rel.parts:
        return crumbs
    acc: list[str] = []
    for part in rel.parts:
        acc.append(part)
        crumbs.append({"name": part, "path": "/".join(acc)})
    return crumbs


def list_subdirs_for_cleanup(uid: str) -> list[str]:
    """Diagnostic helper: list all subdirectories (for tests)."""
    root = user_files_root(uid)
    return sorted([p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_dir()])
