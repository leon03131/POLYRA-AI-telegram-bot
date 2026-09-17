import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import type {
  AdminStats,
  AdminUsersResponse,
  AlibabaStatus,
  AuditResponse,
  ChatsResponse,
  GeminiProjectsResponse,
  GeminiQuotasResponse,
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
  geminiProjects: ["admin", "gemini", "projects"] as const,
  geminiQuotas: ["admin", "gemini", "quotas"] as const,
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

export function useChats() {
  return useQuery({ queryKey: qk.chats, queryFn: () => api<ChatsResponse>("/api/chats") });
}

export function useSettings() {
  return useQuery({ queryKey: qk.settings, queryFn: () => api<UserSettings>("/api/settings") });
}

export function useMemories() {
  return useQuery({ queryKey: qk.memories, queryFn: () => api<MemoriesResponse>("/api/memory") });
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

export function useAudit(limit = 100) {
  return useQuery({
    queryKey: [...qk.audit, limit] as const,
    queryFn: () => api<AuditResponse>(`/api/admin/audit?limit=${limit}`),
  });
}
