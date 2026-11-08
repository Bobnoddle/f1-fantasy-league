"""Discord webhook token protection.

The ``webhook_token`` column holds a bearer credential: anyone holding it can
post into the league's channel. It must not sit in plaintext next to a database
backup on hosted mode, and it must never appear in logs.

When ``TOKEN_ENCRYPTION_KEY`` is set, tokens are Fernet-encrypted before write
and decrypted on read. The key is any random string (``openssl rand -hex 32``);
we derive the 32-byte Fernet key via SHA-256 so operators never handle raw
base64. When no key is set (self-host default) tokens are stored as-is — an
absent key is a valid deployment, not an error.

Stored format is ``enc:v1:<fernet-token>``. Anything else is treated as a
legacy plaintext row and returned as-is, so enabling the key later does not
break leagues created before it. A row that is encrypted but cannot be
decrypted (rotated-away key) fails closed to None rather than raising mid-request.
"""

from __future__ import annotations

import base64
import hashlib

PREFIX = "enc:v1:"


def _fernet(key: str):
    from cryptography.fernet import Fernet

    raw = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
    return Fernet(raw)


def is_encrypted(stored: str | None) -> bool:
    return bool(stored) and str(stored).startswith(PREFIX)


def encrypt_token(plaintext: str | None, key: str | None) -> str | None:
    """Encrypt before write. No key → store as-is (self-host)."""
    if plaintext is None:
        return None
    if not key:
        return plaintext
    if plaintext.startswith(PREFIX):
        return plaintext
    return PREFIX + _fernet(key).encrypt(plaintext.encode()).decode()


def decrypt_token(stored: str | None, key: str | None) -> str | None:
    """Decrypt for use. Legacy plaintext passes through; undecryptable → None."""
    if stored is None:
        return None
    if not stored.startswith(PREFIX):
        return stored
    if not key:
        return None
    try:
        return _fernet(key).decrypt(stored[len(PREFIX) :].encode()).decode()
    except Exception:
        return None
