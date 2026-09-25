# Final review — polyra-search-security (tools / search / SSRF)

**Аудитор:** final-review subagent (READ-ONLY аудит)
**Дата:** 2026-09-25
**Checkout:** O:\work\aibot (рабочее дерево; git-команды, меняющие состояние, не выполнялись)
**Scope:** `app/search/**`, `app/security/ssrf.py`, `app/security/crypto.py`, `app/llm/tools/**`, связанные части `app/services/generation.py` (tool policy / effective tools / ToolContext), `tests/unit/test_{search,ssrf,tools,crypto}.py` (+ смежные `tests/unit/test_generation.py`, `tests/integration/test_fix_v2_usage_ledger.py` для tool loop).
Примечание: `app/search/security.py` и `app/security/secrets.py` **не существуют** в текущем дереве (в scope-листе значились «если есть»).

## Метод и окружение

- Чтение реального кода с file:line; сверка тестов с production-путём (уровень перехвата моков).
- Исследование фактического поведения транспортного стека по исходникам установленного venv: **httpx 0.28.1, httpcore 1.0.9, Python 3.14.7** (см. A19).
- Офлайн-проверки поведения (без сети): `_pin_request` с IPv4/IPv6 pinned IP; `ipaddress.is_global` для IPv4-mapped/NAT64.
- Разрешённые офлайн-тесты:
  - `.venv\Scripts\python -m pytest tests/unit/test_search.py tests/unit/test_ssrf.py tests/unit/test_tools.py tests/unit/test_crypto.py -q` → **132 passed in 1.45s, exit code 0**.
  - дополнительно (смежные, offline): `pytest tests/integration/test_fix_v2_usage_ledger.py tests/unit/test_generation.py -q` → **39 passed in 3.60s, exit code 0**.
- Live-пробы, запуск приложения, git-мутации — не выполнялись (запрещено заданием).

---

## A12 — Memory Off / Web Off как полная серверная политика tools

**Статус: FIXED_PARTIAL**

### Что реализовано (проверено кодом)

1. **Effective tools вычисляются один раз** в `_prepare`:
   - `app/services/generation.py:1016-1022`:
     ```python
     tool_defs = self._resolve_tool_defs(
         web_enabled=web_enabled, memory_enabled=memory_enabled,
         chat_title=chat_title, permissions=permissions)
     llm_tools = make_llm_tools(tool_defs) if tool_defs else None
     ```
   - `_resolve_tool_defs` (`generation.py:488-509`): grant-права (`registry.list_enabled(permissions)`) ∩ chat/user `web_enabled` (фильтр `required_permission != "web_search"`) ∩ `memory_enabled` (фильтр `"memory"`) ∩ title-already-set (фильтр `set_chat_title`). Docstring прямо декларирует контракт: «Тот же набор попадает в LLMRequest.tools и в ToolRunner.allowed_tool_names».
   - `web_enabled`/`memory_enabled` учитывают права: `generation.py:994-1000` (`bool(permissions.can_use_web_search) and web_mode != "off"`; аналогично memory).
2. **Тот же набор — в LLM request и в executor:**
   - LLM request: `generation.py:629` (`tools=llm_tools` в `LLMRequest`) и `generation.py:410-416` (runner создаётся только при `prepared.llm_tools`).
   - Executor: `generation.py:698-707` — ToolContext строится **на каждый раунд** с
     ```python
     allowed_tool_names=frozenset(t.name for t in (llm_tools or [])),
     ```
     т.е. из того же списка, что рекламируется модели.
3. **Неизвестные/необъявленные вызовы отклоняются до любых side effects:**
   - `app/llm/tools/runner.py:65-70`:
     ```python
     # A12: enforcement effective tools ДО любого lookup/выполнения/сети.
     allowed = context.allowed_tool_names
     if allowed is not None and call.name not in allowed:
         return await self._finish(call, context, "denied", "Tool not allowed", ...)
     ```
     Проверка идёт **раньше** registry lookup → даже unknown tool получает `denied` (тест `test_unknown_tool_not_in_allowed_is_denied_not_error`).
   - Далее `runner.py:72-84`: unknown → «error: Unknown tool»; disabled → denied; **право перепроверяется на каждый вызов** (`has_permission(context.permissions, tool.required_permission)`).
4. **Cancellation перед каждым side effect:** `generation.py:711-714`:
   ```python
   for call in round_calls:
       # A11: отмена проверяется ПЕРЕД каждым side effect, не после пачки.
       if cancellation.is_set(): return True
       execution = await tool_runner.execute(...)
   ```
   Выполнение строго последовательное (await в цикле). Тест: `tests/integration/test_fix_v2_usage_ledger.py:210-268` (отмена во время tool первого раунда → второй LLM-вызов не стартует).
5. **Memory Off выключает и retrieval/extraction, и инструменты:** retrieval — `generation.py:1002` (`if memory_enabled and ...`); extraction — `generation.py:797-799` + `:1073` (`memory_extraction_enabled=memory_enabled`); remember/forget — фильтр в `_resolve_tool_defs`.
6. **Runner-level тесты** (`tests/unit/test_tools.py:440-482`): вне allowed → denied, handler не вызывается; в allowed → ok; `allowed_tool_names=None` → прежнее поведение (обратная совместимость).

### Остаточные проблемы (почему PARTIAL)

1. **Permissions — неизменяемый snapshot на весь запуск.** `EffectivePermissions` — frozen dataclass (`app/services/access.py:36-46`), вычисляется в middleware на update (`app/bot/middleware/access.py:79-101`) и передаётся в `generate()`. Изменение прав в БД **между tool-раундами не перечитывается**: снятие только `can_use_web_search`/`can_use_memory` (без suspend/revoke/ban) не действует на текущую генерацию до её конца. Полный revoke/suspend/ban останавливает задачи (`app/api/routes/admin_access.py:14-24,108,124,140` → `GenerationRegistry.stop_all_for_user`, `generation.py:112-119`), но тот метод **только ставит cancellation event и не делает `task.cancel()`** (в отличие от пользовательского Stop, `generation.py:121-131`) — зависшее чтение провайдера прервётся только между чанками; инструменты между собой отмену видят (п.4 выше), так что для tools-пути это закрывается, для transport — зона A11.
2. **Нет generation-level теста на фильтрацию `_resolve_tool_defs`** (web off → web_search/open_url исключены из набора). Механизм runner протестирован, проводка «web off → подставленный web_search получает denied без сети» покрыта только компонентно (runner + frozenset), не через `_stream_loop`/`_prepare`. `test_generation.py` содержит только echo-tool сценарии.
3. Мелочь: `ToolContext.settings` получает env-`self._settings`, а не `effective_settings` (`generation.py:703`) — `memory_dedup_threshold` в remember не подчиняется DB system settings (зона A13).

**Вывод:** ядро A12 (единый effective set в advertised+executor, fail-closed на неизвестные/необъявленные вызовы, перепроверка права и отмены перед side effect) реализовано и протестировано на runner-уровне; остаток — отсутствие mid-run refresh прав (только полный revoke останавливает) и пробел в тестах проводки.

---

## A19 — SSRF: DNS rebinding / resolve-then-connect

**Статус: FIXED_VERIFIED** (offline-верификация; live TLS не проверялся — запрещено заданием)

### Реализация

- `app/security/ssrf.py:50-76` `validate_url_public_ips`: схема строго http/https (`:61-62`), hostname обязателен, DNS A/AAAA resolve (`resolve_ips:28-47`, IP-литерал passthrough), **каждый** resolved IP проверяется `_assert_ip_public` (`:74-75`). Возвращает `(hostname, ips)` — контракт pinned connect: «caller ОБЯЗАН подключаться к ним, а не перезапрашивать DNS».
- Egress-запрет `ssrf.py:20-25`: явная блокировка `169.254.169.254` (metadata) + `is_multicast/is_reserved/is_unspecified/not is_global` → loopback/private/link-local и пр. заблокированы для IPv4 и IPv6. Офлайн-проверка на Python 3.14.7: `::ffff:127.0.0.1` → `is_global=False` (заблокирован), `::ffff:169.254.169.254` → False; NAT64 `64:ff9b::/96` → True (публичный, пропускается — приемлемо).
- **Pinned connect:** `app/search/fetcher.py:45-74` `PinnedHTTPTransport(httpx.AsyncHTTPTransport)`:
  ```python
  request.url = url.copy_with(host=self.pinned_ip)      # connect-цель
  request.headers["Host"] = self.real_host (+ ":port")  # реальный хост
  request.extensions["sni_hostname"] = self.real_host   # только https
  ```
  и `_fetch_raw_single` (`fetcher.py:221-242`): каждый hop = `validate_url_public_ips` → транспорт на `ips[0]` → свой `httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=Timeout(connect=5, read=timeout, ...))`.
- **Redirects:** ручные (`fetcher.py:150-158, 192-209`) — каждый Location: `urljoin` → **повторная** `validate_url_public_ips` (`:209`) → новый pinned hop. `max_redirects=5`. Redirect-into-private блокируется до второго запроса (тест `test_fetch_redirect_to_private_blocked`, `calls == 1`).
- **TLS verification НЕ отключена:** транспорт не трогает `verify` (дефолт `AsyncHTTPTransport` verify=True).

### Верификация production-пути (главный вопрос «тест мокает, а прод другой»)

Тесты перехватывают `httpx.AsyncHTTPTransport.handle_async_request` (`test_search.py:682-695`) — это ровно слой, где работает `_pin_request`. Ниже этого слоя я проверил **реальные исходники venv**:
- `httpx/_transports/default.py:381-392`: httpx строит `httpcore.Request` из `request.url` (уже переписанного на pinned IP) и прокидывает `request.extensions` (с `sni_hostname`).
- `httpcore/_async/connection_pool.py:304`: `origin = pool_request.request.url.origin` → пул/соединение ключуется по pinned IP.
- `httpcore/_async/connection.py:116-124`: TCP connect идёт на `self._origin.host` (= pinned IP), DNS при connect **не выполняется вообще**.
- `httpcore/_async/connection.py:107,140-153`: `sni_hostname = request.extensions.get("sni_hostname")`; `start_tls(server_hostname=sni_hostname or origin.host)` → **SNI и проверка сертификата по реальному хосту**, ssl_context — httpx-дефолтный (verify=True).

Т.е. в проде соединение действительно идёт к проверенному IP с Host/SNI реального хоста — поведение совпадает с тестами, разрыва «тест/прод» нет. httpx 0.28.1 тянет httpcore 1.x (в venv 1.0.9), где `sni_hostname` поддерживается; `pyproject.toml` pin `httpx>=0.27` (0.27+ также требует httpcore>=1.0).

Офлайн-смоуки: `_pin_request` с IPv6 pinned IP (`2606:2800:...`) → `url.host`/`raw_host`/Host/SNI корректны.

Тесты: `test_fetch_pinned_connect_uses_validated_ip` (rebinding: `resolves == 1`, connect-цель = проверенный IP, Host/SNI = реальный хост), `test_fetch_pinned_per_redirect_hop` (каждый hop пиннится к IP своего хоста), `test_fetch_pinned_ip_literal_same_path`, `test_pinned_transport_rewrites_connect_target`; плюс ssrf-тесты (схемы, IP-литералы, private-resolve, DNS-failure, mixed-контракт `validate_url_public_ips`).

### Дедлайн и лимиты

Per-hop timeout (`fetcher.py:240`: connect=5s, read/write/pool=`timeout`=20s дефолт) + `max_redirects=5` + max_bytes=2 МБ (`fetcher.py:36,129-139`, stream-read с обрезкой) + общий потолок всего open_url — tool timeout 30 с (`asyncio.wait_for` в runner, `builtin.py:282`).

### Остаточные замечания

1. **Тесты не проверяют реальный сокет/TLS-handshake** (нет сети в среде) — поведение подтверждено чтением исходников httpcore; live-проверка остаётся непроверенной (честно зафиксировано и в `fix-v2/polyra-search-security/wave1.md:78`).
2. **KNOWN_ISSUES.md:38-40 не обновлён**: пункт 9 всё ещё описывает DNS rebinding как «принятый риск» с «усилением позже», хотя pinned transport уже реализован (рассинхрон документации, зона A38).
3. Мелочь: mixed A/AAAA покрывается кодом (проверяются ВСЕ IP ответа, `ssrf.py:74-75`), но отдельного теста mixed-ответа нет.
4. `http_client`-инъекция в `fetch_url` обходит pinning, но production-вызыватель (`builtin._open_url_handler:181`) её не использует — только тесты.

**Вывод:** DNS-rebinding/TOCTOU-окно закрыто архитектурно (validate→pin per hop, включая редиректы), TLS verify включён, egress-запрет полный, дедлайны есть.

---

## A30 (search-часть) — lifecycle клиентов поиска

**Статус: FIXED_VERIFIED**

- **Кэш инстансов:** `app/search/manager.py:96-117` `_get_backend` под `asyncio.Lock`: реестр `backend_id → _BackendEntry(instance, signature=(enabled, encrypted_api_key))`; смена конфига/ключа → `aclose()` старого + пересоздание. `_load_backends` (`:119-137`) переиспользует инстансы (backends создаются только для enabled, `:128-129`, кроме явного `include_disabled` в health check — и те кэшируются и закрываются на shutdown).
- **Shutdown:** `manager.aclose()` (`:162-179`) — закрывает все инстансы + reader, идемпотентно, ошибки гасятся в лог. Вызывается из `app/main.py:135` в `finally` («закрыть все транспорты ровно один раз»: llm_stream, search, bot session, DB engine) и закрывает единый инстанс менеджера.
- **Один инстанс на процесс:** `app/main.py:75` создаёт `SearchManager` и передаёт его и в `GenerationService` (`:89`), и в `create_app` (`:104`); `app/api/app.py:43-46` использует переданный (`search_manager or SearchManager(...)` — второй создаётся только при автономном запуске API без main, что в текущей сборке не встречается).
- Ключи: decrypt при создании (`:87-94`), сбой decrypt → None (backend не configured) — не crash.
- Тесты: `test_search.py:997-1058` — `test_manager_reuses_single_backend_instance` (2 поиска → 1 инстанс; aclose×2 → ровно 1 close), `test_manager_recreates_backend_on_key_change` (старый закрыт, новый с новым ключом), reader-кэш/пересоздание.
- Все backend-классы имеют `aclose()` с `owns_client`-семантикой (`serper.py:38-40`, `brave.py:43-45`, `jina.py:45-47,114-116`, `serpapi:45-47`, `playwright:42-43`), Protocol дополнен (`base.py:78`).

Остаток по A30 вне моего scope (Alibaba/Gemini factory-кеш) — у polyra-alibaba/gemini.

---

## A31 — JinaReader в рабочем пути open_url

**Статус: FIXED_PARTIAL**

### Реализовано (проверено)

- **Получение reader:** `app/search/manager.py:139-160` `get_reader()` — из конфига `jina_search` (enabled + ключ; reader и search делят ключ jina), кэшируется, смена ключа → aclose + пересоздание.
- **Внедрение в рабочий путь:** `app/services/generation.py:449-457` `_resolve_jina_reader()` (менеджер → get_reader; любая ошибка → None с warning) и `generation.py:705`:
  ```python
  jina_reader=await self._resolve_jina_reader(),
  ```
  ToolContext строится на каждый tool-раунд. GenerationService получает `search_manager` из `main.py:89` — проводка реальная, default None остаётся только для тестов/неполных сборок.
- **Использование:** `app/llm/tools/builtin.py:181` `page = await fetch_url(url, reader=context.jina_reader)`.
- **Fallback:** `app/search/fetcher.py:245-260` `_extract_text`: reader вернул None **или бросил любое исключение** → локальная `html_to_text` уже скачанного тела (warning в лог). Тесты: `test_fetch_uses_reader_when_provided`, `test_fetch_reader_none_falls_back_to_local_extraction`, `test_fetch_reader_exception_falls_back_to_local_extraction` (A31), `test_jina_reader_error_returns_none`.
- **SSRF/bytes обоими путями — не обходятся:** порядок в `fetch_url` — сначала наш собственный SSRF-safe fetch (validate → pinned connect → content-type → max_bytes), и только затем `_extract_text` с reader поверх **уже проверенного** `final_url`. Т.е. reader физически не может получить тело, не прошедшее наши проверки; контент помечен недоверенным (`builtin.py:186-191`).

### Остаток (почему PARTIAL)

1. **Разделение search/reader capabilities/ключей/enablement НЕ реализовано** (прямое требование fix-текста A31 «разделить search и reader capabilities/ключи/enablement»): enablement reader жёстко привязан к enabled+key бэкенда `jina_search` (`manager.py:147-151` — без ключа reader вообще недоступен, хотя `JinaReaderBackend.is_configured()`=True и безключевой режим 20 RPM задуман, `jina.py:111-112`). Отдельного admin-переключателя/ключа для reader нет (`_BACKEND_FACTORIES` в `manager.py:41-47` не содержит `jina_reader`).
2. **Нет теста через настоящий tool loop** (что `ToolContext.jina_reader` реально приходит из менеджера в `_run_tool_round` и до `fetch_url`): проводка `generation.py:705` покрыта только чтением кода; тесты есть на уровне менеджера (`test_get_reader_from_jina_config`) и fetcher'а.
3. Мелочь: `_resolve_jina_reader()` на каждый раунд делает чтение БД (`get_reader` → session) — допустимо, но лишние round-trip'ы; ключ и так кэширован.

---

## A32 — sources и AI Overview end-to-end

**Статус: FIXED_PARTIAL**

### Реализовано (проверено)

- **AUTO: обычный поиск первым.** `app/search/manager.py:183-220` `search()`: для `normal|auto` сначала `_normal_chain`; AIO пробуется только если normal вернул 0 результатов ИЛИ упал (`:213`), с последующим возвратом исходного normal-исхода/ошибки. Эвристика «вопрос → сразу AIO» удалена (подтверждено отсутствием `_looks_like_question` в manager). Тесты: `test_manager_auto_normal_first_even_for_question` (aio.calls==0), `test_manager_auto_empty_normal_falls_to_ai_overview`, `test_manager_auto_normal_error_falls_to_ai_overview`, `test_manager_auto_both_failed_raises`, `test_manager_auto_empty_normal_and_no_aio_returns_empty`.
- **Overview без references — не «подтверждённый»:** (a) backend `serpapi_ai_overview.py:80-83` — пустые references → `_degrade` в organic, текст не выдаётся; (b) страховка в менеджере `manager.py:239-242` (`ai_overview_text and not outcome.results` → fallback на normal). Тесты: `test_serpapi_overview_without_references_degrades`, `test_manager_ai_overview_without_references_falls_back`.
- **show_sources default:** `app/config.py:37` `show_sources: bool = True` (было False — актуальное проверено). Конфликт с просьбой владельца (19.09) задокументирован в `KNOWN_ISSUES.md:54-56` (V2 выбран, есть env-выключатель SHOW_SOURCES=0).
- **«Источники:» блок:** `generation.py:438-442` — при `show_sources` и реально использованном поиске (`collect_sources` по **успешным** web_search tool records, `:242-253`, dedupe по url) к финалу добавляется `build_sources_suffix` (`:256-261`, `**Источники:**` + `[title](url)`), без дубликата, если модель уже сослалась (`"Источники" not in final_outcome.text`). Модель получает в tool-результате маркер `SOURCES: url | url` (`builtin.py:129-153`) и системный запрет выдумывать ссылки (`config.py:30-32`).
- Тесты: `test_generation.py:373-415` (extract_sources numbered/marker, collect_sources dedupe+skip errors, build_sources_suffix).

### Остаток (почему PARTIAL)

1. **Источники ходят кругом через ТЕКСТ, а не структуру.** SearchOutcome.results (структурированные) → `_format_search_outcome` (текст) → модель → `extract_sources` **парсит обратно текст tool-результата** (`generation.py:217-239`). Fix-требование «сохранять структурированные source references до final renderer» выполнено только в текстово-парсируемом виде; fragile-by-design.
2. **Паттерн «фикс проходит тест, но ломается в проде» — truncation режет SOURCES.** У `web_search` `max_result_size` не переопределён → дефолт 4000 (`registry.py:33`), runner обрезает (`runner.py:148-150`). При `max_results=10` (schema max, `builtin.py:36`) и сниппетах ~300 симв. (jina) суммарный текст ~4.3–5 КБ → обрезка съедает хвостовую строку `SOURCES: ...`; citations тогда собираются только из выживших нумерованных записей (`N. title\nurl`), т.е. «гарантированные citations при реально использованном поиске» выполняется не строго (тесты используют 2 коротких результата — worst-case не покрыт).
3. **Clickable только в rich tier:** суффикс — Markdown; в plain tier'ах draft.py шлёт `parse_mode=None` (`draft.py:177,199,206...`) — ссылки остаются сырым текстом `[title](url)` (рендер — зона A20/A21, но end-to-end «final Telegram во всех tiers содержит [clickable] источники» не доказан).
4. AIO sections/references сохраняются в tool-результате как текст (sections сведены в `ai_overview_text`), структурно до рендерера не доходят.
5. Нет E2E/интеграционного теста «web_search → final message содержит Источники во всех tiers».

---

## A33 — устойчивость к некорректной структуре ответа search backend

**Статус: FIXED_VERIFIED**

- **Валидация типов во всех парсерах** (isinstance-гварды, невалидная структура → `SearchBackendError`, null-поля tolerated, битые item'ы skip):
  - serper: `serper.py:59-84` (payload dict / `organic` list / item dict / link str).
  - brave: `brave.py:91-104` `_result_items` (payload/web/results) + `:64-88` (item поля, profile не dict → fallback на hostname).
  - serpapi: `serpapi_ai_overview.py:99-101` (payload), `:107-114` (`organic_results`), `:136-142` (`text_blocks`), `:164-170` (`references`), `:65-77` (ai_overview dict, page_token str).
  - jina: `jina.py:63-96` (payload dict/list, `data` list).
  - JSON: `base.py:105-110` `parse_json` — invalid JSON → SearchBackendError.
- **Fallback менеджера:** `manager.py:250-276` `_normal_chain` ловит именно `SearchBackendError` у каждого бэкенда → записывает health → **переходит к следующему**; все упали → агрегированная `SearchBackendError`. HTTP-классификация в `base.py:113-152` `request_with_retry`: 401/403 → BackendUnavailable; 429/5xx/timeout → 1 повтор → retryable; прочие 4xx → ошибка; network → ошибка.
- **Playwright:** `playwright_google.py:66-79` — 429/503 и captcha (`/sorry/`, recaptcha iframe) → `BackendUnavailableError`; все прочие исключения оборачиваются в `SearchBackendError` (`:76-79`). Cooldown: `manager.py:280-297` — только для `playwright_google`, `BackendUnavailableError` → in-memory 15-мин cooldown (monotonic), бэкенд скипается в цепочке. Bounded, per-process — для experimental приемлемо; CAPTCHA bypass/stealth **отсутствуют** (проверено чтением playwright_google.py), disabled по умолчанию (`is_configured()` = установлен ли playwright).
- Тесты: malformed fixtures ×8 (`test_search.py:795-895`), null-поля brave, `test_manager_429_falls_to_next_backend`, `test_playwright_cooldown_skips_backend` (повторно не дёргается: calls==1), manager fallback order/unavailable.
- Остаточная мелочь: менеджер ловит только `SearchBackendError` — теоретически неожиданный тип исключения внутри бэкенда (баг) оборвал бы цепочку (но уходил бы в tool как безопасный «Tool error», не crash процесса); все известные пути (httpx/JSON/типы/playwright) уже обёрнуты.

---

## A39 — tool loop: ограничения и сохранение assistant turn

**Статус: FIXED_PARTIAL**

### Реализовано (проверено)

- **Полный assistant turn:** `generation.py:759-777` `_assistant_tool_message(tool_calls, round_text)` — видимый текст раунда + tool_call parts **с `provider_meta`** (Gemini thought signatures сохраняются, A39 «не отключать signatures» — fields передаются as-is, `:773`). Вызов: `generation.py:709` с `outcome.text`. Тест: `test_stream_loop_executes_tool_calls_and_continues` (assistant + tool result в следующем payload), `test_fix_v2_usage_ledger.py:163-207`.
- **Max calls в batch:** `config.py:56` `max_tool_calls_per_round: int = 4`; `generation.py:711` `round_calls = outcome.tool_calls[: self._settings.max_tool_calls_per_round]`.
- **Общий deadline:** `config.py:57` `max_generation_seconds: int = 240`; `generation.py:618-623` — проверка в верхе каждого цикла (`time.monotonic() > deadline → break` с warning).
- **Timeout per tool:** `runner.py:126` `asyncio.wait_for(tool.handler(...), timeout=tool.timeout)` (TimeoutError → статус timeout; web_search 40 с, open_url 30 с, set_chat_title 10 с — `builtin.py:272,282,296`).
- **Последовательное выполнение + отмена перед каждым вызовом:** `generation.py:712-714` (см. A12). Отмена во время tool-раунда покрыта интеграционным тестом (`test_fix_v2_usage_ledger.py:210-268`).
- **Max iterations:** `config.py:55` `max_tool_iterations: int = 8` (конфигурируемый, включая DB system settings — `services/settings.py:113-114`, `generation.py:948`); `generation.py:754-756` — стоп с warning. Тест: `test_stream_loop_respects_max_iterations` (ровно N исполнений).
- Usage между раундами суммируется per-round (`_sum_usage`, `generation.py:647-648`) — зона A07, подтверждено тестом.
- НЕТ shell/filesystem/python-инструментов: `build_default_registry` (`builtin.py:260-319`) содержит ровно 5 tools из ТЗ §24.

### Остатки и дефекты (почему PARTIAL)

1. **ДЕФЕКТ (прод-паттерн «лимит есть, но протокол ломается»): batch-лимит молча отбрасывает хвост вызовов.** `generation.py:709` кладёт в assistant turn **ВСЕ** `outcome.tool_calls`, а исполняются только первые 4 (`:711`). Вызовы №5+ не получают tool_result-частей. Провайдер-маппинг отправляет их как есть: `app/llm/providers/alibaba.py:103-117` (`_map_assistant_message` кладёт все `tool_call` parts в `tool_calls`). OpenAI-совместимые API (dashscope compatible-mode) и Gemini требуют tool-message/functionResponse на **каждый** tool_call → следующий запрос получает 400 «tool_calls must be followed by tool messages» / mismatch function responses. Ни один тест не подаёт >4 вызовов в раунде — дефект не ловится. Корректно было бы либо исполнить-и-отказать (denied «batch limit exceeded» как tool result), либо резать assistant turn до исполненного набора. (Отмена посреди batch безопасна — следующего LLM-запроса уже нет.)
2. **Нет агрегатного result/token budget на tool-результаты между раундами:** пер-инструментная обрезка 4000 симв. (`registry.py:33`, `runner.py:148-150`) есть, но накопленные `tool`-сообщения не перебюджетируются перед следующим LLM-вызовом (`_stream_loop` строит LLMRequest без повторной проверки бюджета; потолок ~8×4×4000 ≈ 128 КБ). Требование «result/token budget» из fix-текста выполнено частично.
3. **Нет явного max calls/run-счётчика** (ограничен неявно iterations×per-round = 32) и **нет явного статуса max-iterations наружу** (только log; если последний раунд был tool-only, пользователь получает generic «вернула пустой ответ», `generation.py:426-436`).
4. Deadline проверяется только вверху цикла: худший просчёт ≈ один batch (4×40 с) + текущий LLM-запрос — не жёсткий дедлайн (зато Stop/`task.cancel` прерывает зависания, A11).

---

## app/security/crypto.py (попутная проверка)

**Статус: OK (замечаний по пунктам аудита нет)**

- Fernet (AES-128-CBC + HMAC, authenticated); `master_key` — либо валидный urlsafe-b64 Fernet-ключ (len 32), либо passphrase → SHA-256 → ключ (`crypto.py:16-30`); `decrypt` на мусор → `ValueError("decryption failed")` (`:36-45`), менеджер по нему возвращает None → backend не configured (fail-closed). `mask_secret` — head/tail с `***` для коротких. Секреты в логах этим модулем не светятся (в `manager.py:93` логируется только backend_id).
- Тесты `test_crypto.py` — 6 passed (roundtrip passphrase/fernet-key, corrupted token, mask).
- Дизайн-заметка (не из аудита): KDF = одиночный SHA-256 без salt/stretching — для производного Fernet-ключа приемлемо (целостность даёт HMAC), но при слабом master-passphrase устойчивость к brute-force ограничена; возможное усиление — PBKDF2/Argon2 при ротации.

---

## Соответствие исходному ТЗ (§24, §26–§32) — кратко

- §24 ToolRegistry generic async + ToolDefinition(name, description, JSON schema, timeout, required_permission, enabled, max_result_size) — `registry.py:22-33`, все поля на месте; 5 начальных tools — `builtin.py`; MAX_TOOL_ITERATIONS ~8 configurable — да (env+DB). Shell/filesystem/python execution — отсутствуют. **+** JSON Schema validator без зависимостей (`schemas.py`), валидация в runner до хендлера.
- §26 SearchBackend abstraction + SearchResult/SearchManager ordered list + admin вкл/выкл/priority/health/masked key — `base.py`, `manager.py` (priority asc из репозитория, health в `search_backend_configs`, admin-роут с masked key у db-api-агента).
- §27 Serper (query/language/country/number/timeout) — `serper.py` (q/gl/hl/num, SEARCH_TIMEOUT 10 с + retry).
- §28 SerpApi AIO (текст, sections, references, режимы normal/ai_overview/auto) — `serpapi_ai_overview.py` + режимы в `SearchOptions`/`manager.search`.
- §29 JinaSearch optional + JinaReader + direct fallback — `jina.py`, fallback в `fetcher._extract_text` (reader не заменяет direct-путь, а надстраивается).
- §30 Brave optional, нет ключа → disabled, не crash — `brave.py` + `is_configured()`-skip в `_normal_chain` (`manager.py:259-260`).
- §31 Playwright optional, disabled by default, NO CAPTCHA bypass/stealth, abort при challenge, cooldown — `playwright_google.py` (нет stealth-импортов; challenge → BackendUnavailable; cooldown 15 мин в manager).
- §32 open_url: scheme validation, DNS resolve, SSRF, fetch, content type, max bytes, extract, truncate; блокировка localhost/private/link-local/metadata/file/ftp/gopher; redirect в private; UNTRUSTED content — `ssrf.py` + `fetcher.py` + `builtin.py:186-191` (маркеры недоверенного контента).

---

## Сводная таблица

| Пункт | Статус | Ключевые файлы:строки | Главное |
|---|---|---|---|
| A12 | FIXED_PARTIAL | generation.py:488-509,1016-1022,698-714; runner.py:65-84 | Effective set един в LLMRequest и runner, unknown/unadvertised → denied, право+отмена перед side effect; НО права — snapshot на запуск (mid-run изменение флага без suspend не действует), нет generation-level теста web/memory off |
| A19 | FIXED_VERIFIED | ssrf.py:20-84; fetcher.py:45-74,150-242; httpcore/_async/connection.py:107-152 | Pinned connect к проверенному IP (per hop, вкл. редиректы), Host+SNI+cert по реальному хосту, verify не отключён, egress-запрет, дедлайны; прод-путь подтверждён исходниками httpx 0.28.1/httpcore 1.0.9; live TLS не проверялся (запрещено); KNOWN_ISSUES:38-40 устарел |
| A30 (search) | FIXED_VERIFIED | manager.py:96-179; main.py:75,135; api/app.py:43-46 | Кэш инстансов по сигнатуре, aclose при смене конфига и shutdown (идемпотентно), один менеджер на процесс; тесты reuse/recreate/close |
| A31 | FIXED_PARTIAL | manager.py:139-160; generation.py:449-457,705; builtin.py:181; fetcher.py:245-260 | Reader реально в ToolContext per round, fallback при ошибке/None, SSRF+bytes необходи́мы обоими путями; НО reader не отделён от jina_search (ключ/enablement общие), безключевой режим недоступен, нет теста через настоящий tool loop |
| A32 | FIXED_PARTIAL | manager.py:183-248; serpapi:80-83; config.py:37; generation.py:214-261,438-442; builtin.py:129-153 | AUTO normal-first, AIO без refs не авторитетен, show_sources=True, «Источники:» блок; НО sources — текстовый re-parse (не структура), обрезка 4000 симв. может съесть SOURCES-строку (worst-case citations не гарантированы), clickable только rich tier, нет E2E |
| A33 | FIXED_VERIFIED | serper.py:59-84; brave.py:91-104; serpapi:99-170; jina.py:63-96; manager.py:250-297; playwright_google.py:66-79 | isinstance-валидация → SearchBackendError → fallback к следующему; 429/5xx retry+fallback; playwright cooldown 15 мин bounded; malformed fixtures в тестах |
| A39 | FIXED_PARTIAL | generation.py:618-623,709-714,754-777; config.py:55-57; runner.py:126-136 | Assistant turn полный (текст+вызовы+signatures), max 4/раунд, deadline 240 с, per-tool timeout, отмена перед каждым вызовом, iterations 8; НО **дефект: хвост batch >4 → orphan tool_calls без tool results → 400 провайдера**; нет агрегатного result/token budget; нет явного max-iterations статуса |
| crypto | OK | crypto.py | Fernet authenticated, fail-closed decrypt, masked secrets; тесты 6 passed |

## Выполненные проверки (для воспроизводимости)

```
.venv\Scripts\python -m pytest tests/unit/test_search.py tests/unit/test_ssrf.py tests/unit/test_tools.py tests/unit/test_crypto.py -q
  → 132 passed in 1.45s, exit 0
.venv\Scripts\python -m pytest tests/integration/test_fix_v2_usage_ledger.py tests/unit/test_generation.py -q
  → 39 passed in 3.60s, exit 0
.venv\Scripts\python -c "import httpx, httpcore" → httpx 0.28.1, httpcore 1.0.9, Python 3.14.7
Offline: _pin_request IPv4/IPv6 pin — OK; ::ffff:127.0.0.1 / ::ffff:169.254.169.254 → is_global=False (blocked)
```

## Ключевые рекомендации

1. **A39-дефект (P2, функциональный):** для вызовов за пределами `max_tool_calls_per_round` генерировать tool result «denied: batch limit exceeded» (или резать assistant turn до исполненных) — иначе вероятен 400 провайдера при ≥5 параллельных вызовах модели.
2. **A32:** поднять/убрать `max_result_size` у `web_search` (или ставить SOURCES-строку первой), либо передавать sources структурированно (из SearchOutcome) вместо re-parse текста.
3. **A12:** определить политику mid-run refresh прав (перечитывать grant перед side-effect-инструментами или расширить stop-семантику `stop_all_for_user` на task.cancel).
4. **A31:** отделить reader-enablement/ключ от jina_search (отдельная строка конфига), дать безключевой режим.
5. **A19/доки:** обновить KNOWN_ISSUES.md:9 (pinned transport уже реализован); добавить mixed A/AAAA тест.
6. Тестовые пробелы: `_resolve_tool_defs` (web/memory off), >4 tool calls в раунде, sources в финальном сообщении по всем tier'ам, jina_reader через настоящий tool loop.
