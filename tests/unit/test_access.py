"""Unit-тесты чистой логики доступа (app.services.access)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.access import (
    GrantView,
    evaluate_access,
    is_model_allowed,
)

NOW = datetime(2026, 9, 18, 12, 0, 0, tzinfo=UTC)


def _grant(
    *,
    status: str = "active",
    expires_at: datetime | None = None,
    requests_per_day: int | None = 100,
    token_limit: int | None = 50000,
    max_concurrent_generations: int = 3,
    can_use_web_search: bool = True,
    can_use_memory: bool = False,
) -> GrantView:
    return GrantView(
        status=status,
        expires_at=expires_at,
        requests_per_day=requests_per_day,
        token_limit=token_limit,
        max_concurrent_generations=max_concurrent_generations,
        can_use_web_search=can_use_web_search,
        can_use_memory=can_use_memory,
    )


# --- owner ---------------------------------------------------------------


def test_owner_allowed_without_grant() -> None:
    perms = evaluate_access(
        is_owner=True, user_status="active", grant=None, allowed_models=None, now=NOW
    )
    assert perms.allowed is True
    assert perms.reason == "ok"
    assert perms.allowed_models is None
    assert perms.can_use_web_search is True
    assert perms.can_use_memory is True
    assert perms.requests_per_day is None
    assert perms.token_limit is None
    assert perms.max_concurrent_generations == 10


def test_owner_allowed_even_if_banned() -> None:
    perms = evaluate_access(
        is_owner=True, user_status="banned", grant=None, allowed_models=None, now=NOW
    )
    assert perms.allowed is True
    assert perms.reason == "ok"


def test_owner_allowed_even_with_expired_grant() -> None:
    grant = _grant(status="revoked", expires_at=NOW - timedelta(days=365))
    perms = evaluate_access(
        is_owner=True,
        user_status="active",
        grant=grant,
        allowed_models={"gpt-5"},
        now=NOW,
    )
    assert perms.allowed is True
    assert perms.reason == "ok"
    assert perms.allowed_models is None  # owner видит все модели


# --- валидный грант -------------------------------------------------------


def test_permanent_grant_allowed_and_fields_propagated() -> None:
    grant = _grant(expires_at=None)
    perms = evaluate_access(
        is_owner=False,
        user_status="active",
        grant=grant,
        allowed_models=None,
        now=NOW,
    )
    assert perms.allowed is True
    assert perms.reason == "ok"
    assert perms.allowed_models is None
    assert perms.can_use_web_search is True
    assert perms.can_use_memory is False
    assert perms.requests_per_day == 100
    assert perms.token_limit == 50000
    assert perms.max_concurrent_generations == 3


def test_future_expiry_allowed() -> None:
    grant = _grant(expires_at=NOW + timedelta(seconds=1))
    perms = evaluate_access(
        is_owner=False,
        user_status="active",
        grant=grant,
        allowed_models=None,
        now=NOW,
    )
    assert perms.allowed is True
    assert perms.reason == "ok"


def test_allowed_models_converted_to_frozenset() -> None:
    grant = _grant()
    perms = evaluate_access(
        is_owner=False,
        user_status="active",
        grant=grant,
        allowed_models={"gemini-2.5-pro", "gpt-5"},
        now=NOW,
    )
    assert perms.allowed is True
    assert perms.allowed_models == frozenset({"gemini-2.5-pro", "gpt-5"})
    assert isinstance(perms.allowed_models, frozenset)


def test_empty_model_set_means_no_models_allowed() -> None:
    grant = _grant()
    perms = evaluate_access(
        is_owner=False,
        user_status="active",
        grant=grant,
        allowed_models=set(),
        now=NOW,
    )
    assert perms.allowed is True
    assert perms.allowed_models == frozenset()
    assert is_model_allowed(perms, "gpt-5") is False


# --- expired --------------------------------------------------------------


def test_expired_one_second_ago_denied() -> None:
    grant = _grant(expires_at=NOW - timedelta(seconds=1))
    perms = evaluate_access(
        is_owner=False,
        user_status="active",
        grant=grant,
        allowed_models=None,
        now=NOW,
    )
    assert perms.allowed is False
    assert perms.reason == "expired"


def test_expired_exactly_now_denied() -> None:
    grant = _grant(expires_at=NOW)
    perms = evaluate_access(
        is_owner=False,
        user_status="active",
        grant=grant,
        allowed_models=None,
        now=NOW,
    )
    assert perms.allowed is False
    assert perms.reason == "expired"


def test_naive_expires_at_treated_as_utc() -> None:
    naive_past = datetime(2026, 9, 18, 11, 59, 59)  # naive, = NOW - 1s в UTC
    grant = _grant(expires_at=naive_past)
    perms = evaluate_access(
        is_owner=False,
        user_status="active",
        grant=grant,
        allowed_models=None,
        now=NOW,
    )
    assert perms.allowed is False
    assert perms.reason == "expired"

    naive_future = datetime(2026, 9, 18, 12, 0, 1)  # naive, = NOW + 1s в UTC
    grant = _grant(expires_at=naive_future)
    perms = evaluate_access(
        is_owner=False,
        user_status="active",
        grant=grant,
        allowed_models=None,
        now=NOW,
    )
    assert perms.allowed is True


# --- banned / suspended / revoked / no_grant -------------------------------


def test_banned_denied_even_with_valid_grant() -> None:
    grant = _grant(expires_at=NOW + timedelta(days=30))
    perms = evaluate_access(
        is_owner=False,
        user_status="banned",
        grant=grant,
        allowed_models={"gpt-5"},
        now=NOW,
    )
    assert perms.allowed is False
    assert perms.reason == "banned"
    assert perms.allowed_models is None
    assert perms.can_use_web_search is False
    assert perms.can_use_memory is False
    assert perms.requests_per_day is None
    assert perms.token_limit is None


def test_no_grant_denied() -> None:
    perms = evaluate_access(
        is_owner=False, user_status="active", grant=None, allowed_models=None, now=NOW
    )
    assert perms.allowed is False
    assert perms.reason == "no_grant"


def test_suspended_grant_denied() -> None:
    grant = _grant(status="suspended")
    perms = evaluate_access(
        is_owner=False,
        user_status="active",
        grant=grant,
        allowed_models=None,
        now=NOW,
    )
    assert perms.allowed is False
    assert perms.reason == "suspended"


def test_revoked_grant_denied() -> None:
    grant = _grant(status="revoked")
    perms = evaluate_access(
        is_owner=False,
        user_status="active",
        grant=grant,
        allowed_models=None,
        now=NOW,
    )
    assert perms.allowed is False
    assert perms.reason == "revoked"


def test_suspended_takes_priority_over_expired() -> None:
    grant = _grant(status="suspended", expires_at=NOW - timedelta(days=1))
    perms = evaluate_access(
        is_owner=False,
        user_status="active",
        grant=grant,
        allowed_models=None,
        now=NOW,
    )
    assert perms.allowed is False
    assert perms.reason == "suspended"


# --- is_model_allowed ------------------------------------------------------


def test_is_model_allowed_denied_permissions() -> None:
    perms = evaluate_access(
        is_owner=False, user_status="active", grant=None, allowed_models=None, now=NOW
    )
    assert is_model_allowed(perms, "gpt-5") is False


def test_is_model_allowed_none_models_means_all() -> None:
    grant = _grant()
    perms = evaluate_access(
        is_owner=False,
        user_status="active",
        grant=grant,
        allowed_models=None,
        now=NOW,
    )
    assert is_model_allowed(perms, "gpt-5") is True
    assert is_model_allowed(perms, "any-model") is True


def test_is_model_allowed_model_in_set() -> None:
    grant = _grant()
    perms = evaluate_access(
        is_owner=False,
        user_status="active",
        grant=grant,
        allowed_models={"gpt-5", "gemini-2.5-pro"},
        now=NOW,
    )
    assert is_model_allowed(perms, "gpt-5") is True
    assert is_model_allowed(perms, "gemini-2.5-pro") is True


def test_is_model_allowed_model_not_in_set() -> None:
    grant = _grant()
    perms = evaluate_access(
        is_owner=False,
        user_status="active",
        grant=grant,
        allowed_models={"gpt-5"},
        now=NOW,
    )
    assert is_model_allowed(perms, "claude-4") is False


def test_is_model_allowed_owner_any_model() -> None:
    perms = evaluate_access(
        is_owner=True, user_status="active", grant=None, allowed_models=None, now=NOW
    )
    assert is_model_allowed(perms, "gpt-5") is True
    assert is_model_allowed(perms, "anything") is True


def test_real_now_does_not_break_permanent_grant() -> None:
    grant = _grant(expires_at=None)
    perms = evaluate_access(
        is_owner=False,
        user_status="active",
        grant=grant,
        allowed_models=None,
        now=datetime.now(UTC),
    )
    assert perms.allowed is True


# --- семантика пустого allowlist vs unrestricted (A03, FIX V2) -----------------


def test_empty_frozenset_denies_every_model() -> None:
    """Пустой allowlist = запрет всех моделей; пустой frozenset НЕ равен None."""
    grant = _grant()
    perms = evaluate_access(
        is_owner=False,
        user_status="active",
        grant=grant,
        allowed_models=frozenset(),
        now=NOW,
    )
    assert perms.allowed is True  # доступ к боту есть, но моделей нет
    assert perms.allowed_models is not None
    assert perms.allowed_models == frozenset()
    for model_id in ("gemini-3.8-flash", "qwen3.8-flash", "kimi-k3", "any-model"):
        assert is_model_allowed(perms, model_id) is False


def test_none_allowed_models_allows_any_model() -> None:
    """None = unrestricted: любая model_id разрешена (включая неизвестные)."""
    grant = _grant()
    perms = evaluate_access(
        is_owner=False,
        user_status="active",
        grant=grant,
        allowed_models=None,
        now=NOW,
    )
    assert perms.allowed_models is None
    for model_id in ("gemini-3.8-flash", "qwen3.8-flash", "unknown-model"):
        assert is_model_allowed(perms, model_id) is True


def test_owner_always_allowed_even_with_empty_allowlist_input() -> None:
    """Owner unrestricted всегда: allowed_models=None независимо от входа."""
    perms = evaluate_access(
        is_owner=True,
        user_status="active",
        grant=None,
        allowed_models=frozenset(),  # даже «пустой список» извне не влияет
        now=NOW,
    )
    assert perms.allowed is True
    assert perms.allowed_models is None
    for model_id in ("gemini-3.8-flash", "unknown-model"):
        assert is_model_allowed(perms, model_id) is True
