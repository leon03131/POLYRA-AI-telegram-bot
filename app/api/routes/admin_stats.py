"""Admin: сводная статистика (прямые select/func) и просмотр audit_log."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.dependencies import OwnerDep, SessionDep, require_owner
from app.db.models import GeminiProject, GenerationRun, QuotaDailyUsage, ToolCallRecord, User
from app.db.repositories import AuditLogRepository
from app.llm.gemini.quota import pacific_day

router = APIRouter(prefix="/admin", dependencies=[Depends(require_owner)])

_MAX_AUDIT_LIMIT = 500


@router.get("/stats")
async def get_stats(current: OwnerDep, session: SessionDep) -> dict[str, Any]:
    """Сводка: пользователи, генерации и токены за сегодня (UTC), пул Gemini.

    V2 extras: requests_by_model_today, errors_today (status=failed),
    rate_limit_429_today (error_category=rate_limit), gemini_usage_today
    (quota_daily_usage за Pacific-дату — как суточные квоты, join project name).
    """
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    active_since = now - timedelta(days=7)

    users_total = (await session.execute(select(func.count(User.id)))).scalar_one()
    users_active_7d = (
        await session.execute(select(func.count(User.id)).where(User.last_seen_at > active_since))
    ).scalar_one()
    generations_today = (
        await session.execute(
            select(func.count(GenerationRun.id)).where(GenerationRun.started_at >= today_start)
        )
    ).scalar_one()
    status_rows = (
        await session.execute(
            select(GenerationRun.status, func.count())
            .where(GenerationRun.started_at >= today_start)
            .group_by(GenerationRun.status)
        )
    ).all()
    tokens_row = (
        await session.execute(
            select(
                func.coalesce(func.sum(GenerationRun.input_tokens), 0),
                func.coalesce(func.sum(GenerationRun.output_tokens), 0),
            ).where(GenerationRun.started_at >= today_start)
        )
    ).one()
    gemini_total = (await session.execute(select(func.count(GeminiProject.id)))).scalar_one()
    gemini_enabled = (
        await session.execute(
            select(func.count(GeminiProject.id)).where(GeminiProject.enabled.is_(True))
        )
    ).scalar_one()
    gemini_healthy = (
        await session.execute(
            select(func.count(GeminiProject.id)).where(GeminiProject.health_status == "healthy")
        )
    ).scalar_one()
    tool_calls_today = (
        await session.execute(
            select(func.count(ToolCallRecord.id)).where(ToolCallRecord.created_at >= today_start)
        )
    ).scalar_one()

    model_rows = (
        await session.execute(
            select(GenerationRun.model_id, func.count())
            .where(GenerationRun.started_at >= today_start)
            .group_by(GenerationRun.model_id)
        )
    ).all()
    errors_today = (
        await session.execute(
            select(func.count(GenerationRun.id)).where(
                GenerationRun.started_at >= today_start,
                GenerationRun.status == "failed",
            )
        )
    ).scalar_one()
    rate_limit_429_today = (
        await session.execute(
            select(func.count(GenerationRun.id)).where(
                GenerationRun.started_at >= today_start,
                GenerationRun.error_category == "rate_limit",
            )
        )
    ).scalar_one()
    gemini_usage_rows = (
        await session.execute(
            select(
                GeminiProject.name,
                func.sum(QuotaDailyUsage.requests_count),
                func.sum(QuotaDailyUsage.tokens_in),
            )
            .join(GeminiProject, QuotaDailyUsage.project_id == GeminiProject.id)
            .where(QuotaDailyUsage.day == pacific_day(now))
            .group_by(GeminiProject.name)
            .order_by(GeminiProject.name)
        )
    ).all()

    return {
        "users_total": users_total,
        "users_active_7d": users_active_7d,
        "generations_today": generations_today,
        "generations_by_status": {status: count for status, count in status_rows},
        "tokens_today": {"input": tokens_row[0], "output": tokens_row[1]},
        "gemini_projects": {
            "total": gemini_total,
            "enabled": gemini_enabled,
            "healthy": gemini_healthy,
        },
        "tool_calls_today": tool_calls_today,
        "requests_by_model_today": {model_id: count for model_id, count in model_rows},
        "errors_today": errors_today,
        "rate_limit_429_today": rate_limit_429_today,
        "gemini_usage_today": [
            {"project_name": name, "requests": int(requests or 0), "tokens_in": int(tokens or 0)}
            for name, requests, tokens in gemini_usage_rows
        ],
    }


@router.get("/audit")
async def get_audit(
    current: OwnerDep,
    session: SessionDep,
    limit: int = Query(default=50),
    offset: int = Query(default=0, ge=0),
    action: str | None = Query(default=None),
) -> dict[str, Any]:
    """Записи audit_log (свежие первыми) с пагинацией и фильтром по action."""
    limit = max(1, min(limit, _MAX_AUDIT_LIMIT))
    repo = AuditLogRepository(session)
    entries = await repo.list(limit=limit, offset=offset, action=action)
    total = await repo.count(action=action)
    return {
        "entries": [
            {
                "id": str(entry.id),
                "actor_telegram_id": entry.actor_telegram_id,
                "action": entry.action,
                "target_type": entry.target_type,
                "target_id": entry.target_id,
                "metadata": entry.metadata_json,
                "created_at": entry.created_at,
            }
            for entry in entries
        ],
        "total": total,
    }
