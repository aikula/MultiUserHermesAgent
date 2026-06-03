"""Encrypt/decrypt user secrets at rest using Fernet symmetric encryption.

Key derivation: PBKDF2-HMAC-SHA256 from JWT_SECRET + user UID.
This ensures each user's secrets are encrypted with a unique key derived from
the application secret + user identity, without storing the key.
"""
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Application secret used for key derivation (from env)
_APP_SECRET = os.environ.get("WEBAPP_INTERNAL_SECRET", os.environ.get("JWT_SECRET", ""))

# Prefix for encrypted values to distinguish from plaintext
_ENC_PREFIX = "enc:v1:"


def _derive_key(uid: str) -> bytes:
    """Derive a Fernet key from app secret + user UID."""
    if not _APP_SECRET:
        raise RuntimeError(
            "Encryption key not configured. "
            "Set WEBAPP_INTERNAL_SECRET or JWT_SECRET environment variable."
        )
    key_material = _APP_SECRET.encode() + uid.encode()

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=hashlib.sha256(uid.encode()).digest(),
        iterations=100_000,
    )
    key = kdf.derive(key_material)
    # Fernet key must be 32 bytes urlsafe base64 encoded
    import base64
    return base64.urlsafe_b64encode(key)


def encrypt(plaintext: str, uid: str) -> str:
    """Encrypt a string value for a specific user. Returns prefixed encrypted string."""
    if not plaintext:
        return ""
    # Already encrypted?
    if plaintext.startswith(_ENC_PREFIX):
        return plaintext
    f = Fernet(_derive_key(uid))
    encrypted = f.encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _ENC_PREFIX + encrypted


def decrypt(ciphertext: str, uid: str) -> str:
    """Decrypt a value. Returns plaintext. If not encrypted, returns as-is (migration)."""
    if not ciphertext:
        return ""
    # Not encrypted? Return as-is (backwards compatibility)
    if not ciphertext.startswith(_ENC_PREFIX):
        return ciphertext
    try:
        f = Fernet(_derive_key(uid))
        token = ciphertext[len(_ENC_PREFIX):]
        return f.decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, Exception):
        return ciphertext


def is_encryption_available() -> bool:
    """Check if encryption key is configured."""
    return bool(_APP_SECRET)


def is_encrypted(value: str) -> bool:
    """Check if a value is encrypted."""
    return bool(value) and value.startswith(_ENC_PREFIX)


def migrate_plaintext_to_encrypted(plaintext: str, uid: str) -> str:
    """Encrypt a plaintext value. Used during migration."""
    if not plaintext or is_encrypted(plaintext):
        return plaintext
    return encrypt(plaintext, uid)
