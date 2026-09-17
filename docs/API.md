# Mini App REST API — контракт (M9/M10)

Дата: 2026-09-18. Backend: FastAPI (`app/api/`), frontend: `miniapp/` (React+TS+Vite).
Базовый префикс: `/api`. Всё JSON. Ошибки: `{"detail": "..."}` (FastAPI-стиль), коды 400/401/403/404/409.

## Аутентификация

1. Frontend берёт `Telegram.WebApp.initData` (сырая строка).
2. `POST /api/auth/telegram` — body: `{"init_data": "<raw initData>"}`.
   Backend валидирует HMAC-подпись (алгоритм Telegram Mini Apps) + `auth_date` (≤ 24ч) +
   активный доступ. Ответ:
   ```json
   {
     "session_token": "string",
     "expires_in": 900,
     "user": {"telegram_user_id": 795063564, "username": "...", "first_name": "..."},
     "is_owner": true
   }
   ```
3. Дальше все запросы: `Authorization: Bearer <session_token>`.
   Session token = `base64url(json{sub: telegram_user_id, exp}).hmac_sha256_hex(master_key)`.
   401 при невалидном/истёкшем токене; 403 для admin-endpoints если не owner.

## User API

- `GET /api/me` → `{user: {telegram_user_id, username, first_name}, is_owner: bool,
  permissions: {allowed_models: string[] | null, can_use_web_search, can_use_memory,
  max_concurrent_generations, requests_per_day | null, token_limit | null}}`
- `GET /api/models` → `{models: [{model_id, display_name, provider, supports_images: bool,
  thinking_modes: string[], default_thinking: string | null}]}` — только разрешённые пользователю, не internal.
- `GET /api/chats` → `{chats: [{id, title: string | null, model_id: string | null,
  thinking_setting: string | null, web_mode: string | null, memory_enabled: bool | null,
  created_at, updated_at, archived_at: string | null, is_current: bool}]}`
- `POST /api/chats` `{title?: string}` → создать + сделать текущим → `{chat: {...}}`
- `POST /api/chats/{id}/open` → `{ok: true}` (сделать текущим)
- `PATCH /api/chats/{id}` `{title?, model_id?, thinking_setting?, web_mode?, memory_enabled?,
  system_prompt_override?}` — null/отсутствие значения = inherit. → `{chat: {...}}`
- `POST /api/chats/{id}/archive` `{archived: bool}` → `{ok}`
- `DELETE /api/chats/{id}` → `{ok}`
- `GET /api/settings` → `{default_model_id, default_thinking, web_mode: "off"|"auto"|"on",
  memory_enabled: bool}`
- `PATCH /api/settings` (те же поля) → `{settings: {...}}`
- `GET /api/memory` → `{memories: [{id, text, category, importance, updated_at, last_used_at}]}`
- `PATCH /api/memory/{id}` `{text?, category?, importance?}` → `{memory: {...}}`
- `DELETE /api/memory/{id}` → `{ok}`

## Admin API (только owner, иначе 403)

### Users & Access
- `GET /api/admin/users?query=&limit=50&offset=0` → `{users: [{id, telegram_user_id, username,
  first_name, status, is_owner, first_seen_at, last_seen_at, grant: {status, expires_at,
  requests_per_day, token_limit, max_concurrent_generations, can_use_web_search,
  can_use_memory} | null, allowed_models: string[] | null}]}`
- `POST /api/admin/access/grant` `{telegram_user_id: int, expires_at: string | null,
  requests_per_day?, token_limit?, max_concurrent_generations?, can_use_web_search?,
  can_use_memory?, note?}` → `{ok}` (expires_at null = permanent)
- `POST /api/admin/access/extend` `{telegram_user_id, expires_at}` → `{ok}`
- `POST /api/admin/access/suspend|revoke` `{telegram_user_id}` → `{ok}`
- `POST /api/admin/users/ban|unban` `{telegram_user_id}` → `{ok}`
- `GET /api/admin/users/{telegram_user_id}/models` → `{allowed_models: string[] | null}`
- `PUT /api/admin/users/{telegram_user_id}/models` `{allowed_models: string[] | null}` → `{ok}`

### Gemini pool
- `GET /api/admin/gemini/projects` → `{projects: [{id, name, key_hint, enabled, health_status,
  rotation_order, cooldown_until, last_success_at, last_error_code, last_error_message}]}`,
  `GET /api/admin/gemini/quotas` → `{quotas: [{model_id, rpm, tpm, rpd}]}`
- `POST /api/admin/gemini/projects` `{name, api_key}` → `{ok, id}`
- `POST /api/admin/gemini/projects/bulk` `{api_keys: string[], name_prefix?: string}` →
  `{added: int, skipped: int}`
- `POST /api/admin/gemini/projects/{id}/enable|disable` → `{ok}`
- `POST /api/admin/gemini/projects/{id}/move` `{direction: -1 | 1}` → `{ok}`
- `DELETE /api/admin/gemini/projects/{id}` → `{ok}`
- `PUT /api/admin/gemini/quotas` `{model_id, rpm: int | null, tpm: int | null, rpd: int | null}` → `{ok}`
- Полные ключи НИКОГДА не возвращаются — только key_hint.

### Alibaba / providers
- `GET /api/admin/providers/alibaba` → `{configured: bool, key_hint: string | null,
  base_url: string, enabled: bool}` (base_url read-only)
- `POST /api/admin/providers/alibaba/key` `{api_key}` → `{ok}`

### Search backends
- `GET /api/admin/search/backends` → `{backends: [{backend_id, enabled, priority, key_hint,
  health_status, last_error}]}`
- `PUT /api/admin/search/backends/{backend_id}` `{enabled?, priority?}` → `{ok}`
- `POST /api/admin/search/backends/{backend_id}/key` `{api_key}` → `{ok}`
- `POST /api/admin/search/backends/{backend_id}/test` → `{ok: bool, error: string | null}`

### System / Stats / Audit
- `GET /api/admin/system` → `{default_model, default_thinking, default_system_prompt,
  max_tool_iterations, context_keep_recent, context_trigger_ratio, memory_retrieval_limit}`
- `PUT /api/admin/system` (те же поля опционально) → `{ok}` (system_settings key-value store)
- `GET /api/admin/stats` → `{users_total, users_active_7d, generations_today,
  generations_by_status: {..}, tokens_today: {input, output}, gemini_projects: {total, enabled,
  healthy}, tool_calls_today: int}`
- `GET /api/admin/audit?limit=50` → `{entries: [{id, actor_telegram_id, action, target_type,
  target_id, metadata, created_at}]}`
