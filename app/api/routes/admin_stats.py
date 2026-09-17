"""Admin: сводная статистика (прямые select/func) и просмотр audit_log."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.dependencies import OwnerDep, SessionDep, require_owner
from app.db.models import GeminiProject, GenerationRun, ToolCallRecord, User
from app.db.repositories import AuditLogRepository

router = APIRouter(prefix="/admin", dependencies=[Depends(require_owner)])

_MAX_AUDIT_LIMIT = 500


@router.get("/stats")
async def get_stats(current: OwnerDep, session: SessionDep) -> dict[str, Any]:
    """Сводка: пользователи, генерации и токены за сегодня (UTC), пул Gemini."""
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
    }


@router.get("/audit")
async def get_audit(
    current: OwnerDep, session: SessionDep, limit: int = Query(default=50)
) -> dict[str, Any]:
    """Последние записи audit_log (свежие первыми)."""
    limit = max(1, min(limit, _MAX_AUDIT_LIMIT))
    entries = await AuditLogRepository(session).list_recent(limit=limit)
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
        ]
    }
