"""Роуты Mini App REST API (префикс /api добавляется в create_app)."""

from app.api.routes import (
    admin_access,
    admin_gemini,
    admin_memory,
    admin_models,
    admin_providers,
    admin_search,
    admin_stats,
    admin_system,
    admin_users,
    auth,
    chats,
    me,
    memory,
    settings,
)

__all__ = [
    "admin_access",
    "admin_gemini",
    "admin_memory",
    "admin_models",
    "admin_providers",
    "admin_search",
    "admin_stats",
    "admin_system",
    "admin_users",
    "auth",
    "chats",
    "me",
    "memory",
    "settings",
]
