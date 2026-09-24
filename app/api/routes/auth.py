"""POST /api/auth/telegram — вход в Mini App по Telegram initData."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.auth import create_session_token, validate_init_data
from app.config import Settings
from app.db.repositories import AccessRepository, ModelPermissionRepository, UserRepository
from app.services.access import GrantView, evaluate_access

router = APIRouter()


class AuthRequest(BaseModel):
    """Тело запроса: сырая строка Telegram.WebApp.initData."""

    init_data: str


@router.post("/auth/telegram")
async def auth_telegram(body: AuthRequest, request: Request) -> dict[str, Any]:
    """Валидировать initData + активный доступ, выдать session token.

    initData проверяется ДО обращения к БД (401 без PostgreSQL); 403, если
    доступ не активен (banned/no_grant/suspended/revoked/expired).
    """
    settings: Settings = request.app.state.settings
    try:
        data = validate_init_data(body.init_data, settings.bot_token)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid init data") from None
    tg_user = data.get("user")
    if not isinstance(tg_user, dict) or not isinstance(tg_user.get("id"), int):
        raise HTTPException(status_code=401, detail="init data has no user")
    telegram_user_id: int = tg_user["id"]

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        user_repo = UserRepository(session)
        user = await user_repo.upsert_telegram_user(
            telegram_user_id,
            username=tg_user.get("username"),
            first_name=tg_user.get("first_name") or "",
            last_name=tg_user.get("last_name"),
            language_code=tg_user.get("language_code"),
        )
        is_owner = telegram_user_id == settings.owner_telegram_id
        if user.is_owner != is_owner:
            # Флаг — только UI-подсказка (права даёт numeric id, A04); синхронизируем.
            user.is_owner = is_owner
            await session.flush()

        grant = await AccessRepository(session).get_grant(user.id)
        grant_view = (
            GrantView(
                status=grant.status,
                expires_at=grant.expires_at,
                requests_per_day=grant.requests_per_day,
                token_limit=grant.token_limit,
                max_concurrent_generations=grant.max_concurrent_generations,
                can_use_web_search=grant.can_use_web_search,
                can_use_memory=grant.can_use_memory,
            )
            if grant is not None
            else None
        )
        allowed_models = await ModelPermissionRepository(session).allowed_model_ids(user.id)
        permissions = evaluate_access(
            is_owner=is_owner,
            user_status=user.status,
            grant=grant_view,
            allowed_models=allowed_models,
            now=datetime.now(UTC),
        )
        await session.commit()  # зафиксировать upsert/last_seen в любом случае
        if not permissions.allowed:
            raise HTTPException(status_code=403, detail=f"access denied: {permissions.reason}")

    return {
        "session_token": create_session_token(
            telegram_user_id,
            ttl_seconds=settings.session_token_ttl_seconds,
            secret=settings.master_encryption_key,
        ),
        "expires_in": settings.session_token_ttl_seconds,
        "user": {
            "telegram_user_id": telegram_user_id,
            "username": tg_user.get("username"),
            "first_name": tg_user.get("first_name") or "",
        },
        "is_owner": is_owner,
    }
