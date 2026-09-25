"""Admin: пул Gemini-проектов (ключи), политики квот, smoke и счётчики.

Полные ключи не возвращаются. Smoke — реальный дешёвый вызов
gemini-3.5-flash-lite (≤16 output tokens) напрямую через GeminiProvider.
"""

import re
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import OwnerDep, SessionDep, require_owner
from app.config import Settings
from app.db.models import GeminiProject, QuotaPolicy
from app.db.repositories import (
    GeminiProjectRepository,
    QuotaPolicyRepository,
    QuotaUsageRepository,
)
from app.llm.base import LLMRequest
from app.llm.providers.gemini import GeminiProvider
from app.llm.registry import ModelRegistry
from app.security.crypto import CryptoBox
from app.services import admin as admin_service

router = APIRouter(prefix="/admin/gemini", dependencies=[Depends(require_owner)])

# Smoke: internal lite-модель, минимальный thinking, ≤16 output tokens (дешевле некуда).
_SMOKE_MODEL = "gemini-3.5-flash-lite"
_SMOKE_THINKING = "minimal"
_SMOKE_MAX_OUTPUT_TOKENS = 16
_USAGE_LIMIT = 50


class GeminiProjectCreateRequest(BaseModel):
    """Тело POST /api/admin/gemini/projects."""

    name: str = Field(min_length=1, max_length=64)
    api_key: str = Field(min_length=1)


class GeminiBulkCreateRequest(BaseModel):
    """Тело POST /api/admin/gemini/projects/bulk."""

    api_keys: list[str]
    name_prefix: str = Field(default="gemini", min_length=1, max_length=32)


class GeminiMoveRequest(BaseModel):
    """Тело POST /api/admin/gemini/projects/{id}/move."""

    direction: int


class QuotaPutRequest(BaseModel):
    """Тело PUT /api/admin/gemini/quotas; None = unlimited."""

    model_id: str
    rpm: int | None = Field(default=None, ge=0)
    tpm: int | None = Field(default=None, ge=0)
    rpd: int | None = Field(default=None, ge=0)


def _project_out(project: GeminiProject) -> dict[str, Any]:
    return {
        "id": str(project.id),
        "name": project.name,
        "key_hint": project.key_hint,
        "enabled": project.enabled,
        "health_status": project.health_status,
        "rotation_order": project.rotation_order,
        "cooldown_until": project.cooldown_until,
        "last_success_at": project.last_success_at,
        "last_error_code": project.last_error_code,
        "last_error_message": project.last_error_message,
    }


def _quota_out(policy: QuotaPolicy) -> dict[str, Any]:
    return {"model_id": policy.model_id, "rpm": policy.rpm, "tpm": policy.tpm, "rpd": policy.rpd}


async def _get_project_or_404(session: AsyncSession, project_id: uuid.UUID) -> GeminiProject:
    project = await GeminiProjectRepository(session).get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


async def _run_gemini_smoke(*, api_key: str, base_url: str) -> tuple[bool, int, str | None]:
    """Дешёвый реальный вызов Gemini (≤16 output tokens) → (ok, latency_ms, error).

    httpx-клиент создаётся и закрывается на время ручного admin-вызова
    (не путь генерации; singleton-инвариант A30 на него не распространяется).
    """
    provider = GeminiProvider(base_url=base_url)
    started = time.monotonic()
    error: str | None = None
    try:
        request = LLMRequest(
            model=_SMOKE_MODEL,
            messages=[{"role": "user", "parts": [{"type": "text", "text": "ping"}]}],
            thinking=_SMOKE_THINKING,
            max_output_tokens=_SMOKE_MAX_OUTPUT_TOKENS,
            metadata={"api_key": api_key},
        )
        async for _event in provider.stream_chat(request):
            pass
    except Exception as exc:  # noqa: BLE001 — smoke отражает любую ошибку в error
        error = f"{type(exc).__name__}: {exc}"[:256]
    finally:
        await provider.aclose()
    return (error is None, int((time.monotonic() - started) * 1000), error)


@router.get("/projects")
async def list_projects(current: OwnerDep, session: SessionDep) -> dict[str, Any]:
    """Все проекты в порядке ротации."""
    projects = await GeminiProjectRepository(session).list_all()
    return {"projects": [_project_out(project) for project in projects]}


def _existing_plaintext_keys(projects: list[Any], crypto: CryptoBox) -> set[str]:
    """A29: дедуп по ПОЛНОМУ ключу (decrypt существующих), не по last4-маске.

    key_hint — только UI-маска; два разных ключа с одним suffix обязаны
    импортироваться, а одинаковый ключ — пропускаться."""
    keys: set[str] = set()
    for project in projects:
        try:
            keys.add(crypto.decrypt(project.encrypted_api_key))
        except ValueError:
            continue  # повреждённая запись не мешает дедупу
    return keys


@router.post("/projects", status_code=201)
async def add_project(
    body: GeminiProjectCreateRequest, request: Request, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Добавить проект (ключ шифруется Fernet); дубль имени или ключа → 409."""
    actor, _ = current
    crypto: CryptoBox = request.app.state.crypto
    repo = GeminiProjectRepository(session)
    existing = await repo.list_all()
    name = body.name.strip()
    if any(project.name == name for project in existing):
        raise HTTPException(status_code=409, detail="project name already exists")
    if body.api_key in _existing_plaintext_keys(existing, crypto):
        raise HTTPException(status_code=409, detail="api key already exists in pool")
    project = await repo.add(name, crypto.encrypt(body.api_key), key_hint=body.api_key[-4:])
    await admin_service.audit(
        session,
        actor_id=actor.telegram_user_id,
        action=admin_service.GEMINI_KEY_ADDED,
        target_type="gemini_project",
        target_id=str(project.id),
        metadata={"name": project.name, "key_hint": project.key_hint},
    )
    await session.commit()
    return {"ok": True, "id": str(project.id)}


@router.post("/projects/bulk")
async def bulk_add_projects(
    body: GeminiBulkCreateRequest, request: Request, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Массовое добавление ключей; дубли по key_hint пропускаются.

    Имена: ``{name_prefix}-NN`` с продолжением нумерации от max существующего.
    """
    actor, _ = current
    crypto: CryptoBox = request.app.state.crypto
    repo = GeminiProjectRepository(session)
    existing = await repo.list_all()
    existing_keys = _existing_plaintext_keys(existing, crypto)  # полный ключ, не mask (A29)
    existing_names = {project.name for project in existing}

    name_pattern = re.compile(rf"^{re.escape(body.name_prefix)}-(\d+)$")
    max_number = 0
    for project in existing:
        match = name_pattern.match(project.name)
        if match:
            max_number = max(max_number, int(match.group(1)))

    added = 0
    skipped = 0
    for raw_key in body.api_keys:
        api_key = raw_key.strip()
        if not api_key:
            skipped += 1
            continue
        key_hint = api_key[-4:]
        if api_key in existing_keys:
            skipped += 1
            continue
        max_number += 1
        name = f"{body.name_prefix}-{max_number:02d}"
        while name in existing_names:
            max_number += 1
            name = f"{body.name_prefix}-{max_number:02d}"
        await repo.add(name, crypto.encrypt(api_key), key_hint=key_hint)
        existing_keys.add(api_key)
        existing_names.add(name)
        added += 1
    await admin_service.audit(
        session,
        actor_id=actor.telegram_user_id,
        action=admin_service.GEMINI_KEY_BULK_ADDED,
        target_type="gemini_project",
        metadata={"added": added, "skipped": skipped, "name_prefix": body.name_prefix},
    )
    await session.commit()
    return {"added": added, "skipped": skipped}


@router.post("/projects/{project_id}/enable")
async def enable_project(
    project_id: uuid.UUID, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Включить проект в ротацию."""
    actor, _ = current
    project = await _get_project_or_404(session, project_id)
    await GeminiProjectRepository(session).set_enabled(project_id, True)
    await admin_service.audit(
        session,
        actor_id=actor.telegram_user_id,
        action=admin_service.GEMINI_KEY_ENABLED,
        target_type="gemini_project",
        target_id=str(project_id),
        metadata={"name": project.name, "key_hint": project.key_hint},
    )
    await session.commit()
    return {"ok": True}


@router.post("/projects/{project_id}/disable")
async def disable_project(
    project_id: uuid.UUID, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Выключить проект из ротации."""
    actor, _ = current
    project = await _get_project_or_404(session, project_id)
    await GeminiProjectRepository(session).set_enabled(project_id, False)
    await admin_service.audit(
        session,
        actor_id=actor.telegram_user_id,
        action=admin_service.GEMINI_KEY_DISABLED,
        target_type="gemini_project",
        target_id=str(project_id),
        metadata={"name": project.name, "key_hint": project.key_hint},
    )
    await session.commit()
    return {"ok": True}


@router.post("/projects/{project_id}/move")
async def move_project(
    project_id: uuid.UUID, body: GeminiMoveRequest, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Сдвинуть проект в порядке ротации (обмен rotation_order с соседом)."""
    actor, _ = current
    if body.direction not in (-1, 1):
        raise HTTPException(status_code=400, detail="direction must be -1 or 1")
    project = await _get_project_or_404(session, project_id)
    await GeminiProjectRepository(session).move(project_id, body.direction)
    await admin_service.audit(
        session,
        actor_id=actor.telegram_user_id,
        action=admin_service.GEMINI_KEY_MOVED,
        target_type="gemini_project",
        target_id=str(project_id),
        metadata={"name": project.name, "direction": body.direction},
    )
    await session.commit()
    return {"ok": True}


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: uuid.UUID, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Удалить проект из пула."""
    actor, _ = current
    project = await _get_project_or_404(session, project_id)
    await GeminiProjectRepository(session).delete(project_id)
    await admin_service.audit(
        session,
        actor_id=actor.telegram_user_id,
        action=admin_service.GEMINI_KEY_DELETED,
        target_type="gemini_project",
        target_id=str(project_id),
        metadata={"name": project.name, "key_hint": project.key_hint},
    )
    await session.commit()
    return {"ok": True}


@router.get("/quotas")
async def list_quotas(current: OwnerDep, session: SessionDep) -> dict[str, Any]:
    """Политики квот по моделям."""
    policies = await QuotaPolicyRepository(session).list_all()
    return {"quotas": [_quota_out(policy) for policy in policies]}


@router.put("/quotas")
async def put_quota(
    body: QuotaPutRequest, request: Request, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Создать/обновить лимиты модели; неизвестная модель → 400."""
    actor, _ = current
    registry: ModelRegistry = request.app.state.registry
    if registry.get_or_none(body.model_id) is None:
        raise HTTPException(status_code=400, detail="unknown model_id")
    await QuotaPolicyRepository(session).upsert(
        body.model_id, rpm=body.rpm, tpm=body.tpm, rpd=body.rpd
    )
    await admin_service.audit(
        session,
        actor_id=actor.telegram_user_id,
        action=admin_service.GEMINI_QUOTA_UPDATED,
        target_type="quota_policy",
        target_id=body.model_id,
        metadata={"rpm": body.rpm, "tpm": body.tpm, "rpd": body.rpd},
    )
    await session.commit()
    return {"ok": True}


@router.post("/projects/{project_id}/test")
async def test_project(
    project_id: uuid.UUID, request: Request, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Живой smoke проекта: дешёвый вызов gemini-3.5-flash-lite (≤16 output tokens)."""
    actor, _ = current
    settings: Settings = request.app.state.settings
    crypto: CryptoBox = request.app.state.crypto
    project = await _get_project_or_404(session, project_id)
    api_key = crypto.decrypt(project.encrypted_api_key)
    ok, latency_ms, error = await _run_gemini_smoke(
        api_key=api_key, base_url=settings.gemini_base_url
    )
    await admin_service.audit(
        session,
        actor_id=actor.telegram_user_id,
        action=admin_service.GEMINI_PROJECT_TESTED,
        target_type="gemini_project",
        target_id=str(project_id),
        metadata={"name": project.name, "ok": ok, "latency_ms": latency_ms, "error": error},
    )
    await session.commit()
    return {"ok": ok, "latency_ms": latency_ms, "error": error}


@router.post("/reset-counters")
async def reset_counters(current: OwnerDep, session: SessionDep) -> dict[str, Any]:
    """Обнулить локальные счётчики квот (quota_minute_usage/quota_daily_usage)."""
    actor, _ = current
    minute_deleted, daily_deleted = await QuotaUsageRepository(session).delete_all()
    await admin_service.audit(
        session,
        actor_id=actor.telegram_user_id,
        action=admin_service.GEMINI_COUNTERS_RESET,
        target_type="quota_usage",
        metadata={"minute_deleted": minute_deleted, "daily_deleted": daily_deleted},
    )
    await session.commit()
    return {"ok": True, "deleted": minute_deleted + daily_deleted}


@router.get("/usage")
async def get_usage(current: OwnerDep, session: SessionDep) -> dict[str, Any]:
    """Фактические счётчики квот: последние 50 минутных и дневных окон."""
    repo = QuotaUsageRepository(session)
    minute = await repo.list_recent_minute(limit=_USAGE_LIMIT)
    daily = await repo.list_recent_daily(limit=_USAGE_LIMIT)
    return {"minute": minute, "daily": daily}
