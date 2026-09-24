"""Admin: credentials сторонних провайдеров (Alibaba) + живой smoke-тест.

Полный ключ не возвращается. Smoke — реальный дешёвый вызов qwen3.8-flash
(≤16 output tokens) напрямую через AlibabaProvider.
"""

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.dependencies import OwnerDep, SessionDep, require_owner
from app.config import Settings
from app.db.repositories import ProviderCredentialRepository
from app.llm.base import LLMRequest
from app.llm.providers.alibaba import AlibabaProvider
from app.security.crypto import CryptoBox
from app.services import admin as admin_service
from app.services.credentials import get_provider_api_key

router = APIRouter(prefix="/admin/providers", dependencies=[Depends(require_owner)])

_ALIBABA = "alibaba"

# Smoke: flash-модель, thinking off, ≤16 output tokens (дешевле некуда).
_SMOKE_MODEL = "qwen3.8-flash"
_SMOKE_THINKING = "off"
_SMOKE_MAX_OUTPUT_TOKENS = 16


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


async def _run_alibaba_smoke(*, api_key: str, base_url: str) -> tuple[bool, int, str | None]:
    """Дешёвый реальный вызов Alibaba (≤16 output tokens) → (ok, latency_ms, error).

    httpx-клиент создаётся и закрывается на время ручного admin-вызова
    (не путь генерации; singleton-инвариант A30 на него не распространяется).
    """
    provider = AlibabaProvider(api_key=api_key, base_url=base_url)
    started = time.monotonic()
    error: str | None = None
    try:
        request = LLMRequest(
            model=_SMOKE_MODEL,
            messages=[{"role": "user", "parts": [{"type": "text", "text": "ping"}]}],
            thinking=_SMOKE_THINKING,
            max_output_tokens=_SMOKE_MAX_OUTPUT_TOKENS,
        )
        async for _event in provider.stream_chat(request):
            pass
    except Exception as exc:  # noqa: BLE001 — smoke отражает любую ошибку в error
        error = f"{type(exc).__name__}: {exc}"[:256]
    finally:
        await provider.aclose()
    return (error is None, int((time.monotonic() - started) * 1000), error)


@router.post("/alibaba/smoke")
async def alibaba_smoke(request: Request, current: OwnerDep, session: SessionDep) -> dict[str, Any]:
    """Живой smoke Alibaba: дешёвый вызов qwen3.8-flash (≤16 output tokens).

    Ключ — provider_credentials (enabled) → env ALIBABA_API_KEY; disabled
    credential = абсолютный запрет (ok=False, обращения к API нет).
    """
    actor, _ = current
    settings: Settings = request.app.state.settings
    crypto: CryptoBox = request.app.state.crypto
    api_key = await get_provider_api_key(session, crypto, _ALIBABA, settings.alibaba_api_key)
    ok: bool
    latency_ms: int
    error: str | None
    if api_key is None:
        ok, latency_ms, error = False, 0, "alibaba api key not configured (or disabled)"
    else:
        ok, latency_ms, error = await _run_alibaba_smoke(
            api_key=api_key, base_url=settings.alibaba_base_url
        )
    await admin_service.audit(
        session,
        actor_id=actor.telegram_user_id,
        action=admin_service.PROVIDER_SMOKE_TEST,
        target_type="provider",
        target_id=_ALIBABA,
        metadata={"ok": ok, "latency_ms": latency_ms, "error": error},
    )
    await session.commit()
    return {"ok": ok, "latency_ms": latency_ms, "error": error}
