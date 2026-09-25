# Final QA/Release Review — POLYRA (polyra-qa-release)

- **Дата:** 2026-09-25
- **Аудитор:** polyra-qa-release (final review, READ-ONLY)
- **Checkout:** O:\work\aibot, HEAD `e4ac383` (FIX V2 commits `a704923` → `a34e26e` → `478a172` → `8ac67cf` → `e97970f` → `e4ac383`), untracked: `.agents/reports/final-review/` (только отчёты аудита)
- **Метод:** только offline-запуски (pytest/ruff/mypy/tsc), статический анализ кода и конфигов, сверка артефактов. Live-вызовы, Docker, git-мутации — не выполнялись (запрещены заданием; Docker в среде отсутствует).

---

## 1. Результаты реальных запусков

| Команда (из O:\work\aibot) | Результат | Exit code |
|---|---|---|
| `.venv\Scripts\python -m pytest tests -q` | `488 passed in 8.21s` (0 failed, 0 skipped, 0 xfailed; collected 488; повторный прогон — `488 passed in 8.39s`, стабильно, без флаков) | **0** |
| `.venv\Scripts\python -m pytest tests/integration -q` | `24 passed in 3.85s` | **0** |
| `.venv\Scripts\python -m ruff check .` | `All checks passed!` | **0** |
| `.venv\Scripts\python -m mypy app` | `Success: no issues found in 129 source files` | **0** |
| `.venv\Scripts\python -m mypy app tests scripts` | `Success: no issues found in 155 source files` | **0** |
| `npx tsc --noEmit` (miniapp/) | без вывода (ошибок нет) | **0** |

Примечания:
- `python -m pytest` fallback не понадобился — venv рабочий (CPython 3.14.7, pytest 9.1.1).
- `npm run build` не запускался (read-only аудит: build пишет в `miniapp/dist`); артефакт `miniapp/dist/` (index.html + assets) существует, typecheck пройден. Заявление FIX_REPORT «tsc strict + vite PASS, 112 modules» этим аудитом не перепроверено, но не противоречит.
- Live-флаги (RUN_*_INTEGRATION) не использовались; в тестах нет ни одного реального HTTP-клиента — все `httpx.AsyncClient` в тестах создаются с `MockTransport`/`ASGITransport` (grep: tests/integration/test_fix_v2_stream_contract.py:65,81; tests/unit/test_alibaba_provider.py:28; tests/unit/test_search.py:36; tests/unit/test_gemini_provider.py:44; tests/unit/test_api.py:71).
- В тестах **0** `skip`/`xfail`/`TODO`/`FIXME` (grep по tests/** — пусто).

Вывод: локальные quality-гейты реально зелёные и соответствуют заявлению FIX_REPORT_V2.md:26-29 («488 passed», ruff, mypy 155 файлов).

---

## 2. A36 — Тесты не доказывают инварианты / закрепляют ошибки

### 2.1 Provider-to-consumer offline contract suite — СУЩЕСТВУЕТ, качество высокое

`tests/integration/`: 3 файла, 24 теста (12 stream-contract + 5 pool + 7 usage-ledger), все offline. Поведенческие ассерты на события, а не на моки:
- `test_fix_v2_stream_contract.py:149-197` — порядок `TextDelta*, Usage, Done`, Usage строго до Done, Done ровно один; consumer: текст склеен, usage сохранён.
- `test_fix_v2_stream_contract.py:200-217` — EOF без Done → `NetworkError("unexpected EOF")`, partial дошёл, retry нет.
- `test_fix_v2_stream_contract.py:240-274` — fragmented tool-args → ровно один ToolCall с полным JSON.
- `test_fix_v2_usage_ledger.py` — сумма usage по раундам (300/50/12), cancel сохраняет известный usage.
- `test_fix_v2_pool.py:200-216` — `mark_success` ровно один при break+`aclose()` потребителя (контракт A06).

Контракты зафиксированы документом `.agents/POLYRA_FIX_V2_CONTRACTS.md` (§1-§10).

### 2.2 Wrong-behavior ожидания из аудита

| Ожидание из аудита | Статус | Доказательство |
|---|---|---|
| break-on-Done терял usage | **исправлено** | парсеры буферизуют Done до usage (`app/llm/providers/alibaba.py:306-324`; `gemini.py:229-256`); `_consume` закрывает стрим в finally (`app/services/generation.py:858-864`); тесты: test_generation.py:296-312 (usage до Done, usage сохранён), test_fix_v2_stream_contract.py:407-425 |
| over-budget recent window (recent выбрасывался) | **исправлено** | `test_context.py:157-169` «recent не влезает — всё равно сохраняется»; `fits=False` при превышении минимума (test_context.py:391-415) |
| dropping historical images | **частично** | Прод-путь (ContextBuilder): плейсхолдер/bytes сохраняются (test_context.py:222-275), rehydration (`generation.py:542-543`). НО legacy-путь `build_messages` по-прежнему выбрасывает image-части истории, и старые тесты это закрепляют: `test_generation.py:232` «skips_image_parts_in_history», `:246` «drops_image_only_history_message». Путь достижим только при `context_builder=None` (generation.py:533-536), в прод-wiring (main.py:47-51) не встречается — но тест «текущего бага» остался |
| unknown-model fallback | **частично** | Пользовательский отказ добавлен (`generation.py:957-965`: «Сохранённая модель … больше недоступна», без fallback). НО: (а) helper `resolve_model_and_thinking` сохранил внутренний fallback (generation.py:203-207); (б) старый тест fallback-семантики не переписан (test_generation.py:187-194); (в) прямого теста на runtime-отказ (текст пользователю) НЕ найдено — grep по тестам на «больше недоступна»/refusal пуст. FIX_REPORT A25 ссылается на «test_api + npm build», где runtime-ветки нет |

### 2.3 PostgreSQL integration tests

**Реальной БД нет ни в одном тесте.** Все сторы — in-memory фейки (`tests/unit/test_gemini_pool.py` FakeQuotaStore/AtomicFakeQuotaStore), session factory в test_api.py — «сломанная» (test_api.py:46-50, RuntimeError). Это моки, не SQLite и не PostgreSQL.

Гонки покрыты честно, но на фейках: `test_gemini_pool.py:694-705` (atomic reserve: gather двух параллельных reserve, rpm=1 → ровно один), `:723-736` (докстринг открыто признаёт: «In-memory fake без suspension-точек гонку не показывает; … нужен atomic-метод»). FIX_REPORT «Непроверенное» п.1 подтверждает: barrier-тест на реальной PostgreSQL не прогнан.

**Проблема (док-долг):** `tests/unit/test_api.py:5-6` обещает «DB-backed интеграционные тесты (реальная PostgreSQL) — отдельный прогон, включаемый через RUN_API_INTEGRATION=1» — **такого набора в репозитории нет** (grep RUN_API_INTEGRATION по всему проекту: единственное вхождение — этот докстринг). Документация на несуществующий тестовый режим.

### 2.4 Frontend E2E

**Отсутствует.** Нет `miniapp/e2e/`, в `miniapp/package.json` нет test-скриптов (только dev/build/typecheck/preview). FIX_REPORT честно признаёт (п.6 «непроверенное»). KIMI_FIX_PROMPT (строка 712) требовал frontend E2E — не выполнено.

### 2.5 Docker smoke / CI

- **CI workflow отсутствует полностью**: каталога `.github/` нет. Требование A36 (KIMI_FIX_PROMPT.md:714: «CI Python 3.12: весь pytest … Ruff, mypy/pyright; frontend npm ci, tsc, build, E2E») — не выполнено. FIX_REPORT A36 не упоминает CI вовсе.
- Docker smoke: автоматизированного нет; заявлен ручной прогон на VPS (непроверяемо из этой среды). Статически конфиги консистентны (см. A37).

### 2.6 Live opt-in / skip-объяснимость

Live-тестов в pytest нет вообще (безопасно по умолчанию). `scripts/smoke_providers.py:616,637,686-717` — ручной CLI с `--strict` (exit 1 только при fail; skipped ≠ fail). README.md:209-211 упоминает будущие RUN_*_INTEGRATION флаги («планируются») — не реализованы, но и не требуются текущими тестами. Пропусков в текущем suite 0 — объяснять нечего.

### 2.7 «Тесты с assert на моках, доказывающие ничего»

Не найдено. Греп `assert.*\.called|mock\.assert|call_count` по tests/** — только имя теста (`test_consume_tool_call_counted_not_appended`, test_generation.py:315). 14 строк `assert …status_code ==` из сотен — в основном auth-gate параметризации, где статус и есть контракт; парные ассерты на payload присутствуют (например test_api.py:202-211 проверяет состав полей моделей). Форма-тесты ограничены двумя параметризованными route-registration тестами в test_api.py, честно задокументированными докстрингом (строки 8-11: «проверяется только регистрация роутов и gate … Бизнес-логика за ними — integration»).

### 2.8 Итог A36

**PARTIAL.** Реально сделано: offline contract suite (24), исправленные ожидания по Done/usage/recent-window, полный зелёный offline-набор 488, нет skip-маскировок. Не сделано из приёмки A36: CI, frontend E2E, PostgreSQL-тесты на реальной БД, Docker smoke (автоматизация), частично — old-wrong expectations (2 из 4). Статус «fixed» в FIX_REPORT для A36 завышен в части инфраструктуры (CI/E2E), хотя честные оговорки в «непроверенное» есть.

---

## 3. A37 — Deployment

### Сверка (все проверено статически; Docker в среде отсутствует)

| Требование | Статус | Доказательство |
|---|---|---|
| Dockerfile: exec после миграций | ✓ | `Dockerfile:61` — `CMD ["sh", "-c", "alembic upgrade head && exec python -m app.main"]` — python PID 1, получает SIGTERM напрямую |
| Dockerfile: multi-stage, non-root | ✓ | `Dockerfile:6` (node:22-alpine build), `:23` (python:3.12-slim runtime), `:53-54` (appuser uid 1000) |
| compose: profiles | ✓ | `docker-compose.yml:60` — caddy в `profiles: ["caddy"]`; без профиля поднимаются только postgres+app |
| compose: порты/expose | ✓ | postgres без публикации порта (строки 23-26, закомментированный localhost-вариант); app `ports: "${APP_BIND:-127.0.0.1}:${APP_PORT:-8080}:8080"` (41-42) — по умолчанию только localhost, публичный вход через Caddy |
| compose: volumes, healthchecks | ✓ | pgdata/caddy_data/caddy_config (73-75); app healthcheck `/health` (50-55); postgres `pg_isready` (18-22) |
| Caddy HTTPS | ✓ | `Caddyfile:4-5` — `{$DOMAIN} { reverse_proxy app:8080 }` (auto-HTTPS от Let's Encrypt) |
| /health vs /ready | ✓ | `app/api/app.py:71-74` — liveness (ok без проверок); `:76-85` — readiness: `SELECT 1`, 503 при недоступности БД. Бот не проверяется — осознанный дизайн (DB-зависимость), заявлено в docstring |
| Telegram IP pin | ✓ (задокументирован) | `docker-compose.yml:43-46` extra_hosts pin + комментарий «Remove when unneeded»; обоснование в KNOWN_ISSUES.md:3-9. Остался inline в основном compose, а не в отдельном override-файле — допустимо, т.к. прокомментирован и вынесен в KNOWN_ISSUES |
| README deployment | ✓ | README.md:71-145 — быстрый старт, импорт ключей, Caddy-профиль, обновление, локальная разработка (147-164) |
| **SIGTERM graceful shutdown** | **✗ не доказан / статический риск** | см. ниже |

### Проблема A37-B: SIGTERM может не завершать процесс

`app/main.py:131`: `await asyncio.gather(dp.start_polling(bot), uvicorn_server.serve())`. На SIGTERM uvicorn ставит `should_exit` и `serve()` завершается, но `gather` продолжает ждать `dp.start_polling()`, у которого нет триггера выхода (aiogram `start_polling` не ставит свои signal handlers; устанавливает их только обёртка `run_polling`, которая здесь не используется). Итог: HTTP-сервер погашен, бот продолжает поллить; `finally` (main.py:132-137: закрытие llm_stream/search/bot/engine) **не выполняется**; процесс живёт до docker SIGKILL по таймауту stop. Никакого теста на SIGTERM/shutdown в tests/** нет. Заявление FIX_REPORT A37 («exec-фор CMD» и silence про сигналы) не покрывает этот случай; критерий приёмки 13 («SIGTERM корректно завершает bot/api/generation/providers») не доказан.
(Замечание основано на статическом анализе семантики uvicorn/aiogram; живой проверки не было — как и в FIX_REPORT.)

### Прочее
- Backup/restore test (критерий 13/14) — отсутствует, нигде не заявлен как выполненный.
- Quoted/encoded DB-credentials: compose интерполирует `${POSTGRES_*}` в URL (docker-compose.yml:36) — README.md:88-90 честно требует URL-safe символы пароля. ОК.

**Итог A37: PARTIAL/GOOD** — статический профиль сети и контейнеров соответствует требованиям; SIGTERM-цепочка не доказана и статически сомнительна; automated smoke/backup отсутствуют.

---

## 4. A38 — Документация vs код

### 4.1 Выборочная сверка статусов FIX_REPORT_V2 с кодом (спорные пункты)

| ID | Заявлено | Проверка | Вердикт |
|---|---|---|---|
| A05 | partial unique index + IntegrityError→busy | `0009_fix_v2.py:107-113` — `uq_generation_runs_active_chat` (WHERE status IN …); `generation.py:1042-1054` — create run, `except IntegrityError` → busy. Per-user concurrency — in-memory `count_active_for_user` (generation.py:905-910): **межпроцессной гарантии нет** (приложение однопроцессное, но критерий «между процессами» не покрыт). Честная оговорка про barrier-тест есть | **fixed (ограничения честно указаны)** |
| A09 | check_and_reserve_atomic, QuotaReservation | `store_db.py:125-225` — единая транзакция, ON CONFLICT DO UPDATE … WHERE по лимитам, RETURNING, rollback пары; `quota.py:55,97-177` — QuotaReservation, reconcile по окнам резервации; тесты test_gemini_pool.py:694-805 | **fixed (offline-уровень; PostgreSQL-барьер честно помечен непроверенным)** |
| A17 | strict summary, per-chat lock | `compactor.py:67-77` `_COMPACTION_LOCKS` + `_chat_lock`; strict-валидация сводки; `:304-318` boundary guard (не движется назад); тесты test_compactor.py:288-344 (`{} → отказ, граница не двигается`), :345 (`concurrent_compaction_same_chat_serialized` — вторая ждёт lock, LLM не вызван) | **fixed** |
| A24 | пагинация chats, GET /chats/{id} | `chats.py:81-101` (limit/offset/include_archived + total), `:104` GET `/chats/{chat_id}`; memory-пагинация `memory.py:42-51`; UI-файлы miniapp/src/pages/ChatsPage.tsx, MemoryPage.tsx | **fixed** |
| A27 | probe enumerates registry, --strict | `smoke_providers.py:85-87` (модели из `default_registry()`, включая internal), `:637` `--strict`, `:686-717` exit-семантика (fail=1, skipped≠fail); ключи маскируются (probe JSON: `key_mask sk-4...4054`) | **fixed** |
| A28 | /api/admin/models и др. | `admin_models.py:14-18` (GET/PUT + require_owner + audit); admin_memory.py, admin_stats.py существуют; UI AdminModelsPage.tsx, AdminMemoryPage.tsx; docs/API.md:105-109 | **fixed** |
| A34 | abort_stale, drop_pending_updates=False | `main.py:115-119` (abort_stale при startup, commit), `:123` `delete_webhook(drop_pending_updates=False)`; repo `generation_runs.py:79` | **fixed** |
| A35 | stats: by_model/errors/429/gemini_usage; audit pagination | `admin_stats.py:72-107` (model_rows, errors_today, rate_limit_429_today, gemini_usage_rows), `:135-157` (audit limit/offset/action + total) | **fixed** |
| A06 (доп.) | Done буферизуется; pool finally report_success | alibaba.py:306-324; gemini pool `pool.py:318-345` (`finally:` → `report_success` ровно один); интеграционные тесты подтверждают | **fixed** |
| B1/B2 (из qa-release/wave2) | xfail-блокеры, заявлены исправленными | B1: `gemini.py:255-256` — `if not terminal_seen: raise NetworkError(…)`; B2: `generation.py:1167-1176` — cancelled-run `finish()` теперь получает input/output/reasoning tokens (комментарий «B2 (QA)»). Оба бывших xfail-теста сняты и проходят (test_fix_v2_stream_contract.py:428-436; test_fix_v2_usage_ledger.py:328+) | **действительно исправлены — xfail снят не «для вида»** |

### 4.2 Probe-отчёты (что реально выполнялось)

- `probe_20260918_130436.json` (UTF-16, real): **gemini: skipped**; alibaba: только 4 модели (qwen3.8-flash, deepseek-v4.1-flash, glm-5.3, kimi-k3) — qwen3.8-max в JSON нет. Старое утверждение KNOWN_ISSUES:15 «27/27 OK — все 5 моделей» на тот момент не соответствовало сохранённому JSON (это и было предметом A38 исходного аудита). Текст 18.09 в KNOWN_ISSUES не откорректирован.
- `probe_20260924_190515.json` (UTF-16 + `[exit 0]` в хвосте): timestamp `2026-09-24T19:02:18+00:00`; **alibaba: 6 моделей** (включая qwen3.8-max и **deepseek-v4-pro**), статусы чеков: **39 ok / 2 skipped** (image у deepseek-v4-pro и glm-5.3 — text-only by design); **gemini: skipped** (прогон был `--provider alibaba`); base_url = требуемый `https://dashscope.aliyuncs.com/compatible-mode/v1`; секретов нет (маска). Заявление FIX_REPORT «Live probe Alibaba … 39 OK / 0 fail / 3 skipped» **подтверждается** (3 skipped = 2 чека + gemini-provider skip). Заявление N04 «probe … text/thinking/FC/image-skip — всё OK» — подтверждается этим JSON.
- MODEL_REGRESSION_MATRIX.md — заполнена (10 моделей, offline/live-столбцы, live опирается на probe 20260924; gemini-3.8/3.7 честно `not_run`). Соответствует registry (`capabilities.py`: 9 исходных ID + deepseek-v4-pro, все ID сохранены, включая glm-5.3/kimi-k3).

### 4.3 Противоречия/устаревания в доках (найденные)

1. **KNOWN_ISSUES.md:38-40 (п.9)**: «SSRF: DNS rebinding / TOCTOU … принятый риск. Усиление позже: connect на проверенный IP с Host/SNI» — устарело: усиление **уже реализовано** (`app/search/fetcher.py:45-75` PinnedHTTPTransport; тесты test_ssrf.py + rebinding). Аналогично A19 в FIX_REPORT «fixed».
2. **KNOWN_ISSUES.md:22-24 (п.1, датировано 18.09)**: «alembic upgrade head против живой PostgreSQL 16 не выполнялся» — противоречит FIX_REPORT_V2.md:30 («upgrade выполнен на живой PostgreSQL 16 (VPS)»). Секции файла датированы вперемешку (заголовок Deployment 24.09, «Обновлено: 2026-09-18», FIX V2 24.09) — устаревший пункт не перечёркнут/не помечен.
3. **KNOWN_ISSUES.md:33-35 (п.6) и :44-45 (п.11)** — дублируют друг друга (rate limits Telegram drafts).
4. **README.md:252-263 «Известные ограничения»**: «Docker-сборка в dev-среде не выполнялась…», «runtime-проверки провайдеров (capability probes) не выполнялись без реальных ключей» — оба устарели (VPS-деплой и два probe JSON с реальными ключами). README не переписан после FIX V2 в этой секции.
5. **README.md:10-13**: список моделей не включает DeepSeek V4 Pro — N04 п.8 явно требует README. (Vendор-дока при этом обновлена: docs/vendor/ALIBABA.md:67-89 «Update 2026-09-24 — deepseek-v4-pro».)
6. **README.md:246-248**: «Структура проекта» не упоминает tests/integration и scripts/smoke_providers.py; секция «Тесты» (201-211) описывает только unit.
7. **FIX_REPORT_V2.md:93** «Команда: на VPS — см. KNOWN_ISSUES», но в KNOWN_ISSUES VPS-команды barrier-теста нет — ссылка в никуда.
8. TASKS.md:130-135 (FIX V2 DONE) — соответствует артефактам (488 тестов, probe, миграция 0009). Противоречий вида «unchecked tasks после DONE» больше нет.
9. ADR-010 (DECISIONS.md:49-53): «результат кеша probe … кешируется (provider_health)» — **provider_health не существует в коде** (grep пуст); результаты probe зашиты статическими `probe_required` флагами в capabilities.py (18.09) и не версионированы/TTL. Критерий приёмки 12 в этой части не выполнен.

### 4.4 N01 — делегирование (прошлая волна)

`.agents/POLYRA_FIX_V2_DISPATCH.md` существует (34 строки) и содержит:
- **4 research-задачи с реальными session IDs**: `ses_f2c366a8`, `ses_f2c469a4`, `ses_f2c46492`, `ses_f2c45e79` (DISPATCH.md:17-20) — агент `general` (tool `task`), scope, items, даты, статус, включая честную пометку «1st attempt failed: provider 400, retried OK».
- **Wave 1 (4 coding-задачи)**: таблица со scope/items (DISPATCH.md:26-29) — но **без session/task references и без времени** (только «general (profile polyra-*)»).
- **Wave 2**: в DISPATCH.md **не отражена вообще**; существует только как 4 отдельных отчёта `.agents/reports/fix-v2/{polyra-gemini,polyra-miniapp,polyra-qa-release,polyra-telegram}/wave2.md` (даты 24-25.09) с конкретным содержанием (реальные прогоны pytest, найденные дефекты B1/B2 с file:line).

Оценка честности: высокая — дочерние артефакты содержат реальные дефекты и прогоны (не «рисование успехов»); xfail-блокеры были заявлены как дефекты production-кода и впоследствии действительно исправлены. Оценка полноты: **частичная** — заявление FIX_REPORT_V2.md:8-9 «12 реальных child calls … dispatch log с task ids» подтверждается напрямую только для 4 из 12 (research); wave1 без ссылок, wave2 не в логе. Dispatch log не соответствует собственному требованию N01 («время запуска/окончания, session/task reference») для 8 coding-вызовов.

### 4.5 N02 — OpenCode профили

**Выполнено и проверяемо:**
- Все 8 профилей установлены: `.opencode/agents/polyra-*.md` (telegram, miniapp, gemini, alibaba, context, search-security, db-api, qa-release).
- Побайтовая идентичность с overlay пакета подтверждена хешами (8/8 match с `docs/POLYRA_KIMI_HANDOFF_V2/opencode-overlay/.opencode/agents/`).
- Установщик безопасен: `install_opencode_agents.py:49-57` — отказ при любом конфликте/различии («Nothing overwritten»), без symlink-приёмников, без сети/подпроцессов.
- Профили — чистые инструкции: front-matter `mode: subagent` + description, **без model/provider override**.
- Ограничение discovery (custom-имена не перечисляются в task tool → штатные агенты с теми же scope) честно записано в DISPATCH.md:5-7 и FIX_REPORT_V2.md:5-7.

**N02: VERIFIED.**

---

## 5. Общие критерии приёмки (14 пунктов KIMI_FIX_PROMPT.md:761-776)

| # | Критерий (сокращённо) | Статус | Обоснование |
|---|---|---|---|
| 1 | Полный pytest/ruff/mypy + frontend typecheck/build; CI не трогает платные API | **частично** | pytest 488/exit 0 (2 прогона), ruff 0, mypy 155 файлов 0, tsc 0 — всё реальными запусками; **CI отсутствует**; build не перепроверялся (read-only), dist существует |
| 2 | Offline provider-contract: порядок чанков, EOF, cancel, malformed, single Done, usage до Done, unknown ≠ 0 | **выполнено** | tests/integration 24 теста (§2.1); Usage до Done, `[DONE]`/finish, EOF=NetworkError, fragmented args, unknown-usage → None |
| 3 | PostgreSQL real-DB barrier-тесты (one-active-chat, RPM/RPD/TPM, minute/day, reconcile, crash-recovery) | **не выполнено** | только in-memory фейки; честно указано в FIX_REPORT «непроверенное» п.1; abort_stale покрыт unit-репо-тестом |
| 4 | Удаление/архив чата не сбрасывает дневные лимиты; ledger сохраняется | **частично** | код: 0009 SET NULL (chat_id NULL + ON DELETE SET NULL); direct тестов на delete-через-API + сохранение лимитов нет (только миграционная семантика и unit-репо) |
| 5 | UUID E2E: owner/grant/модели/лимиты/empty-list/старый is_owner | **частично** | E2E нет; на API/unit-уровне: empty frozenset deny-all (test_access.py:332-346), stale is_owner 403 (test_api.py), UNSET-vs-null (test_api) — семантика покрыта, сквозного сценария нет |
| 6 | Chat E2E: photo, follow-up, override, архив, пагинация >50, unknown model → ошибка | **частично** | юнит-покрытие фото (test_bot_wiring.py:121-150), override (test_api: chat_out …), пагинация API; runtime-отказ unknown model — код есть, теста нет (§2.2) |
| 7 | Context: coverage boundary, полный бюджет, пустая summary не двигает границу, concurrent compaction | **выполнено** | test_context.py:281-321 (uncovered segment), :391-415 (current image/margin → fits=False), test_compactor.py:288-344 (invalid/empty → граница на месте), :345 (serialization) |
| 8 | Stop ≤2s deadline; без новых side effects; partial без потерь; идемпотентные финализаторы | **частично** | cancel: provider между чанками (тесты), перед каждым tool call (generation.py:713-715 + тесты), cancelled usage сохраняется (B2 fixed); формального deadline-теста «≤2 с» нет; общий deadline 240s (config.py:57) |
| 9 | Telegram renderer: raw `<div>`, `&`, tiers, not-modified, RetryAfter; без обрезаний | **выполнено** | test_draft_streamer.py:210-308, 332-348 (parse_mode=None, not-modified=успех), 401-434 (RetryAfter cooldown без смены tier) |
| 10 | SSRF: direct IP, AAAA, редиректы, rebinding, max bytes; TLS on; результаты поиска — untrusted | **выполнено** | test_ssrf.py + test_search.py (pinned connect, per-hop, rebinding-тест); PinnedHTTPTransport (fetcher.py:45); TLS verify не отключён |
| 11 | Tool policy единая; off-режимы запрещают side effects; limits | **выполнено** | generation.py:488-509 (`_resolve_tool_defs`), test_tools.py:475-476 (unknown tool → denied не error); max calls/round 4 + deadline 240s (config.py:56-57) |
| 12 | Probe всех моделей opt-in; timestamp/TTL/версионирование; RUN_*_флаги | **частично** | probe-скрипт enum из registry + --strict (exit-семантика) ✓; **provider_health-кеш/TTL не существует** (ADR-010 обещал); RUN_*_INTEGRATION нигде не реализован (live-тестов нет вовсе — безопасно, но обещание README «планируются» не выполнено) |
| 13 | Пустая БД upgrade/restart; compose профили; /ready; SIGTERM; HTTPS-профиль | **частично** | /ready 503 при потере БД ✓ (app.py:76-85); compose/Caddy-профили ✓ статически; миграции на существующей БД заявлены (VPS, непроверяемо); **SIGTERM не доказан + статический риск gather (§3 A37-B)**; backup+restore — нет |
| 14 | Backup/restore проверен или честно не проверен; деградация старых записей описана | **частично** | backup-restore не выполнялся и не заявлен выполненным (честно); старые фото-записи без file_id → плейсхолдер «[изображение]» (test_context.py:222-243) — деградация описана тестом, отдельного док-описания нет |

**Сводка: выполнено 5 (№2,7,9,10,11), частично 8 (№1,4,5,6,8,12,13,14), не выполнено 1 (№3).**

---

## 6. Качество тестов: инварианты vs форма

- 423 test-функций → 488 собранных (параметризация). Распределение: integration 24; unit по модулям — search 59, tools 41, gemini_pool 36, compactor 32, generation 32, alibaba 31, draft 28, memory 28, api 34, api_auth 18, ssrf 25, context 24, access 24, gemini_provider 26, registry 7, crypto 7, bot_helpers 6, bot_wiring 6.
- **0** skip/xfail/pytest.mark.skip/TODO/FIXME по tests/**.
- Нет «assert mock.called»-паттернов; ассерты на payload/события/состояния. A11 (cancel перед side effects), A06 (single Done + aclose), A09 (atomic reserve, reconcile-окна), A17 (lock+strict), A22 (RetryAfter), A19 (pinned IP) — все имеют поведенческие тесты.
- Форма-тесты: 2 параметризованных route-registration теста в test_api.py (~30 cases; «500 = gate пройден» при сломанной БД) — задокументированы как проверка регистрации/gate, бизнес-логика вынесена в сервисные тесты. Это ~6% набора.
- Сентинел-механика бывших xfail-блокеров отработала правильно: B1/B2 были честно помечены xfail(strict) с file:line-обоснованием, позже исправлены в коде (gemini.py:255-256; generation.py:1171-1174), маркеры сняты, тесты зелёные — проверено этим аудитом по коду.
- Открытые слабости: legacy `build_messages`-тесты закрепляют выбрасывание фото-истории (test_generation.py:232,246); runtime-отказ unknown-модели не протестирован напрямую; обещанный RUN_API_INTEGRATION-набор не существует.

Оценка: ~94-95% тестов проверяют runtime-инварианты, ~5-6% — форму (регистрация роутов/гейты).

---

## 7. Сводный вердикт

**Реально подтверждено (артефактами и запусками):**
- Offline-гейты: pytest 488 / ruff / mypy / tsc — все exit 0, стабильно, без skip-маскировок.
- Интеграционный stream/usage/pool-контракт (24 теста) — ключевые инварианты A06/A07/A09/A23 доказаны offline.
- Сэмплированные статусы FIX_REPORT (A05, A06, A09, A17, A24, A27, A28, A34, A35, B1, B2) соответствуют коду.
- Registry сохраняет все 10 model IDs (включая новые glm-5.3/kimi-k3 и добавленный deepseek-v4-pro: text-only, без LOW — по N04).
- Probe 2026-09-24: реальный прогон Alibaba 6 моделей (39 ok / 2 check-skip), подтверждает live-статусы матрицы.
- N02: 8 профилей установлены штатно и идентичны пакету.
- Честность limitations-секций FIX_REPORT в основном высокая (PostgreSQL-барьеры, frontend E2E, A29 live не выдаются за проверенное).

**Главные расхождения/риски:**
1. **A36 «fixed» завышен**: CI отсутствует (требование приёмки A36), frontend E2E отсутствуют, PostgreSQL-тестов на реальной БД нет, Docker smoke не автоматизирован. (2 из 4 wrong-тестов переписаны; 2 — частично.)
2. **A37: SIGTERM-цепочка не доказана и статически сомнительна** (gather ждёт start_polling после выхода uvicorn; finally-кleanup не выполнится). Тестов shutdown нет.
3. **A38: доки частично устарели**: KNOWN_ISSUES п.1/п.9 (SSRF-«риск» уже исправлен; «миграции не выполнялись» vs VPS), дубли п.6/11; README (модели без Pro; устаревшие ограничения; tests/unit-only; отсутствие smoke_providers в структуре); RUN_API_INTEGRATION — ссылка на несуществующий набор; provider_health из ADR-010 не реализован; «Команда: на VPS» без команды.
4. **N01 частично**: session-refs доказуемы для 4 из 12 child calls; wave2 не внесена в DISPATCH.md.
5. **Мелкие**: per-user concurrency — только in-process (in-memory registry) — межпроцессная защита не заявлена и не обеспечена (однопроцессный деплой это компенсирует).

**Рекомендации (для lead):**
- Добавить CI workflow (pytest/ruff/mypy + npm ci/tsc/build) — закрывает A36-приёмку и критерий 1.
- Убрать/переписать legacy image-drop тесты (build_messages) либо пометить путь как явно unsupported; добавить прямой тест runtime-отказа unknown/disabled модели (generation.py:957-965).
- Реализовать или удалить упоминание RUN_API_INTEGRATION; согласовать README (модели, тесты, ограничения) и KNOWN_ISSUES (п.1/п.9/дубли) с фактом FIX V2.
- Доказать SIGTERM-shutdown тестом (или переделать main-цикл на cancellation при выходе uvicorn), иначе честно пометить A37-остаток.
- Дополнить DISPATCH.md wave2-строками (по имеющимся wave2-отчётам) с реальными references.
