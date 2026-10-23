"""Unit tests for database.crypto - EncryptedText round-trips, legacy
plaintext passthrough, and prefix idempotence."""

from __future__ import annotations

import pytest

from database.crypto import (
    EncryptedText,
    decrypt_value,
    encrypt_value,
    is_encrypted,
)


def test_round_trip() -> None:
    encrypted = encrypt_value("super-secret-token")
    assert is_encrypted(encrypted)
    assert decrypt_value(encrypted) == "super-secret-token"


def test_legacy_plaintext_passthrough_on_read() -> None:
    legacy = "old-plaintext-value"
    assert not is_encrypted(legacy)
    # Reading an un-migrated value returns it unchanged instead of failing.
    assert decrypt_value(legacy) == legacy


def test_encrypt_is_idempotent_for_already_encrypted_values() -> None:
    once = encrypt_value("value")
    assert encrypt_value(once) == once


def test_encrypt_empty_string_untouched() -> None:
    assert encrypt_value("") == ""


def test_wrong_key_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    import database.crypto as crypto

    encrypted = crypto.encrypt_value("payload")
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", "a-different-key")
    monkeypatch.setattr(crypto, "_fernet", None)
    fresh = importlib.reload(crypto)
    with pytest.raises(ValueError, match="could not be decrypted"):
        fresh.decrypt_value(encrypted)
    importlib.reload(crypto)  # restore for other tests


def test_type_decorator_binds_and_resolves() -> None:
    column = EncryptedText()
    stored = column.process_bind_param("plain", None)
    assert stored != "plain" and is_encrypted(stored)
    assert column.process_result_value(stored, None) == "plain"
    assert column.process_bind_param(None, None) is None
    assert column.process_result_value(None, None) is None
