# Wave 3 — A27: probe → runtime wiring

Дата: 2026-09-25. Субагент: polyra-alibaba / wave3.

## Scope

- `scripts/smoke_providers.py` — флаг `--write-runtime`, запись результатов probe в `system_settings`.
- `app/api/routes/me.py` — `GET /api/models` с probe-фильтрацией thinking-режимов.
- `tests/unit/test_api.py` — тесты A27 + адаптация существующего теста `/api/models`.

Другие файлы не тронуты. `app/services/settings.py` (`get_probe_capabilities`, `CAPABILITY_PREFIX`, `CAPABILITY_TTL_SECONDS`) уже существовал — используется как есть.

## Файлы и решения

### scripts/smoke_providers.py

- CLI: добавлен `--write-runtime` (`store_true`, default False) в `parse_args`; docstring модуля и usage дополнены.
- `_runtime_entries(report)` — чистая функция извлечения: для каждой модели из секций `gemini`/`alibaba` со статусом `ok` собирает запись:
  - `accepted_thinking` — уровни из checks `thinking:<level>` со status `ok` (порядок = порядок checks = `thinking_modes` из registry; `off` включается наравне с остальными, т.к. `thinking:off` — обычный check);
  - `text_ok` — `text_stream` со status `ok`;
  - `at` — iso timestamp (UTC, seconds);
  - `endpoint` — `"gemini-proxy"` для gemini-секции, `"alibaba"` для alibaba.
  - Модели skipped/not run (секция не `ok` → `models` пуст/отсутствует) в записи не попадают → старые доказательства в БД не затираются.
- `_write_runtime(report)` — создаёт отдельный engine (`_run` к этому моменту уже dispose'нул свой), открывает сессию из `make_session_factory`, upsert через `SystemSettingRepository.set_value(f"{CAPABILITY_PREFIX}{model_id}", value)` + `session.commit()`. Любое исключение (БД недоступна) → `logger.warning` + возврат 0, probe не падает. Импорт `CAPABILITY_PREFIX` из `app.services.settings` — префикс не дублируется.
- Порядок в `main()`: запись ПОСЛЕ формирования `report` (`asyncio.run(_run(args))`), ПЕРЕД печатью сводки/таблицы. Печатается строка о числе записанных моделей (или «ничего не записано»).

### app/api/routes/me.py

- `list_models` теперь принимает `session: SessionDep` (как остальные роуты: `chats.py`, `settings.py`, ...) и грузит `get_probe_capabilities(session)` (TTL-фильтр 7 дней — внутри сервиса, stale-записи до роута не доходят).
- Вынесена `_model_out(model, probe)`:
  - свежая запись с непустым `accepted_thinking` (list) → `thinking_modes = [m for m in model.thinking_modes if m in accepted_thinking]` (per-mode acceptance; фильтр от полного списка registry, т.е. probe-доказательство перекрывает `probe_required`-скрытие);
  - `accepted_thinking == []`, отсутствующая/устаревшая запись → registry как есть (`probe_required`-фильтрация сохранена, контракт не сломан);
  - новое поле `probe_at` — iso-строка `at` свежей записи или `None` (для UI).
- Состав ответа расширен только полем `probe_at`; остальные ключи без изменений.

### tests/unit/test_api.py

- Хелпер `_override_probe_capabilities(monkeypatch, app, probe)`: override `get_db_session` (async-gen фейк) + monkeypatch `me_routes.get_probe_capabilities`.
- Обновлён `test_models_excludes_internal_and_probe_modes`: роут теперь требует сессию → подключён хелпер с пустым probe; в exact-key-set добавлен `probe_at`, проверено `probe_at is None`. Старые assertions (internal_only, probe_required) сохранены.
- Новые тесты:
  - `test_models_probe_record_filters_thinking_modes` — свежая запись `accepted_thinking=["off","high"]` для kimi-k3 → режимы отфильтрованы в порядке registry, `probe_at` выставлен; модель без записи (qwen3.8-flash) — registry как есть, `probe_at is None`.
  - `test_models_probe_empty_accepted_falls_back_to_registry` — `accepted_thinking=[]` → фильтрация НЕ применяется, `probe_at` при этом показывается (запись свежая).
  - `test_models_stale_probe_record_ignored` — контракт роута на входе «stale отфильтрован сервисом» (пустой dict) → registry как есть.
  - `test_get_probe_capabilities_filters_stale_and_malformed` — сервисный уровень через фейк-репозиторий `get_all`: свежая запись возвращается по model_id; stale (TTL+60s), не-dict value, запись без `at`, чужой ключ (`default_model`) — отбрасываются.

## Тесты / проверка

pytest НЕ запускался (по инструкции). Выполнено:

- `py_compile` (venv Python 3.12) для всех трёх файлов — OK.
- Офлайн-прогон `_runtime_entries` на синтетическом отчёте: accepted-порядок, `text_ok`, `endpoint`, skipped-секция → `{}` — OK.
- Офлайн-прогон `_model_out`: без записи / accepted-подмножество / `accepted=[]` / битый `accepted` (не list) — OK.
- Ручной ASGI-прогон (не pytest) `/api/models` с override сессии и probe: фильтрация, `probe_at`, fallback при пустом probe — OK (200, ожидаемые поля).
- Офлайн-прогон `get_probe_capabilities` с фейк-репозиторием (логика теста): TTL/malformed-фильтры — OK.

## Не проверено

- Полный прогон pytest (запрещён инструкцией).
- Реальный `scripts/smoke_providers.py --write-runtime` против живой БД/провайдеров (нужны ключи и PostgreSQL).
- Интеграция UI с полем `probe_at`.
