# Final review — POLYRA Mini App (React+TS+Vite)

**Аудитор:** polyra-miniapp (subagent B). **Дата:** 2026-09-25. **Режим:** READ-ONLY.
**Рабочий checkout:** `O:\work\aibot`, HEAD `e4ac383`, `git status` чист (кроме каталога отчётов аудита).
**Scope:** `miniapp/**` + контракт-соответствие API (frontend ↔ backend routes).

## Выполненные проверки окружения

| Проверка | Команда | Результат |
|---|---|---|
| `node_modules` | наличие | EXISTS (npm ci уже сделан) |
| TypeScript strict | `npm run typecheck` (tsc --noEmit) | **PASS, exit 0** |
| Production build | не запускал (read-only: rebuild перезаписал бы `dist/`) | `dist/` существует, собран 2026-09-25 02:33 (index.html + assets/index-ehHhysVE.js 282 КБ, base "./") |
| Frontend тесты | grep/glob | **ОТСУТСТВУЮТ**: нет `test`-скрипта, нет vitest/jest/playwright в package.json; каталога `miniapp/e2e/` нет |
| git | `git status --porcelain` | чисто (только `?? .agents/reports/final-review/`) |

package.json (miniapp/package.json:6-11): scripts = dev/build/typecheck/preview. Зависимости: react 18.3, react-router-dom 6.30, @tanstack/react-query 5.62. **Ни одного тестового фреймворка.** FIX_REPORT_V2.md:101-102 честно признаёт «Frontend E2E … не поднимал», но таблица (стр. 74) помечает A36 как fixed — по miniapp-части это не так: 0 automated frontend тестов.

---

## A01 — UUID/string на всём пути API→hooks→components

**Статус: FIXED_VERIFIED**

Доказательства (текущий код):

- `miniapp/src/api/types.ts:50-62` — `Chat { id: string; … system_prompt_override: string | null; archived_at: string | null; is_current: boolean }`; `MemoryItem.id: string` (:97); `GeminiProject.id: string` (:157); `AuditEntry.id: string` (:230); `AdminUser.id: string` (:129).
- Grep `Number\(` по `miniapp/src` — 27 совпадений, все на числовых полях, не на id: `formatNumber` (utils.ts:32), `importance: Number(editImportance)` (MemoryPage.tsx:177), `priority: Number(priority)` (AdminSearchPage.tsx:80), `Number(grantById.trim())` → telegram_user_id (AdminUsersPage.tsx:434 — числовой по контракту), `Number(e.target.value)` системных лимитов (AdminSystemPage.tsx:105-134). `parseInt` отсутствует.
- BackButton-условие: `App.tsx:37` — `const isDetail = /^\/chats\/[^/]+/.test(location.pathname);` — regex «любой не-slash сегмент», UUID-safe. Плюс fallback `navigate(-1)`/`navigate("/chats")` (App.tsx:40-43).
- `ChatSettingsPage.tsx:19-26` — `useParams<{id: string}>` → `useChat(id)` → `api<ChatResponse>(\`/api/chats/${id}\`)` (hooks.ts:79-85). Все пути (`/open`, PATCH, `/archive`, DELETE) — строковые UUID (ChatsPage.tsx:97,116-131).
- Backend: `app/api/routes/chats.py:49` `"id": str(chat.id)`; `memory.py:29`, `admin_stats.py:147` (`str(entry.id)`), `admin_users.py:44` — id сериализуются строками. Контракт «UUID как string» согласован на всём пути.
- `tsc --noEmit` — exit 0 (строгий режим, noUnusedLocals/Parameters).

Замечания (не блокирующие):
- Клиентские типы не генерируются из OpenAPI (в аудите это «по возможности»). Ручная синхронизация сейчас точная, но хрупкая к будущим правкам бэка.
- Приёмка «E2E: … ни один запрос не содержит NaN» не проверена runtime-тестом — e2e отсутствуют; статически NaN-невозможна (нет числового преобразования id).

## A24 — Архив + длинные списки + пагинация памяти

**Статус: FIXED_PARTIAL** (функционально для ≤200 записей; паттерн «тест проходит, прод ломается» при >200)

Сделано:
- Backend: `GET /api/chats` с `limit`/`offset`/`include_archived` + `total` (chats.py:81-101; repo `list_for_user`/`count_for_user` — chats.py репо:29-53, фильтр `archived_at.is_(None)`); `GET /api/chats/{chat_id}` отдельным роутом (chats.py:104-112) — фронт использует через `useChat`.
- Frontend: архивная вкладка лениво грузится `useChats({limit, include_archived: true, enabled: archiveOpen})` (ChatsPage.tsx:87), клиентская фильтрация `c.archived_at !== null` (:158-159); восстановление — `POST /api/chats/{id}/archive` с `{archived:false}` toggle (:124-128, :176) и отдельная кнопка в ChatSettingsPage (:164-175). «Загрузить ещё» для активных (:193-204) и архива (:226-237). Пагинация памяти: MemoryPage.tsx:131-142 «Загрузить ещё» + `useMemories({limit})` + `total` (hooks.ts:96-103; memory.py:38-51).

Проблемы:
1. **Пагинация реализована ростом `limit` (offset всегда 0), а backend зажимает limit ≤ 200** (`chats.py:21` `_MAX_LIMIT=200`, `memory.py:16` `_MAX_LIMIT=200`, `admin_memory.py:15` `_MAX_LIMIT=200`, `admin_stats.py:16` `_MAX_AUDIT_LIMIT=500`). При >200 записей: `hasMore = length < total` остаётся true, клик «Загрузить ещё» увеличивает limit до >200, сервер возвращает те же 200 строк → записи 201+ **недостижимы никогда**, кнопка «вечная». Приёмка «60 чатов» проходит; прод с большим объёмом — нет. Затрагивает: ChatsPage, MemoryPage, AdminMemoryPage (200), AdminAuditPage (500). Рекомендация: offset-based пагинация (параметр `offset` в хуках уже поддержан) или cursor.
2. Архивная вкладка запрашивает `include_archived=true`, т.е. ВСЕ чаты (активные+архивные) одним списком и фильтрует на клиенте; `total` — общий. Работает, но нет отдельного `archived=true`-фильтра, и комбинированный лимит 200 делит квоту между вкладками.
3. Удаление/архив текущего чата: UI инвалидирует кеш; в активном списке `is_current`-чата больше нет → HomePage показывает «Нет активного чата». Разумно, но явного поведения «current при archive» нет и на бэке (open/archive не очищают current) — лишь визуально согласовано.
4. E2E «создать 60 чатов, архивировать, восстановить самый старый» — тестов нет (TEST_ONLY недостижим: тестов просто нет).

## A25 — raw overrides / effective settings / model+thinking PATCH

**Статус: FIXED_PARTIAL**

Сделано:
- `system_prompt_override` возвращается API: `chats.py:55` в `_chat_out`; тип `Chat.system_prompt_override` (types.ts:57).
- Prompt не сбрасывается: ChatSettingsPage.tsx:33-39 — `setPrompt(chat.system_prompt_override ?? "")` один раз (`promptLoaded`-guard); после мутации refetch не затирает ввод; пустое → `null` (inherit) при сохранении (:154). Read/edit/reload без потерь ✓.
- Одновременный PATCH `model=null + thinking`: ChatSettingsForm.tsx:27-33 — `onPatch({ model_id: null, thinking_setting: null })` в одном body (spread); backend `chats.py:154-169` обрабатывает оба ключа через `exclude_unset`, при явном null модели берёт user default → system default для валидации thinking.
- Runtime: неизвестная/отключённая сохранённая модель — явный отказ, не fallback: `generation.py:955-965` «⛔ Сохранённая модель «…» больше недоступна…» + `_model_denial` (:459-486) для disabled/permissions. (В `resolve_model_and_thinking` :199-211 docstring всё ещё описывает silent-fallback, но ветка недостижима — вызовguarded выше.)

Проблемы:
1. **«Наследуемая системная модель отражается как effective model для thinking selector» — выполнено только до user-уровня.** ChatSettingsForm.tsx:23: `effectiveModelId = chat.model_id ?? settings?.default_model_id ?? null` — глобальный (admin System) default в эффективную цепочку фронта не входит (бэкенд включает: chats.py:167, settings.py:76, generation.py:202). Если chat override и user default оба null: `thinkingModes=[]` → **thinking-селектор disabled**, хотя backend разрешил бы thinking для системной модели; пользователь не может задать per-chat thinking. Также метка «По умолчанию (не задана)» скрывает реальную системную модель.
2. «Raw overrides и effective settings раздельно»: raw-override чат возвращает отдельно ✓, но отдельного effective-settings API нет — effective вычисляется на клиенте из `/api/settings` (user-уровень) c пробелом из п.1.
3. Тест «исправить тест, ожидающий unknown-model fallback» — backend-фича; на фронте тестов нет вовсе.

## A26 — очистка числовых лимитов; обещание остановки генераций

**Статус: FIXED_VERIFIED** (frontend + контракт)

- GrantModal: чекбоксы «Без лимита (снять)» → явный `body.requests_per_day = null` / `token_limit = null` / `max_concurrent_generations = null` (AdminUsersPage.tsx:63-65, 105-110, 153-196); комментарий фиксирует семантику «Backend (exclude_unset): поле отсутствует → не менять; явный null → снять лимит» (:98). Пустое поле без чекбокса → ключ отсутствует → «без изменений» ✓.
- Backend: `AccessGrantRequest` → `model_dump(exclude_unset=True)` (admin_access.py:66) → `grant_access` с UNSET-сентинелом: «UNSET (не передано) — колонку не трогаем; explicit None — записать NULL» (app/services/admin.py:46-48, 98-103, 116). Read-back `null` = unlimited ✓ (AdminUsersPage.tsx:353-354 рендерит «∞»).
- Обещание «Генерации будут остановлены» (ACTION_TEXT.suspend.message, AdminUsersPage.tsx:24) — реально выполняется: suspend/revoke/ban → `_stop_user_generations` → `registry.stop_all_for_user(user.id)` (admin_access.py:14-24, 108, 124, 140; GenerationRegistry.stop_all_for_user — generation.py:112). API возвращает `stopped_generations` (count).
- Замечания (мелкие): UI не показывает число остановленных генераций из ответа; extend-режим меняет только `expires_at` (by design).

## A28 — полная админ-панель

**Статус: FIXED_PARTIAL**

Разделы (AdminLayout.tsx:4-14): Дашборд, Пользователи, Модели, Gemini, Провайдеры, Память, Поиск, Система, Аудит. Все admin-роутеры закрыты `Depends(require_owner)` (проверено в каждом admin_*.py).

Реально работающие вертикали (UI → API → эффект → audit):
- **Models** (AdminModelsPage): список из `/api/admin/models` = `registry.list_all()` + DB-overrides (admin_models.py:39-44) — Pro и все модели из registry, не из отдельного списка ✓; enable/disable → `PUT /api/admin/models/{id}` → `model_overrides` + audit MODEL_ENABLED_CHANGED (admin.py:279-295); capabilities показаны (thinking, max_context/max_output, internal, text-only chip :39-40). **Нет CRUD** (создать/удалить модель из UI нельзя — registry кодовый; частично соответствует «Pro появляется из API registry»).
- **Alibaba** (AdminProvidersPage): masked key (`key_hint`) :64, status, read-only base_url (:70-73, Input readOnly), **Run Smoke Test — реальный живой вызов** qwen3.8-flash ≤16 output tokens (admin_providers.py:84-106, 109-138) с audit PROVIDER_SMOKE_TEST; disabled credential → 0 HTTP-вызовов (:123-124). Ключ сохраняется Fernet + audit (:60-81). **Model health (по моделям) — ОТСУТСТВУЕТ** (ТЗ §37 «model health» для Alibaba; в A28 прямо значилось). Единственный health — провайдерный smoke.
- **Gemini Pool** (AdminGeminiPage): masked key_hint (:175), health_status chip + cooldown + rotation_order + last_error (:170-191); Add (:29-37) / Bulk Import (:84-88) / Enable-Disable (:146-151) / Move Up-Down (:153-160) / **Test project — реальный smoke gemini-3.5-flash-lite** (admin_gemini.py:334-356, audit GEMINI_PROJECT_TESTED) / Delete / **Reset Local Counters** (:292-300 → admin_gemini.py:359-372, удаление quota_minute_usage+quota_daily_usage, audit) / **minute/day счётчики** — GET /usage, реальные окна (admin_gemini.py:375-381; repo gemini.py:310-360; имена полей совпадают с types.ts:270-289) / per-model RPM/TPM/RPD редактирование (:232-244 → PUT /admin/gemini/quotas, валидация model_id, audit). Полный набор кнопок из ТЗ §37 присутствует и делает реальные вызовы.
- **Web Search** (AdminSearchPage): 5 бэкендов (manager.py:41-47: serper, brave, serpapi_aio, jina_search, playwright_google), Enabled/Priority (PUT → audit SEARCH_BACKEND_CHANGED), Masked key (Fernet, key_hint), Health (health_status/last_error), Test — реальный health_check через search_manager (admin_search.py:114-126). Отклонение: «Test query» — это минимальный health-запрос, произвольный поисковый запрос админ задать не может.
- **Admin Memory** (AdminMemoryPage): read-only, фильтр по telegram_user_id, пагинация (рост limit, кап 200) — backend admin_memory.py:31-64 ✓. Действий над записями нет (ТЗ их и не требовал явно).
- **Audit Log** (AdminAuditPage): фильтр по action + load-more; backend `GET /admin/audit` с limit/offset/action + total (admin_stats.py:131-158) — A35-часть закрыта.
- **Dashboard** (AdminDashboardPage): базовые totals + V2 (errors_today, rate_limit_429_today, requests_by_model_today, gemini_usage_today — admin_stats.py:19-128) — рендер условный (:61-72, типы optional types.ts:223-228 — backcompat).

Отсутствует:
- **Per-model health** (см. Alibaba) — нигде в UI.
- **Диагностика generation errors** — только агрегатные счётчики ошибок/429 на дашборде; страницы со списком упавших ранов, категориями ошибок, latency/TTFT нет (A28/A35-остаток).
- Users-список без пагинации (limit=50 жёстко, hooks.ts:114; backend поддерживает offset) — мелочь.
- Матрица приёмки «UI → API → DB → audit → reload» не покрыта e2e-тестами (тестов нет).

## A40 (UI-часть) — Modal/Toggle accessibility, Telegram ergonomics

**Статус: FIXED_PARTIAL**

Сделано:
- Modal.tsx:14-21 — Escape закрывает; :26 backdrop-click закрывает; :30 — `aria-label="Закрыть"` у кнопки ✕.
- Toggle.tsx:9-15 — нативный `<input type="checkbox">` внутри `<label>` → оперируем с клавиатуры, фокус виден.
- List.tsx:31-37 — кликабельный ряд рендерится `<button type="button">` (клавиатурная доступность).
- Telegram mobile ergonomics: bottom-sheet модалка (Modal.css max-height 85% viewport, styles.css:576), safe-area insets (styles.css:18-19), `--tg-viewport-stable-height` (:77,98,576), haptic + showConfirm с fallback (webapp.ts:66-88), viewport-fit=cover (index.html:8).

Не сделано:
- **Нет focus trap / начального фокуса / возврата фокуса** при закрытии; нет `role="dialog"`/`aria-modal`; Tab уводит фокус в фон под модалкой.
- **Toggle без доступного имени**: текст («Бессрочно», «Веб-поиск»…) рендерится соседним `<span>`, не связан с чекбоксом (AdminUsersPage.tsx:126-129, 198-204; GrantModal/ProjectCard аналогично); `form-label` не связаны `htmlFor` ни с одним Input/Select/Textarea.
- Нет body scroll-lock при открытой модалке.
- Accessibility smoke tests отсутствуют (в FIX_REPORT_V2 A40 честно помечен optional-deferred: «accessibility-тесты UI» отложены; P3).

## N04 (UI-часть) — DeepSeek V4 Pro в Mini App

**Статус: FIXED_VERIFIED** (UI-скоуп; live-заявления FIX_REPORT здесь не перепроверялись)

- **Выбор модели**: в miniapp НЕТ захардкоженного списка моделей — все селекты питаются `GET /api/models` ← `registry.filter_by_permissions` (me.py:41-64). `deepseek-v4-pro` определён в registry (capabilities.py:128-139: display_name «DeepSeek V4 Pro», `input_modalities={"text"}`, max_output 393 216, thinking `(OFF, HIGH, MAX)` — LOW не выдуман, комментарий :136-137) → Pro автоматически появляется в выборе модели ChatSettingsForm/SettingsPage/HomePage и в admin-списках. Registry-источник, не отдельный список ✓.
- **Отдельная admin-галочка**: ModelsModal (AdminUsersPage.tsx:230-317) — по-model чекбокс из `/api/models`, PUT `/api/admin/users/{id}/models` с `allowed_models: allowAll ? null : Array.from(selected)` — Pro индивидуально разрешается/запрещается; различие `[]` (запрет всех) vs `null` (unrestricted) сохранено (admin.py:239-276, mode all/list).
- **Не default**: `config.py:26` `default_model: str = "gemini-3.8-flash"` — Pro не назначен дефолтом; AdminSystemPage дефолт выбирается вручную.
- **Thinking для Pro**: режимы из registry (OFF/HIGH/MAX; LOW отсутствует) — соответствует N04 «не добавлять LOW». `thinkingLabel` (utils.ts:45-54) поддерживает high/max/off.
- **Text-only guard подсказка**: в админке — chip «text-only» (AdminModelsPage.tsx:40). В пользовательском UI ChatSettingsForm подсказки о text-only нет (тип `ModelInfo.supports_images` есть, но не используется); загрузки фото в Mini App нет вовсе, фактический guard — серверный: `generation.py:475-485` «⛔ Модель … не принимает изображения. Модели с поддержкой изображений: …» с перечнем альтернатив (контракт-проверено). Частичный пробел — только UX-подсказка при выборе модели.

## Исходное ТЗ — разделы 34–37 (miniapp)

- **§34 (React+TS+Vite, Telegram theme, mobile-first):** ✓. Vite 5 + React 18 + TS strict; `styles.css:5-30` — CSS-переменные из `--tg-theme-*` (light/dark через `.tg-dark`/`.tg-light`, webapp.ts:15-20 переключает классы на themeChanged); safe-area/viewport-stable-height; index.html подключает telegram-web-app.js; main.tsx:8 — early `initTelegramWebApp()` (ready/expand/theme/setHeaderColor). Mobile-first: tabbar, bottom-sheet; на desktop модалка центрируется (Modal.tsx:12 комментарий).
- **§35 (initData raw → backend, session token):** ✓. auth.tsx:26-39 — `POST /api/auth/telegram` с телом `{ init_data: <raw initData string> }`; backend auth.py:17-38 валидирует подпись bot_token, выдаёт `session_token`; client.ts:3-33 — Bearer в память+sessionStorage, 401 → ровно один re-auth + один retry (client.ts:115-125), дедупликация параллельных auth (auth.tsx:62-69).
- **§36 (USER UI):** ✓. Главная (HomePage: текущий чат + быстрые настройки), Чаты (list/rename/archive/delete/switch — все мутации реальные), Модель только разрешённые (me.py фильтрует по permissions; internal скрыты), Thinking зависит от capabilities (selector disabled при пустых thinking_modes; probe_required-опции скрыты сервером, me.py:57-59), Интернет Off/Auto/On, Память On/Off (user-level Toggle + per-chat tri-state), Настройки (+ отображение effective permissions). Проверено типами и tsc.
- **§37 (ADMIN UI):** частично ✓ — см. A28. Отсутствует model health для Alibaba, полноценная диагностика ошибок генерации, Stats-разрезы latency/TTFT; Web Search «Test query» — health-запрос без произвольного запроса; System не экспонирует всю memory config (есть memory_retrieval_limit; memory_extraction_min_chars — нет).

## Сводная таблица

| Пункт | Статус | Ключевые файлы | Главное |
|---|---|---|---|
| A01 | FIXED_VERIFIED | types.ts, App.tsx:37, hooks.ts, ChatsPage/ChatSettingsPage | id:string везде; Number(id) нет; UUID-safe BackButton; tsc PASS |
| A24 | FIXED_PARTIAL | ChatsPage.tsx, MemoryPage.tsx, hooks.ts:59-103, chats.py:81-101 | архив/pagination работают, НО limit-рост × backend cap 200 → записи 201+ недостижимы; e2e нет |
| A25 | FIXED_PARTIAL | ChatSettingsPage.tsx:33-39,154, ChatSettingsForm.tsx:23-33, chats.py:154-177 | prompt сохраняется/грузится; PATCH model=null+thinking ✓; но effective chain не включает системный default → thinking selector disabled при null-null; отдельного effective-API нет |
| A26 | FIXED_VERIFIED | AdminUsersPage.tsx:63-110,153-196, admin.py:46-131, admin_access.py:14-24 | UNSET vs null реализован; suspend/revoke/ban реально останавливают генерации |
| A28 | FIXED_PARTIAL | все Admin*Page, admin_*.py | Models enable/capabilities ✓, smoke/test/reset/usage ✓ реальные; НЕТ per-model health, generation-error диагностики, Models CRUD; users-лист без пагинации |
| A40 (UI) | FIXED_PARTIAL | Modal.tsx, Toggle.tsx, List.tsx | Escape/backdrop/aria-label ✓, нативный чекбокс ✓; нет focus trap/aria-modal, Toggle без accessible name, form-label без htmlFor; a11y-тесты отложены (P3, задокументировано) |
| N04 (UI) | FIXED_VERIFIED | capabilities.py:128-139, me.py:41-64, AdminModelsPage.tsx:40, AdminUsersPage ModelsModal | Pro из registry во всех селектах; отдельная галочка; не default (gemini-3.8-flash); OFF/HIGH/MAX без LOW; text-only guard — серверный, UI-подсказки в user-UI нет (мелочь) |
| §34/§35/§36 | FIXED_VERIFIED | styles.css, webapp.ts, auth.tsx, client.ts, все pages | тема/initData/разделы соответствуют ТЗ |
| §37 | FIXED_PARTIAL | admin/* | см. A28; модель health и диагностика ошибок — главные пробелы |
| Тесты фронта | NOT_FIXED | package.json | нет ни unit/component, ни e2e; только typecheck+build; FIX_REPORT A36 «fixed» по miniapp-части завышен (сам отчёт в «ограничениях» это частично признаёт) |

## Паттерн «фикс проходит тест, но ломается в проде» — находки

1. **Пагинация limit-ростом** (A24): приёмка «60 чатов» зелёная, но _MAX_LIMIT=200 делает записи 201+ вечнодостижимой кнопкой «Загрузить ещё» (Chats/Memory/AdminMemory/AdminAudit). Нужен offset/cursor.
2. **Thinking selector при наследовании системной модели** (A25): в тест-сценарии «user default задан» всё работает; при null/null селектор отключён, хотя серверная effective-модель thinking поддерживает.
3. **A36/A28 claims vs reality**: «npm build PASS» и 488 pytest — верно для бэкенда, но frontend-тестов 0; admin-матрица приёмки (UI→API→DB→audit) не автоматизирована.

## Блокеры и ограничения аудита

- Не запускал `npm run build` (переписал бы `dist/` — конфликт с read-only); свежий dist присутствует, typecheck репрезентативен.
- Live-поведение (Telegram WebView, реальные API-вызовы smoke) не проверял — вне прав read-only аудита; соответствие backend-контрактам проверено чтением роутов.
- `dist/` gitignored — соответствие билда текущему src не гарантируется коммитом, но время сборки (25.09 02:33) позже всех правок miniapp в истории.
