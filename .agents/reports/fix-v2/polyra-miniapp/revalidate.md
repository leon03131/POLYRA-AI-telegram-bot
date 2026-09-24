# polyra-miniapp — ревалидация исторических находок аудита

- Дата: 2026-09-24
- Режим: read-only + отчёт (production код не менялся)
- Checkout: O:\work\aibot (актуальный, не ZIP)

---

## A01 — UUID→number на фронте

**Verdict: confirmed-broken** (критично для ChatSettingsPage; остальное — ложные типы, runtime уцелел)

Backend отдаёт все entity id как строки UUID, фронт типизирует их как `number`:

| Сущность | Backend (str UUID) | Frontend тип |
|---|---|---|
| Chat | app/api/routes/chats.py:48 `"id": str(chat.id)` | miniapp/src/api/types.ts:51 `id: number` |
| MemoryItem | app/api/routes/memory.py:27 `"id": str(memory.id)` | miniapp/src/api/types.ts:92 `id: number` |
| GeminiProject | app/api/routes/admin_gemini.py:52,104 `"id": str(project.id)` | miniapp/src/api/types.ts:151 `id: number` |
| AuditEntry | app/api/routes/admin_stats.py:91 `"id": str(entry.id)` | miniapp/src/api/types.ts:219 `id: number` |
| AdminUser | app/api/routes/admin_users.py:44 `"id": str(user.id)` | miniapp/src/api/types.ts:123 `id: string` — **OK, единственный правильный** |

`Number(...id...)` вызовы:
- miniapp/src/pages/ChatSettingsPage.tsx:26 — `const chatId = Number(id);` → для UUID-строки даёт `NaN`.
- miniapp/src/pages/ChatSettingsPage.tsx:27 — `.find((c) => c.id === chatId)` → `string === NaN` всегда false → страница настроек чата **всегда** падает в «Чат не найден» (строки 76–86). Дальнейшие мутации ушли бы на `/api/chats/NaN` (строки 45, 51, 59).
- miniapp/src/pages/MemoryPage.tsx:160 — `Number(editImportance)` — легитимно (select-значение, не id).
- miniapp/src/admin/AdminUsersPage.tsx:390 — `Number(grantById.trim())` — легитимно (telegram_user_id числовой).

Сравнения id: единственное `===` по id — ChatSettingsPage.tsx:27 (сломано). Остальные использования id — интерполяция в URL (`/api/chats/${id}`, `/api/memory/${id}`, `/api/admin/gemini/projects/${p.id}/...`: ChatsPage.tsx:90,99,110,119,124; MemoryPage.tsx:38,46; AdminGeminiPage.tsx:139,147,262) и React keys — runtime работает со строками, пострадала только типизация.

BackButton: miniapp/src/App.tsx:35 — `/^\/chats\/\d+/.test(location.pathname)` не матчит UUID (hex+дефисы) → нативная BackButton Telegram **никогда не показывается** на странице чата.

**Что чинить:** types.ts — id→string для Chat, MemoryItem, GeminiProject, AuditEntry; ChatSettingsPage.tsx — убрать `Number(id)`, сравнивать строки; App.tsx — regex на UUID-сегмент (напр. `^/chats/[^/]+`).

---

## A24 — архивные чаты и пагинация >50

**Verdict: confirmed-broken** (UI архива мёртв; пагинации нет ни в API, ни в UI)

- GET /api/chats: app/api/routes/chats.py:79–87 — роут без query-параметров, вызывает `ChatRepository.list_for_user(user.id)` с дефолтами.
- Репозиторий: app/db/repositories/chats.py:29–41 — дефолты `include_archived: bool = False` (строка 33), `limit: int = 50` (строка 34). Итог: архивные **никогда** не возвращаются; активные сверх 50 молча обрезаются.
- UI: miniapp/src/pages/ChatsPage.tsx:148–149 фильтрует active/archived на клиенте и рендерит секцию «Архив» (строки 184–190) — но источник данных архивных не содержит → секция всегда пуста. Пагинации/«показать ещё» нет.
- Memory: app/api/routes/memory.py:36–41 — роут без параметров, дефолт репозитория `limit: int = 100` (app/db/repositories/memories.py:18–30). MemoryPage.tsx:90–91 рендерит всё разом («Записей: N»), пагинации нет → сверх 100 записей молча теряются.

**Что чинить:** API shape (параметры `archived`/`limit`/`offset` или курсор для /api/chats и /api/memory) — согласовать с backend-владельцем (профиль запрещает менять backend напрямую); затем hooks.ts (useChats/useMemories с параметрами), ChatsPage (рабочая архивная вкладка + догрузка), MemoryPage (догрузка).

---

## A25 — effective settings расхождение

**Verdict: partially-fixed**

- `_chat_out` **не возвращает** `system_prompt_override`: app/api/routes/chats.py:46–58 (поля id..is_current, override отсутствует). Соответственно тип Chat без поля: miniapp/src/api/types.ts:50–61.
- Форма **не загружает** существующий override: miniapp/src/pages/ChatSettingsPage.tsx:33–39 — при появлении chat безусловно `setPrompt("")` (строка 36). Последствие: открыв страницу и нажав «Сохранить промпт» с пустым полем, пользователь молча затирает существующий override (`null`, строка 142). → **confirmed-broken подпункт.**
- Thinking selector при inherit модели: miniapp/src/components/ChatSettingsForm.tsx:23–25 — `effectiveModelId = chat.model_id ?? settings?.default_model_id`, режимы берутся из эффективной модели → при inherit селектор работает. → **fixed.**
  - Остаточная мелочь: ChatSettingsPage.tsx:68 ждёт только `chatsQ.isLoading || modelsQ.isLoading`, но не `settingsQ` → первый кадр с `settings=null` даёт `thinkingModes=[]` и заблокированный селектор (ChatSettingsForm.tsx:56) до подгрузки settings.
- PATCH model=null + thinking: backend корректен — app/api/routes/chats.py:136–144: при явном `model_id=None` `base_model_id` падает в ветку user default (строки 138–141), валидация thinking идёт против дефолтной модели. Сброс thinking при смене модели на фронте тоже есть (ChatSettingsForm.tsx:27–33). → **fixed.**

**Что чинить:** backend: добавить `system_prompt_override` в `_chat_out` (согласовать с владельцем chats.py); frontend: тип Chat + загрузка override в `setPrompt` вместо `""`; добавить `settingsQ.isLoading` в loading-gate ChatSettingsPage.

---

## A26 — снятие числового лимита через GrantModal

**Verdict: confirmed-broken** (снять лимит невозможно — null теряется в backend)

- Фронт: miniapp/src/admin/AdminUsersPage.tsx:98–100 — `requests_per_day: numOrNull(rpd)` и т.д.; `numOrNull("")` → `null` (miniapp/src/utils.ts:38–43). Т.е. очищенное поле уходит как явный JSON `null` — фронт корректен.
- Pydantic-схема не различает «поле не передано» и «передан null»: app/api/routes/admin_access.py:15–25 (`int | None = None`), роут передаёт значения как есть (строки 47–58).
- Сервис **выбрасывает** None: app/services/admin.py:91–100 — `fields.update({key: value ... if value is not None})` → `upsert_grant` не трогает колонку → **старый лимит сохраняется**. Для существующего гранта с rpd=100 повторный grant с очищенным полем молча оставляет 100.
- Не затронуто: булевы can_use_web_search/can_use_memory всегда отправляются (AdminUsersPage.tsx:101–102) и работают; новый грант с пустыми полями получает DB-дефолт NULL — тоже ок. Баг бьёт именно по сценарию «снять существующий лимит».

**Что чинить:** API shape: distinguish unset/null (напр. `model_dump(exclude_unset=True)` + явный null → очистка колонки) — backend-владелец; после согласования фронту менять нечего (уже шлёт null). Зафиксировать как blocker с proposal к G.

---

## A28 — полнота админки

**Verdict: confirmed-broken** (4 из 5 возможностей отсутствуют end-to-end; model health — частично)

Вкладки админки: miniapp/src/admin/AdminLayout.tsx:4–12 — Дашборд, Пользователи, Gemini, Провайдеры, Поиск, Система, Аудит. Роуты: App.tsx:128–136.

| Возможность | UI | Backend | Статус |
|---|---|---|---|
| Models CRUD/enable | Нет страницы/вкладки (AdminLayout.tsx:4–12); AdminSystemPage.tsx:64–70 лишь выбирает default из юзерского GET /api/models (hooks.ts:44–50) | Нет admin_models.py в app/api/routes/; me.py:42 — только публичный список | **missing** |
| Gemini Test | Нет кнопки (AdminGeminiPage.tsx:190–200 — только ↑/↓/Удалить; toggle:163) | Нет endpoint (admin_gemini.py: projects CRUD/move/quotas только) | **missing** |
| Gemini Reset counters | Нет (cooldown/health read-only: AdminGeminiPage.tsx:155,162–184) | Нет endpoint (admin_gemini.py полностью прочитан) | **missing** |
| Alibaba Run Smoke Test | Нет (AdminProvidersPage.tsx:44–97 — статус + установка ключа) | Нет endpoint (admin_providers.py:1–68 — только GET status + POST key). Контраст: у search-бэкендов Test есть и в API (admin_search.py:115), и в UI (AdminSearchPage.tsx:108–117) | **missing** |
| Admin Memory раздел | Нет вкладки/страницы | Нет admin-memory роутов (листинг app/api/routes/) | **missing** |
| Model health view | Частично: per-project Chip health_status (AdminGeminiPage.tsx:162), агрегат на дашборде (AdminDashboardPage.tsx:53–58) | Per-project health в _project_out (admin_gemini.py:56), агрегат в stats (admin_stats.py:55–59,72–76); per-model health нет | **partially-fixed** |

**Что чинить:** backend endpoints (models admin CRUD/enable, gemini test+reset, alibaba smoke test, admin memory list) — proposals владельцам; затем новые страницы/кнопки в miniapp/src/admin/*, вкладки в AdminLayout, роуты в App.tsx. Per-model health — отдельный proposal или учесть в Models-странице.

---

## Сводная таблица

| ID | Verdict |
|---|---|
| A01 | confirmed-broken (ChatSettingsPage мёртв: NaN; BackButton мёртв: regex; типы 4/5 врут) |
| A24 | confirmed-broken (архив не доходит до UI; >50 чатов и >100 memory молча режутся) |
| A25 | partially-fixed (override round-trip сломан; inherit-thinking и PATCH model=null+thinking — ок) |
| A26 | confirmed-broken (null дропается в grant_access → лимит не снять) |
| A28 | confirmed-broken (models CRUD, gemini test/reset, alibaba smoke, admin memory — отсутствуют; model health — частично) |

## Файлы, планируемые к правке в coding-волне (writable scope: miniapp/**)

1. `miniapp/src/api/types.ts` — id: string (Chat, MemoryItem, GeminiProject, AuditEntry); Chat += system_prompt_override (после backend); типы пагинации (после API shape).
2. `miniapp/src/pages/ChatSettingsPage.tsx` — убрать `Number(id)`, строковое сравнение; загрузка override; settingsQ в loading-gate.
3. `miniapp/src/App.tsx` — BackButton regex под UUID (`/^\/chats\/[^/]+/`).
4. `miniapp/src/api/hooks.ts` — параметры archived/limit/offset для useChats/useMemories (после согласования API).
5. `miniapp/src/pages/ChatsPage.tsx` — рабочая архивная вкладка + догрузка (после API).
6. `miniapp/src/pages/MemoryPage.tsx` — догрузка/пагинация (после API).
7. `miniapp/src/admin/AdminGeminiPage.tsx` — кнопки Test / Reset counters (после backend endpoints).
8. `miniapp/src/admin/AdminProvidersPage.tsx` — кнопка Smoke Test (после backend endpoint).
9. `miniapp/src/admin/AdminLayout.tsx` + `App.tsx` — новые вкладки/роуты (Models, Memory) — после backend endpoints.
10. `miniapp/src/admin/AdminUsersPage.tsx` — правок не требуется по A26 (фронт уже шлёт null); возможный UX-хинт «лимит будет снят» — после решения API shape.

**Blockers / нужны согласования (backend не трогаю по профилю):**
- A24: shape пагинации/archived-фильтра для /api/chats, /api/memory → владелец app/api/routes/*.
- A25: добавить `system_prompt_override` в `_chat_out` → владелец chats.py.
- A26: distinguish unset vs null в AccessGrantRequest/grant_access → владелец admin backend (proposal к G).
- A28: все четыре missing-фичи требуют новых backend endpoints → proposals к G.
