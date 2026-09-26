# Round 4 bug-hunt — POLYRA Mini App (React+TS+Vite)

**Аудитор:** polyra-miniapp (subagent B). **Дата:** 2026-09-26. **Режим:** READ-ONLY (код не менялся; единственная запись — этот отчёт; `npm run build` перегенерировал gitignored `dist/`, что разрешено заданием).
**Checkout:** `O:\work\aibot`, HEAD `4925c40` (после `0be0524` — batch-фикс GLM-реаудита). Scope: `miniapp/src/api/**`, `src/pages/**`, `src/admin/**`, `src/components/**`, `src/telegram/**`, `App.tsx`, `package.json`. Сверка фронта с `app/api/routes/*.py` + `app/services/**` + `app/db/**`.

## Прогоны (новые, этой сессии)

| Команда | Результат |
|---|---|
| `npm run typecheck` (tsc --noEmit, strict) | **exit 0** |
| `npm run build` (tsc && vite build) | **exit 0** — 112 modules, `dist/assets/index-DZ-9lDSY.js` 280.84 kB (gzip 84.52 kB) |
| Frontend тесты | отсутствуют (нет test-скрипта в package.json, vitest/jest/playwright нет; `miniapp/e2e/` нет) — заявленных «503 тестов» миниапп-часть не покрывает |

Класс P0 из `0be0524` (TYPE_CHECKING-импорт в рантайме) в miniapp не воспроизводится: все type-only импорты оформлены `import type` (grep `^import \{[^}]*type ` — 0 совпадений), tsc strict + isolatedModules чисты. Рантайм-крашей (NaN id, null-доступ) не найдено.

---

## P1

### P1-1. AdminAuditPage: пагинация «ростом limit» упирается в backend cap 500 — кнопка «Загрузить ещё» становится вечным no-op

- **Фронт:** `miniapp/src/admin/AdminAuditPage.tsx:22` `const [limit, setLimit] = useState(PAGE_SIZE);` → `:54` `const hasMore = entries.length < total;` → `:112` `onClick={() => setLimit((v) => v + PAGE_SIZE)}`. Offset всегда 0 (`hooks.ts:199` — `useAudit` умеет offset, но страница его не использует).
- **Бэк:** `app/api/routes/admin_stats.py:16` `_MAX_AUDIT_LIMIT = 500` и `:152` `limit = max(1, min(limit, _MAX_AUDIT_LIMIT))`.
- **Механика:** при `total > 500` limit дорастает до 500; все дальнейшие клики шлют `limit=550,600,…`, сервер зажимает до 500 и возвращает те же 500 записей. `entries.length (500) < total` навсегда → кнопка не исчезает и ничего не догружает; записи 501+ **принципиально недостижимы**.
- **Регрессия-контекст:** round-3 (`.agents/reports/final-review/polyra-miniapp.md`, A24, проблема 1) пометил этот паттерн для Chats/Memory/AdminMemory/Audit; `0be0524` перевёл на offset-пагинацию **только** `useChats`/`useMemories` (git show 0be0524 --stat: ChatsPage/MemoryPage/hooks.ts; AdminAuditPage/AdminMemoryPage не тронуты). Частичный фикс — класс «тест проходит, прод ломается»: приёмка на ≤500 событий зелёная.
- **Прод-проявление:** у владельца активного бота audit_log > 500 записей; админ листает, после 500-й кнопка «крутится» без эффекта.
- **Фикс (предложение):** перевести на `useInfiniteQuery` c offset — как сделано в `useChats` (hooks.ts:72-91), у `/api/admin/audit` есть `offset` (admin_stats.py:148) и `total`.

### P1-2. AdminMemoryPage: тот же паттерн, backend cap 200

- **Фронт:** `miniapp/src/admin/AdminMemoryPage.tsx:12` `useState(PAGE_SIZE)`, `:36` `hasMore = memories.length < total`, `:84` `setLimit((v) => v + PAGE_SIZE)`.
- **Бэк:** `app/api/routes/admin_memory.py:15` `_MAX_LIMIT = 200`, `:40` `limit = max(1, min(limit, _MAX_LIMIT))`.
- **Механика/проявление:** идентично P1-1, порог 200 записей памяти (у «админской» памяти всех пользователей это близкая величина). Записи 201+ недостижимы, кнопка вечная.
- **Фикс:** offset-пагинация (`useAdminMemories` уже принимает `offset`, hooks.ts:225-239).

### P1-3. Admin-disable модели не отражается в /api/models и в валидации PATCH — «мёртвый круг» с сообщением генерации

- **Бэк (владелец контракта — передать G):** `app/api/routes/me.py:81` `models = registry.filter_by_permissions(permissions.allowed_models)` — `ModelRegistry.filter_by_permissions` (app/llm/registry.py:43-51) фильтрует только по кодовому `ModelDefinition.enabled`; **DB-overrides (`model_overrides`, A28) не читаются**. `app/api/routes/chats.py:71-78` `_validate_model` и `app/api/routes/settings.py:67-72` — тоже только registry+permissions.
- **Фронт:** `AdminModelsPage.tsx:85` footer обещает «Отключённая модель недоступна пользователям»; toggle пишет override через `PUT /api/admin/models/{id}` (валидно: admin_models.py:23-44 effective = registry ∪ override). Но пользовательские селекты (ChatSettingsForm/SettingsPage/HomePage/ModelsModal) питаются `/api/models` — отключённая модель **по-прежнему в списке**, PATCH чата/настроек её принимает (200).
- **Замыкание круга:** при генерации отказ есть — `app/services/generation.py:509-513` `if not model_def.enabled or overrides.get(...) is False: "⛔ Модель … отключена администратором. Откройте Mini App и выберите другую модель."` — но Mini App показывает ту же отключённую модель, «выбрать другую» по подсказке нельзя понять, какую: все выглядят валидными.
- **Прод-проявление:** админ отключает модель → пользователь выбирает её в Mini App (успех), пишет боту → отказ с советом открыть Mini App → в Mini App модель всё ещё доступна. Состояние чата указывает на модель, которая гарантированно откажется.
- **Фикс-направление:** `me.py` — мерджить `ModelOverrideRepository.get_all()` (как admin_models.py:43) в `filter_by_permissions`; согласовать `_validate_model`/`patch_settings`. Frontend без бэкенд-изменения не лечится.

---

## P2

### P2-1. ModelsModal: PUT allowed_models не инвалидирует `userModels` — повторное открытие в течение 10s показывает старые чекбоксы

- `miniapp/src/admin/AdminUsersPage.tsx:250-260` — `onSuccess: () => { void qc.invalidateQueries({ queryKey: qk.adminUsersAll }); onClose(); }` — `qk.adminUsersAll = ["admin","users"]` (hooks.ts:32) **не накрывает** ключ `["admin","user-models", tgId]` (hooks.ts:33).
- `App.tsx:28` `staleTime: 10_000` (дефолт) → при переоткрытии модалки < 10s `useQuery` отдаёт кеш без refetch; `useState(null)` + `useEffect` (AdminUsersPage.tsx:239-248) инициализирует чекбоксы из **старых** `allowed_models`.
- **Прод:** админ сохраняет ограничения, тут же открывает заново — видит прошлые галочки («сохранение не сработало»), может «поправить» и пересохранить неверное.
- **Фикс:** в onSuccess добавить `qc.invalidateQueries({ queryKey: ["admin","user-models"] })` (или точечный `qk.userModels(user.telegram_user_id)`).

### P2-2. Ошибка догрузки страницы 2 у infinite-запросов стирает весь список со экрана (без retry)

- `miniapp/src/pages/ChatsPage.tsx:142-148`: `if (chatsQ.error) return <EmptyState …/>` — ранний return **до** рендера загруженных страниц; то же `MemoryPage.tsx:61-67`. Для infinite-query `error` ставится и при падении `fetchNextPage` — реакция: весь экран ошибки, кнопка «Загрузить ещё» пропала, загруженная страница 1 не показана, повтора нет (`retry: 1` — App.tsx:26). Возврат только перезагрузкой/перемонтированием.
- **Прод:** всплеск 5xx/сети при догрузке — пользователь теряет видимый список чатов/памяти целиком.
- **Фикс:** обрабатывать ошибку fetchNextPage отдельно (react-query v5 хранит `data` при ошибке), рендерить список + строку ошибки + retry (`fetchNextPage`/`refetch`).

### P2-3. AdminDashboardPage: тона статусов генераций не совпадают с реальным enum

- `miniapp/src/admin/AdminDashboardPage.tsx:107`: `status === "ok" || status === "success" ? "ok" : status === "error" ? "err" : "default"`.
- Реальные статусы GenerationRun: `queued` (db/models/generation_run.py:56 default), `running`/`completed`/`failed` (services/generation.py:1105, 1179, 1271), `aborted` (db/repositories/generation_runs.py:135). Строк `"ok"`/`"success"`/`"error"` в статусах ранов **нет** → «completed» никогда не зелёный, «failed» никогда не красный (все — default).
- **Прод:** дашборд «Генерации по статусам» без цветового различия failed/completed — админ пропускает всплески ошибок. Косметика, но прямой контракт-промах enum.
- **Фикс:** `tone={status === "completed" ? "ok" : ["failed","aborted"].includes(status) ? "err" : "default"}` (или healthTone-маппинг).

### P2-4. AdminSystemPage: Number("")→0 при очистке числовых полей (A13) и семантика null для default_system_prompt — «отображаемое ≠ эффективное»

- **Часть A (A13, memory_extraction_min_chars):** `AdminSystemPage.tsx:141-146` `Input type="number" min={1} … onChange={(e) => set("memory_extraction_min_chars", Number(e.target.value))}`. Очистка поля → `Number("") === 0` → PUT шлёт `0` → бэк честно отклоняет `admin_system.py:116-119` `400 "memory_extraction_min_chars must be positive"`. Защита есть, но UX: невалидное состояние не ловится на клиенте, ошибка — сырой англ. detail. На вопрос «null vs undefined»: фронт **всегда** шлёт все 8 полей объектом `form` (save.mutate(form), :153), `memory_extraction_min_chars: number` (types.ts:216) — null/undefined для int-полей не шлётся вовсе; семантика `exclude_unset` «не трогать» не нужна (полная форма). Имя поля совпадает с `_KEY_MEMORY_EXTRACTION_MIN_CHARS` (admin_system.py:23). Контракт OK, кроме papercut 0/валидации.
- **Часть B (default_system_prompt):** очистка Textarea → `set("default_system_prompt", null)` (:93) → PUT хранит **null** (`admin_system.py:124-125` `set_value(key, None)`) → GET возвращает null (`:63-65` `stored.get(key, fallback)` — ключ есть, значение None) → UI «Не задан», но эффективные настройки молча падают в env-дефолт с warning (`app/services/settings.py:111-113` `_non_empty_str(None) → _INVALID` → env default, :129-141). Админ видит «промпта нет», система генерирует с env-промптом. То же для `default_model: null` (эффективный — env default_model при UI «Не задана»).
- **Прод:** владелец очищает системный промпт, рассчитывая «без промпта» — все генерации идут со старым env-промптом; рассинхрон UI↔поведения без сообщения пользователю.
- **Фикс-направление:** backend — различать «ключ не заведён» и «null» (например, DELETE ключа при null для optional-полей) или фронту не давать сохранять null без подтверждения; на стороне miniapp — client-side валидация чисел (`Number.isFinite && > 0`) до mutate.

### P2-5. Thinking-селектор недоступен при chat.model_id=null и user default=null (системная модель не входит в эффективную цепочку фронта) — carried over, не исправлено

- `miniapp/src/components/ChatSettingsForm.tsx:23` `const effectiveModelId = chat.model_id ?? settings?.default_model_id ?? null;` → `thinkingModes = []` → `:56` `disabled={busy || thinkingModes.length === 0}`.
- Бэк считает системный дефолт: `chats.py:163-167` (`base_model_id … or settings.default_model`), `settings.py:75-77`, generation.py resolve — т.е. сервер принял бы thinking для системной модели. Фронт цепочку не достраивает (нужен системный default из `/api/admin/system` — недоступен обычному пользователю).
- Round-3 уже фиксировал как A25 проблему 1; `0be0524`/`4925c40` не трогали — **дефект остаётся открытым**.
- **Прод:** пользователь без per-user дефолта модели не может задать per-chat thinking (селектор задизейблен), хотя сервер поддержал бы.

### P2-6. Архивация текущего чата: «Нет активного чата» на HomePage при живом current; /open разрешает archived-чат

- `chats.py:129-138` `open_chat` — нет проверки `archived_at`; `set_archived` (db/repositories/chats.py:75-82) не чистит `current_chat_id` (services/chats.py:45-51).
- Фронт: HomePage.tsx:42-45 ищет `is_current` только в **активном** списке (`useChats()` — include_archived=false) → после архивации текущего чата пустое состояние «Нет активного чата» (:47-59); в ChatsPage архивная строка показывает «●» (ChatsPage.tsx:36 `chat.is_current`) при некликабельном ряду (:31-34). Генерация тем временем молча перенаправит сообщения в «последний активный» чат или создаст новый (`get_or_create_current_chat`, services/chats.py:53-64) — не в тот, что видит пользователь.
- Round-3 отмечал как A24-заметку 3 — остаётся открытым. Кросс-стек; минимум на фронте — показывать честное состояние («текущий чат в архиве, выберите другой»).
- **Прод:** заархивировал текущий чат → пишет боту → ответ падает в другой старый чат (тихо сменившийся current), в Mini App — «Нет активного чата».

---

## P3 (мелкие/граничные)

1. **AuditEntry типы**: `types.ts:253-256` `target_type: string; target_id: string` — бэк nullable (`db/models/audit_log.py:22-23`, `services/admin.py:56-57` defaults None; admin_system.py:136-137 пишет audit с `target_type="system_settings"` без target_id → null в ответе). Рантайм-безопасно (`{e.target_id ? … : ""}`), но тип неточен. Правка: `string | null`.
2. **Пустой title = "" вместо null**: ChatsPage rename (:280) и ChatSettingsPage (:119) шлют `title: title.trim()` — пустая строка сохраняется (ChatPatchRequest без min_length), в списках `chat.title ?? "Без названия"` рендерит пусто ("" не nullish). Отправлять null при пустом.
3. **GrantModal «Бессрочно» в extend-режиме**: Toggle показывается для обоих режимов (AdminUsersPage.tsx:126-129 вне `mode === "grant"`-блока), но submit в extend при permanent всегда падает с «Укажите новую дату окончания» (:90-96). Скрыть Toggle в extend.
4. **MemoryPage: пустой text/category сохраняются** — нет клиентской валидации (:166-178), бэк `MemoryPatchRequest` без min_length (memory.py:19-24) → записи с пустым текстом/категорией. Валидировать до mutate.
5. **Offset-пагинация vs конкурентные изменения порядка**: invalidate рефетчит все страницы параллельно; bump `updated_at` чата ботом между страничными ответами (или перенос памяти по importance при edit) может дать дубль строки/дубль React-ключа (`key={c.id}`). Уточнение гонки, вероятность низкая.
6. **Select со значением вне опций**: если `chat.thinking_setting`/`default_thinking` ссылается на режим, убранный probe-фильтром `/api/models` (me.py:46-53), или `model_id` вне разрешённых — Select рендерится пустым, реальное значение скрыто от пользователя (ChatSettingsForm.tsx:55, SettingsPage.tsx:65). Показывать «текущее: X (недоступно)».
7. **AdminMemoriesResponse**: бэк отдаёт `user_id`/`telegram_user_id` в элементах (admin_memory.py:18-28) — в `MemoryItem` их нет (лишние поля безвредны). `AdminModel.probe_at/probe_fresh` (types.ts:280-281) бэк сейчас не шлёт — dead-optional, задокументировано wave3 (рендер условный, AdminModelsPage.tsx:34-39 — при отсутствии просто скрыт, при пустых thinking_modes — «—» :29). Крашей нет.
8. **staleTime/probe**: `/api/models` кешируется 60s (hooks.ts:55) — probe-обновления (script `smoke_providers.py --write-runtime`) доходят до UI с задержкой ≤60s/до перемонтирования; out-of-band события инвалидации нет. Приемлемо, зафиксировать как известное.
9. **HomePage/ChatSettingsPage не гейтят `settingsQ.error`** (HomePage.tsx:26-40, ChatSettingsPage.tsx:68-74): при падении `/api/settings` silently подставляются hardcoded «Авто»/«Вкл» в inherit-лейблах (ChatSettingsForm.tsx:35-36).
10. **Числовой поиск admin users**: query.isdigit → точный id ИЛИ username ilike (users.py:68-82); частичный id («795») не ищет по префиксу id — только username. UX-нюанс, задокументирован в placeholder неточно.
11. **ChatSettingsPage id→id**: переход /chats/:a → /chats/:b не перемонтирует компонент (Router reuse), `promptLoaded` остаётся true → поля от чата A при URL чата B. В текущем UI пути id→id нет (переходы только из списка/домашней), латентно.

## OK — проверено и чисто

- **Контракт types.ts ↔ routes, поле-в-поле** (все сверены по исходникам): TelegramUser/AuthResponse (auth.py:82-95), Permissions/MeResponse (me.py:21-40), ModelInfo+probe_at (me.py:57-65), Chat/ChatsResponse/ChatPatch (chats.py:47-60, 24-38), UserSettings/SettingsPatch (settings.py:20-35), MemoryItem/MemoriesResponse/MemoryPatch (memory.py:19-35), AccessGrant/AdminUser/AdminUsersResponse (admin_users.py:26-54), GrantBody↔AccessGrantRequest (admin_access.py:30-44), GeminiProject/Quota/Usage (admin_gemini.py:68-84; gemini.py:310-360 — ключи minute_ts/day совпадают), AlibabaStatus (admin_providers.py:52-57), SearchBackend (admin_search.py:31-39), SystemSettings все 8 ключей (admin_system.py:59-83), RecentFailedRun/AdminStats V2-extras (admin_stats.py:102-140 — requests_by_model_today/errors_today/rate_limit_429_today/gemini_usage_today/avg_ttft_s/error_rate_today/recent_failed_runs; условный рендер `!== undefined` корректен, AdminDashboardPage.tsx:66-91), ProviderTestResult, AdminModelsResponse. Единственные отклонения — P3-1/P3-7 (nullability/extra-поля).
- **useChats/useMemories offset-пагинация**: `getNextPageParam` по `loaded < total` (hooks.ts:85-88, 120-123) — off-by-one нет (полная последняя страница → undefined; total=21 → страницы 20+1, стоп), пустые страницы не зацикливают (авто-fetch нет, только клик), NB о cap 200 учтён дизайном (offset, не limit-рост). Дублей ключей страниц нет (offset = фактически загруженное).
- **useAdminUsers эвристика «полная страница»** (hooks.ts:153-157): корректна; ровно N×50 пользователей → один лишний запрос с пустой страницей → кнопка скрывается (задокументировано в комментарии); backend-срез `[offset:]` от `search(limit=limit+offset)` (admin_users.py:66-69) даёт консистентные страницы.
- **A27 в AdminModelsPage**: `probe_at` отсутствует → строка «проверено: …» не рендерится (AdminModelsPage.tsx:34-39); пустые thinking_modes → «—» (:29); `probe_fresh === false` → «(устарело)». На /api/models probe_at всегда присутствует (null безопасен). Крашей нет; admin thinking_modes — полный registry-список (не probe-фильтрованный) — асимметрия с /api/models by design (админ = raw capabilities).
- **401 → re-auth**: ровно один повтор (`allowRetry`), дедуп параллельного auth через inFlight/reauthPromise (client.ts:101-125, auth.tsx:62-69); 401 = запрос не исполнен → повтор POST не создаёт дубль; старта запросов до готовности auth нет (AppShell spinner при status=loading). Двойных запросов не найдено.
- **A13**: имя поля/тип согласованы (см. P2-4A — контракт OK, null-семантика не задействована).
- **ChatSettingsPage промпт**: `"" → null → inherit` (:154), `promptLoaded`-guard не затирает ввод при refetch (:33-39). PATCH `model_id+thinking_setting` одним body при смене модели — null-сброс несохраняемого режима (ChatSettingsForm.tsx:27-33) согласован с exclude_unset бэка.
- **UNSET vs null в grant**: комментарий фронта (AdminUsersPage.tsx:98) = бэк-семантика (admin_access.py:30-35, admin.py UNSET-сентинел); чекбоксы «Без лимита (снять)» шлют явный null. Различие `[]` (запрет всех) vs `null` (unrestricted) в ModelsModal сохранено (:254).
- **BackButton**: только `/chats/:id` (App.tsx:37, UUID-safe regex), fallback `navigate(-1)`→`/chats` при `key === "default"`; cleanup offClick/hide корректен (webapp.ts:48-64).
- **Синхронизация current chat**: инвалидация `["chats"]` накрывает и list-, и detail-ключи (hooks.ts:75, 96); удаление текущего чата самовосстанавливается бэком (get_or_create_current_chat). Остаточная асимметрия — P2-6.
- **Модели из API-registry**: в miniapp нет захардкоженных списков моделей (все селекты из `/api/models`/`/api/admin/models`) — инвариант «Pro из registry, не отдельный список» соблюдён; model IDs существующих не тронуты; Alibaba base_url в UI read-only (AdminProvidersPage.tsx:70-73).
- **Прогоны**: typecheck exit 0, build exit 0 (см. таблицу).

## Сводка для владельца (координация G — backend)

| # | Что нужно от бэка | Front-файл | Backend-файл |
|---|---|---|---|
| P1-3 | `/api/models` + `_validate_model` мердж `model_overrides` | AdminModelsPage.tsx:85 | me.py:81, chats.py:71-78, settings.py:67-72 |
| P2-4B | семантика null в `system_settings` (DELETE vs null-значение) | AdminSystemPage.tsx:93 | admin_system.py:63-65,124-125; services/settings.py:111-113 |
| P2-5 | экспозить системный default model/thinking в user-API (или effective-chain) | ChatSettingsForm.tsx:23 | chats.py:163-167 |
| P2-6 | `open_chat` отклонять archived (или чистить current при архивации) | HomePage.tsx:45 | chats.py:129-138, services/chats.py:53-64 |
