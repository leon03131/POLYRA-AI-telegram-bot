"""GET /api/me и GET /api/models — профиль и доступные модели пользователя."""

from typing import Any

from fastapi import APIRouter, Request

from app.api.dependencies import CurrentUserDep, SessionDep, is_owner_user
from app.config import Settings
from app.llm.capabilities import ModelDefinition
from app.llm.registry import ModelRegistry
from app.services.settings import get_probe_capabilities

router = APIRouter()


@router.get("/me")
async def get_me(request: Request, current: CurrentUserDep) -> dict[str, Any]:
    """Профиль + effective permissions (allowed_models из grant/permissions)."""
    user, permissions = current
    settings: Settings = request.app.state.settings
    return {
        "user": {
            "telegram_user_id": user.telegram_user_id,
            "username": user.username,
            "first_name": user.first_name,
        },
        "is_owner": is_owner_user(user, settings),
        "permissions": {
            "allowed_models": (
                sorted(permissions.allowed_models)
                if permissions.allowed_models is not None
                else None
            ),
            "can_use_web_search": permissions.can_use_web_search,
            "can_use_memory": permissions.can_use_memory,
            "max_concurrent_generations": permissions.max_concurrent_generations,
            "requests_per_day": permissions.requests_per_day,
            "token_limit": permissions.token_limit,
        },
    }


def _model_out(model: ModelDefinition, probe: dict[str, Any] | None) -> dict[str, Any]:
    """Сериализация модели для UI; свежая probe-запись фильтрует thinking_modes."""
    probe_at: str | None = None
    accepted = probe.get("accepted_thinking") if probe else None
    if isinstance(accepted, list) and accepted:
        # A27 per-mode acceptance: только подтверждённые probe режимы
        # ("off" считается accepted, если thinking:off прошёл со status ok).
        thinking_modes = [mode for mode in model.thinking_modes if mode in accepted]
    else:
        # Нет свежей записи или accepted пуст → registry как есть (probe_required).
        thinking_modes = [mode for mode in model.thinking_modes if mode not in model.probe_required]
    if probe:
        at = probe.get("at")
        probe_at = at if isinstance(at, str) else None
    return {
        "model_id": model.model_id,
        "display_name": model.display_name,
        "provider": model.provider,
        "supports_images": model.supports_images,
        "thinking_modes": thinking_modes,
        "default_thinking": model.default_thinking,
        "probe_at": probe_at,
    }


@router.get("/models")
async def list_models(
    request: Request, current: CurrentUserDep, session: SessionDep
) -> dict[str, Any]:
    """Модели, разрешённые пользователю: не internal, enabled, по permissions.

    thinking-опции из ModelDefinition.probe_required скрыты до runtime probe;
    свежие probe-записи (capability_probe:<model_id>, A27) фильтруют режимы
    по факту acceptance. probe_at — время последнего probe (для UI).
    """
    _, permissions = current
    registry: ModelRegistry = request.app.state.registry
    probe = await get_probe_capabilities(session)
    models = registry.filter_by_permissions(permissions.allowed_models)
    return {"models": [_model_out(model, probe.get(model.model_id)) for model in models]}
