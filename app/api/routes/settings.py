"""GET/PATCH /api/settings — персональные настройки пользователя."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.dependencies import CurrentUserDep, SessionDep
from app.config import Settings
from app.db.models import UserSettings
from app.db.repositories import UserSettingsRepository
from app.llm.registry import ModelRegistry
from app.services.access import is_model_allowed

router = APIRouter()

_WEB_MODES = frozenset({"off", "auto", "on"})


class SettingsPatchRequest(BaseModel):
    """Поля PATCH /api/settings; null у nullable-полей = наследовать системное."""

    default_model_id: str | None = None
    default_thinking: str | None = None
    web_mode: str | None = None
    memory_enabled: bool | None = None


def _settings_out(user_settings: UserSettings) -> dict[str, Any]:
    return {
        "default_model_id": user_settings.default_model_id,
        "default_thinking": user_settings.default_thinking,
        "web_mode": user_settings.web_mode,
        "memory_enabled": user_settings.memory_enabled,
    }


@router.get("/settings")
async def get_settings(current: CurrentUserDep, session: SessionDep) -> dict[str, Any]:
    """Текущие настройки пользователя (запись создаётся с дефолтами при отсутствии)."""
    user, _ = current
    user_settings = await UserSettingsRepository(session).get_or_create(user.id)
    return _settings_out(user_settings)


@router.patch("/settings")
async def patch_settings(
    body: SettingsPatchRequest,
    request: Request,
    current: CurrentUserDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Обновить настройки; валидация model/thinking/web_mode, затем commit."""
    user, permissions = current
    settings: Settings = request.app.state.settings
    registry: ModelRegistry = request.app.state.registry
    data = body.model_dump(exclude_unset=True)

    if "web_mode" in data and data["web_mode"] not in _WEB_MODES:
        raise HTTPException(status_code=400, detail="web_mode must be off|auto|on")
    if "memory_enabled" in data and data["memory_enabled"] is None:
        raise HTTPException(status_code=400, detail="memory_enabled must be boolean")

    repo = UserSettingsRepository(session)
    user_settings = await repo.get_or_create(user.id)

    if "default_model_id" in data and data["default_model_id"] is not None:
        model_def = registry.get_or_none(data["default_model_id"])
        if model_def is None or model_def.internal_only:
            raise HTTPException(status_code=400, detail="unknown model_id")
        if not is_model_allowed(permissions, data["default_model_id"]):
            raise HTTPException(status_code=403, detail="model not allowed")

    if "default_thinking" in data and data["default_thinking"] is not None:
        effective_model_id = data.get("default_model_id") or user_settings.default_model_id
        effective_model_id = effective_model_id or settings.default_model
        model_def = registry.get_or_none(effective_model_id)
        if model_def is not None and data["default_thinking"] not in model_def.thinking_modes:
            raise HTTPException(status_code=400, detail="thinking mode not supported by model")

    updated = await repo.update(user.id, **data)
    await session.commit()
    return {"settings": _settings_out(updated)}
