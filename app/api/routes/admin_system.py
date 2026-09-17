"""Admin: системные настройки (system_settings store с fallback на env-дефолты)."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.dependencies import OwnerDep, SessionDep, require_owner
from app.config import Settings
from app.db.repositories import SystemSettingRepository
from app.llm.registry import ModelRegistry
from app.services import admin as admin_service

router = APIRouter(prefix="/admin/system", dependencies=[Depends(require_owner)])

_KEY_DEFAULT_MODEL = "default_model"
_KEY_DEFAULT_THINKING = "default_thinking"
_KEY_DEFAULT_SYSTEM_PROMPT = "default_system_prompt"
_KEY_MAX_TOOL_ITERATIONS = "max_tool_iterations"
_KEY_CONTEXT_KEEP_RECENT = "context_keep_recent"
_KEY_CONTEXT_TRIGGER_RATIO = "context_trigger_ratio"
_KEY_MEMORY_RETRIEVAL_LIMIT = "memory_retrieval_limit"

_ALL_KEYS = (
    _KEY_DEFAULT_MODEL,
    _KEY_DEFAULT_THINKING,
    _KEY_DEFAULT_SYSTEM_PROMPT,
    _KEY_MAX_TOOL_ITERATIONS,
    _KEY_CONTEXT_KEEP_RECENT,
    _KEY_CONTEXT_TRIGGER_RATIO,
    _KEY_MEMORY_RETRIEVAL_LIMIT,
)

_INT_KEYS = frozenset(
    {_KEY_MAX_TOOL_ITERATIONS, _KEY_CONTEXT_KEEP_RECENT, _KEY_MEMORY_RETRIEVAL_LIMIT}
)


class SystemPutRequest(BaseModel):
    """Тело PUT /api/admin/system; все поля опциональны."""

    default_model: str | None = None
    default_thinking: str | None = None
    default_system_prompt: str | None = None
    max_tool_iterations: int | None = None
    context_keep_recent: int | None = None
    context_trigger_ratio: float | None = None
    memory_retrieval_limit: int | None = None


def _system_out(stored: dict[str, Any], settings: Settings) -> dict[str, Any]:
    return {
        _KEY_DEFAULT_MODEL: stored.get(_KEY_DEFAULT_MODEL, settings.default_model),
        _KEY_DEFAULT_THINKING: stored.get(_KEY_DEFAULT_THINKING),
        _KEY_DEFAULT_SYSTEM_PROMPT: stored.get(
            _KEY_DEFAULT_SYSTEM_PROMPT, settings.default_system_prompt
        ),
        _KEY_MAX_TOOL_ITERATIONS: stored.get(
            _KEY_MAX_TOOL_ITERATIONS, settings.max_tool_iterations
        ),
        _KEY_CONTEXT_KEEP_RECENT: stored.get(
            _KEY_CONTEXT_KEEP_RECENT, settings.context_keep_recent
        ),
        _KEY_CONTEXT_TRIGGER_RATIO: stored.get(
            _KEY_CONTEXT_TRIGGER_RATIO, settings.context_trigger_ratio
        ),
        _KEY_MEMORY_RETRIEVAL_LIMIT: stored.get(
            _KEY_MEMORY_RETRIEVAL_LIMIT, settings.memory_retrieval_limit
        ),
    }


@router.get("")
async def get_system(request: Request, current: OwnerDep, session: SessionDep) -> dict[str, Any]:
    """Эффективные системные настройки: store → fallback на settings.*."""
    settings: Settings = request.app.state.settings
    stored = await SystemSettingRepository(session).get_many(_ALL_KEYS)
    return _system_out(stored, settings)


@router.put("")
async def put_system(
    body: SystemPutRequest, request: Request, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Обновить системные настройки; валидация значений → 400."""
    actor, _ = current
    settings: Settings = request.app.state.settings
    registry: ModelRegistry = request.app.state.registry
    repo = SystemSettingRepository(session)
    data = body.model_dump(exclude_unset=True)

    default_model = data.get(_KEY_DEFAULT_MODEL)
    if default_model is not None and registry.get_or_none(default_model) is None:
        raise HTTPException(status_code=400, detail="unknown default_model")
    default_thinking = data.get(_KEY_DEFAULT_THINKING)
    if default_thinking is not None:
        if default_model is None:
            stored = await repo.get_many([_KEY_DEFAULT_MODEL])
            default_model = stored.get(_KEY_DEFAULT_MODEL, settings.default_model)
        model_def = registry.get_or_none(default_model)
        if model_def is not None and default_thinking not in model_def.thinking_modes:
            raise HTTPException(status_code=400, detail="thinking mode not supported by model")
    for key in _INT_KEYS:
        value = data.get(key)
        if value is not None and value <= 0:
            raise HTTPException(status_code=400, detail=f"{key} must be positive")
    ratio = data.get(_KEY_CONTEXT_TRIGGER_RATIO)
    if ratio is not None and not 0 < ratio < 1:
        raise HTTPException(status_code=400, detail="context_trigger_ratio must be in (0, 1)")

    for key, value in data.items():
        await repo.set_value(key, value)
    if data:
        prompt_updated = _KEY_DEFAULT_SYSTEM_PROMPT in data
        await admin_service.audit(
            session,
            actor_id=actor.telegram_user_id,
            action=(
                admin_service.SYSTEM_PROMPT_UPDATED
                if prompt_updated
                else admin_service.SYSTEM_SETTINGS_UPDATED
            ),
            target_type="system_settings",
            metadata={"changed": data},
        )
    await session.commit()
    return {"ok": True}
