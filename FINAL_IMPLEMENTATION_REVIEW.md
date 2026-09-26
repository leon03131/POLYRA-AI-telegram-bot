# FINAL_IMPLEMENTATION_REVIEW — независимый аудит текущего состояния POLYRA

**Дата:** 25.09.2026. **Рабочий checkout:** commit `e4ac383` (HEAD, рабочее дерево чистое).
**Входные документы:** исходное ТЗ `docs/PROMT.md` (46 разделов), задание на исправления
`docs/POLYRA_KIMI_HANDOFF_V2/KIMI_FIX_PROMPT.md` (A01–A40 + N01–N04), сданный отчёт `FIX_REPORT_V2.md`.

## Методика

Аудит выполнен 8 реальными субагентами OpenCode (2 волны по 4 параллельных, непересекающиеся
scopes по SUBAGENT_PLAN), детальные отчёты каждого — в `.agents/reports/final-review/`:

| Субагент | Scope | Отчёт |
|---|---|---|
| polyra-telegram | app/bot/**, стриминг, Stop | `.agents/reports/final-review/polyra-telegram.md` |
| polyra-gemini | app/llm/gemini/**, gemini provider | `.agents/reports/final-review/polyra-gemini.md` |
| polyra-alibaba | alibaba provider, registry, DeepSeek V4 Pro | `.agents/reports/final-review/polyra-alibaba.md` |
| polyra-context | app/context/**, app/memory/** | `.agents/reports/final-review/polyra-context.md` |
| polyra-miniapp | miniapp/** | `.agents/reports/final-review/polyra-miniapp.md` |
| polyra-search-security | app/search/**, SSRF, tools | `.agents/reports/final-review/polyra-search-security.md` |
| polyra-db-api | app/db/**, app/api/**, access-сервисы | `.agents/reports/final-review/polyra-db-api.md` |
| polyra-qa-release | тесты, CI, Docker, документация | `.agents/reports/final-review/polyra-qa-release.md` |

Главный агент лично перепроверил все критичные находки, где статус расходился с
FIX_REPORT_V2.md (результаты — в разделе 6). Код не изменялся; запусклись только
offline-тесты и статический анализ.

## Результаты реальных запусков (сегодня,offline)

| Проверка | Команда | Итог |
|---|---|---|
| Полный test suite | `.venv/Scripts/python -m pytest tests -q` | **488 passed, 0 failed/skipped/xfailed, exit 0** (2 прогона, стабильно) |
| Ruff | `ruff check .` | exit 0 |
| mypy | `mypy app` / `mypy app tests scripts` | exit 0 (129 / 155 файлов) |
| Frontend typecheck | `npm run typecheck` (miniapp) | exit 0 |

Замечание: все HTTP в тестах идут через MockTransport/ASGI-моки; ни один тест в репо
не использует реальную PostgreSQL (integration-набор `tests/integration/` — 24 теста
на in-memory фейках). Live-флаги не использовались.

## Главный вывод

1. **Проект реально существует и в целом соответствует исходному ТЗ**: архитектура
   (единый LLM-интерфейс, пул Gemini, tool engine, context/memory, Mini App, admin) —
   не пустышка; 488 тестов проходят, strict-типизация чистая.
2. **Большинство фиксов A01–A40 настоящие и работают в runtime**: SSRF-pinning
   подтверждён вплоть до исходников httpx/httpcore, квоты-резервации транзакционны,
   access-политика server-side, DeepSeek V4 Pro (N04) внедрена корректно.
3. **FIX_REPORT_V2.md систематически завышает статусы**: заявлено 40/40 «fixed»,
   по факту аудита — **13 исправлено полностью, 26 частично, 1 отложено (P3)**;
   в частичных случаях ядро фикса есть, но остались прод-дефекты или незакрытые
   требования приёмки.
4. **Найдено 7 подтверждённых (перепроверены главным агентом лично)
   «фикс-проходит-тест-но-ломается-в-проде» дефектов** — см. раздел 6. Наиболее
   тяжёлые: Stop-partial отправляется как HTML (TelegramBadRequest), orphan
   tool_calls при batch >4 (400 от провайдера), потеря середины истории при
   compaction, дублирование current-сообщения при наличии summary.
5. **Систематические пробелы вокруг кода**: нет ни одного PostgreSQL-теста, нет ни
   одного frontend-теста и e2e, нет CI, probe-результаты не подключены к runtime
   (A27), SIGTERM-обработка не доказана (A34/A37).

---

## 1. Таблица A01–A40 и N01–N04

Статусы: **ИСПРАВЛЕНО** (реально, работает в runtime), **ЧАСТИЧНО** (ядро есть,
есть прод-проблемы/незакрытая приёмка), **НЕ РЕАЛИЗОВАНО**, **ОТЛОЖЕНО** (P3).

| пункт | статус | файлы | доказательство | проблемы |
|---|---|---|---|---|
| **A01** UUID vs Number(id) | ИСПРАВЛЕНО | miniapp/src/api/types.ts:50-62; app/api/routes/chats.py:49; App.tsx:37 | `Chat.id: string`; все 27 `Number(` — только на числовых полях; tsc exit 0; API возвращает `str(chat.id)` | нет OpenAPI codegen, нет e2e «no NaN» |
| **A02** /admin 404, menu button | ИСПРАВЛЕНО | app/bot/routers/commands.py:31-33; app/main.py:125; app/bot/dispatcher.py:74-89; miniapp/src/App.tsx:153 | `build_admin_url` → `…/#/admin`; `setup_menu_button` при startup глобально | прямой deep-link `/admin` (без hash) — 404; вне контракта, все код-пути дают hash-URL |
| **A03** [] = все модели | ИСПРАВЛЕНО | app/db/repositories/access.py:95-106; app/services/admin.py:239-276; миграция 0009:72-92 | mode all/list; пустой set ≠ None; backfill 'list'; одна политика на API/bot/генерацию | нет DB-интеграционного теста (unit идёт через сломанную session factory) |
| **A04** is_owner vs numeric ID | ИСПРАВЛЕНО | app/api/dependencies.py:33-39; app/bot/routers/commands.py:160-163; 0009:116 | owner = только `telegram_user_id == 795063564`; миграция чистит чужие флаги; тесты test_api.py:232-265 | — |
| **A05** атомарный захват чата/лимитов | ЧАСТИЧНО | app/services/generation.py:1041-1058; app/db/migrations/versions/0009_fix_v2.py:107-113; app/db/models/generation_run.py:33-40 | per-chat: partial unique index `uq_generation_runs_active_chat` + IntegrityError→busy (межпроцессно) | per-user concurrency/daily — check-then-act на in-memory реестре; PostgreSQL barrier-тест не прогнан (признано в FIX_REPORT_V2.md:91) |
| **A06** Done до usage/финализации | ИСПРАВЛЕНО | app/llm/providers/alibaba.py:271-325; app/llm/gemini/pool.py:318-351; app/services/generation.py:841-864; tests/integration/test_fix_v2_stream_contract.py:149-197 | Alibaba: Done буферизуется до EOF/[DONE], Usage строго до Done; Gemini: `_stream_attempt` finally → report_success×1; integration-тест на реальной цепочке провайдер→consumer | пул Gemini финализируется отложенно (asyncgen-finalizers); `suppress(Exception)` в pool.py:344 глотает ошибки БД молча (подтверждено пробой субагента) |
| **A07** суммирование usage по раундам | ЧАСТИЧНО | app/services/generation.py:612-653,631; _save_cancelled:1171-1175 | `_sum_usage` по раундам (тест 300/50 по двум раундам); cancelled сохраняет usage; unknown ≠ 0 | `gemini_project_id` колонка run не заполняется; attempts уходят в metadata запроса, персистенция в run не показана |
| **A08** удаление чата обнуляет лимиты | ИСПРАВЛЕНО | app/db/models/generation_run.py:43-47; 0009:94-104; app/db/repositories/generation_runs.py:69-101 | `chat_id SET NULL ON DELETE`; лимиты считаются по user_id; IntegrityError при удалении mid-run ловится | live PostgreSQL-теста нет (миграция на VPS прошла по FIX_REPORT) |
| **A09** атомарность квот Gemini | ЧАСТИЧНО | app/llm/gemini/store_db.py:125-225; app/llm/gemini/quota.py:54-65; app/db/repositories/gemini.py | `check_and_reserve_atomic`: одна транзакция, ON CONFLICT+WHERE+RETURNING, rollback пары; QuotaReservation с окнами резервации; тесты minute-boundary | нет PG-барьер-теста (только in-memory фейки); reconcile не идемпотентен (дубль задвоит расход); нет release при cancel (отменённые запросы «съедают» RPD/RPM); TPM post-factum |
| **A10** cooldown/retry области действия | ЧАСТИЧНО | app/llm/gemini/pool.py:209-246,263-306 | 429 → cooldown per (project,model); 400/safety — без ротации; 5xx/timeout → 1 retry + ротация; один проход (PoolExhausted); request.model неизменен | **PERMISSION_DENIED → перманентный disable проекта (pool.py:224-230) вопреки ТЗ §9 («403 → cooldown, следующий проект»)**; нет jitter/deadline прохода пула |
| **A11** Stop прерывает stream/tools | ЧАСТИЧНО | app/services/generation.py:121-131,712-715; app/llm/providers/gemini.py:343-350; alibaba.py:397-406 | `stop()` = event + `task.cancel()`; отмена проверяется перед каждым tool-вызовом; проба субагента: висящий стрим прерывается за ~0.0001с | **CancelledError ловится только в `_consume`**: Stop во время tool-раунда/финализации → partial потерян, run зависает «running» до рестарта; повторный Stop рвёт `_save_cancelled`; hang-теста на транспорте в репо нет; read timeout 300s |
| **A12** серверная политика tools | ЧАСТИЧНО | app/services/generation.py:488-509,698-714; app/llm/tools/runner.py:65-84 | effective set считается один раз (`_resolve_tool_defs`), тот же набор в LLMRequest.tools и ToolContext.allowed_tool_names; enforcement в runner до lookup; `cancellation.is_set()` перед side effect; тесты 440-482 | права — frozen-snapshot на весь запуск: снятие web/memory между раундами не действует; `stop_all_for_user` ставит только event без task.cancel |
| **A13** Admin→System настройки в runtime | ЧАСТИЧНО | app/services/settings.py; app/services/generation.py:942-954; app/api/routes/admin_system.py:16-48 | EffectiveSystemSettings (DB поверх env, per-request), wired в `_prepare`; тесты test_api.py:431-500 | **`memory_extraction_min_chars`: DB-значение вычисляется (generation.py:950) и выбрасывается — extractor построен на env-значении; ключа нет в Admin API GET/PUT**; DB context_keep_recent/trigger_ratio не доходят до ContextBuilder (строится из env в main.py) |
| **A14** disabled credential vs env fallback | ИСПРАВЛЕНО | app/services/credentials.py:21-26; app/services/llm_factory.py:71-90; app/api/routes/admin_providers.py:119-124 | env fallback только при отсутствии DB-записи; disabled → None → AuthError, 0 HTTP; матрица 6 тестов test_api.py:348-398 | мелочь: кеш клиента не закрывается при disable без ротации |
| **A15** потеря истории до суммаризации | ЧАСТИЧНО | app/context/builder.py:130-137,171-174; app/services/generation.py:538-543,935-938 | граница covered_until_message_id реализована; `list_all` при сводке; тесты test_context.py:281-359 | **1) без summary список `recent_history_limit*2`=40 молча теряет всё старше — compaction не триггерится (needs_compaction=False, пока всё влезает). 2) НОВЫЙ баг фикса: user-сообщение записано ДО `_build_context` (938), `list_all` (541) в той же сессии включает его → current дублируется при каждом запросе со сводкой (фото — дважды). 3) Нет PG-теста** |
| **A16** TokenBudget ограничивает запрос | ЧАСТИЧНО | app/context/builder.py:150-157; app/services/generation.py:556-562,620-633; gemini.py:139-140; alibaba.py:165-166 | полный счёт system+tools+current+recent; `fits=False` → честный отказ; max_output_tokens реально доходит до провайдеров; boundary-тесты | **нет re-check бюджета между tool-раундами** (8×4×4000 chars ≈ 37k токенов сверх бюджета); дубль current (см. A15); legacy-путь (generation.py:533-536) вообще без бюджета; `tools_estimate` — fresh TokenBudgetManager вместо сконфигурированного |
| **A17** пустая summary / перезапись | ЧАСТИЧНО | app/context/compactor.py:106-125,285-321,323-343; app/db/repositories/chat_summaries.py:24-47 | `{}`/пустой текст/неверные типы → None, boundary не двигается (299-302); один repair; per-chat asyncio.Lock + monotonic guard; тесты 255-439 | **«порционный» compaction = элизия середины (`_cap_dialog`, 12k) с продвижением boundary на ВЕСЬ сегмент (304) — вырезанная середина теряется навсегда; тест 445-467 закрепляет это как ожидаемое**; нет DB CAS/версий (допустимо для 1 реплики) |
| **A18** file_id фото / история | ЧАСТИЧНО | app/bot/routers/photos.py:59-74; app/context/builder.py:47-70; app/services/generation.py:539-543,566-589 | photos.py пишет telegram_file_id+metadata, base64 отбрасывается; rehydration bounded; text-only модель → отказ со списком альтернатив | **rehydration вызывается только при наличии summary (539) — сценарий «фото→ответ→follow-up» в молодом чате получает плейсхолдер `[изображение]`, исходный баг жив в главном пути**; `_rehydrate_images` — 0 тестов; legacy build_messages всё ещё режет images (мёртвый путь, закреплён тестом) |
| **A19** SSRF DNS rebinding | ИСПРАВЛЕНО | app/security/ssrf.py:20-84; app/search/fetcher.py:45-74,150-242 | PinnedHTTPTransport: connect к проверенному IP, Host+SNI по реальному хосту; проверено по исходникам httpx 0.28.1/httpcore 1.0.9 (connect идёт к origin из request.url, DNS при connect не выполняется); per-hop re-validate; egress: non-global + metadata + IPv4-mapped; TLS verify включён; тесты rebinding | KNOWN_ISSUES.md:38-40 устарел («принятый риск», хотя pin реализован); mixed A/AAAA теста нет; live-сокет не проверялся (запрещено) |
| **A20** raw LLM text как HTML | ЧАСТИЧНО | app/bot/streaming/draft.py:177,199,206,213,289,309,316 (все `parse_mode=None`); dispatcher.py:40; generation.py:1148 | все plain-вызовы DraftStreamer с parse_mode=None (+тесты) | **ПРОД-ДЕФЕКТ (перепроверен): `_save_cancelled` шлёт Stop-partial через `bot.send_message` (generation.py:1148) БЕЗ parse_mode=None при default HTML (dispatcher.py:40) → BadRequest на `<`/незакрытом теге → пользователь не получает partial; тесты мокают Bot и бага не видят**; commands.py:140 — неэкранированный chat.title |
| **A21** обрезка финала/партиала | ЧАСТИЧНО | app/bot/streaming/draft.py:86-90,170-171,176; app/services/generation.py:1146 | not-modified → идемпотентный успех (3 теста); tier3 final = edit; plain-финал разбивается по частям | **финальный rich всё ещё `_tail(text, RICH_LIMIT)` (draft.py:170): TAIL_PREFIX + text[-32768:] = 32770 > 32768 → downgrade в plain / потеря rich; Stop-partial обрезан первым 4096-фрагментом (generation.py:1146) вместо разбиения** |
| **A22** throttle/retry_after | ИСПРАВЛЕНО | app/bot/streaming/draft.py:141,227-252,273-329 | `append` резюмится после первого упавшего flush (`_next_attempt_at`); TelegramRetryAfter во всех tiers без смены tier; bounded backoff 0.5→5с; тесты 401-488 | нет jitter (не критично) |
| **A23** битый SSE = успех | ЧАСТИЧНО | alibaba.py:271-325,185-208,384-395; gemini.py:229-256; test_fix_v2_stream_contract.py | EOF без finish/[DONE] → NetworkError; pending tools при EOF → ошибка; error frame → raise; retry после partial запрещён (`events_started`); интеграционные фикстуры EOF/malformed | Gemini: malformed JSON-фреймы пропускаются молча (не классифицируются); структурная валидация чанков отсутствует; Alibaba: `content_filter` мисклассифицируется как EOF/NetworkError (fail-closed); Retry-After не парсится; нет теста error-frame-после-текста |
| **A24** архив/пагинация чатов | ЧАСТИЧНО | app/api/routes/chats.py:81-112; app/db/repositories/chats.py:29-53; miniapp/src/pages/ChatsPage.tsx:87,158-176,193-237; memory.py:38-51 | archived filter + limit/offset/include_archived + total; GET по UUID; UI лениво грузит архив + «Загрузить ещё» | **frontend наращивает limit, backend cap 200 → записи 201+ недостижимы (Chats/Memory/AdminMemory/AdminAudit — «вечная» кнопка)**; offset вместо cursor; юнит-тестов пагинации нет; e2e нет |
| **A25** effective settings vs runtime | ЧАСТИЧНО | app/api/routes/chats.py:55,154-169; app/services/generation.py:955-983; miniapp/src/pages/ChatSettingsPage.tsx:33-39,154 | system_prompt_override в _chat_out; UI грузит override (promptLoaded-guard), PATCH `{model_id:null,thinking_setting:null}` одним body; runtime: unknown/disabled → явный отказ «⛔ …больше недоступна» | **PATCH не проверяет model_overrides (disabled модель сохраняется)»; `resolve_model_and_thinking` хранит латентную silent-fallback ветку; старый тест `test_resolve_unknown_model_falls_back_to_default` (test_generation.py:187-194) не исправлен — прямо требовался**; effective chain фронта без системного default → thinking-селектор disabled при null/null |
| **A26** снятие лимита / остановка при revoke | ЧАСТИЧНО | app/services/admin.py:46-48,85-131; app/api/routes/admin_access.py:108-140; app/services/generation.py:112-119; AdminUsersPage.tsx:63-110 | UNSET-сентинел → explicit null записывает NULL; suspend/revoke/ban → `stop_all_for_user` + stopped_generations; тесты | **нет bounds на числовые лимиты (отрицательные принимаются)**; stop per-process (event без task.cancel); `stopped_generations` не показывается в UI |
| **A27** capability probe → runtime | ЧАСТИЧНО | scripts/smoke_providers.py:85-87,615-720 | перебор из registry (10 baseline + Pro, internal помечен); `--strict` exit 1; skip≠pass; ключи маскированы; live-артефакт probe 2026-09-24 (6 alibaba-моделей ok, Pro ok) | **нет runtime capability-кеша с TTL/версионированием; probe-результаты НЕ влияют на registry/API/UI (требование приёмки не реализовано); `--strict` при no-key → exit 0; артефакт сохранён в UTF-16 с консольным хвостом (не машиночитаем)** |
| **A28** полная админ-панель | ЧАСТИЧНО | app/api/routes/admin_models.py, admin_providers.py:84-137, admin_gemini.py:334-381, admin_memory.py; miniapp/src/admin/* | Models enable+audit; Alibaba smoke (реальный вызов ≤16 токенов); Gemini Test/Reset/usage/quotas; admin memory; audit pagination/filter; все вызовы с owner guard + masked keys | **нет per-model health, диагностики generation errors, Models CRUD; Users limit=50 без пагинации; search test без audit**; admin-матрица UI→API→DB→audit не покрыта тестами |
| **A29** дедуп Gemini keys | ИСПРАВЛЕНО | app/api/routes/admin_gemini.py:127-218; scripts/import_gemini_keys.py:64-103 | дедуп по полному ключу (decrypt-compare) в API и CLI; dry-run без записи; 409 на дубль | нет отдельной Google project identity; dry-run не сверяет с БД |
| **A30** lifecycle HTTP clients | ЧАСТИЧНО | app/services/llm_factory.py:33-100; app/main.py:132-137; app/search/manager.py:96-179 | один shared Gemini-клиент, идемпотентный aclose; кеш Alibaba по ключу + eviction+aclose при ротации; SearchManager — реестр инстансов + aclose в main.finally; тесты reuse/key-change/idempotent | нет lifecycle-тестов с counters закрытия; **ротация ключа mid-stream рвёт активный стрим (aclose общего клиента)** |
| **A31** JinaReader в open_url | ЧАСТИЧНО | app/search/manager.py:139-160; app/services/generation.py:449-457,705; app/search/fetcher.py:245-260 | `_resolve_jina_reader()` per round → ToolContext.jina_reader → `fetch_url(reader=…)`; fallback при None и при исключении; SSRF-путь всегда первый | reader не отделён от jina_search (общие ключ/enablement); безключевой режим недоступен; нет теста через настоящий tool loop |
| **A32** sources / AI Overview | ЧАСТИЧНО | app/search/manager.py:183-248; app/services/generation.py:214-261,438-442; app/llm/tools/builtin.py:129-153 | AUTO: normal первым, AIO только при пустом/ошибке (тест `aio.calls==0`); AIO без references → деградация; `show_sources=True`; блок «Источники:» из успешных web_search records, dedupe | sources — текстовый re-parse tool-результата, не структура; **обрезка 4000 симв. (max_result_size) может съесть строку SOURCES при длинных сниппетах — citations не строго гарантированы**; clickable только в rich tier; нет E2E во всех tiers |
| **A33** malformed backend response | ИСПРАВЛЕНО | app/search/serper.py:59-84; brave.py:91-104; serpapi_ai_overview.py:99-170; jina.py:63-96; manager.py:250-297; playwright_google.py:66-79 | isinstance-валидация во всех парсерах → SearchBackendError → fallback к следующему; 429/5xx → 1 retry → fallback (тест); playwright: challenge → BackendUnavailable + cooldown 15 мин; CAPTCHA bypass/stealth отсутствуют | мелочь: manager ловит только SearchBackendError (неожиданный тип оборвал бы цепочку, но уходит в безопасный Tool error) |
| **A34** stale runs / pending updates | ЧАСТИЧНО | app/main.py:114-137; app/db/repositories/generation_runs.py:79-90 | startup: `abort_stale` (queued/running → aborted); `drop_pending_updates=False`; finally закрывает 4 транспорта | **фоновые задачи fire-and-forget (не дрейнятся); нет SIGTERM-supervisor: `gather(start_polling, serve())` (main.py:131) — после SIGTERM polling не завершается, finally не выполнится**; нет live kill/restart-теста |
| **A35** observability/audit | ЧАСТИЧНО | app/api/routes/admin_stats.py:19-158; app/db/models/audit_log.py; app/observability/logging.py | audit на всех admin mutations + limit/offset/action + total; requests_by_model/errors_today/429/gemini_usage; redaction логов | нет latency/TTFT, error-rate, correlation IDs, диагностики фейленных runs, usage по tool/search backend; search health-check без audit |
| **A36** тесты доказывают инварианты | ЧАСТИЧНО | tests/** (488 тестов); tests/integration/ (24 контрактных) | контрактный suite высокий: Usage до Done, EOF=NetworkError, fragmented tool-args, сумма usage 300/50; ~95% тестов поведенческие; 0 skip/xfail | **CI (.github) отсутствует; frontend-тестов 0 (нет test-скрипта, miniapp/e2e нет); PostgreSQL-тестов 0 (in-memory фейки); test_api.py:5-6 обещает RUN_API_INTEGRATION-набор, которого не существует; 2 wrong-behavior теста живы (legacy image-drop test_generation.py:232,246; unknown-model fallback:187-194)**; runtime-отказ unknown-model без теста |
| **A37** deployment сигналы/readiness | ЧАСТИЧНО | Dockerfile:61; docker-compose.yml:42; Caddyfile; app/api/app.py:76-85; app/main.py:131 | exec после миграций; multi-stage + non-root; postgres без порта; app bind 127.0.0.1 (APP_BIND override); /ready = DB 503; caddy profile; IP pin задокументирован | **SIGTERM не доказан + статический риск gather (см. A34); backup/restore теста нет; docker compose build/up в этой среде не выполнялся** |
| **A38** документация vs код | ЧАСТИЧНО | FIX_REPORT_V2.md; KNOWN_ISSUES.md; README.md; .agents/reports/probe_*.json | сэмпл статусов сэмпл-проверен кодом (A05/A09/A17/A24/A27/A28/A34/A35 — подтверждены); probe-артефакты реальны; MODEL_REGRESSION_MATRIX заполнена | KNOWN_ISSUES: п.9 (SSRF «позже» — уже реализован), п.1 «миграции не выполнялись» vs VPS-заявка, дубли; **README: список моделей без DeepSeek V4 Pro**; ADR-010 provider_health не существует; FIX_REPORT: 40/40 fixed — завышено (см. таблицу) |
| **A39** assistant turn / лимиты tool-loop | ЧАСТИЧНО | app/services/generation.py:708-711,618-623,759-777; app/llm/tools/runner.py:126-136 | полный assistant turn (текст+вызовы+provider_meta signatures) в следующий request (wire-тест 281-343); max 4 calls/round; deadline 240с; iterations 8; cancel перед каждым вызовом | **ПРОД-ДЕФЕКТ (перепроверен): assistant turn содержит ВСЕ tool_calls (709), исполняются только первые 4 (711) → orphan tool_call_ids → вероятный 400 следующего запроса при ≥5 параллельных вызовах; тесты 1-2 вызова не ловят**; deadline проверяется только между раундами; нет aggregate result/token budget; нет теста per-round лимита |
| **A40** усиление памяти (P3) | ОТЛОЖЕНО/ЧАСТИЧНО | app/db/repositories/memories.py:105-122; 0009:127-133; app/memory/extractor.py:243-263 | GIN-индекс FTS (0009, выражение совпадает с запросом); bounded dedup-скан 200 | нет DB unique fingerprint (user_id, normalized_text) → конкурентные точные дубли; extraction не сериализована по user; a11y-тесты отложены (P3, задокументировано) |
| **N01** реальные субагенты | ЧАСТИЧНО | .agents/POLYRA_FIX_V2_DISPATCH.md | 4 research-задачи с реальными ses_* ID; wave-отчёты с реальными дефектами (B1/B2 реально исправлены: gemini.py:255-256, generation.py:1171-1174) | wave1 без session refs/времени; wave2 вообще не в DISPATCH.md; «12 child calls» доказуемо только для 4. **Текущий аудит: 8 реальных субагентов, отчёты в .agents/reports/final-review/** |
| **N02** среда/профили OpenCode | ИСПРАВЛЕНО | .opencode/agents/polyra-*.md; scripts/install_opencode_agents.py | 8/8 профилей побайтово идентичны overlay (хеши сверены); установщик без перезаписи; профили без model override | — |
| **N03** сохранить модели/нестабильность | ЧАСТИЧНО | app/llm/capabilities.py:56-165; app/llm/providers/alibaba.py:359-395; tests/unit/test_registry.py:7-25 | все 10 ID сохранены (тест-замок точного множества); retry только той же модели/эндпоинта; transient ничего не удаляет; unknown usage ≠ 0; live-probe 2026-09-24: 39 ok/2 skip; причины нестабильности устранены (schema-sanitizer, STOP-tool-calls, EOF, PERMISSION_DENIED auto-disable, 503-ротация) | нет capability-store со states supported/unsupported/unknown + TTL/stale-маркерами; probe→runtime не подключён; нет jitter/Retry-After; 429→cooldown in-memory (не переживает рестарт) |
| **N04** DeepSeek V4 Pro | ИСПРАВЛЕНО | app/llm/capabilities.py:128-139; app/llm/providers/alibaba.py:63-69; app/services/generation.py:475-485; miniapp AdminUsersPage/AdminModelsPage; config.py:23 | exact ID `deepseek-v4-pro`, text-only (image НЕ скопирован), OFF/HIGH/MAX без LOW, нет thinking_budget/preserve_thinking/clear_thinking (forbidden-тест), `max_completion_tokens`, фото → guard с перечнем альтернатив, отдельная admin-галочка, probe отдельно от Flash, endpoint + trust_env=False; live-probe 2026-09-24 OK | HIGH/MAX не шлют явный `enable_thinking=true` (функционально эквивалентно — thinking ON по умолчанию; live OK); полный Telegram E2E не проверяем без live |

**Итог по A01–A40 + N01–N04:** исправлено полностью — 14; частично — 25; отложено (P3) — 1;
не реализовано (optional по ТЗ) — Kimi DTL (см. §25 ниже).

---

## 2. Соответствие исходному ТЗ (docs/PROMT.md)

| Раздел ТЗ | Статус | Комментарий |
|---|---|---|
| §0 процесс (субагенты, docs-first) | Соответствует | docs/vendor/**, .agents/reports/** существуют |
| §2 стек | Соответствует | Python 3.12/aiogram/FastAPI/SQLAlchemy 2 async/Alembic/React+TS+Vite/Docker+Caddy; Redis нет |
| §3 owner/доступ | Соответствует | numeric ID 795063564, гранты/permissions server-side (A03/A04) |
| §4–§5 архитектура/структура | Соответствует | слои разделены, handlers не знают HTTP API провайдеров |
| §6 единый LLM-интерфейс | Соответствует | LLMProvider/LLMEvent; ReasoningDelta не показывается (проверено тестами обоих провайдеров) |
| §7 model registry | Соответствует | capabilities.py централизован; «if model ==» в handlers нет |
| §8 Gemini | Соответствует | thinking_level LOW/MED/HIGH (+MINIMAL для 3.6); custom proxy extraordinary-piroshki-…netlify.app (default в config, raw httpx, trust_env=False); автоматического proxy-smoke нет |
| §9 Gemini pool | Частично | ротация/health/cooldown/классификация ошибок есть; **отклонение: 403 PERMISSION_DENIED → перманентный disable вместо cooldown (ТЗ §9)**; quota RPM/TPM/RPD seed + admin (не hardcoded); нет release reservation при cancel |
| §10 внутренняя 3.5-flash-lite | Соответствует | internal_only, тот же pool, per-model policy (unlimited-local для internal); thinking внутренних задач — env-only, не через admin |
| §11 Alibaba | Соответствует | только compatible-mode/v1; без proactive квот; обработка ошибок есть |
| §12 модели Alibaba | Соответствует | Qwen OFF/LOW/MED/MAX; DeepSeek Flash; GLM (clear_thinking); Kimi probe + fallback; **DTL не реализован — optional по ТЗ §25** |
| §13 запрет cross-model fallback | Соответствует | model_id неизменен во всех ротациях; PoolExhausted не меняет модель; unknown/disabled → явный отказ |
| §14 network routing | Соответствует | Alibaba DIRECT trust_env=False; Gemini через proxy; Telegram отдельный transport |
| §15 команды бота | Соответствует | /start /new /chats /settings /help; /admin numeric-only; menu button при startup |
| §16 Telegram streaming | Частично | tiers draft→fallback→edit, throttle, can_stop, stopped_message_generation; **Stop-финализация и partial-доставка — дефекты A11/A20/A21** |
| §17 photos | Частично | file_id+metadata хранится; **rehydration только при summary (A18)** |
| §18 чаты | Соответствует | все поля; одна активная генерация через partial unique index (PG-теста нет) |
| §19 messages/parts | Соответствует | схемы/типы parts; расширяемость есть |
| §20 chat title | Соответствует | set_chat_title пока title IS NULL (generation.py:506-508); fallback TitleGenerator через flash-lite (titles.py, generation.py:786-792) |
| §21 context compaction | Частично | covered-граница есть; **потери без summary, дубль current, нет re-бюджета между раундами (A15/A16)** |
| §22 structured summary | Частично | JSON-структура, один repair; **элизия середины с продвижением boundary = потеря данных (A17)** |
| §23 automatic memory | Частично | extractor/retriever/dedup; FTS+GIN; **нет DB-фингерпринта, extraction не сериализована (A40)** |
| §24 tool engine | Соответствует | registry/runner/schemas, 5 tools, iterations=8, timeout, tool_calls лог; shell/fs/python нет |
| §25 Kimi DTL | Не реализовано (optional) | в alibaba.py нет dynamic tool loading; ТЗ требует только при подтверждении docs+smoke |
| §26–§30 search backends | Соответствует | Serper primary, SerpApi AIO, Brave optional (no-key→disabled), Jina, direct fallback |
| §31 Playwright Google | Соответствует | optional, disabled by default, challenge→abort+cooldown 15 мин, без CAPTCHA bypass/stealth |
| §32 open_url | Соответствует | полный pipeline + SSRF (A19); web content как untrusted |
| §33 citations | Частично | блок «Источники:» есть, clickable в rich; **обрезка max_result_size может съесть sources (A32)** |
| §34–§36 Mini App | Соответствует | React+TS+Vite, theme vars, все разделы; initData raw → backend |
| §37 admin UI | Частично | 9 разделов реальные; **нет per-model health, диагностики ошибок, Users-пагинации (A28)** |
| §38 БД | Соответствует | 21 модель, миграции 0001–0009 линейны, UUID/BIGINT |
| §39 secrets | Соответствует | Fernet (auth AES-CBC+HMAC), masked keys, redaction логов; мастер-ключ в env |
| §40 audit log | Соответствует | actor/action/target/metadata; все admin mutations |
| §41 generation runs | Частично | поля/статусы есть; **gemini_project_id не заполняется (A07)** |
| §42 observability | Частично | requests/model, ошибки, 429, gemini usage; **нет TTFT/latency/error-rate (A35)** |
| §43 concurrency | Частично | partial unique index; **per-user — in-memory check-then-act (A05)** |
| §44 error UX | Соответствует | user_error_message без traceback; PoolExhausted — нормальное сообщение |
| §45 capability probe | Частично | smoke_providers по registry, --strict; **не подключён к runtime (A27)** |
| §46 тесты | Частично | access/pool/providers/context покрыты; **PG/frontend/e2e/CI отсутствуют (A36)** |

---

## 3. Фиксы, которые проходят тесты, но ломаются в проде

Перепроверены главным агентом лично (файл:строка — актуальный код):

1. **Stop-partial как HTML (A20).** `app/services/generation.py:1148` —
   `await bot.send_message(tg_chat_id, text)` без `parse_mode=None`; бот создан с
   `DefaultBotProperties(parse_mode=ParseMode.HTML)` (`app/bot/dispatcher.py:40`).
   Незакрытый тег или `<` в LLM-ответе → TelegramBadRequest → пользователь не
   получает partial вовсе. Все 9 тестов Stop мокают Bot.
2. **Orphan tool_calls (A39).** `app/services/generation.py:709` кладёт в assistant
   turn ВСЕ `outcome.tool_calls`, а `:711` исполняет `[:max_tool_calls_per_round]` (4).
   При ≥5 параллельных вызовах следующий запрос содержит tool_call без tool_result →
   400 от OpenAI-совместимого/Gemini API. Тесты покрывают только 1–2 вызова.
3. **Обрезка финала и partial (A21).** `app/bot/streaming/draft.py:170` —
   `_tail(text, 32768)` = `TAIL_PREFIX + text[-32768:]` → 32770 > лимит rich; финальный
   ответ деградирует/теряет хвост. `generation.py:1146` — Stop-partial режется первым
   фрагментом 4096 вместо разбивки.
4. **Дублирование current-сообщения (A15).** `generation.py:938` пишет user-сообщение
   до `_build_context`; при наличии summary `list_all` (`:541`) в той же сессии возвращает
   его снова → `builder` кладёт его в recent, а `:563` добавляет `current_parts` ещё раз.
   Каждый запрос со сводкой дублирует текущее сообщение (фото — дважды).
5. **Rehydration только при summary (A18).** `generation.py:539-543` —
   `if summary_row is not None:` … `_rehydrate_images`. Главный сценарий ТЗ
   «фото→ответ→follow-up» в чате без сводки получает плейсхолдер `[изображение]`.
   Функция не покрыта ни одним тестом.
6. **DB-настройка memory_extraction_min_chars выбрасывается (A13).** Вычисляется в
   `generation.py:950`, но extractor уже построен на env-значении; ключа нет в Admin
   System API — владелец меняет настройку в UI, эффекта ноль.
7. **Элизия середины истории (A17).** `compactor.py:304` продвигает
   covered_until на весь сегмент даже когда prompt был урезан `_cap_dialog` —
   вырезанная середина никогда не суммаризируется и теряется. Тест
   `test_compactor.py:445-467` закрепляет это как «ожидаемое» поведение.

Дополнительные прод-риски высокой вероятности (по субагентам, без личной
перепроверки): SIGTERM не завершает polling (A34/A37, main.py:131 gather);
ротация ключа Alibaba mid-stream рвёт активный стрим (A30);
`content_filter`-finish классифицируется как NetworkError (A23, fail-closed).

---

## 4. Расхождения FIX_REPORT_V2.md с реальностью

- Заявлено «40/40 fixed» — по факту 14 полных, 25 частичных, 1 отложено.
- A27 «probe кешируется» — кеша/TTL и влияния на runtime нет (только JSON-отчёт).
- A36 «frontend E2E» — в «непроверенном» признано честно, но статус «fixed»
  при 0 frontend-тестов завышен; test_api.py:5-6 обещает несуществующий
  RUN_API_INTEGRATION-набор.
- A11 «partial сохраняется один раз» — верно только при отмене внутри `_consume`;
  при Stop в tool-раунде/финализации partial теряется (см. §3.1).
- A21 «final/partial — полная разбивка» — final rich режется `_tail` (§3.3),
  partial — один фрагмент.
- A38 частично честен (раздел «Непроверенное» признан PG-барьер и др.), но README
  не знает про DeepSeek V4 Pro, KNOWN_ISSUES содержит устаревший SSRF-пункт.
- N01 «12 child calls» — доказуемы только 4 (research); coding-волны без session refs.

Что FIX_REPORT_V2 не завысил (подтверждено): A01–A04, A08, A14, A19, A22, A29, A33,
N02, N04; probe-артефакты 2026-09-24 реальны; миграция 0009 корректна; команды
pytest/ruff/mypy воспроизводимы (488/0/0).

---

## 5. Ограничения аудита

- Live-проверки (Telegram-клиент, реальный VPS, Gemini/Alibaba endpoints, Docker
  build/up, PostgreSQL) не выполнялись — только код, offline-тесты и статика.
- PostgreSQL-инварианты (A05/A08/A09) проверены статически по DDL/миграциям;
  runtime-поведение на реальной БД не подтверждено.
- probe_2026-09-24 артефакты приняты как заявления владельца/VPS-сессии, не как
  результат этого аудита.
- npm run build не запускался (read-only аудит); typecheck прошёл.

## 6. Рекомендации (без правок кода — по запросу)

1. P1: закрыть 7 дефектов §3 (A20 send, A39 orphan calls, A21 обрезка, A15 дубль,
   A18 rehydration, A13 wiring, A17 элизия) — все точечные, в известных файлах.
2. P1: SIGTERM-супервизор в main.py (сейчас gather без отмены polling).
3. P2: подключить probe-кеш к runtime registry/API/UI (A27); идемпотентный
   reconcile + release reservation (A09); per-model health в admin (A28).
4. P2: минимальный PG-барьер-тест (A05/A09), CI с pytest+ruff+mypy+tsc (A36).
5. P3: A40 — DB-фингерпринт памяти, a11y-тесты UI.

---

### Приложение: индекс доказательств

Детальные отчёты субагентов с file:line и цитатами:
`.agents/reports/final-review/polyra-telegram.md`,
`polyra-gemini.md`, `polyra-alibaba.md`, `polyra-context.md`,
`polyra-miniapp.md`, `polyra-search-security.md`, `polyra-db-api.md`,
`polyra-qa-release.md`.
Команды и exit codes прогонов — в `polyra-qa-release.md` (раздел «Результаты реальных запусков»).

---

# ДОПОЛНЕНИЕ: раунды 2 и 3 (закрытие находок аудита)

## Раунд 2 — ре-аудит фиксов KIMI (commit `0be0524`, проверен 25.09.2026)

Главный агент лично перепроверил каждый дефект §3 и процессные фиксы по диффу
`e4ac383..0be0524` и свежим прогонам.

**Подтверждено исправленным (код + прогоны 492 passed / ruff / mypy 155 / tsc / build):**
- Все 7 прод-дефектов §3: Stop-partial `parse_mode=None` + `_split_text`; orphan
  tool_calls (в историю только исполненные); exclude_message_id (дубль current);
  `_cap_segment_prefix` (элизия убрана, boundary на покрытый префикс, тест
  переписан); rehydration всегда для image-моделей + legacy-плейсхолдеры;
  per-call `min_chars` + ContextBuilder на effective (DB) настройках; `_tail`
  с префиксом внутри лимита.
- Процессные фиксы в коде: A10 (PERMISSION_DENIED → cooldown 24ч, тест
  переписан), A23 (content_filter → SafetyError), A26-bounds (валидация),
  A27 (`--write-runtime` → `capability_probe:<id>` в system_settings, `/api/models`
  фильтрует thinking_modes по свежему probe с TTL 7 дней + 4 поведенческих
  теста), A30 (graveyard вместо mid-stream aclose), A32 (SOURCES первой
  строкой — переживает truncation), A34 (supervisor `asyncio.wait
  FIRST_COMPLETED` + cancel), A35 (avg_ttft_s/error_rate/recent_failed_runs),
  A36 (`.github/workflows/ci.yml`), A38 (README/KNOWN_ISSUES), offset-пагинация
  фронта (`useInfiniteQuery` — «вечная кнопка» убрана).

**Расхождения раунда 2 (4 шт.):**
1. A13: `memory_extraction_min_chars` не экспонирован в Admin System API
   (admin_system.py не менялся) — обвязка DB→extractor работала, но ручки в
   админке не было.
2. A26: `ValueError` из `grant_access` не ловился роутом → 500 вместо 400.
3. 4 из 7 P1-фиксов без регрессионных тестов (приложение к FIX_REPORT_V2
   завышало покрытие): Stop-partial (мок стал толерантным, без ассерта),
   orphan tool_calls (теста с >4 вызовами нет), дубль current (test_context
   не менялся), per-call min_chars (не тестировался).
4. «Честные остатки» неполные: A15-1 (без summary сообщения старше окна
   `recent*2` молча выпадают — builder.py не трогался), A11-остаток (Stop во
   время tool-раунда/финализации теряет partial).

**P0-регрессия, найденная при закрытии расхождений (см. раунд 3):** коммит
`0be0524` использовал `ContextBuilder` в рантайме (`generation.py:592`),
импортировав его только под `TYPE_CHECKING` → `NameError` на builder-пути
каждого запроса генерации (production wiring `main.py` всегда передаёт
context_builder). Все гейты это пропускали: mypy доволен TYPE_CHECKING-импортом,
pytest не гоняет `_build_context` через реальный модуль. Показательный пример
паттерна «тест-зелёный — прод-сломан», внесённого самим фиксом.

## Раунд 3 — закрытие расхождений (сессия GLM, субагенты + lead)

Делегировано 3 субагентам с непересекающимися scope (отчёты в
`.agents/reports/fix-v2/`: `polyra-db-api/a13-admin-system-minchars_a26-grant-400.md`,
`polyra-qa-release/a20-a39-a15-regression-tests.md`,
`polyra-context/A13-minchars-override-tests_A17-compactor-docstring.md`);
`app/services/generation.py` — зона lead (правка импорта).

Сделано:
1. **A13**: `memory_extraction_min_chars` в Admin System API (GET/PUT/валидация
   >0, `admin_system.py`) + поле в `AdminSystemPage.tsx` + тип + 5 тестов.
2. **A26**: `ValueError` → `HTTPException(400)` в `admin_access.py` + тест
   (grant с `requests_per_day=-5` → 400).
3. **11 регрессионных тестов**: A20 Stop-partial (`parse_mode=None` + полная
   разбивка, HTML-опасный текст), A39 orphan tool_calls (5 вызовов при лимите 4:
   в следующем запросе ровно 4 tool_call ↔ 4 tool_result), A15 дубль current
   (маркер ровно 1 раз при сводке; тест содержит guard P0 — рантайм-импорт
   `ContextBuilder`), A13 per-call min_chars (3 теста: override вверх/вниз/None),
   A13 admin API (5 тестов). Тесты A20/A39/A15 проверены «упали бы до фикса»
   подстановкой `git show 0be0524^` — реальные провалы, не рассуждение.
4. **P0-fix (lead)**: `ContextBuilder` перенесён в рантайм-импорт
   (`generation.py:29`); проверено `hasattr(generation, 'ContextBuilder') == True`;
   тестовый шим заменён на постоянный guard.
5. Косметика: докстринг `_cap_segment_prefix` приведён к коду (без «усечения»).

**Итоговые гейты (свежий прогон):** pytest `tests -q` → **503 passed, 0 failed/
skipped, exit 0**; `ruff check .` → exit 0; `mypy app tests scripts` → exit 0
(156 файлов); `npm run typecheck` + `npm run build` (miniapp) → exit 0.

## Открытые пункты после раунда 3

- **A15-1**: без summary сообщения старше `recent_history_limit*2` не попадают
  в контекст и не триггерят compaction (builder.py) — P2.
- **A11-остаток**: Stop во время tool-раунда/финализации теряет partial, run
  зависает «running» до рестарта (CancelledError ловится только в `_consume`) — P2.
- PostgreSQL barrier-тесты (A05/A09), frontend E2E, live-сценарии (Telegram
  draft tiers, VPS SIGTERM, Gemini/Alibaba live) — не прогонялись в этой среде.
- Kimi DTL — optional по ТЗ §25, не реализован.
- CI-файл создан, но реальных прогонов GitHub Actions из этой среды не видно.

**Вердикт дополнения:** 7/7 прод-дефектов раунда 1 закрыты и теперь закрыты
тестами; процессные фиксы подтверждены; P0-регрессия 0be0524 (NameError) поймана
и исправлена. FIX_REPORT_V2 оставался завышенным по 4 позициям — все 4 закрыты
в раунде 3. Документ не переоценивает статус: A15-1/A11-остаток и live-проверки
остаются открытыми.
