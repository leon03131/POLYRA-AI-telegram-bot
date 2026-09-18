"""HTTP-уровень Mini App API без живой БД: /health, auth-guards, сериализация.

Session factory здесь "сломанная" (RuntimeError при открытии сессии): проверяем
только пути, которые либо не требуют БД, либо должны вернуть 500 без traceback.
DB-backed интеграционные тесты (реальная PostgreSQL) — отдельный прогон,
включаемый через RUN_API_INTEGRATION=1 (не часть unit-набора).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import create_app
from app.api.dependencies import get_current_user
from app.config import Settings
from app.db.models import User
from app.llm.registry import default_registry
from app.security.crypto import CryptoBox
from app.services.access import GrantView, evaluate_access

BOT_TOKEN = "123:abc"
MASTER_KEY = "unit-test-master-key"
OWNER_TG_ID = 795063564


class _BrokenSessionFactory:
    """Фабрика, падающая при открытии сессии: юнит-тесты без PostgreSQL."""

    def __call__(self) -> object:
        raise RuntimeError("no database in unit tests")


def _make_app() -> FastAPI:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]  # hermetic: не читать реальный .env
        bot_token=BOT_TOKEN,
        owner_telegram_id=OWNER_TG_ID,
        master_encryption_key=MASTER_KEY,
        miniapp_dist="nonexistent-miniapp-dist",
    )
    return create_app(
        settings=settings,
        session_factory=_BrokenSessionFactory(),
        crypto=CryptoBox(MASTER_KEY),
        registry=default_registry(),
    )


def _client(app: FastAPI) -> AsyncClient:
    """ASGI-клиент; 500 отдаются как ответ, а не исключение (raise_app_exceptions=False)."""
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )


def _make_init_data(fields: dict[str, str]) -> str:
    data = dict(fields)
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), digestmod=hashlib.sha256)
    data["hash"] = hmac.new(
        secret_key.digest(), data_check_string.encode(), digestmod=hashlib.sha256
    ).hexdigest()
    return urlencode(data)


def _valid_init_data() -> str:
    user = {"id": OWNER_TG_ID, "first_name": "Owner", "username": "owner"}
    return _make_init_data(
        {
            "auth_date": str(int(datetime.now(UTC).timestamp())),
            "user": json.dumps(user, separators=(",", ":")),
        }
    )


def _fake_user(*, is_owner: bool) -> User:
    user = User(
        telegram_user_id=OWNER_TG_ID if is_owner else 111,
        username="fake",
        first_name="Fake",
    )
    user.is_owner = is_owner
    user.status = "active"
    return user


# --- без БД --------------------------------------------------------------------


async def test_health_ok() -> None:
    async with _client(_make_app()) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


async def test_auth_invalid_init_data_401() -> None:
    async with _client(_make_app()) as client:
        response = await client.post("/api/auth/telegram", json={"init_data": "garbage=1"})
    assert response.status_code == 401


async def test_auth_valid_init_data_without_db_500() -> None:
    """Валидная initData проходит проверку подписи, затем БД падает → 500 без traceback."""
    async with _client(_make_app()) as client:
        response = await client.post("/api/auth/telegram", json={"init_data": _valid_init_data()})
    assert response.status_code == 500
    assert response.json() == {"detail": "internal error"}


async def test_me_without_authorization_401() -> None:
    async with _client(_make_app()) as client:
        response = await client.get("/api/me")
    assert response.status_code == 401


async def test_me_with_garbage_token_401() -> None:
    async with _client(_make_app()) as client:
        response = await client.get("/api/me", headers={"Authorization": "Bearer not-a-token"})
    assert response.status_code == 401


async def test_admin_without_authorization_401() -> None:
    async with _client(_make_app()) as client:
        response = await client.get("/api/admin/users")
    assert response.status_code == 401


# --- через dependency_overrides (без БД) -----------------------------------------


def _override_user(app: FastAPI, *, is_owner: bool) -> None:
    user = _fake_user(is_owner=is_owner)
    grant = GrantView(
        status="active",
        expires_at=None,
        requests_per_day=100,
        token_limit=50000,
        max_concurrent_generations=3,
        can_use_web_search=True,
        can_use_memory=True,
    )
    permissions = evaluate_access(
        is_owner=is_owner,
        user_status="active",
        grant=grant,
        allowed_models=None,
        now=datetime.now(UTC),
    )
    app.dependency_overrides[get_current_user] = lambda: (user, permissions)


async def test_me_serialization_with_override() -> None:
    app = _make_app()
    _override_user(app, is_owner=False)
    async with _client(app) as client:
        response = await client.get("/api/me")
    assert response.status_code == 200
    body = response.json()
    assert body["user"] == {"telegram_user_id": 111, "username": "fake", "first_name": "Fake"}
    assert body["is_owner"] is False
    assert body["permissions"]["allowed_models"] is None
    assert body["permissions"]["max_concurrent_generations"] == 3
    assert body["permissions"]["requests_per_day"] == 100
    assert body["permissions"]["token_limit"] == 50000


async def test_models_excludes_internal_and_probe_modes() -> None:
    app = _make_app()
    _override_user(app, is_owner=True)
    async with _client(app) as client:
        response = await client.get("/api/models")
    assert response.status_code == 200
    models = response.json()["models"]
    model_ids = {model["model_id"] for model in models}
    assert "gemini-3.5-flash-lite" not in model_ids  # internal_only
    kimi = next(model for model in models if model["model_id"] == "kimi-k3")
    # probe 2026-09-18 подтвердил OFF на MS endpoint — режим доступен в UI
    assert "off" in kimi["thinking_modes"]
    # probe_required-режимы скрываются до подтверждения (на текущий момент таких нет)
    for model in models:
        assert not (set(model["thinking_modes"]) & set(model.get("probe_required") or ()))
    for model in models:
        assert set(model) == {
            "model_id",
            "display_name",
            "provider",
            "supports_images",
            "thinking_modes",
            "default_thinking",
        }


async def test_admin_forbidden_for_non_owner() -> None:
    app = _make_app()
    _override_user(app, is_owner=False)
    async with _client(app) as client:
        response = await client.get("/api/admin/users")
    assert response.status_code == 403
