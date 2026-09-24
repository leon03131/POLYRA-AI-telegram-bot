# polyra-search-security — wave1 (A19, A30-search, A31, A32, A33, A12-runner)

**Дата:** 2026-09-24
**Scope (строго):** `app/search/**`, `app/security/ssrf.py`, `app/llm/tools/{registry,runner,builtin,schemas}.py`, `tests/unit/{test_search,test_ssrf,test_tools}.py`, этот отчёт. Чужие scope не тронуты (`app/services/**`, `app/bot/**`, `app/db/**` — только чтение).
**Верификация:** pytest НЕ запускался (ограничение задачи). Использованы `py_compile` (все изменённые файлы — OK), `ruff check` (All checks passed) и точечные async-смоуки через `.venv` python (без сети: транспортный слой monkeypatch'ился) — каждый сценарий новых тестов прогнан вручную и прошёл.

---

## A19 — SSRF pinned connect (fetcher.py + ssrf.py)

**Проблема:** старый `fetch_url` валидировал URL (`assert_url_public`), но connect шёл через обычный httpx-клиент → DNS перезапрашивался при connect (DNS-rebinding/TOCTOU gap).

**Реализация:**
- `app/security/ssrf.py`: новая `validate_url_public_ips(url, *, resolver) -> (hostname, list[IPAddress])` — схема + resolve + публичность каждого IP, возвращает проверенные IP. `assert_url_public` сохранена как обёртка (обратная совместимость).
- `app/search/fetcher.py`: `PinnedHTTPTransport(httpx.AsyncHTTPTransport)`:
  - `request.url.host → pinned_ip` (connect-цель);
  - `Host: real_host` (+ `:port` для нестандартного порта);
  - `request.extensions["sni_hostname"] = real_host` для https.
  - **Проверено по исходнику httpcore 1.0.9** (в .venv): `AsyncHTTPConnection._connect` читает `sni_hostname` из extensions и передаёт его как `server_hostname` в `start_tls` → и SNI, и ssl hostname verification идут по реальному хосту. **verify НЕ отключается** (ssl_context дефолтный). Фолбэк-вариант с ручным match_hostname не понадобился.
  - `httpx.URL.copy_with(host=...)` корректно рендерит IPv6 (`[...]`) — проверено.
- `fetch_url`: каждый hop (включая каждый редирект) = `validate_url_public_ips` → pinned transport (свой клиент на hop) → один GET. IP-литералы — тот же pinned-путь (host==ip, resolver не вызывается). `FetchedPage.url` — реальный хост (не pinned IP).
- Тестовые инъекции сохранены: `http_client` (legacy-путь, как раньше), новая `transport_factory(pinned_ip, real_host)`; `resolver` как было.

**Тесты (test_search.py, test_ssrf.py):**
- `test_pinned_transport_rewrites_connect_target` — Host с нестандартным портом, sni только для https.
- `test_fetch_pinned_connect_uses_validated_ip` — «DNS сменился после validation»: connect ушёл на СТАРЫЙ проверенный IP (запись транспорта), DNS на connect не перезапрашивался (`resolves == 1`), Host/SNI = реальный хост, TLS verify не отключён.
- `test_fetch_pinned_ip_literal_same_path` — IP-литерал: pinned-путь, resolver не вызывается.
- `test_fetch_pinned_per_redirect_hop` — каждый hop пиннится к IP своего хоста.
- `test_validate_*` (5 шт.) в test_ssrf.py.
- Существующие fetcher-тесты (redirect-into-private, max_bytes, content-type, timeout mapping) — на legacy-инъекции, логика ручных редиректов/лимитов сохранена (проверено смоуком).

## A30 (search-часть) — lifecycle клиентов

`SearchManager`:
- Реестр `_entries: backend_id → _BackendEntry(instance, signature=(enabled, encrypted_api_key))` под `asyncio.Lock`. `_load_backends` больше НЕ создаёт инстансы на каждый вызов — переиспользует; смена enabled/ключа (set_key через админку) → `aclose()` старого + пересоздание при следующем вызове (ленивая инвалидация по подписи, подписка не нужна).
- `async def aclose()` — закрывает все инстансы + reader, идемпотентно, ошибки закрытия гасятся в лог.
- `async def get_reader() -> JinaReaderBackend | None` — из конфига `jina_search` (enabled + key; reader и search делят ключ jina), кэшируется, смена ключа → aclose + пересоздание.
- `SearchBackend` Protocol дополнен `aclose()` (все реализации его уже имели).

**Тесты:** `test_manager_reuses_single_backend_instance` (2 search → 1 инстанс; aclose×2 → ровно 1 close), `test_manager_recreates_backend_on_key_change` (старый закрыт, новый с новым ключом), `test_get_reader_from_jina_config` (кэш, isinstance JinaReaderBackend), `test_get_reader_none_when_disabled_or_keyless`, `test_get_reader_recreates_on_key_change`. Фейки: `_FakeConfigRepo` + подмена `_BACKEND_FACTORIES["serper"]` на считающую фабрику (monkeypatch).

## A31 — reader в open_url (builtin.py)

- `open_url` handler: `reader=context.jina_reader` — оставлено.
- Fallback при сбое reader: был при `read() → None`; **добавлено** — исключение reader'а тоже → локальная экстракция (`_extract_text` в fetcher.py, warning в лог).
- SSRF: прямой fetch — pinned-путь (A19); reader получает только URL, прошедший `validate_url_public_ips` (Jina — внешний сервис, fetch делает он; наши проверки применены ДО передачи URL). Задокументировано в docstring модуля fetcher и handler'а.
- Тест: `test_fetch_reader_exception_falls_back_to_local_extraction`.

## A32 — sources contract + AUTO порядок

- `SearchManager.search`: **auto = сначала normal-цепочка**; AIO только если normal вернул 0 результатов ИЛИ упал (`SearchBackendError`). Эвристика «вопрос → сразу AIO» (`_looks_like_question`, `_QUESTION_STARTERS`) **удалена**. Если AIO тоже не дал результата — возвращается исходный normal-исход (включая пустой) либо оригинальная ошибка normal.
- AI Overview без references → не авторитетен: (a) `serpapi_ai_overview.ai_overview` при пустых references деградирует в organic (`ai_overview_text=None`); (b) страховка в `manager._try_ai_overview`: `ai_overview_text` без results → fallback на normal.
- `_format_search_outcome` (builtin.py): человеко-читаемый список `N. title\nurl\nsnippet` + строка `SOURCES: url | url` сохранены (формат парсится `generation.extract_sources` — не менял); результаты без url теперь отбрасываются → title+url всегда вместе, SOURCES соответствует списку 1:1.

**Тесты:** `test_manager_auto_normal_first_even_for_question` (заменил старый `test_manager_auto_question_uses_ai_overview` — поведение изменено по A32), `test_manager_auto_empty_normal_falls_to_ai_overview`, `test_manager_auto_normal_error_falls_to_ai_overview`, `test_manager_auto_both_failed_raises`, `test_manager_auto_empty_normal_and_no_aio_returns_empty`, `test_manager_ai_overview_without_references_falls_back`, `test_serpapi_overview_without_references_degrades`. Существующие `test_manager_auto_non_question_goes_normal` (заменён аналогом), `test_manager_ai_overview_failure_falls_back_to_normal` — актуальны.

## A33 — валидация ответов бэкендов

- **serper**: payload не dict → `SearchBackendError`; `organic` не list → `SearchBackendError`; item не dict / link не str → skip; title/snippet/date — только str (null → ""/None).
- **brave**: payload не dict, `web` не dict, `web.results` не list → `SearchBackendError` (маппинг вынесен в `_result_items` — ruff C901); null-поля item'ов tolerated; profile не dict → hostname fallback.
- **jina_search**: payload не dict/list → `SearchBackendError`; `data` не list → `SearchBackendError`; content не str → пустой snippet.
- **serpapi**: `organic_results`/`text_blocks`/`references` не list → `SearchBackendError`; `page_token` не str → degrade; null-поля tolerated.
- Все такие ошибки — `SearchBackendError` → manager fallback работает (ловит именно его).
- **Playwright cooldown**: in-memory `_playwright_cooldown_until` (monotonic, 15 мин) в manager; ставится на `BackendUnavailableError` от `playwright_google`; в cooldown бэкенд пропускается в normal-цепочке (health_check не затрагивает). Persistent на процесс — ок для experimental.

**Тесты:** malformed fixtures — 8 новых (`test_serper_malformed_*` ×3, `test_brave_malformed_*` ×2 + null-fields, `test_jina_malformed_*` ×2, `test_serpapi_malformed_organic_not_list`); `test_manager_429_falls_to_next_backend`; `test_playwright_cooldown_skips_backend` (2-й вызов — calls==1).

## A12 (runner side) — enforcement effective tools

- `ToolContext`: добавлено `allowed_tool_names: frozenset[str] | None = None` (None = прежнее поведение по permissions). Обратная совместимость: поле с дефолтом, существующие вызовы не тронуты.
- `ToolRunner.execute`: проверка **первой**, до registry lookup — `call.name ∉ allowed_tool_names` → `status="denied"`, "Tool not allowed", handler не вызывается, сети нет (аудит в tool_calls пишется как у прочих denied).

**Тесты (test_tools.py):** `test_not_in_allowed_tool_names_denied` (handler не вызван), `test_allowed_tool_names_allows_listed`, `test_allowed_tool_names_none_keeps_permission_behavior`, `test_unknown_tool_not_in_allowed_is_denied_not_error`.

## Не проверено

- pytest прогон целиком (запрещён задачей) — новые и обновлённые тесты прогнаны вручную эквивалентными async-смоуками; formal `pytest tests/unit/test_search.py test_ssrf.py test_tools.py` — за lead/CI.
- Реальный TLS-handshake к pinned IP с cert verify по SNI (нужна сеть; логика опирается на задокументированное поведение httpcore 1.0.9 `sni_hostname` — проверено чтением исходника).

## Нужно от lead

1. **`ToolContext.allowed_tool_names` — передача:** generation.py (L464) создаёт ToolContext без `allowed_tool_names` — нужно вычислить effective tools один раз на запрос (registry ∩ permissions ∩ chat-level off) и прокидывать `frozenset(names)` и в `LLMRequest.tools`, и в ToolContext (контракт §4).
2. **`jina_reader` wiring:** ToolContext.jina_reader никем не заполняется → `context.jina_reader = await search_manager.get_reader()` в generation.py (или при старте).
3. **Shutdown wiring:** `SearchManager.aclose()` вызвать при остановке в `app/main.py` (L73) и `app/api/app.py` (L38) — два отдельных инстанса SearchManager.
4. Опционально: решить, нужен ли `show_sources` flag (контракт §9) — формат SOURCES не менялся.
