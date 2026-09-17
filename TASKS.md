# TASKS — трекер

Легенда: [ ] todo, [~] in progress, [x] done. Дата старта: 2026-09-18.

## M0 — Research + vendor docs

- [x] R-TG: Bot API 10.3 подтверждён, aiogram 3.31.0 покрывает drafts/rich/stop нативно
- [x] R-GEMINI: все 4 модели подтверждены; thinking_level; raw httpx адаптер (base_url риск)
- [x] R-ALIBABA: все 5 моделей подтверждены; маппинг thinking; противоречия зафиксированы -> probe
- [x] R-SEARCH: Serper primary, SerpApi AIO 2-step, Brave, Jina; таймауты/маппинг
- [x] docs/vendor/TELEGRAM_BOT_API.md
- [x] docs/vendor/TELEGRAM_MINI_APPS.md
- [x] docs/vendor/AIOGRAM.md
- [x] docs/vendor/GEMINI.md
- [x] docs/vendor/ALIBABA.md
- [x] docs/vendor/SEARCH_BACKENDS.md

## M1 — Skeleton + users/access  ✅ DONE (2026-09-18)

- [x] pyproject.toml (deps, ruff, mypy, build-system), .env.example, .gitignore
- [x] app/config.py (pydantic-settings), app/observability/logging.py (redaction)
- [x] app/security/crypto.py (Fernet CryptoBox + mask_secret)
- [x] app/db: base/session, модели users/access_grants/user_model_permissions/user_settings,
      Alembic + миграция 0001
- [x] repositories (users/access) + services/access.py (чистая логика)
- [x] tests: test_access.py (18), test_crypto.py (7) — 25+ тестов зелёные
- Гейты: ruff ✓, mypy strict ✓, pytest ✓

## M2 — Telegram basic bot + chat persistence  ✅ DONE (2026-09-18)

- [x] Модели chats/messages/message_parts/generation_runs + миграция 0002 (alembic heads ✓)
- [x] ChatRepository, MessageRepository; app/services/chats.py (ChatService, current chat)
- [x] AccessMiddleware (numeric id, owner=795063564, GrantView/evaluate_access, session-per-update)
- [x] Errors handler (без утечки traceback пользователю)
- [x] /start (menu button ⚙️ Настройки), /help, /new, /chats (inline switch), /settings, /admin (owner)
- [x] chat router (text → save, заглушка M3), photos router (file_id+metadata, лимит 15 МБ, M3)
- [x] stop router (stopped_message_generation зарегистрирован, отмена — M5)
- [x] app/main.py: long polling, delete_webhook, setup commands
- [x] tests: test_bot_helpers.py (+4) — всего 32 зелёные; ruff ✓, mypy ✓ (42 файла)
- Исправлено при интеграции: shadowing `text` в MessagePart (sql_text), alembic.ini → ASCII

## M3 — LLM abstractions + providers  ✅ DONE (2026-09-18)

- [x] app/llm: base/events/errors (единая таксономия ProviderError)/capabilities/registry/router
- [x] ModelRegistry: 9 моделей из ТЗ, thinking_modes, internal_only для 3.5-flash-lite
- [x] providers/gemini.py: raw httpx SSE :streamGenerateContent, thought→ReasoningDelta,
      thoughtSignature capture, usage, safety, 429 RetryInfo.retry_after
- [x] providers/alibaba.py: OpenAI-compatible, trust_env=false, thinking-таблица по 5 моделям,
      tool_calls accumulation, include_usage, bounded retry (2 попытки до первого события)
- [x] tests: 18 gemini + 19 alibaba + 16 registry/access — 85 всего; ruff ✓ mypy ✓
- Интеграционные правки: рефакторинг SSE-парсеров (C901), фиксы тестовых helpers

## M4 — Gemini project pool  ✅ DONE (2026-09-18)

- [x] Модели gemini_projects/quota_policies/quota_minute_usage/quota_daily_usage + миграция 0003
- [x] repositories/gemini.py (projects CRUD, move, health, cooldown, quota pg_insert ON CONFLICT)
- [x] llm/gemini/quota.py (UTC minute window, Pacific day, QuotaTracker) + pool.py
      (round-robin, cooldowns по ADR-005, stream_with_failover, PoolExhaustedError)
- [x] llm/gemini/store_db.py (DB-адаптеры ProjectStore/QuotaStore, session_factory)
- [x] tzdata добавлена (Windows ZoneInfo)
- [x] tests: 19 pool-тестов (все правила ADR-005 + race + cancel) — 104 всего; ruff/mypy ✓

## M5 — streaming + cancellation (NEXT)

- [ ] bot/streaming/draft.py (rich draft → message draft → throttled edit; throttle; sanitize)
- [ ] services/generation.py + GenerationRegistry (cancel по (chat_id, draft_id))
- [ ] provider_credentials (таблица + миграция 0004) + seed quota_policies (4/249999/19)
- [ ] wiring: chat.py/photos.py → реальная генерация; stop.py → отмена
- [ ] scripts/import_gemini_keys.py
- [ ] tests: stream buffer, stop cancels, partial persist, photo flow

## M6..M12

См. PLAN.md §5. Детализация добавляется перед стартом каждого milestone.
