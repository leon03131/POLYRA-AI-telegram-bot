"""Admin: конфигурация поисковых бэкендов (enabled/priority/key) и health-check."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.dependencies import OwnerDep, SessionDep, require_owner
from app.db.models import SearchBackendConfig
from app.db.repositories import SearchConfigRepository
from app.search.manager import _BACKEND_FACTORIES
from app.security.crypto import CryptoBox
from app.services import admin as admin_service

router = APIRouter(prefix="/admin/search", dependencies=[Depends(require_owner)])


class SearchBackendPutRequest(BaseModel):
    """Тело PUT /api/admin/search/backends/{backend_id}."""

    enabled: bool | None = None
    priority: int | None = None


class SearchBackendKeyRequest(BaseModel):
    """Тело POST /api/admin/search/backends/{backend_id}/key."""

    api_key: str = Field(min_length=1)


def _backend_out(config: SearchBackendConfig) -> dict[str, Any]:
    return {
        "backend_id": config.backend_id,
        "enabled": config.enabled,
        "priority": config.priority,
        "key_hint": config.key_hint,
        "health_status": config.health_status,
        "last_error": config.last_error,
    }


def _check_known_backend(backend_id: str) -> None:
    if backend_id not in _BACKEND_FACTORIES:
        raise HTTPException(status_code=404, detail="unknown backend_id")


@router.get("/backends")
async def list_backends(current: OwnerDep, session: SessionDep) -> dict[str, Any]:
    """Все бэкенды по приоритету; строки для известных backend_id создаются-заглушки."""
    repo = SearchConfigRepository(session)
    created = False
    for backend_id in _BACKEND_FACTORIES:
        if await repo.get(backend_id) is None:
            await repo.upsert(backend_id)
            created = True
    if created:
        await session.commit()
    configs = await repo.list_all()
    return {"backends": [_backend_out(config) for config in configs]}


@router.put("/backends/{backend_id}")
async def put_backend(
    backend_id: str, body: SearchBackendPutRequest, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Обновить enabled/priority бэкенда (upsert)."""
    actor, _ = current
    _check_known_backend(backend_id)
    data = body.model_dump(exclude_unset=True)
    await SearchConfigRepository(session).upsert(backend_id, **data)
    await admin_service.audit(
        session,
        actor_id=actor.telegram_user_id,
        action=admin_service.SEARCH_BACKEND_CHANGED,
        target_type="search_backend",
        target_id=backend_id,
        metadata=data,
    )
    await session.commit()
    return {"ok": True}


@router.post("/backends/{backend_id}/key")
async def set_backend_key(
    backend_id: str,
    body: SearchBackendKeyRequest,
    request: Request,
    current: OwnerDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Установить API-ключ бэкенда (Fernet); пустой ключ → 400."""
    actor, _ = current
    _check_known_backend(backend_id)
    crypto: CryptoBox = request.app.state.crypto
    api_key = body.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key must not be blank")
    key_hint = api_key[-4:]
    await SearchConfigRepository(session).upsert(
        backend_id, encrypted_api_key=crypto.encrypt(api_key), key_hint=key_hint
    )
    await admin_service.audit(
        session,
        actor_id=actor.telegram_user_id,
        action=admin_service.SEARCH_BACKEND_CHANGED,
        target_type="search_backend",
        target_id=backend_id,
        metadata={"key_updated": True, "key_hint": key_hint},
    )
    await session.commit()
    return {"ok": True}


@router.post("/backends/{backend_id}/test")
async def test_backend(
    backend_id: str, request: Request, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Health-check бэкенда (минимальный запрос); обновляет health в конфиге."""
    _check_known_backend(backend_id)
    search_manager = request.app.state.search_manager
    ok = await search_manager.health_check(backend_id)
    error: str | None = None
    if not ok:
        config = await SearchConfigRepository(session).get(backend_id)
        error = (config.last_error if config else None) or "health check failed"
    return {"ok": ok, "error": error}
