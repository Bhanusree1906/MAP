"""Encryption at rest for secret columns (git/Jira/GitHub/Salesforce/Codex
tokens) - see scripts/encrypt-stored-secrets.py for the one-time migration
that encrypts pre-existing plaintext rows, and EncryptedText below for
transparent encrypt-on-write / decrypt-on-read at the ORM layer so callers
never handle ciphertext directly.

SECRET_ENCRYPTION_KEY is any passphrase (not a raw Fernet key) - it's
SHA-256 hashed and base64 urlsafe-encoded into a valid 32-byte Fernet key,
so operators can set a normal secret string rather than generating a
Fernet key by hand. Losing this value makes every already-encrypted row
permanently unreadable; there is no recovery path other than the
underlying credential being re-authorized from scratch.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import Text, TypeDecorator

_PREFIX = "enc:v1:"

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = os.environ["SECRET_ENCRYPTION_KEY"]
        derived = base64.urlsafe_b64encode(hashlib.sha256(key.encode("utf-8")).digest())
        _fernet = Fernet(derived)
    return _fernet


def is_encrypted(value: str) -> bool:
    return value.startswith(_PREFIX)


def encrypt_value(value: str) -> str:
    if not value:
        return value
    if is_encrypted(value):
        return value
    token = _get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")
    return _PREFIX + token


def decrypt_value(value: str) -> str:
    if not value or not is_encrypted(value):
        return value
    token = value[len(_PREFIX) :]
    try:
        return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError(
            "Stored secret could not be decrypted - SECRET_ENCRYPTION_KEY may be wrong, "
            "unset, or the value was encrypted under a different key."
        ) from exc


class EncryptedText(TypeDecorator):
    """Text column that encrypts on write and decrypts on read, transparent
    to every caller - see scripts/encrypt-stored-secrets.py's SECRET_COLUMNS
    for which columns use this.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return encrypt_value(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return decrypt_value(value)
