"""Admin: пул Gemini-проектов (ключи) и политики квот. Полные ключи не возвращаются."""

import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import OwnerDep, SessionDep, require_owner
from app.db.models import GeminiProject, QuotaPolicy
from app.db.repositories import GeminiProjectRepository, QuotaPolicyRepository
from app.llm.registry import ModelRegistry
from app.security.crypto import CryptoBox
from app.services import admin as admin_service

router = APIRouter(prefix="/admin/gemini", dependencies=[Depends(require_owner)])


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


@router.get("/projects")
async def list_projects(current: OwnerDep, session: SessionDep) -> dict[str, Any]:
    """Все проекты в порядке ротации."""
    projects = await GeminiProjectRepository(session).list_all()
    return {"projects": [_project_out(project) for project in projects]}


@router.post("/projects", status_code=201)
async def add_project(
    body: GeminiProjectCreateRequest, request: Request, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Добавить проект (ключ шифруется Fernet); дубль имени → 409."""
    actor, _ = current
    crypto: CryptoBox = request.app.state.crypto
    repo = GeminiProjectRepository(session)
    name = body.name.strip()
    if any(project.name == name for project in await repo.list_all()):
        raise HTTPException(status_code=409, detail="project name already exists")
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
    existing_hints = {project.key_hint for project in existing}
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
        if key_hint in existing_hints:
            skipped += 1
            continue
        max_number += 1
        name = f"{body.name_prefix}-{max_number:02d}"
        while name in existing_names:
            max_number += 1
            name = f"{body.name_prefix}-{max_number:02d}"
        await repo.add(name, crypto.encrypt(api_key), key_hint=key_hint)
        existing_hints.add(key_hint)
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
