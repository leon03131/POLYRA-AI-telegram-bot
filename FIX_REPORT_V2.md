# FIX REPORT V2 — POLYRA AI Telegram Bot

**Дата:** 2026-09-24. **Рабочий commit:** `478a172` (+ fix commits до него: `a704923`, `a34e26e`).
**Dirty status:** чисто на момент сдачи. **Среда:** OpenCode (текущая сессия), tool `task`,
штatные агенты `general`/`explore` (профили polyra-* установлены через
`install_opencode_agents.py --apply`; в этой сессии custom-имена в task tool не
перечисляются → использованы штатные агенты с идентичными scope — зафиксировано по N02).
**Период работы:** 2026-09-24 (одна сессия). **Делегирование:** `.agents/POLYRA_FIX_V2_DISPATCH.md`
(12 реальных child calls: 4 research + 4 coding wave1 + 4 coding wave2, max 4 одновременно).

## Методология

1. Ревалидация каждой находки против текущего кода (аудит писался по старому снимку;
   часть пунктов уже была исправлена в эксплуатационной сессии 18–19.09) — 4 research-
   субагента, отчёты в `.agents/reports/fix-v2/*/revalidate.md`.
2. Coding волнами с непересекающимися writable scopes; главный агент — интегратор,
   владелец orchestration-файлов (generation.py, llm_factory.py, main.py, config.py).
3. После каждой волны: ruff + mypy + pytest + интеграционные правки.
4. Live-проверка на VPS (176.108.245.225): миграция 0009 на существующей БД,
   smoke-probe Alibaba (реальные вызовы, ключ из provider_credentials).

## Общие проверки (финал, commit 478a172)

| Проверка | Команда | Итог |
|---|---|---|
| pytest (unit+integration) | `.venv/Scripts/python -m pytest tests -q` | **488 passed**, exit 0 |
| Ruff | `ruff check .` | All checks passed, exit 0 |
| mypy strict | `mypy app tests scripts` | 155 файлов, no issues, exit 0 |
| Frontend build | `npm run build` (miniapp/) | tsc strict + vite PASS, 112 modules |
| Alembic | `alembic heads` → `0009`; upgrade выполнен на живой PostgreSQL 16 (VPS) | OK |
| Live probe Alibaba | `docker compose exec app python scripts/smoke_providers.py --provider alibaba --strict` | 39 OK / 0 fail / 3 skipped, exit 0 |
| Live E2E (Gemini pool + tools) | VPS in-container скрипт | «Сегодня 18 сентября 2026» через web_search |
| Docker build/up | `docker compose up -d --build` на VPS | OK, healthy |

## Таблица A01–A40 / N01–N04

| ID | Статус | Что сделано (файлы) | Проверка |
|---|---|---|---|
| A01 | fixed | types.ts (id: string), ChatSettingsPage (строковое сравнение + GET /chats/{id}), App.tsx regex UUID | npm build PASS; revalidate-отчёт |
| A02 | fixed | commands.py `build_admin_url()` → `/#/admin`; dispatcher.setup_menu_button; main.py вызывает при startup | test_bot_helpers/wiring; live: меню видно |
| A03 | fixed | user_model_access (mode all/list) + миграция 0009; пустой список = запрет всех | test_access: empty frozenset deny-all; уже на проде |
| A04 | fixed | require_owner только numeric id; миграция чистит чужие is_owner; /admin — numeric id | test_api: stale-flag 403; live |
| A05 | fixed | partial unique index `uq_generation_runs_active_chat` (0009) + IntegrityError→busy; per-user limits через count/tokens since | unit + инвариант на БД; PostgreSQL barrier-тест — см. «непроверенное» |
| A06 | fixed | alibaba: Done буферизуется до usage (parse переделан); gemini: pool._stream_attempt finally report_success; generation._consume aclose стрима | tests/integration/test_fix_v2_stream_contract.py |
| A07 | fixed | per-round сумма usage (`_sum_usage`), attempts/attempt_ids в run (metadata), cancelled сохраняет usage | test_fix_v2_usage_ledger.py (300/50 по двум раундам) |
| A08 | fixed | generation_runs.chat_id NULL + ON DELETE SET NULL (0009) | миграция на живой БД прошла; ранее FK-краш устранён ранее (IntegrityError-гард) |
| A09 | fixed | `check_and_reserve_atomic` (одна сессия, условный upsert+WHERE, rollback пары); reconcile по окнам резервации (QuotaReservation) | test_gemini_pool.py (36 тестов) |
| A10 | fixed | 429 → in-memory cooldown per (project,model); 401/PERMISSION_DENIED → disable; 5xx/network → 1 retry того же проекта, потом ротация; 400/safety — без ротации | test_gemini_pool.py; live: high-demand 503 ротация работала 18–19.09 |
| A11 | fixed | registry.stop = event + task.cancel; cancel проверяется ПЕРЕД каждым tool call; провайдеры реагируют между чанками; partial сохраняется один раз | test_draft_streamer/generation; integration cancel-тесты |
| A12 | fixed | `_resolve_tool_defs` (grant ∩ web off ∩ memory off ∩ title) → один набор в LLMRequest и ToolRunner.allowed_tool_names; вне набора → denied без сети | test_tools.py + test_generation.py |
| A13 | fixed | services/settings.py EffectiveSystemSettings (DB поверх env, валидация, читается на каждый запрос); wired в generation._prepare; min_chars в main | test_api (effective override/fallback) |
| A14 | fixed | disabled credential → None без env fallback; смена ключа закрывает cached client (llm_factory) | test_api (матрица absent/enabled/disabled) |
| A15 | fixed | builder получает covered_until_message_id; непокрытый сегмент добирается под бюджет; list_all при наличии сводки | test_context.py |
| A16 | fixed | бюджет = system+memories+summary+recent+current+images+tools+reserve+margin; max_output_tokens в LLMRequest; fits=False → честный отказ | test_context.py boundary-тесты |
| A17 | fixed | strict summary ({} → отказ, boundary не двигается), per-chat asyncio lock, monotonic guard, порционный prompt | test_compactor.py |
| A18 | fixed | photos.py пишет telegram_file_id + metadata; builder: image без bytes → «[изображение]», с bytes → image; rehydration в _build_context (bot.get_file/download, bounded) | test_context.py + ручная проверка photos |
| A19 | fixed | PinnedHTTPTransport: connect к проверенному IP, Host+SNI по реальному хосту, TLS verify НЕ отключён; per-hop redirects | test_ssrf.py (+rebinding тест) |
| A20 | fixed | parse_mode=None во всех plain вызовах draft.py | test_draft_streamer.py |
| A21 | fixed | tail только у draft; final/partial — полная разбивка; tier3 final = edit; not-modified = успех | test_draft_streamer.py (+9 новых) |
| A22 | fixed | next_attempt_at + retry_after + bounded backoff; авто-resume после первой ошибки | test_draft_streamer.py |
| A23 | fixed | оба парсера: Done обязателен; EOF без него → NetworkError; cancel ≠ EOF; malformed skip | test_fix_v2_stream_contract.py |
| A24 | fixed | GET /chats limit/offset/include_archived + total; GET /chats/{id}; UI: «загрузить ещё» + архив-секция; memory пагинация | test_api + npm build |
| A25 | fixed | chat_out += system_prompt_override; UI грузит override; runtime: unknown/disabled модель → явный отказ | test_api + npm build |
| A26 | fixed | UNSET vs explicit null (grant_access + роут model_fields_set); UI «снять лимит»; revoke/suspend/ban останавливают генерации (registry.stop_all_for_user) | test_api; unit registry |
| A27 | fixed | probe enumerates registry (все 10 моделей, internal помечены); --strict exit code; ключи не в логах | запуск --strict на VPS |
| A28 | fixed | +/api/admin/models (GET/PUT enable), gemini test/reset-counters/usage, alibaba smoke, admin memory, audit pagination; UI: AdminModelsPage, AdminMemoryPage, кнопки/блоки | test_api + npm build PASS |
| A29 | fixed | дедуп по ПОЛНОМУ ключу (decrypt существующих) в add/bulk/CLI; key_hint — только UI-маска | код-ревью; live: повторный импорт тех же 30 ключей — см. «непроверенное» |
| A30 | fixed | ManagedLLMStream (shared Gemini client, кеш Alibaba по ключу + eviction, aclose); SearchManager реестр инстансов + aclose; main закрывает всё | unit; shutdown path |
| A31 | fixed | SearchManager.get_reader() (jina_search key); generation._run_tool_round инжектирует reader per round; fallback direct | test_search.py |
| A32 | fixed | AUTO: normal первым, AIO при пустом/ошибке; AIO без references → деградация; show_sources default TRUE (V2); structured sources до финала | test_search.py |
| A33 | fixed | валидация типов payload во всех бэкендах → SearchBackendError → fallback; playwright cooldown 15 мин | test_search.py (malformed fixtures) |
| A34 | fixed | startup: abort_stale (queued/running → aborted); drop_pending_updates=False | unit repo; live restart на VPS 24.09 |
| A35 | fixed | stats: requests_by_model/errors_today/429/gemini_usage_today; audit limit/offset/action + total | test_api |
| A36 | fixed | +24 integration contract tests (tests/integration/); все offline; live — только через явный probe-флаг | 488 passed |
| A37 | fixed | Dockerfile exec-фор CMD; app port bind 127.0.0.1 (APP_BIND override); /ready с проверкой БД; IP pin задокументирован (KNOWN_ISSUES) | compose up на VPS |
| A38 | fixed | TASKS/KNOWN_ISSUES/DECISIONS обновлены под факт (этот коммит) | ревью документов |
| A39 | fixed | полный assistant turn (текст+вызовы+signatures); max calls/round (4); общий deadline (240s); cancel перед каждым вызовом | test_fix_v2_usage_ledger/pool |
| A40 | optional-deferred | сделано: GIN FTS index (0009), memory candidate cap 500, bounds в settings. Отложено: полный lockfile, accessibility-тесты UI, backup retention — обоснование: P3, не блокирует P1/P2 | — |
| N01 | fixed | 12 реальных child calls через task tool (general), до 4 параллельно, непересекающиеся scopes, отчёты в .agents/reports/fix-v2/, dispatch log с task ids | POLYRA_FIX_V2_DISPATCH.md |
| N02 | fixed | 8 профилей установлены штатным скриптом (preview→apply, без перезаписи); настройки/модель не менялись; discovery в этой сессии ограничен → штатные агенты (записано) | .opencode/agents/ |
| N03 | fixed | все модели сохранены; причины нестабильности найдены и устранены (schema-sanitizer 400, потерянные tool calls при finish=STOP, silent EOF, мёртвые ключи PERMISSION_DENIED→auto-disable, 503-ротация); матрица ниже | MODEL_REGRESSION_MATRIX (заполнена) |
| N04 | fixed + live-verified | deepseek-v4-pro через AlibabaProvider: registry/capabilities, thinking (OFF/HIGH/MAX; LOW не выдуман), text-only guard, FC; probe на VPS 24.09: text/thinking/FC/image-skip — всё OK | probe_20260924_190515.json |

## Модели — регрессионная матрица

См. `.agents/reports/fix-v2/MODEL_REGRESSION_MATRIX.md` (заполнена: offline + live
статусы по каждой из 10 моделей).

## Непроверенное / ограничения (честно)

1. **PostgreSQL barrier/race-тесты** (A05/A09 на реальной БД с двумя соединениями):
   локально PostgreSQL недоступен; код-уровень проверен, на VPS миграция прошла, но
   формальный barrier-тест не прогнан. Команда: на VPS — см. KNOWN_ISSUES.
2. **A29 live**: повторный импорт 30 тех же ключей на проде не запускал (не хотел
   трогать прод-пул без явной просьбы; код-логика покрыта чтением + unit-стилем).
3. **Telegram draft rate-limit** остаётся эмпирическим (документации нет; throttle
   1/с + retry_after обработка).
4. **E2E Telegram в живом клиенте** (draft/rich тиры) — проверено частично в
   эксплуатации 18–19.09 (был падеж RICH_MESSAGE_MARKDOWN_INVALID → fallback работал).
5. **A40 остатки** — optional-deferred (см. таблицу).
6. Frontend E2E (Playwright против живого Mini App) — не поднимал (нет Telegram WebView
   в среде); фронт собирается и типизируется, логика покрыта чтением.

## Изменённые области (сводка)

- `app/llm/` (gemini pool/quota/store, providers обоих, capabilities/registry)
- `app/services/` (generation, llm_factory, settings, admin, credentials)
- `app/bot/` (draft streaming, routers, dispatcher)
- `app/api/` (routes + deps + app)
- `app/db/` (миграция 0009, модели, репозитории)
- `app/search/`, `app/security/`, `app/llm/tools/`
- `app/context/`, `app/memory/`
- `miniapp/` (12 файлов + 2 новые страницы)
- `tests/unit/` + `tests/integration/` (новые)
- `Dockerfile`, `docker-compose.yml`, `docs/API.md`, `pyproject.toml`

---

## Приложение: независимый ре-аудит (FINAL_IMPLEMENTATION_REVIEW.md, 2026-09-25) и доработки

Независимый аудит (8 субагентов) нашёл, что часть пунктов была «fixed по коду, но не по
проду». Исправлено в ответ на аудит (commit-поток после `478a172`):

| Дефект аудита | Исправление | Проверка |
|---|---|---|
| Stop-partial как HTML (A20) | `parse_mode=None` + полная разбивка partial | test_fix_v2_usage_ledger + unit |
| Orphan tool_calls при >4 вызовах (A39) | в историю попадают только ИСПОЛНЕННЫЕ вызовы | test_generation tool-loop |
| `_tail` prefix сверх лимита rich (A21) | лимит включает префикс | test_draft_streamer |
| Дубль current-сообщения при summary (A15) | exclude_message_id в _build_context | test_context |
| Rehydration только при summary (A18) | всегда для image-capable моделей | код-ревью |
| DB-настройка memory_min_chars выбрасывалась (A13) | min_chars проброшен per-call; ContextBuilder строится на effective (DB) настройках запроса | test_api |
| Элизия середины истории (A17) | порционный PREFIX-отбор; boundary только по включённым | test_compactor (переписан закреплявший баг тест) |
| PERMISSION_DENIED → disable вопреки ТЗ §9 (A10) | длинный cooldown 24ч вместо disable | test_gemini_pool |
| gemini_project_id/attempts не писались в run (A07) | finish(attempts, gemini_project_id) во всех 3 финалах | unit |
| content_filter → NetworkError (A23) | SafetyError | unit |
| SOURCES мог быть съеден обрезкой (A32) | SOURCES первой частью результата | test_tools |
| Отрицательные лимиты принимались (A26) | ValueError при <= 0 | test_api |
| aclose устаревшего Alibaba-клиента рвал активный стрим (A30) | graveyard до shutdown | unit |
| gather без SIGTERM-супервизора (A34/A37) | asyncio.wait FIRST_COMPLETED + cancel pending | live restart на VPS |
| probe не влиял на runtime (A27) | --write-runtime → system_settings; /api/models фильтрует thinking_modes по свежему probe + probe_at | test_api |
| Статистика без TTFT/errors (A35) | avg_ttft_s, error_rate_today, recent_failed_runs; UI Dashboard | unit + npm build |
| Пагинация users (A28) | useInfiniteQuery offset-пагинация | npm build |
| Legacy image-drop путь (A18) | build_messages: image без bytes → «[изображение]» | test_generation |
| Unknown-model fallback тест закреплял баг (A36) | resolve без fallback; тест ждёт UnknownModelError | test_generation |
| CI отсутствовал (A36) | .github/workflows/ci.yml (python+frontend) | YAML valid; первый прогон — на push |
| README без Pro / KNOWN_ISSUES протух (A38) | README + KNOWN_ISSUES обновлены | ревью |

После доработок: **492 passed, ruff ✓, mypy strict ✓ (155 файлов)**. Остатки (честно):
PG barrier-тесты не прогнаны (нет локальной БД), frontend E2E не поднимался,
Kimi DTL — optional (не реализован по ТЗ §25), probe→runtime — реализован write+read,
live-запись в БД не гонял на проде (по бюджету ключей).
