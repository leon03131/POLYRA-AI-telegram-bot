# Round 4 — финальный QA-release ревью (POLYRA @ `4925c40`)

Дата: 2026-09-26. Режим: READ-ONLY (код не менялся; правки только этот отчёт).
Scope: tests/**, .github/workflows/ci.yml, pyproject.toml, Dockerfile, docker-compose.yml, Caddyfile, alembic.ini, app/main.py (lifecycle), scripts/**.
Цель: найти дыры гейтов и баги класса «тест-зелёный — прод-сломан» (эталон: P0 из `0be0524` — TYPE_CHECKING-only импорт `ContextBuilder`, инстанцированный в рантайме `generate()`; все 503 теста были зелёные).

---

## 1. Реальные прогоны (команды / exit codes)

| # | Команда | Результат | Exit |
|---|---------|-----------|------|
| 1 | `.venv\Scripts\python -m pytest tests -q` | **503 passed in 8.36s** (0 failed, 0 skipped, 0 xfail) | 0 |
| 2 | `.venv\Scripts\python -m pytest tests -q -W error::DeprecationWarning` | 503 passed in 7.87s (даже как error) | 0 |
| 3 | `.venv\Scripts\python -m ruff check .` (голый `ruff` не на PATH в dev-среде; в CI ставится pip'ом — ок) | All checks passed! | 0 |
| 4 | `.venv\Scripts\python -m mypy app tests scripts` | Success: no issues found in 156 source files | 0 |
| 5 | `npm run typecheck` (miniapp) | tsc --noEmit, чисто | 0 |
| 6 | `npm run build` (miniapp) | tsc && vite build — 112 modules, dist 280.84 kB js | 0 |
| 7 | import-smoke (скрипт ниже) | 128/129 модулей app импортируются; 1 «провал» — `app.db.migrations.env` (ожидаемо: работает только под `alembic` CLI) | 1* |
| 8 | Part B smoke: TC-only имена в рантайм-позициях | **0 находок на HEAD** | — |
| 9 | Валидация детектора на `git show 0be0524:app/services/generation.py` | флагает ровно `(line 592, 'ContextBuilder')` — P0 ловится | — |
| 10 | Part C smoke: кросс-модульные `from app.x import Y` (app+scripts+tests, вкл. ленивые и relative) | 865 имён, все резолвятся | 0 |
| 11 | Part D smoke: `__all__` всех модулей | все entries существуют | 0 |
| 12 | `.venv\Scripts\python -c "from app.main import main"` | OK, callable | 0 |
| 13 | `.venv\Scripts\python -c "from app.api import create_app"` | OK, callable | 0 |
| 14 | `.venv\Scripts\python scripts\smoke_providers.py --help` | usage напечатан; exit 0 (через `cmd /c`; артефакт `-1` в PS — обрыв пайпа `Select-Object -First`) | 0 |
| 15 | `.venv\Scripts\python -m alembic heads` / `history` | едиственная head `0009`, линейная цепочка 0001→0009 | 0 |
| 16 | `npm ci --dry-run` (miniapp) | lockfile синхронен с package.json | 0 |
| 17 | `python -c "import playwright"` | ModuleNotFoundError — ожидаемо: опциональная lazy-зависимость (см. OK-7) | 1* |

\* — запланированный/ожидаемый ненулевой код, не дефект.

Import-smoke скрипт (временная папка, НЕ в репо): `C:\Users\LEON03~1\AppData\Local\Temp\opencode\import_smoke.py`.
Логика: (A) importlib по каждому `app/**/*.py`; (B) AST: имена, импортированные ТОЛЬКО внутри `if TYPE_CHECKING:`, использованные в рантайм-позициях (Name вне аннотаций и вне TC-блока; аннотации исключены, `from __future__ import annotations` учтён); (C) резолв всех `from app.x import Y` через importlib+hasattr; (D) консистентность `__all__`.

**Вывод по классу 0be0524-P0: на текущем HEAD дефектов этого класса НЕТ (Part B = 0). Но ни один штатный гейт (ruff/mypy/pytest) этот класс не ловит — детектор существует только вне репо (см. P1-1).**

---

## 2. P1 — дыры гейтов, скрывающие прод-баги

### P1-1. Прод-entrypoint `app/main.py` не исполняется ни одним тестом и CI-гейтом
- Доказательство: `grep "app.main" tests/**` → 0 совпадений; `.github/workflows/ci.yml:28-35` — только ruff/mypy/pytest; шага import-smoke / запуска entrypoint нет.
- Класс скрытых багов: import-time ошибки в main.py (NameError/kwargs-мисматч `create_dispatcher`/`create_app`/`uvicorn.Config`), поломка wiring при рефакторинге сигнатур. mypy ловит типы, но не рантайм-импорты; ruff — только синтаксис/линт. `from app.main import main` (прогон 12) сегодня exit 0 — но это ничем не закреплено.
- Fix (рекомендация владельцу, не патч): добавить в CI шаг `python -c "import app.main; import app.api"` + зачислить import_smoke.py (8 сек) или тест-wiring, собирающий объекты main() на фейках.

### P1-2. `GenerationService.generate()` end-to-end не исполняется ни одним тестом
- Доказательство: тесты вызывают только `_consume` (tests/unit/test_generation.py:303-374), `_stream_loop` (test_fix_v2_reaudit.py:284,354; test_fix_v2_stream_contract.py:188), `_save_cancelled` (reaudit:300; usage_ledger:353), `_build_context` (reaudit:441). Вызовов `.generate(` в tests/** — 0 (только `_FakeGenerationService.generate` в test_bot_wiring.py:116).
- Именно здесь жил 0be0524-P0 (generation.py:592). Неисполняемые рантайм-сегменты:
  - `_prepare` (generation.py:968-1134): порядок проверок (busy → unknown model → denial → лимиты), TOCTOU busy-check, создание run + IntegrityError-ветка (A05), `model_copy(update=...)` на effective settings;
  - `_rehydrate_images` (generation.py:619-642): реальный `bot.get_file`/`download_file` и контракт `part.data_base64` — **мягкий контракт между модулями** (`# type: ignore[attr-defined]` на :640; builder читает `getattr(part, "data_base64", None)` — builder.py:59). Если ORM-Part потеряет runtime-атрибут, тесты это не увидят (фейки — SimpleNamespace);
  - `_schedule_maintenance` (generation.py:833-881) + `_spawn_background` (:869-881): fire-and-forget задачи; падение (AttributeError как в 0be0524) маскируется warning'ом в лог — пользовательский ответ уже отправлен;
  - `DraftStreamer` против реального surface aiogram Bot (draft.py:259/287/170: `send_rich_message_draft`, `send_message_draft`, `send_rich_message`) — тесты используют фейк-бота. Сегодня методы в aiogram 3.31.0 есть (проверено импортом, прогон «OK-6»), но смена aiogram-мажора (>=3.31,<4.0 в pyproject) может их переименовать — фейки не заметят.
- Fix-класс: интеграционный тест generate() на фейках с реальным `GenerationService` (session_factory-фейк + MockTransport-провайдер), покрывающий happy path / cancel / provider-fail.

### P1-3. aiogram-хендлеры chat.py/commands.py и middleware не вызываются тестами
- Доказательство: test_bot_wiring.py покрывает только `on_photo_message` (:120), `on_generation_stopped` (:64), `setup_menu_button` (:173), workflow_data (:24). `chat.py:on_text_message` (chat.py:20-41), все хендлеры commands.py (:51-178: /start /help /new /chats /settings /admin, open_chat callback), `AccessMiddleware.__call__` (middleware/access.py:40-105), `on_error` (middleware/errors.py:13-29) — 0 прямых вызовов в тестах.
- Класс скрытых багов: DI по имени параметра (aiogram инъектирует `generation_service`/`session_factory`/`settings` — переименование параметра хендлера роняет ТОЛЬКО рантайм-апдейт), регрессии фильтров (`F.text, ~F.text.startswith("/")`), семантика коммит-before-handler в AccessMiddleware (access.py:97-103 — upsert пользователя фиксируется до хендлера; сломается — «пользователь не сохраняется» без падения).
- Fix-класс: прямой вызов хендлеров с SimpleNamespace-сообщениями (как уже сделано для photos/stop) — дёшево и ловит сигнатуры/DI.

### P1-4. Реальный DB-слой не исполняется ни одним тестом; миграции не сверяются с моделями
- Доказательство: в tests/** 0 skipif/RUN_* гейтов (grep «RUN_\w+|skipif» → 1 совпадение — упоминание в докстринге test_api.py:6; DB-suite фактически не существует в репо); unit-тесты API используют `_BrokenSessionFactory` (test_api.py:49-53). SQL не исполняется нигде: repositories (app/db/repositories/*), `DbProjectStore`/`DbQuotaStore` (app/llm/gemini/store_db.py:26-225 — pg_insert ON CONFLICT, Postgres-only, на SQLite не воспроизвести), `DbSummaryStore` (compactor.py:211), `DbMemoryStore` (extractor.py:126), `abort_stale` (main.py:116, A34-recovery).
- Класс скрытых багов: drift models↔migrations (в CI нет `alembic check`; сегодня head 0009 единственный — проверено прогоном 15, но это не гейт), ошибки колонок/порядка в запросах, невыполнимые constraints на commit-путях, гонки A05/A09 (признано в KNOWN_ISSUES: «формально на живой БД не прогонялось»).
- Fix-класс: CI-джоба с postgres-сервисом (alembic upgrade head + smoke-тест репозиториев) или contract-тесты на aiosqlite для не-pg-specific репозиториев.

### P1-5. Docker-сборка не проверяется в CI
- Доказательство: ci.yml — только jobs python/frontend; `docker build` отсутствует. Dockerfile:11 `COPY miniapp/package.json miniapp/package-lock.json` — переименование любого COPY-пути (app/, scripts/, alembic.ini, miniapp/*) обнаружится только при деплое.
- Класс: «CI зелёный, deploy падает» целиком.

---

## 3. P2 — замечания

### P2-1. CI/Docker воспроизводимость: пинов нет, кэш ≠ lock
- `cache: pip` + `cache-dependency-path: pyproject.toml` — **валидно** (setup-python хэширует файл; но это кэш загрузок pip, НЕ пин версий). Зависимости — свободные диапазоны (pyproject.toml:10-24: `aiogram>=3.31,<4.0`, `pydantic>=2.7,<2.14`, `fastapi>=0.115`…). Свежий CI-ран или docker-ребилд может вытянуть новые минорки → результат гейта «дышит» без изменения кода; образ на проде может получить иные версии, чем протестированы локально. Признано A40-optional (KNOWN_ISSUES:57), но в связке с P1-2 (фейки Bot-surface) риск реален.
- `npm ci` в CI и Dockerfile **корректен**: `miniapp/package-lock.json` закоммичен (git ls-files) и синхронен (`npm ci --dry-run` exit 0).

### P2-2. Healthcheck проверяет только liveness; /ready не используется никем
- compose app healthcheck (docker-compose.yml:51): `GET /health` → `{"ok": True}` безусловно (app/api/app.py:71-74). Readiness `/ready` (app.py:76-85, SELECT 1 → 503) существует, но ни compose, ни Caddy его не опрашивают: падение PostgreSQL не отражается на health-статусе; трафик продолжает идти. Плюс `caddy.depends_on app` без condition (:70-71) — Caddy стартует, пока alembic ещё мигрирует (краткие 502).
- Рекомендация: healthcheck на /ready (или отдельный readiness) + `depends_on: app: condition: service_healthy` для caddy.

### P2-3. Мутабельные class-атрибуты фейков → порядок-зависимость тестов
- `_FakeChatSummaryRepository.summary_row` (test_fix_v2_reaudit.py:195, присваивается в тесте :415 и НЕ сбрасывается после); `_FakeMessageRepository.calls` (:155; сброс только в одном тесте :261); `_FakeGenerationRunRepository.finish_calls` (:173; сброс :262). Аналогичный паттерн в test_fix_v2_usage_ledger.py:293/311 (сброс :332-333 — дисциплина ручная, per-test).
- Сегодня порядок детерминирован (плагина random-order нет, 503 стабильно зелёные), но любой новый тест после A15-теста, рассчитывающий на дефолт `summary_row=None`, унаследует SimpleNamespace прошлого теста. Landmine, не активная бомба.
- Рекомендация: инстанс-состояние в `__init__` или autouse-fixture сброса.

### P2-4. Модульные глобалы в проде
- `_COMPACTION_LOCKS` (app/context/compactor.py:67, :74-76): process-global dict, никогда не чистится (по одному Lock на чат — пренебрежимо, но рост неограничен); создаётся лениво в running loop (в проде один loop — ок; тесты путь не трогают — и это тоже дыра покрытия, см. P1-2). `GenerationRegistry` утечки нет (pop в finally, generation.py:488).
- `SearchManager._entries/_graveyard` (manager.py:79-81, :85-91) — управляемый lifecycle (aclose идемпотентен), ок.

### P2-5. Admin-роуты: тесты проверяют только регистрацию и gate
- test_api.py:8-13 (докстринг), :426 (`POST /api/admin/providers/alibaba/smoke` → «500 = gate пройден»). Тела хендлеров `admin_search.test_backend` (admin_search.py:114-126 → SearchManager.health_check manager.py:301-314) и `_run_alibaba_smoke` (admin_providers.py:84-106) не исполняются. Live-исключение легитимно, но не-сетевая логика внутри (audit-записи, маппинг ошибок) не покрыта. Имя `RUN_API_INTEGRATION` из докстринга ничем не гейтится (реальных skipif нет).

### P2-6. Frontend: тестов нет вообще
- miniapp/package.json:6-10 — только dev/build/typecheck/preview. CI-джоб frontend = `npm ci` + `npm run build` (ci.yml:45-54) — проверяет компилируемость, не поведение. Прогоняется «впустую» относительно рантайм-логики Mini App.

### P2-7. scripts/** в CI не исполняются (только mypy-статика)
- smoke_providers.py / import_gemini_keys.py: поломка CLI (argparse) ловится только вручную. Сейчас `--help` exit 0 (прогон 14). Дёшево добавить `python scripts/smoke_providers.py --help` в CI.

### P2-8. Startup-хрупкость main()
- main.py:115-125: `abort_stale` (БД) и `delete_webhook`/`set_my_commands` (Telegram) выполняются ДО старта uvicorn — при недоступности Telegram (pin IP из extra_hosts устарел — KNOWN_ISSUES) или БД процесс падает и уходит в crash-loop `restart: unless-stopped`. Fail-fast — валидный дизайн, но без backoff-ретрая на delete_webhook шумный рестарт-цикл. Shutdown-порядок (main.py:144-149: llm_stream → search_manager → bot.session → engine) корректен; `search_manager.aclose` глушит ошибки per-backend (manager.py:170-179), `ManagedLLMStream.aclose` идемпотентен (llm_factory.py:93-104). SIGTERM: exec-CMD (Dockerfile:61) → PID1 python → uvicorn ловит SIGTERM → `asyncio.wait(FIRST_COMPLETED)` гасит polling → finally. Логика корректна, но ни разу не тестировалась (P1-1/P1-2).

---

## 4. OK — проверено и чисто

1. **Класс 0be0524-P0 на HEAD отсутствует**: Part B (TC-only имена в рантайме) — 0 находок по всем app/** и scripts/**; детектор провалидирован на старой ревизии (флагает ровно generation.py:592 `ContextBuilder` в версии 0be0524). Регрессионный guard уже есть в тесте A15 (test_fix_v2_reaudit.py:422 `hasattr(generation_module, "ContextBuilder")`) — но он точечный, общий детектор в репо отсутствует.
2. **Кросс-импорт-целостность**: 865 имён `from app.x import Y` (вкл. ленивые внутри функций и relative) резолвятся; `__all__` всех модулей консистентен; `app.api.create_app` / `app.main.main` импортируются.
3. **Секреты**: `.env` и `gemini_keys.txt` в .gitignore, в git НЕ закоммичены (git ls-files), в образ не попадают (.dockerignore:31); содержимое ключей не читалось и в отчёт не попало.
4. **Dockerfile**: multi-stage (node:22-alpine → python:3.12-slim); exec-CMD через `sh -c "... && exec python -m app.main"` (PID1/SIGTERM корректен); non-root appuser (uid 1000); .dockerignore исключает miniapp/node_modules, miniapp/dist, .env, tests; `pip install .` двухфазный (deps-layer → код) — легитимно; `alembic upgrade head` на каждом старте идемпотентен (alembic_version; единая head 0009, миграции 0001-0009 закоммичены).
5. **compose**: pgdata volume (persist ✓); postgres healthcheck pg_isready + `depends_on: condition: service_healthy` — alembic не стартует раньше БД; APP_BIND/APP_PORT интерполяция с безопасным дефолтом 127.0.0.1 (публичный вход — только Caddy); БД-порт не публикуется; caddy profile изолирован; caddy_data/config volumes (сертификаты переживают рестарт).
6. **aiogram 3.31.0 surface**: `send_rich_message_draft` / `send_message_draft` / `send_rich_message` / `MessageGenerationStopped` существуют (живая проверка hasattr — сегодня фейки не врут; см. риск в P1-2).
7. **playwright**: корректно опционален — lazy import + `is_configured()=False` (playwright_google.py:31-40,47-51); тестам/CI не нужен; в Docker его нет — бэкенд честно деградирует в BackendUnavailableError.
8. **CI pytest-конфиг**: `asyncio_mode=auto`, `pythonpath=["."]`, testpaths из pyproject.toml:50-53 — идентично локальному прогону; conftest.py не нужен и его нет; сеть/БД/playwright не требуются (MockTransport, BrokenSessionFactory).
9. **mypy strict**: строгий конфиг с осознанными ослаблениями для tests/* и игнором alembic/migrations (pyproject.toml:55-72); команда в CI совпадает с локальной.
10. **Тестовая гигиена прочая**: 503 стабильно (два прогона), 0 skipped/xfail, DeprecationWarning-чисто; фейки per-instance в стрим-контрактах (FakeStreamer test_fix_v2_stream_contract.py:109-127) — ок; `_gen()` в test_generation.py:51-67 создаёт задачи в текущем loop корректно.

---

## 5. Итог

- **Активных P0 на HEAD не найдено**; класс 0be0524 проверен детектором — чисто.
- Все 4 локальных гейта + miniapp + смоук-прогоны зелёные (раздел 1).
- Главный системный вывод: **гейты проверяют код, но не исполняют прод-пути**. Три наибольшие дыры: (1) entrypoint main() не запускается никем; (2) generate() end-to-end и хендлеры chat/commands/middleware не вызываются; (3) SQL/миграции не проверяются и Docker не собирается в CI. Любая из них способна пропустить баг класса 0be0524 в следующий релиз.
- Быстрые дёшевые усиления (по убыванию эффекта): import-smoke шаг в CI (P1-1), wiring-тест main() на фейках (P1-1/P2-8), direct-call тесты хендлеров chat/commands (P1-3), smoke generate() happy-path (P1-2), `python scripts/smoke_providers.py --help` в CI (P2-7), healthcheck на /ready (P2-2).
