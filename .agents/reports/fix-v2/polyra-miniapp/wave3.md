# Wave 3 — polyra-miniapp (frontend)

Дата: 2026-09-25. Scope: только `miniapp/**`. `app/**` не изменялся (изменения в app/** в worktree — от параллельных субагентов, использованы read-only для сверки контрактов).

## Задача 1 (A28) — AdminUsersPage: пагинация «Загрузить ещё»

- `useAdminUsers(query)` переведён на `useInfiniteQuery` (limit=50 фикс., offset = pageParam).
- `GET /api/admin/users` не возвращает `total` (проверено по `app/api/routes/admin_users.py`) → видимость кнопки по эвристике «последняя страница пришла полной» (`lastPage.users.length === 50`). Если итог кратен 50 — один холостой запрос, вернувший пустую страницу, после чего кнопка скрывается (задокументировано в комментарии хука).
- Кнопка «Загрузить ещё (показано N)» под списком; loading = `isFetchingNextPage`. Инвалидация `qk.adminUsersAll` (grant/ban/...) работает — React Query перезапрашивает все загруженные страницы.

## Задача 2 (A35/A28) — Dashboard extras

- `AdminStats` дополнен опциональными `avg_ttft_s?: number | null`, `error_rate_today?: number`, `recent_failed_runs?: RecentFailedRun[]` (все рендерятся условно — старый backend их не отдаёт).
- Контракт сверен с реализацией параллельного backend-агента (`app/api/routes/admin_stats.py`, diff worktree):
  - `avg_ttft_s` — float|null (null → «—»); карточка «Средний TTFT, с».
  - `error_rate_today` — доля 0..1 → выводится как `%` (`formatRate`).
  - `recent_failed_runs` — последние failed/aborted: таблица «Последние неудачные генерации» (модель mono, статус Chip по healthTone, error_category (+ error_code в скобках), время `formatDateTime`). Поля типа: `{id, chat_id, model_id, status, error_category, error_code, started_at, duration_s}`.

## Задача 3 (A27 UI) — per-model health/capability

- AdminModelsPage уже корректно рендерит `thinking_modes` (chips через `thinkingLabel`) — изменений логики не потребовалось.
- Добавлен условный показ «проверено: \<дата\>» (+ суффикс «(устарело)» при `probe_fresh === false`) в колонке Thinking; поля `probe_at?: string | null`, `probe_fresh?: boolean` добавлены в `AdminModel` (на текущем `/api/admin/models` их нет — появятся при доработке backend, рендер условный).
- Замечено: параллельный backend-агент добавил `probe_at` и фильтрацию thinking_modes по probe acceptance в user-эндпоинт `GET /api/models` (`app/api/routes/me.py`) → `probe_at?: string | null` добавлен также в `ModelInfo` для точности типов (в user UI не отображается — вне задачи).

## Задача 4 (A24) — Chats/Memory: offset-пагинация вместо limit-наращивания

Подтверждено: backend cap `limit ≤ 200` (`_MAX_LIMIT = 200` в `chats.py`, `memory.py`, `admin_users.py`). Прежний подход `setLimit(v => v + PAGE_SIZE)` после 200 записей упирался в cap → `loaded(200) < total` оставалось истинным вечно («вечная кнопка», записи за пределами 200 недоступны).

Исправлено переводом на `useInfiniteQuery` с offset-наращиванием (`offset += loaded`, limit фиксирован):
- `useChats({include_archived, enabled})` — pageSize 20, `getNextPageParam` по `total` из ответа. Потребители: ChatsPage (активные + архив), HomePage (только первая страница — достаточно для поиска current чата).
- `useMemories()` — pageSize 50, по `total`.
- ChatsPage/MemoryPage: плоские списки через `pages.flatMap`, кнопки на `fetchNextPage` / `isFetchingNextPage`, счётчики «N из total».

## Файлы

- `miniapp/src/api/types.ts` — AdminStats extras, RecentFailedRun, AdminModel.probe_*, ModelInfo.probe_at.
- `miniapp/src/api/hooks.ts` — `useChats`/`useMemories`/`useAdminUsers` → `useInfiniteQuery` (offset), константы `CHATS_PAGE_SIZE=20`, `MEMORIES_PAGE_SIZE=50`, `ADMIN_USERS_PAGE_SIZE=50`; `ChatsParams` упрощён (limit/offset убраны), `MemoriesParams` удалён.
- `miniapp/src/pages/ChatsPage.tsx` — кнопки «Загрузить ещё» на fetchNextPage (активные и архив).
- `miniapp/src/pages/MemoryPage.tsx` — то же для памяти.
- `miniapp/src/pages/HomePage.tsx` — адаптация под infinite-форму `useChats` (1 строка).
- `miniapp/src/admin/AdminUsersPage.tsx` — кнопка «Загрузить ещё (показано N)».
- `miniapp/src/admin/AdminDashboardPage.tsx` — карточки TTFT/error rate + таблица recent_failed_runs (условно).
- `miniapp/src/admin/AdminModelsPage.tsx` — «проверено: \<дата\>» (условно).

## Статус сборки

`npm run build` (tsc && vite build) в `miniapp/` — **PASS**:

```
> aibot-miniapp@0.1.0 build
> tsc && vite build
vite v5.4.21 building for production...
✓ 112 modules transformed.
dist/index.html                 0.54 kB │ gzip:  0.33 kB
dist/assets/index-1nPEDc8B.css  9.42 kB │ gzip:  2.66 kB
dist/assets/index-B6Zz5aQq.js  280.53 kB │ gzip: 84.46 kB
✓ built in 1.01s
```

## Не проверено

- Живой прогон против backend (нет запущенного сервера в среде): корректность offset-пагинации и эвристики «полная страница» проверена только по контрактам/коду backend.
- `AdminModel.probe_at/probe_fresh` — backend `/api/admin/models` полей пока не отдаёт (рендер условный, типы готовы).
- Эвристика hasMore для users при итоге кратном 50 — один лишний пустой запрос (ожидаемо, осознанный trade-off при отсутствии `total`).
- Архивные чаты: backend `/api/chats?include_archived=true` возвращает смешанный список, фильтрация `archived_at !== null` остаётся на клиенте (как было); отдельного параметра «только архивные» в API нет.
