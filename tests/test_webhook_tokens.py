"""Webhook token encryption. Pure — no database, no network."""

from __future__ import annotations

from app.discord.tokens import decrypt_token, encrypt_token, is_encrypted


def test_no_key_stores_plaintext():
    assert encrypt_token("tok123", None) == "tok123"
    assert decrypt_token("tok123", None) == "tok123"


def test_roundtrip_with_key():
    stored = encrypt_token("secret-token", "test-key")
    assert stored is not None
    assert is_encrypted(stored)
    assert "secret-token" not in stored
    assert decrypt_token(stored, "test-key") == "secret-token"


def test_each_encryption_is_unique():
    a = encrypt_token("same", "k")
    b = encrypt_token("same", "k")
    assert a != b
    assert decrypt_token(a, "k") == decrypt_token(b, "k") == "same"


def test_legacy_plaintext_survives_enabling_the_key():
    assert decrypt_token("plain-legacy", "new-key") == "plain-legacy"


def test_wrong_key_fails_closed():
    stored = encrypt_token("tok", "right-key")
    assert decrypt_token(stored, "wrong-key") is None


def test_encrypted_without_key_fails_closed():
    stored = encrypt_token("tok", "some-key")
    assert decrypt_token(stored, None) is None


def test_none_passthrough():
    assert encrypt_token(None, "k") is None
    assert decrypt_token(None, "k") is None
    assert is_encrypted(None) is False
