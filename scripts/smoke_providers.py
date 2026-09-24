"""Capability probe: минимальные РЕАЛЬНЫЕ запросы к провайдерам LLM.

Запуск ТОЛЬКО ВРУЧНУЮ из корня репозитория (не при старте приложения, не в CI):

    python scripts/smoke_providers.py [--provider gemini|alibaba|all]
                                      [--alibaba-key KEY]
                                      [--gemini-project NAME]

Ключи (CLI печатает только маски, никогда сами ключи):
- Gemini: первый enabled-проект из gemini_projects (decrypt через
  MASTER_ENCRYPTION_KEY из .env); --gemini-project задаёт проект по имени.
- Alibaba: --alibaba-key → provider_credentials (БД) → env ALIBABA_API_KEY.

Без ключей провайдер помечается "skipped". Коды выхода: 0 — ok (или fail при
запуске без --strict: это отчёт, а не тест); 1 — при --strict есть хотя бы один
check fail (skipped за fail НЕ считается); 130 — KeyboardInterrupt.
Результаты: таблица в stdout + JSON в .agents/reports/probe_*.json.

Списки моделей — НЕ hardcoded, а enumeration из ModelRegistry (default_registry):
новые модели попадают в прогон автоматически; internal_only помечаются в notes.

Назначение — подтвердить/опровергнуть capabilities из docs/vendor/GEMINI.md и
docs/vendor/ALIBABA.md: acceptance thinking-уровней (accepted/rejected), images,
function calling, формат тела ошибок, Kimi Dynamic Tool Loading (raw-тест).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Позволяет запускать `python scripts/smoke_providers.py` без editable-install.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import httpx  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

from app.config import Settings, get_settings  # noqa: E402
from app.db.repositories import GeminiProjectRepository  # noqa: E402
from app.db.session import create_engine_from_url, make_session_factory  # noqa: E402
from app.llm.base import LLMProvider, LLMRequest, LLMTool, MessageDict  # noqa: E402
from app.llm.capabilities import THINKING_OFF, ModelDefinition  # noqa: E402
from app.llm.errors import InvalidRequestError, ProviderError  # noqa: E402
from app.llm.events import Done, ReasoningDelta, TextDelta, ToolCall, Usage  # noqa: E402
from app.llm.gemini.pool import PoolExhaustedError  # noqa: E402
from app.llm.providers.alibaba import AlibabaProvider  # noqa: E402
from app.llm.providers.gemini import GeminiProvider  # noqa: E402
from app.llm.registry import ModelRegistry, default_registry  # noqa: E402
from app.security.crypto import CryptoBox, mask_secret  # noqa: E402
from app.services.credentials import get_provider_api_key  # noqa: E402

logger = logging.getLogger("smoke_providers")

# Таймауты probe-запросов (короче продакшн-дефолтов провайдеров: там read=300).
_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=30.0)

# 32x32 PNG (минимум Alibaba: стороны > 10 px — 1x1 отклоняется с invalid_parameter).
_PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAKUlEQVR4nO3NsQkAAAzDsJye00uP6FAQ9q40"
    "PX0DAAAAAAAAAAAA+QAMd+8APdkyBqwAAAAASUVORK5CYII="
)

_ECHO_TOOL = LLMTool(
    name="echo",
    description="Echo back the provided text",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string", "description": "Text to echo"}},
        "required": ["text"],
    },
)

CheckResult = dict[str, str]  # {"status": "ok" | "fail" | "skipped", "detail": str}


def _registry_models(registry: ModelRegistry, provider: str) -> list[ModelDefinition]:
    """Все модели провайдера из registry, включая internal (помечаются в notes)."""
    return [m for m in registry.list_all() if m.provider == provider]


def _ok(detail: str) -> CheckResult:
    return {"status": "ok", "detail": detail}


def _fail(detail: str) -> CheckResult:
    return {"status": "fail", "detail": detail}


def _skip(detail: str) -> CheckResult:
    return {"status": "skipped", "detail": detail}


def _user(text: str) -> MessageDict:
    """Нормализованное user-сообщение с одной text-part."""
    return {"role": "user", "parts": [{"type": "text", "text": text}]}


def _format_provider_error(exc: ProviderError) -> str:
    """Компактная строка ошибки: category, http-статус, raw_code, сообщение."""
    parts = [f"category={exc.category}"]
    if exc.status_code is not None:
        parts.append(f"http={exc.status_code}")
    if exc.raw_code:
        parts.append(f"raw_code={exc.raw_code}")
    parts.append(str(exc).replace("\n", " ")[:160])
    return ", ".join(parts)


class _StreamSummary:
    """Агрегат собранных событий стрима."""

    def __init__(self) -> None:
        self.text_chars = 0
        self.text_deltas = 0
        self.reasoning_deltas = 0
        self.usage: Usage | None = None
        self.done_reason: str | None = None
        self.tool_calls: list[ToolCall] = []


async def _collect(provider: LLMProvider, request: LLMRequest) -> _StreamSummary:
    """Прогнать stream_chat до конца и агрегировать события."""
    summary = _StreamSummary()
    async for event in provider.stream_chat(request):
        if isinstance(event, TextDelta):
            summary.text_deltas += 1
            summary.text_chars += len(event.text)
        elif isinstance(event, ReasoningDelta):
            summary.reasoning_deltas += 1
        elif isinstance(event, Usage):
            summary.usage = event
        elif isinstance(event, Done):
            summary.done_reason = event.finish_reason
        elif isinstance(event, ToolCall):
            summary.tool_calls.append(event)
    return summary


def _missing_events(summary: _StreamSummary) -> list[str]:
    """Каких обязательных событий (TextDelta/Done/Usage) не хватило."""
    missing = []
    if summary.text_deltas == 0:
        missing.append("TextDelta")
    if summary.done_reason is None:
        missing.append("Done")
    if summary.usage is None:
        missing.append("Usage")
    return missing


def _usage_str(summary: _StreamSummary) -> str:
    usage = summary.usage
    if usage is None:
        return ""
    return (
        f", tokens in/out/reason="
        f"{usage.input_tokens}/{usage.output_tokens}/{usage.reasoning_tokens}"
    )


async def _run_check(
    checks: dict[str, CheckResult],
    name: str,
    coro: Coroutine[Any, Any, CheckResult],
) -> None:
    """Запустить check с изоляцией сбоя: одна поломка не роняет остальные."""
    try:
        checks[name] = await coro
    except PoolExhaustedError as exc:
        checks[name] = _fail(f"pool exhausted: {exc}")
    except ProviderError as exc:
        checks[name] = _fail(_format_provider_error(exc))
    except Exception as exc:  # probe обязан пережить любой сбой одного check
        checks[name] = _fail(f"unexpected {type(exc).__name__}: {str(exc)[:200]}")
    logger.info("%s -> %s", name, checks[name]["status"])


# --- Gemini checks ---------------------------------------------------------


def _gemini_request(
    model: str,
    api_key: str,
    messages: list[MessageDict],
    *,
    thinking: str | None = None,
    max_output_tokens: int | None = None,
) -> LLMRequest:
    """LLMRequest для Gemini: ключ в metadata (ADR-015), провайдер stateless."""
    return LLMRequest(
        model=model,
        messages=messages,
        thinking=thinking,
        max_output_tokens=max_output_tokens,
        metadata={"api_key": api_key},
    )


async def _check_gemini_text(provider: LLMProvider, model: str, api_key: str) -> CheckResult:
    """Базовый text stream: TextDelta + Done, Usage присутствует."""
    request = _gemini_request(model, api_key, [_user("Скажи «ок»")], max_output_tokens=32)
    summary = await _collect(provider, request)
    missing = _missing_events(summary)
    if missing:
        return _fail(f"нет событий: {', '.join(missing)}; done={summary.done_reason}")
    return _ok(f"done={summary.done_reason}, text_chars={summary.text_chars}{_usage_str(summary)}")


async def _check_thinking_level(
    provider: LLMProvider,
    model: str,
    level: str,
    *,
    api_key: str | None = None,
) -> CheckResult:
    """Acceptance thinking-уровня: accepted (стрим прошёл) / rejected (400)."""
    messages = [_user("Скажи «ок»")]
    if api_key is not None:
        request = _gemini_request(model, api_key, messages, thinking=level, max_output_tokens=512)
    else:
        # reasoning budget (напр. kimi low=8192) не должен превышать лимит ответа
        request = LLMRequest(
            model=model, messages=messages, thinking=level, max_output_tokens=16384
        )
    try:
        summary = await _collect(provider, request)
    except InvalidRequestError as exc:
        return _fail(f"rejected: {_format_provider_error(exc)}")
    reasoning = (
        f"ReasoningDelta x{summary.reasoning_deltas}"
        if summary.reasoning_deltas
        else "без ReasoningDelta"
    )
    return _ok(f"accepted, {reasoning}, done={summary.done_reason}")


async def _check_image(
    provider: LLMProvider, model: str, *, api_key: str | None = None
) -> CheckResult:
    """32x32 PNG base64 → ожидаем текстовый ответ (TextDelta)."""
    message: MessageDict = {
        "role": "user",
        "parts": [
            {"type": "image", "mime_type": "image/png", "data_base64": _PNG_1X1_BASE64},
            {"type": "text", "text": "Что на изображении? Ответь одним словом."},
        ],
    }
    if api_key is not None:
        request = _gemini_request(model, api_key, [message], max_output_tokens=512)
    else:
        request = LLMRequest(model=model, messages=[message], max_output_tokens=16384)
    summary = await _collect(provider, request)
    if summary.text_deltas:
        return _ok(f"TextDelta приходит (chars={summary.text_chars}), done={summary.done_reason}")
    return _fail(f"TextDelta отсутствует; done={summary.done_reason}")


async def _check_gemini_error_body(provider: LLMProvider, model: str, api_key: str) -> CheckResult:
    """Битый запрос (пустые messages): класс ошибки и raw_code (уточнение 400/429)."""
    request = _gemini_request(model, api_key, [], max_output_tokens=1)
    try:
        async for _ in provider.stream_chat(request):
            pass
    except ProviderError as exc:
        return _ok(f"{type(exc).__name__}: {_format_provider_error(exc)}")
    return _fail("пустой messages принят без ошибки — ожидали 400")


# --- Alibaba checks --------------------------------------------------------


async def _check_alibaba_text(provider: LLMProvider, model: str) -> CheckResult:
    """Базовый text stream: TextDelta/Done/Usage (usage — последним чанком)."""
    request = LLMRequest(model=model, messages=[_user("Скажи «ок»")], max_output_tokens=1024)
    summary = await _collect(provider, request)
    missing = _missing_events(summary)
    if missing:
        return _fail(
            f"нет событий: {', '.join(missing)}; done={summary.done_reason}, "
            f"chars={summary.text_chars}"
        )
    return _ok(f"done={summary.done_reason}, chars={summary.text_chars}{_usage_str(summary)}")


async def _check_alibaba_fc(provider: LLMProvider, model: str) -> CheckResult:
    """Function calling: ожидаем ToolCall echo с валидным arguments_json."""
    request = LLMRequest(
        model=model,
        messages=[_user("Вызови функцию echo с text=привет")],
        tools=[_ECHO_TOOL],
        max_output_tokens=1024,
    )
    summary = await _collect(provider, request)
    for call in summary.tool_calls:
        if call.name != "echo":
            continue
        try:
            arguments = json.loads(call.arguments_json)
        except json.JSONDecodeError:
            return _fail(f"echo вызван, но arguments_json битый: {call.arguments_json[:80]!r}")
        return _ok(f"ToolCall echo, args={json.dumps(arguments, ensure_ascii=False)[:80]}")
    return _fail(f"ToolCall не получен; done={summary.done_reason}, chars={summary.text_chars}")


def _echo_tool_wire() -> dict[str, Any]:
    """OpenAI-формат tools для raw-запросов (DTL probe)."""
    return {
        "type": "function",
        "function": {
            "name": _ECHO_TOOL.name,
            "description": _ECHO_TOOL.description,
            "parameters": _ECHO_TOOL.parameters,
        },
    }


async def _check_alibaba_dtl(client: httpx.AsyncClient, model: str) -> CheckResult:
    """Kimi Dynamic Tool Loading (raw): tools внутри system-сообщения, stream=false.

    В normalized-формате это не выразить — шлём сырой POST /chat/completions
    БЕЗ top-level tools. Фиксируем: 200 + tool_calls / 400 / игнорируется.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "tools": [_echo_tool_wire()]},
            {"role": "user", "content": "вызови echo"},
        ],
        "stream": False,
    }
    response = await client.post("/chat/completions", json=payload)
    if response.status_code != 200:
        body = response.text.replace("\n", " ")[:160]
        return _fail(f"http={response.status_code}: {body}")
    data = response.json()
    choices = data.get("choices") or []
    message = (choices[0].get("message") or {}) if choices else {}
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        names = [(tc.get("function") or {}).get("name") for tc in tool_calls]
        return _ok(f"200 + tool_calls {names} — DTL на этом endpoint работает")
    content = str(message.get("content") or "")[:80]
    return _fail(f"200, но tools из system проигнорированы; content={content!r}")


# --- Probes ----------------------------------------------------------------


async def _probe_gemini_model(
    provider: LLMProvider, definition: ModelDefinition, api_key: str
) -> dict[str, Any]:
    """Все checks одной Gemini-модели (изоляция сбоев — в _run_check)."""
    model = definition.model_id
    checks: dict[str, CheckResult] = {}
    await _run_check(checks, "text_stream", _check_gemini_text(provider, model, api_key))
    for level in definition.thinking_modes:
        coro = _check_thinking_level(provider, model, level, api_key=api_key)
        await _run_check(checks, f"thinking:{level}", coro)
    await _run_check(checks, "image", _check_image(provider, model, api_key=api_key))
    await _run_check(checks, "error_body", _check_gemini_error_body(provider, model, api_key))
    notes = ["internal_only — служебная модель"] if definition.internal_only else []
    return {"checks": checks, "notes": notes}


async def _probe_alibaba_model(
    provider: LLMProvider, client: httpx.AsyncClient, definition: ModelDefinition
) -> dict[str, Any]:
    """Все checks одной Alibaba-модели; image/DTL — по capabilities."""
    model = definition.model_id
    checks: dict[str, CheckResult] = {}
    notes: list[str] = []
    await _run_check(checks, "text_stream", _check_alibaba_text(provider, model))
    for level in definition.thinking_modes:
        coro = _check_thinking_level(provider, model, level)
        await _run_check(checks, f"thinking:{level}", coro)
    if THINKING_OFF in definition.probe_required:
        notes.append("thinking off помечен probe_required в capabilities — см. thinking:off")
    await _run_check(checks, "function_calling", _check_alibaba_fc(provider, model))
    if definition.supports_images:
        await _run_check(checks, "image", _check_image(provider, model))
    else:
        checks["image"] = _skip("vision не поддерживается (registry input_modalities)")
    if model == "kimi-k3":
        await _run_check(checks, "dtl_system_tools", _check_alibaba_dtl(client, model))
        notes.append("DTL проверен raw-запросом: tools внутри system, без top-level tools")
    return {"checks": checks, "notes": notes}


async def _resolve_gemini_key(
    session_factory: async_sessionmaker[AsyncSession],
    crypto: CryptoBox,
    project_name: str | None,
) -> tuple[str, str] | None:
    """(label, api_key) первого enabled-проекта (или по имени); None — enabled нет."""
    async with session_factory() as session:
        projects = await GeminiProjectRepository(session).list_all()
    if project_name is not None:
        project = next((p for p in projects if p.name == project_name), None)
        if project is None:
            raise LookupError(f"проект {project_name!r} не найден в gemini_projects")
        if not project.enabled:
            raise LookupError(f"проект {project_name!r} существует, но disabled")
    else:
        project = next((p for p in projects if p.enabled), None)
    if project is None:
        return None
    api_key = crypto.decrypt(project.encrypted_api_key)
    return f"{project.name} (***{project.key_hint})", api_key


async def _resolve_alibaba_key(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    crypto: CryptoBox | None,
    explicit_key: str | None,
) -> tuple[str, str] | None:
    """(api_key, source): CLI → provider_credentials (БД) → env ALIBABA_API_KEY."""
    if explicit_key:
        return explicit_key, "--alibaba-key (CLI)"
    if crypto is not None:
        try:
            async with session_factory() as session:
                key = await get_provider_api_key(session, crypto, "alibaba", env_fallback="")
            if key:
                return key, "provider_credentials (БД)"
        except Exception as exc:  # БД недоступна — честно пробуем env fallback
            logger.warning("alibaba: ключ из БД не получен (%s), fallback на env", exc)
    if settings.alibaba_api_key:
        return settings.alibaba_api_key, "env ALIBABA_API_KEY"
    return None


async def probe_gemini(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    crypto: CryptoBox | None,
    project_name: str | None,
) -> dict[str, Any]:
    """Probe Gemini через proxy владельца (settings.gemini_base_url, НЕ менять)."""
    result: dict[str, Any] = {"status": "skipped", "models": {}}
    if crypto is None:
        result["error"] = "нет ключей: MASTER_ENCRYPTION_KEY не задан (decrypt невозможен)"
        return result
    try:
        resolved = await _resolve_gemini_key(session_factory, crypto, project_name)
    except Exception as exc:
        result["status"] = "fail"
        result["error"] = f"ключ не получен: {type(exc).__name__}: {str(exc)[:200]}"
        return result
    if resolved is None:
        result["error"] = "нет ключей: enabled-проекты в gemini_projects отсутствуют"
        return result
    project_label, api_key = resolved
    result.update(status="ok", project=project_label, base_url=settings.gemini_base_url)
    client = httpx.AsyncClient(
        base_url=settings.gemini_base_url.rstrip("/"),
        trust_env=False,
        timeout=_TIMEOUT,
    )
    provider = GeminiProvider(base_url=settings.gemini_base_url, http_client=client)
    registry = default_registry()
    try:
        for definition in _registry_models(registry, "gemini"):
            model = definition.model_id
            result["models"][model] = await _probe_gemini_model(provider, definition, api_key)
    finally:
        await client.aclose()
    return result


async def probe_alibaba(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    crypto: CryptoBox | None,
    explicit_key: str | None,
) -> dict[str, Any]:
    """Probe Alibaba Model Studio (settings.alibaba_base_url, trust_env=False)."""
    result: dict[str, Any] = {"status": "skipped", "models": {}}
    resolved = await _resolve_alibaba_key(settings, session_factory, crypto, explicit_key)
    if resolved is None:
        result["error"] = "нет ключей: provider_credentials пуст и ALIBABA_API_KEY не задан"
        return result
    api_key, source = resolved
    result.update(
        status="ok",
        key_source=source,
        key_mask=mask_secret(api_key),
        base_url=settings.alibaba_base_url,
    )
    client = httpx.AsyncClient(
        base_url=settings.alibaba_base_url,
        trust_env=False,  # ADR-004: системные прокси игнорируем
        timeout=_TIMEOUT,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    provider = AlibabaProvider(
        api_key=api_key, base_url=settings.alibaba_base_url, http_client=client
    )
    registry = default_registry()
    try:
        for definition in _registry_models(registry, "alibaba"):
            model = definition.model_id
            result["models"][model] = await _probe_alibaba_model(provider, client, definition)
    finally:
        await client.aclose()
    return result


# --- Отчёт -----------------------------------------------------------------


def _alibaba_recommendations(report: dict[str, Any], registry: ModelRegistry) -> list[str]:
    """Сводка: какие UI thinking-уровни реально показывать по каждой модели."""
    section = report.get("alibaba") or {}
    if not isinstance(section, dict) or section.get("status") != "ok":
        return []
    lines: list[str] = []
    models = section.get("models") or {}
    for definition in _registry_models(registry, "alibaba"):
        model = definition.model_id
        model_report = models.get(model) or {}
        checks = model_report.get("checks") or {}
        text_check = checks.get("text_stream") or {}
        if text_check.get("status") != "ok":
            # Базовый запрос не прошёл (auth/quota/...) — выводы по уровням недостоверны.
            detail = str(text_check.get("detail", ""))[:60]
            lines.append(f"{model}: базовый запрос fail ({detail}) — уровни не оценены")
            continue
        accepted = [
            level
            for level in definition.thinking_modes
            if (checks.get(f"thinking:{level}") or {}).get("status") == "ok"
        ]
        rejected = [
            level
            for level in definition.thinking_modes
            if (checks.get(f"thinking:{level}") or {}).get("status") == "fail"
        ]
        line = (
            f"{model}: показывать [{', '.join(accepted) or '—'}]; "
            f"отклонены [{', '.join(rejected) or '—'}]"
        )
        if THINKING_OFF in definition.thinking_modes:
            verdict = "принят" if THINKING_OFF in accepted else "НЕ принят — скрыть из UI"
            line += f"; OFF {verdict}"
        lines.append(line)
    return lines


def _print_table(report: dict[str, Any]) -> None:
    """Плоская таблица результатов в stdout (detail обрезан до 60 символов)."""
    rows: list[tuple[str, str, str, str, str]] = []
    for provider_id in ("gemini", "alibaba"):
        section = report.get(provider_id)
        if not isinstance(section, dict):
            continue
        if section.get("status") != "ok":
            status = str(section.get("status", "?")).upper()
            rows.append((provider_id, "-", "-", status, str(section.get("error", ""))[:60]))
            continue
        models = section.get("models") or {}
        for model, model_report in models.items():
            for check_name, check in (model_report.get("checks") or {}).items():
                status = str(check.get("status", "?")).upper()
                detail = str(check.get("detail", ""))[:60]
                rows.append((provider_id, model, check_name, status, detail))
    if not rows:
        print("Нет результатов.")
        return
    headers = ("PROVIDER", "MODEL", "CHECK", "STATUS", "DETAIL")
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    line_format = "  ".join(f"{{:<{width}}}" for width in widths)
    print(line_format.format(*headers))
    print(line_format.format(*("-" * width for width in widths)))
    counts = {"OK": 0, "FAIL": 0, "SKIPPED": 0}
    for row in rows:
        print(line_format.format(*row))
        counts[row[3]] = counts.get(row[3], 0) + 1
    print(f"\nИтого: ok={counts['OK']}, fail={counts['FAIL']}, skipped={counts['SKIPPED']}")


def _write_report(report: dict[str, Any]) -> Path:
    """JSON в .agents/reports/probe_YYYYMMDD_HHMMSS.json (каталог создаётся)."""
    reports_dir = _REPO_ROOT / ".agents" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    path = reports_dir / f"probe_{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


# --- CLI -------------------------------------------------------------------


def _reconfigure_stdio() -> None:
    """UTF-8 для stdout/stderr (Windows-консоль cp1251 не тянет юникод)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI: --provider, --alibaba-key, --gemini-project, --strict."""
    parser = argparse.ArgumentParser(
        description="Capability probe: реальные (дёшево) запросы к Gemini/Alibaba"
    )
    parser.add_argument(
        "--provider",
        choices=("gemini", "alibaba", "all"),
        default="all",
        help="кого проверять (по умолчанию all)",
    )
    parser.add_argument(
        "--alibaba-key",
        default=None,
        help="явный ключ Alibaba; иначе provider_credentials (БД), затем env ALIBABA_API_KEY",
    )
    parser.add_argument(
        "--gemini-project",
        default=None,
        help="имя проекта из gemini_projects; иначе первый enabled по rotation_order",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit code 1, если хоть один check fail (skipped за fail не считается)",
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    """Собрать ключи, выполнить probes, вернуть полный отчёт."""
    settings = get_settings()
    crypto = CryptoBox(settings.master_encryption_key) if settings.master_encryption_key else None
    engine = create_engine_from_url(settings.database_url)
    session_factory = make_session_factory(engine)
    report: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    try:
        if args.provider in ("gemini", "all"):
            report["gemini"] = await probe_gemini(
                settings, session_factory, crypto, args.gemini_project
            )
        else:
            report["gemini"] = {"status": "skipped", "error": "не выбран (--provider)"}
        if args.provider in ("alibaba", "all"):
            report["alibaba"] = await probe_alibaba(
                settings, session_factory, crypto, args.alibaba_key
            )
        else:
            report["alibaba"] = {"status": "skipped", "error": "не выбран (--provider)"}
    finally:
        await engine.dispose()
    return report


def _count_failures(report: dict[str, Any]) -> int:
    """Число fail по всем check обоих провайдеров (skipped НЕ считается fail)."""
    failures = 0
    for provider_id in ("gemini", "alibaba"):
        section = report.get(provider_id)
        if not isinstance(section, dict) or section.get("status") != "ok":
            continue  # skipped/fail уровня провайдера (нет ключей) — не check fail
        for model_report in (section.get("models") or {}).values():
            for check in (model_report.get("checks") or {}).values():
                if check.get("status") == "fail":
                    failures += 1
    return failures


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI. 0 — ok; 1 — есть fail при --strict; skipped != fail."""
    _reconfigure_stdio()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # не шумим per-request логами
    args = parse_args(argv)
    print("ВНИМАНИЕ: будут выполнены РЕАЛЬНЫЕ API-запросы к провайдерам (стоимость низкая).")
    report = asyncio.run(_run(args))
    for provider_id in ("gemini", "alibaba"):
        section = report.get(provider_id)
        if not isinstance(section, dict) or section.get("status") != "ok":
            continue
        if provider_id == "gemini":
            print(f"gemini: проект {section.get('project')}, base_url={section.get('base_url')}")
        else:
            print(
                f"alibaba: ключ {section.get('key_mask')} ({section.get('key_source')}), "
                f"base_url={section.get('base_url')}"
            )
    _print_table(report)
    registry = default_registry()
    recommendations = _alibaba_recommendations(report, registry)
    if recommendations:
        alibaba = report["alibaba"]
        if isinstance(alibaba, dict):
            alibaba["recommendations"] = recommendations
        print("\nРекомендации UI thinking-уровней (alibaba):")
        for line in recommendations:
            print(f"  {line}")
    path = _write_report(report)
    print(f"\nJSON-отчёт: {path}")
    failures = _count_failures(report)
    if args.strict and failures:
        print(f"STRICT: fail-проверок: {failures} — exit 1")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
