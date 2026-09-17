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

## M3 — LLM abstractions + providers (NEXT)

- [ ] app/llm: base (LLMProvider/LLMRequest), events (TextDelta/ReasoningDelta/ToolCall/ToolResult/Usage/Error/Done), registry, capabilities (ModelDefinition + thinking mapping)
- [ ] app/llm/providers/alibaba.py (OpenAI-compatible, trust_env=false, reasoning скрыт)
- [ ] app/llm/providers/gemini.py (raw httpx через proxy владельца, SSE :streamGenerateContent)
- [ ] tests: thinking mapping, hidden reasoning, no cross-model fallback, image mapping

## M4..M12

См. PLAN.md §5. Детализация добавляется перед стартом каждого milestone.
