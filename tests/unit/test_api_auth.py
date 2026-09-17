"""Unit-тесты app.api.auth: валидация Telegram initData и session tokens."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest

from app.api.auth import create_session_token, validate_init_data, verify_session_token

BOT_TOKEN = "123:abc"
SECRET = "unit-test-master-key"
NOW = datetime(2026, 9, 18, 12, 0, 0, tzinfo=UTC)


def _make_init_data(
    fields: dict[str, str], *, bot_token: str = BOT_TOKEN, tamper: dict[str, str] | None = None
) -> str:
    """Собрать валидно подписанную initData-строку (алгоритм Telegram Mini Apps)."""
    data = dict(fields)
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), digestmod=hashlib.sha256)
    data["hash"] = hmac.new(
        secret_key.digest(), data_check_string.encode(), digestmod=hashlib.sha256
    ).hexdigest()
    if tamper:
        data.update(tamper)  # изменение ПОСЛЕ подписи
    return urlencode(data)


def _default_fields() -> dict[str, str]:
    user = {"id": 795063564, "first_name": "Test", "username": "tester", "language_code": "ru"}
    return {
        "auth_date": str(int(NOW.timestamp())),
        "query_id": "AAHdZ3k-AAAAAN1neT4abcde",
        "user": json.dumps(user, separators=(",", ":")),
    }


# --- validate_init_data ------------------------------------------------------


def test_validate_accepts_valid_init_data() -> None:
    data = validate_init_data(_make_init_data(_default_fields()), BOT_TOKEN, now=NOW)
    assert data["auth_date"] == str(int(NOW.timestamp()))
    assert data["user"]["id"] == 795063564
    assert data["user"]["username"] == "tester"
    assert "hash" not in data


def test_validate_rejects_tampered_field() -> None:
    raw = _make_init_data(_default_fields(), tamper={"auth_date": str(int(NOW.timestamp()) - 10)})
    with pytest.raises(ValueError, match="invalid signature"):
        validate_init_data(raw, BOT_TOKEN, now=NOW)


def test_validate_rejects_wrong_bot_token() -> None:
    raw = _make_init_data(_default_fields())
    with pytest.raises(ValueError, match="invalid signature"):
        validate_init_data(raw, "999:wrong", now=NOW)


def test_validate_rejects_stale_auth_date() -> None:
    stale = NOW - timedelta(hours=25)
    fields = {**_default_fields(), "auth_date": str(int(stale.timestamp()))}
    with pytest.raises(ValueError, match="stale"):
        validate_init_data(_make_init_data(fields), BOT_TOKEN, now=NOW)


def test_validate_accepts_fresh_within_max_age() -> None:
    recent = NOW - timedelta(hours=23, minutes=59)
    fields = {**_default_fields(), "auth_date": str(int(recent.timestamp()))}
    data = validate_init_data(_make_init_data(fields), BOT_TOKEN, now=NOW)
    assert data["user"]["id"] == 795063564


def test_validate_rejects_missing_hash() -> None:
    with pytest.raises(ValueError, match="hash"):
        validate_init_data(urlencode(_default_fields()), BOT_TOKEN, now=NOW)


def test_validate_rejects_missing_auth_date() -> None:
    fields = _default_fields()
    del fields["auth_date"]
    with pytest.raises(ValueError, match="auth_date"):
        validate_init_data(_make_init_data(fields), BOT_TOKEN, now=NOW)


def test_validate_rejects_garbage_string() -> None:
    with pytest.raises(ValueError):
        validate_init_data("not-a-query-string", BOT_TOKEN, now=NOW)
    with pytest.raises(ValueError):
        validate_init_data("", BOT_TOKEN, now=NOW)


def test_validate_rejects_bad_user_json() -> None:
    fields = {**_default_fields(), "user": "{broken"}
    with pytest.raises(ValueError, match="user"):
        validate_init_data(_make_init_data(fields), BOT_TOKEN, now=NOW)


# --- session tokens ------------------------------------------------------------


def test_session_token_roundtrip() -> None:
    token = create_session_token(795063564, ttl_seconds=900, secret=SECRET)
    assert verify_session_token(token, secret=SECRET) == 795063564


def test_session_token_expired() -> None:
    token = create_session_token(42, ttl_seconds=60, secret=SECRET)
    future = datetime.now(UTC) + timedelta(seconds=61)
    with pytest.raises(ValueError, match="expired"):
        verify_session_token(token, secret=SECRET, now=future)


def test_session_token_forged_signature() -> None:
    token = create_session_token(42, ttl_seconds=900, secret=SECRET)
    body, _ = token.split(".")
    forged = hmac.new(b"wrong-secret", body.encode("ascii"), digestmod=hashlib.sha256)
    with pytest.raises(ValueError, match="invalid signature"):
        verify_session_token(f"{body}.{forged.hexdigest()}", secret=SECRET, now=NOW)


def test_session_token_wrong_secret() -> None:
    token = create_session_token(42, ttl_seconds=900, secret=SECRET)
    with pytest.raises(ValueError, match="invalid signature"):
        verify_session_token(token, secret="another-secret", now=NOW)


@pytest.mark.parametrize("token", ["", "abc", "a.b.c", "onlyonepart.", ".sig"])
def test_session_token_malformed(token: str) -> None:
    with pytest.raises(ValueError):
        verify_session_token(token, secret=SECRET, now=NOW)
