# Round4-P1: вечная кнопка пагинации в AdminAuditPage / AdminMemoryPage

Профиль: B (Mini App / UX / типы). База: HEAD 4925c40, свежий checkout `O:\work\aibot`.

## Суть багов

- **AdminAuditPage (P1):** рост `limit` (`setLimit(v+PAGE_SIZE)`) упирался в backend-cap 500
  (`app/api/routes/admin_stats.py:16 _MAX_AUDIT_LIMIT=500`); `hasMore = entries.length < total`
  оставался true навсегда — записи 501+ недостижимы, кнопка «Загрузить ещё» вечная.
- **AdminMemoryPage (P1):** то же с cap 200 (`app/api/routes/admin_memory.py:15 _MAX_LIMIT=200`).

## Решение

Перевод обеих страниц на offset-пагинацию по образцу готовых `useChats`/`useMemories`
(`useInfiniteQuery`, `limit=PAGE_SIZE` + `offset=pageParam`, `initialPageParam: 0`,
`getNextPageParam: loaded < lastPage.total ? loaded : undefined`). Backend-эндпоинты
поддерживают `offset` и возвращают нужные поля (сверено, менять backend не потребовалось):

- `/api/admin/audit` → `{ entries: [...], total }` (+ query-параметры `limit`, `offset`, `action`)
- `/api/admin/memory` → `{ memories: [...], total }` (+ `telegram_user_id`, `limit`, `offset`)

## Изменённые файлы

### 1. `miniapp/src/api/hooks.ts`

- `useAudit` переписан на `useInfiniteQuery` (имя сохранено — grep подтвердил, что потребитель
  только AdminAuditPage; выбранный «минимальный вариант»). Параметры: `AuditParams = { action?: string }`
  (limit/offset теперь управляются хуком). Новый экспорт `AUDIT_PAGE_SIZE = 50`.
  queryKey: `[...qk.audit, "list", { action }]`; queryFn шлёт `limit=50&offset=<pageParam>`.
- `useAdminMemories` переписан аналогично (потребитель только AdminMemoryPage).
  `AdminMemoriesParams = { telegram_user_id?: number | null }`, новый экспорт
  `ADMIN_MEMORIES_PAGE_SIZE = 50`, queryKey `[...qk.adminMemory, "list", { telegram_user_id }]`.
- Комментарии с пометкой `round4-P1` объясняют причину (backend cap 500/200 → догрузка offset'ом).

### 2. `miniapp/src/admin/AdminAuditPage.tsx`

- Убраны `limit`-state и эффект сброса `setLimit(PAGE_SIZE)` (смена фильтра меняет queryKey —
  React Query сам сбрасывает пагинацию на первую страницу). Локальный `PAGE_SIZE` удалён
  (tsconfig `noUnusedLocals`).
- Список: `pages = auditQ.data?.pages ?? []; entries = pages.flatMap(p => p.entries)`;
  `total` — из последней страницы; `hasMore = auditQ.hasNextPage ?? false`.
- Кнопка «Загрузить ещё»: `onClick={() => void auditQ.fetchNextPage()}`,
  `loading={auditQ.isFetchingNextPage}` (Button при loading сам disabled); рендерится только
  при `hasMore` (как в ChatsPage).
- Error-рендер не стирает список: полный экран ошибки только при `auditQ.error && !auditQ.data`;
  при ошибке с накопленными страницами — инлайн `error-text`, таблица остаётся.

### 3. `miniapp/src/admin/AdminMemoryPage.tsx`

- Аналогично: убраны `limit`/`setLimit` и локальный `PAGE_SIZE`; `memories = pages.flatMap(...)`;
  `total` из последней страницы; `hasMore = memoriesQ.hasNextPage ?? false`;
  кнопка → `fetchNextPage` + `isFetchingNextPage`.
- EmptyState-ошибка рендерится только при `memories.length === 0`; при ошибке с данными —
  инлайн `error-text` (список не стирается).

### 4. `miniapp/src/api/types.ts`

**Без изменений** — `AuditResponse` и `AdminMemoriesResponse` уже содержат поля `total`
(проверено; бэкенд-поля `entries`/`memories`/`total` совпадают с типами).

## Проверки (реальные прогоны, miniapp/)

| Прогон | Команда | Результат |
|---|---|---|
| Typecheck | `npm run typecheck` | **exit 0** |
| Build | `npm run build` (`tsc && vite build`) | **exit 0**, 112 modules, built in 799ms |

## Инварианты

- Только назначенные файлы изменены (scope соблюдён); AdminUsersPage/ChatsPage/MemoryPage,
  backend и другие агенты не тронуты.
- Существующие хуки не удалялись (переписаны на месте только те два, чей единственный
  потребитель — целевые страницы); имена хуков сохранены.
- Никаких git-операций, БД/permissions/настроек не менялось, живых API-вызовов не было.

## Blockers

Нет.
