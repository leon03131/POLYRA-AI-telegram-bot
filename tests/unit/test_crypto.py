"""Unit-тесты интерфейса app.security.crypto (реализация — отдельный модуль)."""

import pytest
from cryptography.fernet import Fernet

from app.security.crypto import CryptoBox, mask_secret


def test_roundtrip_with_passphrase() -> None:
    box = CryptoBox("test-master-key-123")
    plaintext = "sk-super-secret-token"
    token = box.encrypt(plaintext)
    assert isinstance(token, str)
    assert token != plaintext
    assert box.decrypt(token) == plaintext


def test_roundtrip_with_fernet_key() -> None:
    box = CryptoBox(Fernet.generate_key().decode())
    plaintext = "another-secret"
    token = box.encrypt(plaintext)
    assert isinstance(token, str)
    assert token != plaintext
    assert box.decrypt(token) == plaintext


def test_decrypt_corrupted_token_raises_value_error() -> None:
    box = CryptoBox("test-master-key-123")
    with pytest.raises(ValueError):
        box.decrypt("not-a-valid-token")


def test_mask_secret_long() -> None:
    assert mask_secret("abcdefghijklmnop") == "abcd...mnop"


def test_mask_secret_short() -> None:
    assert mask_secret("short") == "***"


def test_mask_secret_empty() -> None:
    assert mask_secret("") == ""


def test_mask_secret_api_key() -> None:
    assert mask_secret("sk-1234567890abcdef") == "sk-1...cdef"
