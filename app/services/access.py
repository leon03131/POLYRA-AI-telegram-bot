"""Чистая логика оценки доступа к боту (без БД и конфига).

Причины отказа — строковые константы (для audit/UX):
"ok", "banned", "no_grant", "suspended", "revoked", "expired".

Даты считаются aware UTC. Если ``grant.expires_at`` naive — трактуется как UTC
(tzinfo заменяется), чтобы сравнение не падало.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

REASON_OK = "ok"
REASON_BANNED = "banned"
REASON_NO_GRANT = "no_grant"
REASON_SUSPENDED = "suspended"
REASON_REVOKED = "revoked"
REASON_EXPIRED = "expired"

OWNER_MAX_CONCURRENT_GENERATIONS = 10


@dataclass(frozen=True, slots=True)
class GrantView:
    status: str  # "active" | "suspended" | "revoked"
    expires_at: datetime | None  # None = permanent
    requests_per_day: int | None
    token_limit: int | None
    max_concurrent_generations: int
    can_use_web_search: bool
    can_use_memory: bool


@dataclass(frozen=True, slots=True)
class EffectivePermissions:
    allowed: bool
    reason: str  # "ok" или причина отказа
    allowed_models: frozenset[str] | None  # None = все модели
    can_use_web_search: bool
    can_use_memory: bool
    requests_per_day: int | None
    token_limit: int | None
    max_concurrent_generations: int


def _deny(reason: str) -> EffectivePermissions:
    return EffectivePermissions(
        allowed=False,
        reason=reason,
        allowed_models=None,
        can_use_web_search=False,
        can_use_memory=False,
        requests_per_day=None,
        token_limit=None,
        max_concurrent_generations=0,
    )


def _as_aware_utc(dt: datetime) -> datetime:
    """Naive datetime трактуем как UTC (заменяем tzinfo), чтобы не падать при сравнении."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def evaluate_access(
    *,
    is_owner: bool,
    user_status: str,  # "active" | "banned"
    grant: GrantView | None,
    allowed_models: set[str] | frozenset[str] | None,
    now: datetime,
) -> EffectivePermissions:
    if is_owner:
        return EffectivePermissions(
            allowed=True,
            reason=REASON_OK,
            allowed_models=None,
            can_use_web_search=True,
            can_use_memory=True,
            requests_per_day=None,
            token_limit=None,
            max_concurrent_generations=OWNER_MAX_CONCURRENT_GENERATIONS,
        )
    if user_status == "banned":
        return _deny(REASON_BANNED)
    if grant is None:
        return _deny(REASON_NO_GRANT)
    if grant.status == "suspended":
        return _deny(REASON_SUSPENDED)
    if grant.status == "revoked":
        return _deny(REASON_REVOKED)
    if grant.expires_at is not None and _as_aware_utc(grant.expires_at) <= _as_aware_utc(now):
        return _deny(REASON_EXPIRED)
    return EffectivePermissions(
        allowed=True,
        reason=REASON_OK,
        allowed_models=frozenset(allowed_models) if allowed_models is not None else None,
        can_use_web_search=grant.can_use_web_search,
        can_use_memory=grant.can_use_memory,
        requests_per_day=grant.requests_per_day,
        token_limit=grant.token_limit,
        max_concurrent_generations=grant.max_concurrent_generations,
    )


def is_model_allowed(permissions: EffectivePermissions, model_id: str) -> bool:
    if not permissions.allowed:
        return False
    if permissions.allowed_models is None:
        return True
    return model_id in permissions.allowed_models
