// TS-типы по docs/API.md (M9/M10). Все поля — точно по контракту backend.

// ---------- Auth ----------

export interface TelegramUser {
  telegram_user_id: number;
  username: string | null;
  first_name: string | null;
}

export interface AuthResponse {
  session_token: string;
  expires_in: number;
  user: TelegramUser;
  is_owner: boolean;
}

// ---------- User API ----------

export interface Permissions {
  allowed_models: string[] | null;
  can_use_web_search: boolean;
  can_use_memory: boolean;
  max_concurrent_generations: number;
  requests_per_day: number | null;
  token_limit: number | null;
}

export interface MeResponse {
  user: TelegramUser;
  is_owner: boolean;
  permissions: Permissions;
}

export interface ModelInfo {
  model_id: string;
  display_name: string;
  provider: string;
  supports_images: boolean;
  thinking_modes: string[];
  default_thinking: string | null;
  /** Время последнего capability probe (A27); null/отсутствует на старом backend. */
  probe_at?: string | null;
}

export interface ModelsResponse {
  models: ModelInfo[];
}

export type WebMode = "off" | "auto" | "on";

export interface Chat {
  id: string;
  title: string | null;
  model_id: string | null;
  thinking_setting: string | null;
  web_mode: string | null;
  memory_enabled: boolean | null;
  system_prompt_override: string | null;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  is_current: boolean;
}

export interface ChatsResponse {
  chats: Chat[];
  total: number;
}

export interface ChatResponse {
  chat: Chat;
}

/** PATCH /api/chats/{id}: null/отсутствие = inherit от user defaults. */
export interface ChatPatch {
  title?: string | null;
  model_id?: string | null;
  thinking_setting?: string | null;
  web_mode?: string | null;
  memory_enabled?: boolean | null;
  system_prompt_override?: string | null;
}

export interface UserSettings {
  default_model_id: string | null;
  default_thinking: string | null;
  web_mode: WebMode;
  memory_enabled: boolean;
}

export interface SettingsPatch {
  default_model_id?: string | null;
  default_thinking?: string | null;
  web_mode?: WebMode;
  memory_enabled?: boolean;
}

export interface MemoryItem {
  id: string;
  text: string;
  category: string;
  importance: number;
  updated_at: string;
  last_used_at: string | null;
}

export interface MemoriesResponse {
  memories: MemoryItem[];
  total: number;
}

export interface MemoryPatch {
  text?: string;
  category?: string;
  importance?: number;
}

// ---------- Admin API ----------

export interface AccessGrant {
  status: string;
  expires_at: string | null;
  requests_per_day: number | null;
  token_limit: number | null;
  max_concurrent_generations: number | null;
  can_use_web_search: boolean;
  can_use_memory: boolean;
}

export interface AdminUser {
  id: string;
  telegram_user_id: number;
  username: string | null;
  first_name: string | null;
  status: string;
  is_owner: boolean;
  first_seen_at: string;
  last_seen_at: string;
  grant: AccessGrant | null;
  allowed_models: string[] | null;
}

export interface AdminUsersResponse {
  users: AdminUser[];
}

export interface GrantBody {
  telegram_user_id: number;
  expires_at: string | null;
  requests_per_day?: number | null;
  token_limit?: number | null;
  max_concurrent_generations?: number | null;
  can_use_web_search?: boolean;
  can_use_memory?: boolean;
  note?: string;
}

export interface GeminiProject {
  id: string;
  name: string;
  key_hint: string;
  enabled: boolean;
  health_status: string;
  rotation_order: number;
  cooldown_until: string | null;
  last_success_at: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
}

export interface GeminiProjectsResponse {
  projects: GeminiProject[];
}

export interface GeminiQuota {
  model_id: string;
  rpm: number | null;
  tpm: number | null;
  rpd: number | null;
}

export interface GeminiQuotasResponse {
  quotas: GeminiQuota[];
}

export interface AlibabaStatus {
  configured: boolean;
  key_hint: string | null;
  base_url: string;
  enabled: boolean;
}

export interface SearchBackend {
  backend_id: string;
  enabled: boolean;
  priority: number;
  key_hint: string | null;
  health_status: string;
  last_error: string | null;
}

export interface SearchBackendsResponse {
  backends: SearchBackend[];
}

export interface SystemSettings {
  default_model: string | null;
  default_thinking: string | null;
  default_system_prompt: string | null;
  max_tool_iterations: number;
  context_keep_recent: number;
  context_trigger_ratio: number;
  memory_retrieval_limit: number;
  /** A13: мин. объём сообщения для извлечения памяти. */
  memory_extraction_min_chars: number;
}

/** Последний failed/aborted run (AdminStats.recent_failed_runs, V2 extra 2026-09-25). */
export interface RecentFailedRun {
  id: string;
  chat_id: string | null;
  model_id: string | null;
  status: string;
  error_category: string | null;
  error_code: string | null;
  started_at: string;
  duration_s: number | null;
}

export interface AdminStats {
  users_total: number;
  users_active_7d: number;
  generations_today: number;
  generations_by_status: Record<string, number>;
  tokens_today: { input: number; output: number };
  gemini_projects: { total: number; enabled: number; healthy: number };
  tool_calls_today: number;
  // V2 additions (могут отсутствовать на старом backend — рендерим условно)
  requests_by_model_today?: Record<string, number>;
  errors_today?: number;
  rate_limit_429_today?: number;
  gemini_usage_today?: { project_name: string; requests: number; tokens_in: number }[];
  /** Средний TTFT за сегодня (с); null, если completed-запусков с first_token_at не было. */
  avg_ttft_s?: number | null;
  /** Доля (failed+aborted) среди генераций сегодня, 0..1. */
  error_rate_today?: number;
  recent_failed_runs?: RecentFailedRun[];
}

export interface AuditEntry {
  id: string;
  actor_telegram_id: number;
  action: string;
  target_type: string;
  target_id: string;
  metadata: unknown;
  created_at: string;
}

export interface AuditResponse {
  entries: AuditEntry[];
  total: number;
}

// ---------- V2 additions (2026-09-24) ----------

export interface AdminModel {
  model_id: string;
  display_name: string;
  provider: string;
  enabled: boolean;
  internal_only: boolean;
  supports_images: boolean;
  thinking_modes: string[];
  default_thinking: string | null;
  max_context: number;
  max_output: number;
  // Capability probe (A27): поля опциональны — старый backend их не отдаёт.
  probe_at?: string | null;
  probe_fresh?: boolean;
}

export interface AdminModelsResponse {
  models: AdminModel[];
}

export interface ProviderTestResult {
  ok: boolean;
  latency_ms: number;
  error: string | null;
}

export interface GeminiUsageMinute {
  project_name: string;
  model_id: string;
  minute_ts: string;
  requests_count: number;
  tokens_in: number;
}

export interface GeminiUsageDaily {
  project_name: string;
  model_id: string;
  day: string;
  requests_count: number;
  tokens_in: number;
}

export interface GeminiUsageResponse {
  minute: GeminiUsageMinute[];
  daily: GeminiUsageDaily[];
}

export interface AdminMemoriesResponse {
  memories: MemoryItem[];
  total: number;
}

// ---------- web5: веб-экран чата (Mini App) ----------

export type WebMessageRole = "user" | "assistant";

/**
 * Сообщение ленты web-чата: GET /api/chats/{id}/messages (ASC).
 * status: pending | streaming | done | cancelled | failed (app/db/models/message.py).
 */
export interface WebMessage {
  id: string;
  role: WebMessageRole;
  status: string;
  created_at: string;
  model_id: string | null;
  text: string;
  has_image: boolean;
}

export interface WebMessagesResponse {
  messages: WebMessage[];
  total: number;
}
