"""Admin: реестр моделей + DB-override enabled (model_overrides)."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.dependencies import OwnerDep, SessionDep, require_owner
from app.db.repositories import ModelOverrideRepository
from app.llm.capabilities import ModelDefinition
from app.llm.registry import ModelRegistry
from app.services import admin as admin_service

router = APIRouter(prefix="/admin/models", dependencies=[Depends(require_owner)])


class ModelPutRequest(BaseModel):
    """Тело PUT /api/admin/models/{model_id}."""

    enabled: bool


def _model_out(model_def: ModelDefinition, overrides: dict[str, bool]) -> dict[str, Any]:
    return {
        "model_id": model_def.model_id,
        "display_name": model_def.display_name,
        "provider": model_def.provider,
        # effective: registry default ∪ DB override (override побеждает)
        "enabled": overrides.get(model_def.model_id, model_def.enabled),
        "internal_only": model_def.internal_only,
        "supports_images": model_def.supports_images,
        "thinking_modes": list(model_def.thinking_modes),
        "default_thinking": model_def.default_thinking,
        "max_context": model_def.max_context,
        "max_output": model_def.max_output,
    }


@router.get("")
async def list_models(request: Request, current: OwnerDep, session: SessionDep) -> dict[str, Any]:
    """Все модели реестра с effective enabled (registry default ∪ model_overrides)."""
    registry: ModelRegistry = request.app.state.registry
    overrides = await ModelOverrideRepository(session).get_all()
    return {"models": [_model_out(model_def, overrides) for model_def in registry.list_all()]}


@router.put("/{model_id}")
async def put_model(
    model_id: str, body: ModelPutRequest, request: Request, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Установить enabled-override модели; неизвестная модель → 404."""
    actor, _ = current
    registry: ModelRegistry = request.app.state.registry
    if registry.get_or_none(model_id) is None:
        raise HTTPException(status_code=404, detail="unknown model_id")
    await admin_service.set_model_enabled(
        session,
        actor_id=actor.telegram_user_id,
        model_id=model_id,
        enabled=body.enabled,
    )
    await session.commit()
    return {"ok": True}
