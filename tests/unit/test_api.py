"""HTTP-уровень Mini App API без живой БД: /health, auth-guards, сериализация.

Session factory здесь "сломанная" (RuntimeError при открытии сессии): проверяем
только пути, которые либо не требуют БД, либо должны вернуть 500 без traceback.
DB-backed интеграционные тесты (реальная PostgreSQL) — отдельный прогон,
включаемый через RUN_API_INTEGRATION=1 (не часть unit-набора).

FIX V2 (2026-09-24): owner-gate numeric-only (A04), пагинация chats/memory,
audit offset/action, новые admin-роуты (models/memory/gemini usage & counters/
alibaba smoke) — здесь проверяется только регистрация роутов и gate (500 = gate
пройден, БД сломана; 403 = gate отклонил). Бизнес-логика за ними — integration.
Чистая логика сервисов (credentials A14, effective system settings A13) —
через monkeypatch репозиториев, без БД.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import create_app
from app.api.dependencies import get_current_user, get_db_session
from app.api.routes import me as me_routes
from app.config import Settings
from app.db.models import User
from app.llm.registry import default_registry
from app.security.crypto import CryptoBox
from app.services import credentials as credentials_service
from app.services import settings as settings_service
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
        _env_file=None,  # hermetic: не читать реальный .env
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


def _make_permissions(is_owner: bool) -> Any:
    grant = GrantView(
        status="active",
        expires_at=None,
        requests_per_day=100,
        token_limit=50000,
        max_concurrent_generations=3,
        can_use_web_search=True,
        can_use_memory=True,
    )
    return evaluate_access(
        is_owner=is_owner,
        user_status="active",
        grant=grant,
        allowed_models=None,
        now=datetime.now(UTC),
    )


def _override_user(app: FastAPI, *, is_owner: bool, user: User | None = None) -> User:
    """Подменить get_current_user; возвращает подставного пользователя."""
    if user is None:
        user = _fake_user(is_owner=is_owner)
    app.dependency_overrides[get_current_user] = lambda: (user, _make_permissions(is_owner))
    return user


def _override_probe_capabilities(
    monkeypatch: pytest.MonkeyPatch, app: FastAPI, probe: dict[str, dict[str, Any]]
) -> None:
    """Подменить DB-сессию (фейк) и probe-capabilities для /api/models (A27)."""

    async def _fake_session() -> Any:
        yield None

    app.dependency_overrides[get_db_session] = _fake_session

    async def _fake_probe(session: Any) -> dict[str, dict[str, Any]]:
        return probe

    monkeypatch.setattr(me_routes, "get_probe_capabilities", _fake_probe)


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


async def test_models_excludes_internal_and_probe_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app()
    _override_user(app, is_owner=True)
    _override_probe_capabilities(monkeypatch, app, {})  # probe-записей нет
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
            "probe_at",
        }
        assert model["probe_at"] is None  # без probe-записей — None


# --- A27: probe → runtime wiring (capability_probe:<model_id> в /api/models) ----

_PROBE_AT = datetime.now(UTC).isoformat(timespec="seconds")


async def test_models_probe_record_filters_thinking_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Свежая probe-запись с accepted_thinking фильтрует режимы по факту acceptance."""
    app = _make_app()
    _override_user(app, is_owner=True)
    _override_probe_capabilities(
        monkeypatch,
        app,
        {
            "kimi-k3": {
                "accepted_thinking": ["off", "high"],
                "text_ok": True,
                "at": _PROBE_AT,
                "endpoint": "alibaba",
            }
        },
    )
    async with _client(app) as client:
        response = await client.get("/api/models")
    assert response.status_code == 200
    models = {model["model_id"]: model for model in response.json()["models"]}
    kimi = models["kimi-k3"]
    # порядок — как в registry thinking_modes, фильтр по accepted
    assert kimi["thinking_modes"] == ["off", "high"]
    assert kimi["probe_at"] == _PROBE_AT
    # модель без probe-записи — registry как есть
    qwen = models["qwen3.8-flash"]
    assert qwen["thinking_modes"] == ["off", "low", "medium", "max"]
    assert qwen["probe_at"] is None


async def test_models_probe_empty_accepted_falls_back_to_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """accepted_thinking=[] — фильтрация НЕ применяется (registry как есть)."""
    app = _make_app()
    _override_user(app, is_owner=True)
    _override_probe_capabilities(
        monkeypatch,
        app,
        {
            "kimi-k3": {
                "accepted_thinking": [],
                "text_ok": False,
                "at": _PROBE_AT,
                "endpoint": "alibaba",
            }
        },
    )
    async with _client(app) as client:
        response = await client.get("/api/models")
    assert response.status_code == 200
    models = {model["model_id"]: model for model in response.json()["models"]}
    kimi = models["kimi-k3"]
    assert kimi["thinking_modes"] == ["off", "low", "high", "max"]
    assert kimi["probe_at"] == _PROBE_AT  # свежая запись есть — время показываем


async def test_models_stale_probe_record_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Устаревшая запись отбрасывается get_probe_capabilities → registry как есть."""
    app = _make_app()
    _override_user(app, is_owner=True)
    # get_probe_capabilities уже отфильтровал stale-записи (TTL в сервисе) — роут
    # получает пустой dict; проверяем контракт роута на этом входе.
    _override_probe_capabilities(monkeypatch, app, {})
    async with _client(app) as client:
        response = await client.get("/api/models")
    assert response.status_code == 200
    models = {model["model_id"]: model for model in response.json()["models"]}
    assert models["kimi-k3"]["thinking_modes"] == ["off", "low", "high", "max"]
    assert models["kimi-k3"]["probe_at"] is None


class _FakeProbeRowRepository:
    """Подмена SystemSettingRepository для get_probe_capabilities: get_all по строкам."""

    def __init__(self, session: Any, rows: list[Any]) -> None:
        self._rows = rows

    async def get_all(self) -> list[Any]:
        return self._rows


def _probe_row(key: str, value: Any) -> Any:
    return SimpleNamespace(key=key, value=value)


async def test_get_probe_capabilities_filters_stale_and_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TTL-фильтр: свежие записи возвращаются по model_id, stale/битые — нет."""
    fresh_at = datetime.now(UTC).isoformat(timespec="seconds")
    stale_at = (
        datetime.now(UTC) - timedelta(seconds=settings_service.CAPABILITY_TTL_SECONDS + 60)
    ).isoformat(timespec="seconds")
    rows = [
        _probe_row(
            "capability_probe:kimi-k3",
            {"accepted_thinking": ["high"], "text_ok": True, "at": fresh_at},
        ),
        # stale (старше TTL) — отбрасывается
        _probe_row(
            "capability_probe:qwen3.8-flash",
            {"accepted_thinking": ["off"], "text_ok": True, "at": stale_at},
        ),
        _probe_row("capability_probe:broken", "not-a-dict"),  # value не dict
        _probe_row("capability_probe:no-at", {"accepted_thinking": ["low"]}),  # нет at
        _probe_row("default_model", {"at": fresh_at}),  # чужой ключ
    ]
    monkeypatch.setattr(
        settings_service,
        "SystemSettingRepository",
        lambda session: _FakeProbeRowRepository(session, rows),
    )
    result = await settings_service.get_probe_capabilities(None)
    assert set(result) == {"kimi-k3"}
    assert result["kimi-k3"]["accepted_thinking"] == ["high"]


async def test_admin_forbidden_for_non_owner() -> None:
    app = _make_app()
    _override_user(app, is_owner=False)
    async with _client(app) as client:
        response = await client.get("/api/admin/users")
    assert response.status_code == 403


# --- A04: owner identity — только numeric telegram_user_id ---------------------


async def test_require_owner_ignores_stale_is_owner_flag() -> None:
    """is_owner=True в БД без совпадения numeric id НЕ даёт admin-доступ (stale privilege)."""
    app = _make_app()
    user = _fake_user(is_owner=False)  # telegram_user_id=111 != OWNER_TG_ID
    user.is_owner = True  # stale флаг (например, после смены OWNER_TELEGRAM_ID)
    assert user.telegram_user_id != OWNER_TG_ID
    _override_user(app, is_owner=False, user=user)
    async with _client(app) as client:
        response = await client.get("/api/admin/users")
    assert response.status_code == 403


async def test_require_owner_numeric_id_passes_without_db_flag() -> None:
    """numeric id совпадает, флаг is_owner=False — gate проходит (500 = упал на БД, не на gate)."""
    app = _make_app()
    user = _fake_user(is_owner=True)  # telegram_user_id == OWNER_TG_ID
    user.is_owner = False  # флаг не выставлен — не важен для gate
    _override_user(app, is_owner=True, user=user)
    async with _client(app) as client:
        response = await client.get("/api/admin/users")
    assert response.status_code == 500  # owner-gate пройден, БД сломана
    assert response.json() == {"detail": "internal error"}


async def test_me_is_owner_ignores_stale_db_flag() -> None:
    """GET /api/me: is_owner вычисляется по numeric id, а не по флагу БД."""
    app = _make_app()
    user = _fake_user(is_owner=False)
    user.is_owner = True  # stale флаг
    _override_user(app, is_owner=False, user=user)
    async with _client(app) as client:
        response = await client.get("/api/me")
    assert response.status_code == 200
    assert response.json()["is_owner"] is False


# --- V2: регистрация новых admin-роутов и owner-gate ----------------------------

_ADMIN_V2_ENDPOINTS = [
    ("GET", "/api/admin/models"),
    ("GET", "/api/admin/memory?telegram_user_id=111&limit=5&offset=0"),
    ("GET", "/api/admin/gemini/usage"),
    ("POST", "/api/admin/gemini/reset-counters"),
    ("POST", "/api/admin/providers/alibaba/smoke"),
    ("GET", "/api/admin/audit?limit=10&offset=5&action=access_granted"),
]


@pytest.mark.parametrize("method,path", _ADMIN_V2_ENDPOINTS)
async def test_admin_v2_routes_forbidden_for_non_owner(method: str, path: str) -> None:
    app = _make_app()
    _override_user(app, is_owner=False)
    async with _client(app) as client:
        response = await client.request(method, path)
    assert response.status_code == 403


@pytest.mark.parametrize("method,path", _ADMIN_V2_ENDPOINTS)
async def test_admin_v2_routes_registered_and_owner_gated(method: str, path: str) -> None:
    """Owner проходит gate → 500 (сломанная БД), т.е. роут зарегистрирован и не 404."""
    app = _make_app()
    _override_user(app, is_owner=True)
    async with _client(app) as client:
        response = await client.request(method, path)
    assert response.status_code == 500
    assert response.json() == {"detail": "internal error"}


# --- V2: сериализация чата (A01/A25) --------------------------------------------


def test_chat_out_includes_system_prompt_override_and_string_id() -> None:
    from app.api.routes.chats import _chat_out

    chat = SimpleNamespace(
        id=uuid.uuid4(),
        title="t",
        model_id="kimi-k3",
        thinking_setting=None,
        web_mode="auto",
        memory_enabled=True,
        system_prompt_override="Будь краток",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        archived_at=None,
    )
    out = _chat_out(chat, current_chat_id=None)
    assert isinstance(out["id"], str)
    assert out["system_prompt_override"] == "Будь краток"
    assert out["is_current"] is False


# --- A14: credentials disabled = абсолютный запрет ------------------------------


class _FakeCredentialRepository:
    """Подмена ProviderCredentialRepository: фиксированная запись или None."""

    def __init__(self, session: Any, credential: Any) -> None:
        self._credential = credential

    async def get(self, provider: str) -> Any:
        return self._credential


def _patch_credential_repo(monkeypatch: pytest.MonkeyPatch, credential: Any) -> None:
    monkeypatch.setattr(
        credentials_service,
        "ProviderCredentialRepository",
        lambda session: _FakeCredentialRepository(session, credential),
    )


_CRYPTO = CryptoBox(MASTER_KEY)


async def test_credentials_absent_record_env_present_returns_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_credential_repo(monkeypatch, None)
    key = await credentials_service.get_provider_api_key(None, _CRYPTO, "alibaba", "env-key")
    assert key == "env-key"


async def test_credentials_absent_record_env_absent_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_credential_repo(monkeypatch, None)
    key = await credentials_service.get_provider_api_key(None, _CRYPTO, "alibaba", "")
    assert key is None


async def test_credentials_enabled_record_returns_db_key_ignoring_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cred = SimpleNamespace(enabled=True, encrypted_api_key=_CRYPTO.encrypt("db-key"))
    _patch_credential_repo(monkeypatch, cred)
    key = await credentials_service.get_provider_api_key(None, _CRYPTO, "alibaba", "env-key")
    assert key == "db-key"


async def test_credentials_enabled_record_without_env_returns_db_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cred = SimpleNamespace(enabled=True, encrypted_api_key=_CRYPTO.encrypt("db-key"))
    _patch_credential_repo(monkeypatch, cred)
    key = await credentials_service.get_provider_api_key(None, _CRYPTO, "alibaba", "")
    assert key == "db-key"


async def test_credentials_disabled_record_env_present_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled credential — абсолютный запрет: env fallback НЕ применяется (A14)."""
    cred = SimpleNamespace(enabled=False, encrypted_api_key=_CRYPTO.encrypt("db-key"))
    _patch_credential_repo(monkeypatch, cred)
    key = await credentials_service.get_provider_api_key(None, _CRYPTO, "alibaba", "env-key")
    assert key is None


async def test_credentials_disabled_record_env_absent_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cred = SimpleNamespace(enabled=False, encrypted_api_key=_CRYPTO.encrypt("db-key"))
    _patch_credential_repo(monkeypatch, cred)
    key = await credentials_service.get_provider_api_key(None, _CRYPTO, "alibaba", "")
    assert key is None


# --- A13: effective system settings (DB override → env fallback + валидация) -----


class _FakeSystemSettingRepository:
    """Подмена SystemSettingRepository: фиксированный dict."""

    def __init__(self, session: Any, stored: dict[str, Any]) -> None:
        self._stored = stored

    async def get_many(self, keys: Any) -> dict[str, Any]:
        return {key: value for key, value in self._stored.items() if key in set(keys)}


def _patch_system_settings_repo(monkeypatch: pytest.MonkeyPatch, stored: dict[str, Any]) -> None:
    monkeypatch.setattr(
        settings_service,
        "SystemSettingRepository",
        lambda session: _FakeSystemSettingRepository(session, stored),
    )


def _service_settings() -> Settings:
    return Settings(
        _env_file=None,
        bot_token=BOT_TOKEN,
        owner_telegram_id=OWNER_TG_ID,
        master_encryption_key=MASTER_KEY,
    )


async def test_effective_settings_db_values_override_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_system_settings_repo(
        monkeypatch,
        {
            "default_model": "kimi-k3",
            "default_thinking": None,
            "max_tool_iterations": 4,
            "context_trigger_ratio": 0.5,
            "memory_extraction_min_chars": 300,
        },
    )
    env = _service_settings()
    eff = await settings_service.get_effective_system_settings(None, env)
    assert eff.default_model == "kimi-k3"
    assert eff.default_thinking is None  # валидный explicit null в DB
    assert eff.max_tool_iterations == 4
    assert eff.context_trigger_ratio == 0.5
    assert eff.memory_extraction_min_chars == 300
    # незаданные ключи → env-дефолты
    assert eff.default_system_prompt == env.default_system_prompt
    assert eff.context_keep_recent == env.context_keep_recent
    assert eff.memory_retrieval_limit == env.memory_retrieval_limit


async def test_effective_settings_invalid_db_values_fall_back_to_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_system_settings_repo(
        monkeypatch,
        {
            "default_model": "",  # пустая строка
            "default_thinking": 123,  # не str/None
            "default_system_prompt": 42,  # не str
            "max_tool_iterations": 0,  # не > 0
            "context_keep_recent": -3,
            "context_trigger_ratio": 1.5,  # вне (0, 1)
            "memory_retrieval_limit": "five",  # не int
            "memory_extraction_min_chars": True,  # bool не считается int
        },
    )
    env = _service_settings()
    eff = await settings_service.get_effective_system_settings(None, env)
    assert eff.default_model == env.default_model
    assert eff.default_thinking is None
    assert eff.default_system_prompt == env.default_system_prompt
    assert eff.max_tool_iterations == env.max_tool_iterations
    assert eff.context_keep_recent == env.context_keep_recent
    assert eff.context_trigger_ratio == env.context_trigger_ratio
    assert eff.memory_retrieval_limit == env.memory_retrieval_limit
    assert eff.memory_extraction_min_chars == env.memory_extraction_min_chars


async def test_effective_settings_empty_store_returns_env_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_system_settings_repo(monkeypatch, {})
    env = _service_settings()
    eff = await settings_service.get_effective_system_settings(None, env)
    assert eff == settings_service.EffectiveSystemSettings(
        default_model=env.default_model,
        default_thinking=None,
        default_system_prompt=env.default_system_prompt,
        max_tool_iterations=env.max_tool_iterations,
        context_keep_recent=env.context_keep_recent,
        context_trigger_ratio=env.context_trigger_ratio,
        memory_retrieval_limit=env.memory_retrieval_limit,
        memory_extraction_min_chars=env.memory_extraction_min_chars,
    )
