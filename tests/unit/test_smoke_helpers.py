"""Юнит-тесты чистых хелперов scripts/smoke_providers.py (A27/N03, P1 round4).

scripts/ — не пакет, поэтому модуль загружается по пути файла через
importlib.util.spec_from_file_location. БД и сеть не нужны: покрыты только
чистые функции — классификация сбоев чеков (rejected/transient), извлечение
свежих вердиктов из отчёта и merge прежней capability-записи со свежей
(transient-сбой НЕ стирает подтверждённые ранее режимы/text_ok).
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from app.llm.errors import (
    AuthError,
    InvalidRequestError,
    NetworkError,
    RateLimitError,
    ServerError,
    TimeoutError_,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_script_module() -> ModuleType:
    """Загрузить scripts/smoke_providers.py как модуль (scripts — не пакет)."""
    spec = importlib.util.spec_from_file_location(
        "smoke_providers_under_test", _REPO_ROOT / "scripts" / "smoke_providers.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["smoke_providers_under_test"] = module
    spec.loader.exec_module(module)
    return module


smoke = _load_script_module()

_OLD_AT = "2026-09-01T00:00:00+00:00"
_NEW_AT = "2026-09-26T12:00:00+00:00"


def _old_record(modes: list[str], text_ok: bool = True) -> dict[str, Any]:
    """Прежняя запись capability_probe (формат system_settings)."""
    return {
        "accepted_thinking": modes,
        "text_ok": text_ok,
        "at": _OLD_AT,
        "endpoint": "alibaba",
    }


def _fresh_verdict(
    accepted: list[str],
    rejected: list[str] | None = None,
    text_ok: bool | None = True,
) -> dict[str, Any]:
    """Свежий вердикт probe (выход _runtime_entries, вход merge)."""
    return {
        "accepted_thinking": accepted,
        "rejected_thinking": rejected or [],
        "text_ok": text_ok,
        "at": _NEW_AT,
        "endpoint": "alibaba",
    }


def _check(status: str, kind: str | None = None) -> dict[str, str]:
    result = {"status": status, "detail": f"detail for {status}"}
    if kind is not None:
        result["kind"] = kind
    return result


# --- _merge_runtime_entry: семантика N03 --------------------------------------


def test_transient_fail_keeps_old_accepted_and_text_ok() -> None:
    """P1-репро: transient thinking-чек НЕ стирает подтверждённый режим."""
    old = _old_record(["off", "low", "medium"], text_ok=True)
    # fresh: low/medium — 429 (transient, вердикта нет); off — ok; text — 429
    fresh = _fresh_verdict(accepted=["off"], text_ok=None)
    merged = smoke._merge_runtime_entry(old, fresh)
    assert set(merged["accepted_thinking"]) == {"off", "low", "medium"}
    assert merged["text_ok"] is True
    assert merged["at"] == _NEW_AT
    assert merged["endpoint"] == "alibaba"


def test_rejected_removes_old_mode() -> None:
    """Свежий явный rejection провайдера убирает режим из accepted."""
    old = _old_record(["off", "low"])
    fresh = _fresh_verdict(accepted=["off"], rejected=["low"], text_ok=True)
    merged = smoke._merge_runtime_entry(old, fresh)
    assert merged["accepted_thinking"] == ["off"]
    assert merged["text_ok"] is True


def test_functional_fail_removes_old_mode() -> None:
    """Функциональный fail (без kind) — негативный вердикт, режим убирается."""
    old = _old_record(["off", "low"])
    fresh = _fresh_verdict(accepted=["off"], rejected=["low"], text_ok=False)
    merged = smoke._merge_runtime_entry(old, fresh)
    assert merged["accepted_thinking"] == ["off"]
    assert merged["text_ok"] is False


def test_fresh_ok_adds_mode_without_duplicates() -> None:
    """Свежий ok добавляет режим; уже подтверждённый не дублируется."""
    old = _old_record(["low"])
    fresh = _fresh_verdict(accepted=["low", "high"], text_ok=True)
    merged = smoke._merge_runtime_entry(old, fresh)
    assert merged["accepted_thinking"] == ["low", "high"]


def test_no_old_record_writes_only_fresh_ok() -> None:
    """Без прежней записи — только свежие ok-вердикты (поведение до merge)."""
    fresh = _fresh_verdict(accepted=["off"], rejected=["low"], text_ok=False)
    merged = smoke._merge_runtime_entry(None, fresh)
    assert merged["accepted_thinking"] == ["off"]
    assert merged["text_ok"] is False
    assert merged["at"] == _NEW_AT


def test_invalid_old_treated_as_absent() -> None:
    """Битая старая запись (не те типы) игнорируется, merge не падает."""
    old = {"accepted_thinking": "garbage", "text_ok": "yes", "at": 123, "endpoint": None}
    fresh = _fresh_verdict(accepted=["high"], text_ok=None)
    merged = smoke._merge_runtime_entry(old, fresh)
    assert merged["accepted_thinking"] == ["high"]
    assert merged["text_ok"] is False  # old text_ok невалиден → не True
    assert merged["at"] == _NEW_AT


def test_old_invalid_mode_items_dropped_valid_kept() -> None:
    """В старом accepted смешан мусор: строки остаются, не-строки отбрасываются."""
    old = {
        "accepted_thinking": ["low", 42, None, True],
        "text_ok": True,
        "at": _OLD_AT,
        "endpoint": "alibaba",
    }
    fresh = _fresh_verdict(accepted=[], text_ok=None)  # всё transient
    merged = smoke._merge_runtime_entry(old, fresh)
    assert merged["accepted_thinking"] == ["low"]
    assert merged["text_ok"] is True


def test_transient_text_fail_without_old_defaults_false() -> None:
    """text-чек transient при отсутствии old → text_ok=False (не изобретаем)."""
    fresh = _fresh_verdict(accepted=["off"], text_ok=None)
    merged = smoke._merge_runtime_entry(None, fresh)
    assert merged["text_ok"] is False


def test_transient_text_fail_old_false_stays_false() -> None:
    """transient не «воскрешает» прежний false."""
    old = _old_record(["off"], text_ok=False)
    fresh = _fresh_verdict(accepted=["off"], text_ok=None)
    merged = smoke._merge_runtime_entry(old, fresh)
    assert merged["text_ok"] is False


def test_all_transient_updates_only_at_endpoint() -> None:
    """Ни одного свежего вердикта: accepted/text_ok не меняются, at обновляется."""
    old = _old_record(["off", "low"], text_ok=True)
    fresh = _fresh_verdict(accepted=[], text_ok=None)
    merged = smoke._merge_runtime_entry(old, fresh)
    assert merged["accepted_thinking"] == ["off", "low"]
    assert merged["text_ok"] is True
    assert merged["at"] == _NEW_AT  # свежесть записи обновлена
    assert merged["endpoint"] == "alibaba"


def test_stored_record_shape_unchanged() -> None:
    """Итог merge — те же 4 ключа, что читает GET /api/models (формат A27)."""
    merged = smoke._merge_runtime_entry(_old_record(["low"]), _fresh_verdict(["low"]))
    assert set(merged) == {"accepted_thinking", "text_ok", "at", "endpoint"}


# --- _runtime_entries: извлечение вердиктов из отчёта -------------------------


def _qwen_report() -> dict[str, Any]:
    """Оффлайн-репро P1: qwen thinking:low — 429, medium — 400 rejected."""
    return {
        "alibaba": {
            "status": "ok",
            "models": {
                "qwen3.8-flash": {
                    "checks": {
                        "text_stream": _check("fail", "transient"),
                        "thinking:off": _check("ok"),
                        "thinking:low": _check("fail", "transient"),
                        "thinking:medium": _check("fail", "rejected"),
                        "thinking:max": _check("fail"),  # функциональный fail
                        "function_calling": _check("ok"),
                        "image": _check("skipped"),
                    },
                }
            },
        }
    }


def test_runtime_entries_classifies_check_kinds() -> None:
    entries = smoke._runtime_entries(_qwen_report())
    entry = entries["qwen3.8-flash"]
    assert entry["accepted_thinking"] == ["off"]
    # rejected + функциональный fail — негативные вердикты
    assert entry["rejected_thinking"] == ["medium", "max"]
    assert entry["text_ok"] is None  # text_stream transient — вердикта нет
    assert entry["endpoint"] == "alibaba"
    assert isinstance(entry["at"], str) and entry["at"]


def test_runtime_entries_merge_preserves_transient_modes() -> None:
    """Полный P1-сценарий: 429 на thinking:low не прячет low на 7 дней."""
    entries = smoke._runtime_entries(_qwen_report())
    old = _old_record(["off", "low"], text_ok=True)
    merged = smoke._merge_runtime_entry(old, entries["qwen3.8-flash"])
    # low подтверждён ранее, свежего негативного вердикта по нему нет — сохранён
    assert set(merged["accepted_thinking"]) == {"off", "low"}
    assert merged["text_ok"] is True  # text_stream тоже 429 — не сброшен


def test_runtime_entries_skips_provider_not_ok() -> None:
    """Секция провайдера не ok → моделей нет, старые записи не затрагиваются."""
    report = {
        "alibaba": {"status": "skipped", "error": "нет ключей"},
        "gemini": {"status": "fail", "models": {"gemini-3.8-flash": {"checks": {}}}},
    }
    assert smoke._runtime_entries(report) == {}


def test_runtime_entries_tolerates_malformed_models() -> None:
    """Битые model_report/чеки не роняют извлечение (валидация isinstance)."""
    report = {
        "alibaba": {
            "status": "ok",
            "models": {
                "qwen3.8-flash": {"checks": None},
                "garbage-entry": "not-a-dict",
                "glm-5.3": {
                    "checks": {
                        "text_stream": _check("ok"),
                        "thinking:low": "not-a-dict-check",
                        "thinking:high": _check("ok"),
                    }
                },
            },
        }
    }
    entries = smoke._runtime_entries(report)
    assert set(entries) == {"qwen3.8-flash", "glm-5.3"}
    assert entries["qwen3.8-flash"]["text_ok"] is None
    glm = entries["glm-5.3"]
    assert glm["accepted_thinking"] == ["high"]
    assert glm["rejected_thinking"] == []
    assert glm["text_ok"] is True


def test_runtime_entries_missing_text_check_keeps_old() -> None:
    """text_stream-чека нет вовсе → вердикта нет, merge оставит прежнее."""
    report = {
        "alibaba": {
            "status": "ok",
            "models": {"glm-5.3": {"checks": {"thinking:low": _check("ok")}}},
        }
    }
    entries = smoke._runtime_entries(report)
    merged = smoke._merge_runtime_entry(_old_record(["high"], text_ok=True), entries["glm-5.3"])
    assert merged["accepted_thinking"] == ["high", "low"]
    assert merged["text_ok"] is True


# --- классификация ошибок провайдера ------------------------------------------


def test_provider_error_kind_invalid_request_is_rejected() -> None:
    exc = InvalidRequestError("invalid parameter thinking", status_code=400)
    assert smoke._provider_error_kind(exc) == "rejected"


def test_provider_error_kind_transient_categories() -> None:
    for exc in (
        RateLimitError("throttling", status_code=429),
        ServerError("internal", status_code=500),
        NetworkError("connection reset"),
        TimeoutError_("read timeout"),
        AuthError("InvalidApiKey", status_code=401),
    ):
        assert smoke._provider_error_kind(exc) == "transient", exc.category


def test_fail_kind_field_only_when_given() -> None:
    plain = smoke._fail("нет событий")
    assert plain == {"status": "fail", "detail": "нет событий"}
    transient = smoke._fail("category=rate_limit, http=429", kind="transient")
    assert transient["kind"] == "transient"
    assert transient["status"] == "fail"
