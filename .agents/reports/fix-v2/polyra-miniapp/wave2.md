# polyra-miniapp — wave2 (coding): A01, A24, A25, A26, A28, N04

- Дата: 2026-09-24
- Writable scope: `miniapp/**` + этот отчёт. Backend (`app/**`), `docs/**`, корень — не тронуты.
- База: вердикты `revalidate.md` + `docs/API.md` секция «V2 additions» (backend уже реализован).

## Решения по задачам

### A01 — UUID как string
- `types.ts`: `Chat.id`, `MemoryItem.id`, `GeminiProject.id`, `AuditEntry.id` → `string`.
- `ChatSettingsPage.tsx`: убран `Number(id)`; вместо поиска в списке — `useChat(id)` (новый `GET /api/chats/{id}`, устойчиво к пагинации списка); все URL-интерполяции — строка.
- `App.tsx`: BackButton regex `/^\/chats\/\d+/` → `/^\/chats\/[^/]+/` (любой непустой сегмент, покрывает UUID).
- `ChatsPage.tsx`, `MemoryPage.tsx`, `HomePage.tsx`, `AdminGeminiPage.tsx`: типы мутаций `id: number` → `id: string`; `AddProjectModal` ответ `{id: string}`.
- Оставшиеся `Number(...)` в коде — не entity id (select-значения, telegram_user_id, системные числа) — не тронуты сознательно.

### A24 — пагинация + архив
- `types.ts`: `ChatsResponse`/`MemoriesResponse` += `total`; `ChatResponse` добавлен.
- `hooks.ts`: `useChats({limit, offset, include_archived, enabled})`, `useChat(id)`, `useMemories({limit, offset})` — query keys параметризованы, `qk.chats`/`qk.memories` остаются префиксами → существующие `invalidateQueries` работают.
- `ChatsPage.tsx`: активные — limit-growth пагинация (PAGE_SIZE=20, «Загрузить ещё (N из total)»); секция «Архив» ленивая (кнопка «Показать архивные чаты» → `include_archived=true`, фильтр `archived_at !== null`, своя догрузка). Restore работает через существующий `archive` endpoint (`archived:false`, кнопка 📤).
- `MemoryPage.tsx`: limit-growth пагинация (PAGE_SIZE=50), заголовок «Записей: {total}».

### A25 — effective settings
- `Chat` += `system_prompt_override: string | null`; `ChatSettingsPage` загружает его в поле (`setPrompt(chat.system_prompt_override ?? "")`) — override больше не затирается при открытии. Сохранение: пустая строка → `null` (backend `exclude_unset`: null → снять override — подтверждено `chats.py:154`).
- Loading-gate += `settingsQ.isLoading` (убран первый кадр с заблокированным thinking-селектором).
- Thinking selector: уже работал через effective модель (`chat.model_id ?? settings.default_model_id`, режимы из `GET /api/models`) — подтверждено, не менялся.

### A26 — снятие лимита в GrantModal
- Backend семантика подтверждена (`admin_access.py:66` — `model_dump(exclude_unset=True)`): поле не передано → не меняется; null → снять.
- `AdminUsersPage.tsx` GrantModal: для `requests_per_day`/`token_limit`/`max_concurrent_generations` добавлен чекбокс «Без лимита (снять)» (input при этом disabled). Сборка body: чекбокс вкл → явный `null`; выкл + непустое поле → число; выкл + пустое → поле не шлётся. Подписи полей: «пусто — без изменений».

### A28 — admin-разделы
- `AdminLayout.tsx`: вкладки «Модели» и «Память»; роуты в `App.tsx`.
- `AdminModelsPage.tsx` (новый): `GET /api/admin/models` → таблица (display_name+model_id, provider, thinking-чипы, max_context/max_output, флаги internal/text-only, Toggle enabled → `PUT /api/admin/models/{model_id} {enabled}`).
- `AdminGeminiPage.tsx`: кнопка «🔍 Тест» на проекте (`POST .../test` → ✅/❌ + latency/error); кнопка «♻ Reset counters» с `ConfirmDialog` (`POST /api/admin/gemini/reset-counters` → «удалено записей N»); ленивая секция «Использование квот» (`GET /api/admin/gemini/usage` → таблицы minute/daily).
- `AdminProvidersPage.tsx`: кнопка «🔍 Run Smoke Test» для Alibaba (`POST .../smoke` → ok/latency/error; disabled пока `!configured`).
- `AdminMemoryPage.tsx` (новый): фильтр по telegram_user_id (debounce 300мс, пусто = все), limit-growth пагинация (50), read-only список с чипами.
- `AdminDashboardPage.tsx`: карточки «Ошибок сегодня»/«429 сегодня» + секция «Запросы по моделям (сегодня)» — рендерятся условно (поля в `AdminStats` опциональные, устойчиво к старому backend).
- `AdminAuditPage.tsx`: фильтр по action (debounce), `total`, limit-growth пагинация (50).

### N04
- Специальных правок не потребовалось: `deepseek-v4-pro` придёт через `GET /api/models`; лейблы `off`/`high`/`max` есть в `THINKING_LABELS`; text-only модель UI не ломает (`supports_images` в юзерском UI не используется, thinking-селектор деградирует в disabled при пустых `thinking_modes`).

## Файлы

Изменены:
- `miniapp/src/api/types.ts` — id→string ×4, `total` в ответах, `system_prompt_override`, V2-типы (AdminModel, ProviderTestResult, GeminiUsage*, AdminMemoriesResponse, ChatResponse), AdminStats extras (optional).
- `miniapp/src/api/hooks.ts` — параметризованные useChats/useChat/useMemories/useAudit; новые useAdminModels/useGeminiUsage/useAdminMemories; ключи adminModels/adminMemory/geminiUsage.
- `miniapp/src/App.tsx` — BackButton regex, роуты models/memory.
- `miniapp/src/pages/ChatSettingsPage.tsx` — string UUID, useChat, override round-trip, settingsQ в gate.
- `miniapp/src/pages/ChatsPage.tsx` — пагинация активных + ленивый архив.
- `miniapp/src/pages/MemoryPage.tsx` — пагинация, string id.
- `miniapp/src/pages/HomePage.tsx` — string id в patchChat.
- `miniapp/src/admin/AdminLayout.tsx` — вкладки Модели/Память.
- `miniapp/src/admin/AdminUsersPage.tsx` — GrantModal: чекбоксы «Без лимита (снять)», условная сборка body.
- `miniapp/src/admin/AdminGeminiPage.tsx` — Test, Reset counters, usage-секция, string id.
- `miniapp/src/admin/AdminProvidersPage.tsx` — Smoke Test.
- `miniapp/src/admin/AdminAuditPage.tsx` — фильтр action + пагинация.
- `miniapp/src/admin/AdminDashboardPage.tsx` — errors/429/requests_by_model.

Созданы:
- `miniapp/src/admin/AdminModelsPage.tsx`
- `miniapp/src/admin/AdminMemoryPage.tsx`

## Статус build

`npm run build` (tsc strict + vite) — **PASS**, реальный лог 2026-09-24:

```
> aibot-miniapp@0.1.0 build
> tsc && vite build
vite v5.4.21 building for production...
✓ 112 modules transformed.
dist/index.html                 0.54 kB │ gzip:  0.33 kB
dist/assets/index-1nPEDc8B.css  9.42 kB │ gzip:  2.66 kB
dist/assets/index-ehHhysVE.js 277.19 kB │ gzip: 83.75 kB
✓ built in 1.08s
```

## Не проверено

- Живой прогон против реального backend (нет запущенного сервера/Telegram initData в среде): фактические ответы V2 endpoints, точная семантика `include_archived=true` (предполагается «включая архивные», фильтр на клиенте), состав полей `AdminMemoryPage`-записей (в доке `{memories: [...]}` — принят shape `MemoryItem`).
- Ручной UI-прогон в Telegram WebView (BackButton, скролл subtab-бара с 9 вкладками).
- Пагинация реализована limit-growth («Загрузить ещё» увеличивает limit, refetch всего окна) — осознанный простой вариант; при тысячах записей можно перейти на offset-accumulation.
