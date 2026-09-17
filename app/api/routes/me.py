"""GET /api/me и GET /api/models — профиль и доступные модели пользователя."""

from typing import Any

from fastapi import APIRouter, Request

from app.api.dependencies import CurrentUserDep, is_owner_user
from app.config import Settings
from app.llm.registry import ModelRegistry

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


@router.get("/models")
async def list_models(request: Request, current: CurrentUserDep) -> dict[str, Any]:
    """Модели, разрешённые пользователю: не internal, enabled, по permissions.

    thinking-опции из ModelDefinition.probe_required скрыты до runtime probe.
    """
    _, permissions = current
    registry: ModelRegistry = request.app.state.registry
    models = registry.filter_by_permissions(permissions.allowed_models)
    return {
        "models": [
            {
                "model_id": model.model_id,
                "display_name": model.display_name,
                "provider": model.provider,
                "supports_images": model.supports_images,
                "thinking_modes": [
                    mode for mode in model.thinking_modes if mode not in model.probe_required
                ],
                "default_thinking": model.default_thinking,
            }
            for model in models
        ]
    }
