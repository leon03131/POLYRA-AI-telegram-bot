"""Admin: credentials сторонних провайдеров (Alibaba). Полный ключ не возвращается."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.dependencies import OwnerDep, SessionDep, require_owner
from app.config import Settings
from app.db.repositories import ProviderCredentialRepository
from app.security.crypto import CryptoBox
from app.services import admin as admin_service

router = APIRouter(prefix="/admin/providers", dependencies=[Depends(require_owner)])

_ALIBABA = "alibaba"


class ProviderKeyRequest(BaseModel):
    """Тело POST /api/admin/providers/alibaba/key."""

    api_key: str = Field(min_length=1)


@router.get("/alibaba")
async def get_alibaba(request: Request, current: OwnerDep, session: SessionDep) -> dict[str, Any]:
    """Статус Alibaba: configured/key_hint/enabled из БД, base_url — read-only из env.

    Bootstrap env-ключ (ALIBABA_API_KEY) учитывается, если записи в БД нет.
    """
    settings: Settings = request.app.state.settings
    credential = await ProviderCredentialRepository(session).get(_ALIBABA)
    if credential is not None:
        configured, key_hint, enabled = True, credential.key_hint, credential.enabled
    elif settings.alibaba_api_key:
        configured, key_hint, enabled = True, settings.alibaba_api_key[-4:], True
    else:
        configured, key_hint, enabled = False, None, False
    return {
        "configured": configured,
        "key_hint": key_hint,
        "base_url": settings.alibaba_base_url,
        "enabled": enabled,
    }


@router.post("/alibaba/key")
async def set_alibaba_key(
    body: ProviderKeyRequest, request: Request, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Сохранить/заменить ключ Alibaba (Fernet); пустой ключ → 400."""
    actor, _ = current
    crypto: CryptoBox = request.app.state.crypto
    api_key = body.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key must not be blank")
    key_hint = api_key[-4:]
    await ProviderCredentialRepository(session).upsert(_ALIBABA, crypto.encrypt(api_key), key_hint)
    await admin_service.audit(
        session,
        actor_id=actor.telegram_user_id,
        action=admin_service.PROVIDER_KEY_SET,
        target_type="provider",
        target_id=_ALIBABA,
        metadata={"key_hint": key_hint},
    )
    await session.commit()
    return {"ok": True}
