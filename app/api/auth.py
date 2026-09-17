"""Mini App аутентификация: валидация Telegram initData и server session tokens.

Алгоритм initData (docs/vendor/TELEGRAM_MINI_APPS.md):
    data_check_string = все поля кроме hash, отсортированные по key, "k=v", через "\\n"
    secret_key        = HMAC_SHA256(key="WebAppData", msg=bot_token)
    calculated        = HEX(HMAC_SHA256(key=secret_key, msg=data_check_string))
    valid             = compare_digest(calculated, hash)

Session token: ``base64url(json{"sub": str(tg_id), "exp": unix}).hmac_sha256_hex(secret)``
(без padding в base64url), подпись считается по ASCII-представлению payload-части.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl

_INIT_DATA_MAX_AGE_SECONDS = 86400  # 24ч — свежесть auth_date


def _as_aware_utc(dt: datetime) -> datetime:
    """Naive datetime трактуем как UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = _INIT_DATA_MAX_AGE_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Проверить подпись и свежесть initData; вернуть поля (user — распарсенный dict).

    Raises:
        ValueError: невалидная строка, нет hash/auth_date, подпись не сошлась,
            данные устарели, user — не JSON.
    """
    if not init_data or not bot_token:
        raise ValueError("init_data and bot_token must not be empty")
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise ValueError("init_data is not a valid query string") from exc
    data = dict(pairs)

    received_hash = data.pop("hash", None)
    if not received_hash:
        raise ValueError("hash is missing")
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), digestmod=hashlib.sha256)
    calculated_hash = hmac.new(
        secret_key.digest(), data_check_string.encode(), digestmod=hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise ValueError("invalid signature")

    auth_date_raw = data.get("auth_date")
    if auth_date_raw is None:
        raise ValueError("auth_date is missing")
    try:
        auth_date = int(auth_date_raw)
    except ValueError as exc:
        raise ValueError("auth_date is not an integer") from exc
    current = _as_aware_utc(now) if now is not None else datetime.now(UTC)
    _check_auth_date(auth_date, current, max_age_seconds)

    user_raw = data.get("user")
    if user_raw is not None:
        try:
            data["user"] = json.loads(user_raw)
        except ValueError as exc:
            raise ValueError("user is not valid JSON") from exc
    return data


def _check_auth_date(auth_date: int, now: datetime, max_age_seconds: int) -> None:
    """Свежесть initData: не старше max_age и не из будущего (допуск 60 с)."""
    age = (now - datetime.fromtimestamp(auth_date, UTC)).total_seconds()
    if age > max_age_seconds:
        raise ValueError("init_data is stale")
    if age < -60:
        raise ValueError("auth_date is in the future")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _signing_key(secret: str) -> bytes:
    """Отдельный ключ подписи сессий (не переиспользуем master key напрямую)."""
    return hmac.new(secret.encode(), b"aibot-session-signing-v1", digestmod=hashlib.sha256).digest()


def create_session_token(telegram_user_id: int, *, ttl_seconds: int, secret: str) -> str:
    """Подписанный session token ``payload.signature`` с exp = now + ttl."""
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    payload = {"sub": str(telegram_user_id), "exp": int(expires_at.timestamp())}
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_signing_key(secret), body.encode("ascii"), digestmod=hashlib.sha256)
    return f"{body}.{signature.hexdigest()}"


def verify_session_token(token: str, *, secret: str, now: datetime | None = None) -> int:
    """Проверить подпись/exp токена; вернуть telegram_user_id.

    Raises:
        ValueError: токен битый, подпись не сошлась или токен истёк.
    """
    parts = token.split(".")
    if len(parts) != 2:
        raise ValueError("malformed session token")
    body, signature = parts
    expected = hmac.new(_signing_key(secret), body.encode("ascii"), digestmod=hashlib.sha256)
    if not hmac.compare_digest(expected.hexdigest(), signature):
        raise ValueError("invalid signature")
    try:
        payload = json.loads(_b64url_decode(body))
    except ValueError as exc:
        raise ValueError("malformed session payload") from exc
    if not isinstance(payload, dict):
        raise ValueError("malformed session payload")
    sub = payload.get("sub")
    exp = payload.get("exp")
    if not isinstance(sub, str) or not isinstance(exp, (int, float)):
        raise ValueError("malformed session payload")
    current = _as_aware_utc(now) if now is not None else datetime.now(UTC)
    if datetime.fromtimestamp(exp, UTC) <= current:
        raise ValueError("session token expired")
    try:
        return int(sub)
    except ValueError as exc:
        raise ValueError("malformed session subject") from exc
