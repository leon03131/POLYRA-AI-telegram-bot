import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import type {
  AdminMemoriesResponse,
  AdminModelsResponse,
  AdminStats,
  AdminUsersResponse,
  AlibabaStatus,
  AuditResponse,
  ChatResponse,
  ChatsResponse,
  GeminiProjectsResponse,
  GeminiQuotasResponse,
  GeminiUsageResponse,
  MeResponse,
  MemoriesResponse,
  ModelsResponse,
  SearchBackendsResponse,
  SystemSettings,
  UserSettings,
} from "./types";

/** Ключи кеша React Query. */
export const qk = {
  me: ["me"] as const,
  models: ["models"] as const,
  chats: ["chats"] as const,
  settings: ["settings"] as const,
  memories: ["memories"] as const,
  adminStats: ["admin", "stats"] as const,
  adminUsers: (query: string) => ["admin", "users", query] as const,
  adminUsersAll: ["admin", "users"] as const,
  userModels: (tgId: number) => ["admin", "user-models", tgId] as const,
  adminModels: ["admin", "models"] as const,
  adminMemory: ["admin", "memory"] as const,
  geminiProjects: ["admin", "gemini", "projects"] as const,
  geminiQuotas: ["admin", "gemini", "quotas"] as const,
  geminiUsage: ["admin", "gemini", "usage"] as const,
  alibaba: ["admin", "providers", "alibaba"] as const,
  searchBackends: ["admin", "search", "backends"] as const,
  system: ["admin", "system"] as const,
  audit: ["admin", "audit"] as const,
};

// ---------- User ----------

export function useMe() {
  return useQuery({ queryKey: qk.me, queryFn: () => api<MeResponse>("/api/me") });
}

export function useModels() {
  return useQuery({
    queryKey: qk.models,
    queryFn: () => api<ModelsResponse>("/api/models"),
    staleTime: 60_000,
  });
}

export interface ChatsParams {
  limit?: number;
  offset?: number;
  include_archived?: boolean;
  /** false — не выполнять запрос (ленивая вкладка). */
  enabled?: boolean;
}

export function useChats(params: ChatsParams = {}) {
  const { limit = 50, offset = 0, include_archived = false, enabled = true } = params;
  const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (include_archived) qs.set("include_archived", "true");
  return useQuery({
    queryKey: [...qk.chats, "list", { limit, offset, include_archived }] as const,
    queryFn: () => api<ChatsResponse>(`/api/chats?${qs.toString()}`),
    enabled,
  });
}

/** Один чат по id (UUID) — GET /api/chats/{id}. */
export function useChat(id: string | undefined) {
  return useQuery({
    queryKey: [...qk.chats, "detail", id ?? ""] as const,
    queryFn: () => api<ChatResponse>(`/api/chats/${id}`),
    enabled: !!id,
  });
}

export function useSettings() {
  return useQuery({ queryKey: qk.settings, queryFn: () => api<UserSettings>("/api/settings") });
}

export interface MemoriesParams {
  limit?: number;
  offset?: number;
}

export function useMemories(params: MemoriesParams = {}) {
  const { limit = 50, offset = 0 } = params;
  const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return useQuery({
    queryKey: [...qk.memories, { limit, offset }] as const,
    queryFn: () => api<MemoriesResponse>(`/api/memory?${qs.toString()}`),
  });
}

// ---------- Admin ----------

export function useAdminStats() {
  return useQuery({ queryKey: qk.adminStats, queryFn: () => api<AdminStats>("/api/admin/stats") });
}

export function useAdminUsers(query: string) {
  return useQuery({
    queryKey: qk.adminUsers(query),
    queryFn: () => api<AdminUsersResponse>(`/api/admin/users?query=${encodeURIComponent(query)}&limit=50`),
  });
}

export function useGeminiProjects() {
  return useQuery({
    queryKey: qk.geminiProjects,
    queryFn: () => api<GeminiProjectsResponse>("/api/admin/gemini/projects"),
  });
}

export function useGeminiQuotas() {
  return useQuery({
    queryKey: qk.geminiQuotas,
    queryFn: () => api<GeminiQuotasResponse>("/api/admin/gemini/quotas"),
  });
}

export function useAlibabaStatus() {
  return useQuery({
    queryKey: qk.alibaba,
    queryFn: () => api<AlibabaStatus>("/api/admin/providers/alibaba"),
  });
}

export function useSearchBackends() {
  return useQuery({
    queryKey: qk.searchBackends,
    queryFn: () => api<SearchBackendsResponse>("/api/admin/search/backends"),
  });
}

export function useSystemSettings() {
  return useQuery({ queryKey: qk.system, queryFn: () => api<SystemSettings>("/api/admin/system") });
}

export interface AuditParams {
  limit?: number;
  offset?: number;
  action?: string;
}

export function useAudit(params: AuditParams = {}) {
  const { limit = 50, offset = 0, action = "" } = params;
  const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (action) qs.set("action", action);
  return useQuery({
    queryKey: [...qk.audit, { limit, offset, action }] as const,
    queryFn: () => api<AuditResponse>(`/api/admin/audit?${qs.toString()}`),
  });
}

// ---------- Admin: V2 additions ----------

export function useAdminModels() {
  return useQuery({
    queryKey: qk.adminModels,
    queryFn: () => api<AdminModelsResponse>("/api/admin/models"),
  });
}

export function useGeminiUsage(enabled = true) {
  return useQuery({
    queryKey: qk.geminiUsage,
    queryFn: () => api<GeminiUsageResponse>("/api/admin/gemini/usage"),
    enabled,
  });
}

export interface AdminMemoriesParams {
  telegram_user_id?: number | null;
  limit?: number;
  offset?: number;
}

export function useAdminMemories(params: AdminMemoriesParams = {}) {
  const { telegram_user_id = null, limit = 50, offset = 0 } = params;
  const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (telegram_user_id !== null) qs.set("telegram_user_id", String(telegram_user_id));
  return useQuery({
    queryKey: [...qk.adminMemory, { telegram_user_id, limit, offset }] as const,
    queryFn: () => api<AdminMemoriesResponse>(`/api/admin/memory?${qs.toString()}`),
  });
}
