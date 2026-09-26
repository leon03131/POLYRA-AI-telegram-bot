import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
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
  WebMessagesResponse,
} from "./types";

/** Ключи кеша React Query. */
export const qk = {
  me: ["me"] as const,
  models: ["models"] as const,
  chats: ["chats"] as const,
  messages: (chatId: string) => ["chats", "messages", chatId] as const,
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
  include_archived?: boolean;
  /** false — не выполнять запрос (ленивая вкладка). */
  enabled?: boolean;
}

export const CHATS_PAGE_SIZE = 20;

/**
 * Чаты с offset-пагинацией (страницы накапливаются через fetchNextPage).
 * NB: backend ограничивает limit (cap 200), поэтому догрузка идёт offset'ом,
 * а не ростом limit — иначе после cap кнопка «Загрузить ещё» становилась вечной.
 */
export function useChats(params: ChatsParams = {}) {
  const { include_archived = false, enabled = true } = params;
  return useInfiniteQuery({
    queryKey: [...qk.chats, "list", { include_archived }] as const,
    queryFn: ({ pageParam }) => {
      const qs = new URLSearchParams({
        limit: String(CHATS_PAGE_SIZE),
        offset: String(pageParam),
      });
      if (include_archived) qs.set("include_archived", "true");
      return api<ChatsResponse>(`/api/chats?${qs.toString()}`);
    },
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((sum, p) => sum + p.chats.length, 0);
      return loaded < lastPage.total ? loaded : undefined;
    },
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

// ---------- web5: сообщения веб-чата ----------

export const MESSAGES_PAGE_SIZE = 50;

/** Страница истории web-чата: ASC-сообщения + фактический offset страницы. */
export interface ChatMessagesPage extends WebMessagesResponse {
  offset: number;
}

async function fetchMessagesPage(chatId: string, offset: number): Promise<ChatMessagesPage> {
  const qs = new URLSearchParams({
    limit: String(MESSAGES_PAGE_SIZE),
    offset: String(offset),
  });
  const res = await api<WebMessagesResponse>(`/api/chats/${chatId}/messages?${qs.toString()}`);
  return { ...res, offset };
}

/**
 * Последняя страница истории: probe offset=0 даёт total; короткий чат
 * (total ≤ limit) закрывается одним запросом, длинный — вторым запросом
 * хвоста. Защита от гонки (история сократилась между запросами): пустой
 * хвост при total>0 перечитывается с актуальным offset.
 */
async function fetchTailPage(chatId: string): Promise<ChatMessagesPage> {
  const probe = await fetchMessagesPage(chatId, 0);
  const tailOffset = probe.total - MESSAGES_PAGE_SIZE;
  if (tailOffset <= 0) return probe;
  const tail = await fetchMessagesPage(chatId, tailOffset);
  if (tail.messages.length === 0 && tail.total > 0) {
    return fetchMessagesPage(chatId, Math.max(0, tail.total - MESSAGES_PAGE_SIZE));
  }
  return tail;
}

/**
 * История сообщений web-чата (web5): изначально грузится ПОСЛЕДНЯЯ страница
 * (offset = max(0, total − limit)), «Загрузить ещё» — fetchPreviousPage
 * (prepend старых). pageParam −1 — режим tail (probe + хвост).
 * staleTime маленький: сообщения, отправленные из Telegram, должны быть
 * видны при каждом открытии чата.
 */
export function useChatMessages(chatId: string | undefined) {
  return useInfiniteQuery({
    queryKey: qk.messages(chatId ?? ""),
    queryFn: ({ pageParam }): Promise<ChatMessagesPage> => {
      if (!chatId) throw new Error("chatId is required");
      return pageParam < 0
        ? fetchTailPage(chatId)
        : fetchMessagesPage(chatId, pageParam as number);
    },
    initialPageParam: -1,
    // пагинация только назад («Загрузить ещё» вверх); вперёд страниц нет
    getNextPageParam: () => undefined,
    getPreviousPageParam: (firstPage) =>
      firstPage.offset > 0
        ? Math.max(0, firstPage.offset - MESSAGES_PAGE_SIZE)
        : undefined,
    enabled: !!chatId,
    staleTime: 3_000,
  });
}

export function useSettings() {
  return useQuery({ queryKey: qk.settings, queryFn: () => api<UserSettings>("/api/settings") });
}

export const MEMORIES_PAGE_SIZE = 50;

/** Память с offset-пагинацией (см. useChats). */
export function useMemories() {
  return useInfiniteQuery({
    queryKey: [...qk.memories, "list"] as const,
    queryFn: ({ pageParam }) => {
      const qs = new URLSearchParams({
        limit: String(MEMORIES_PAGE_SIZE),
        offset: String(pageParam),
      });
      return api<MemoriesResponse>(`/api/memory?${qs.toString()}`);
    },
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((sum, p) => sum + p.memories.length, 0);
      return loaded < lastPage.total ? loaded : undefined;
    },
  });
}

// ---------- Admin ----------

export function useAdminStats() {
  return useQuery({ queryKey: qk.adminStats, queryFn: () => api<AdminStats>("/api/admin/stats") });
}

export const ADMIN_USERS_PAGE_SIZE = 50;

/**
 * Пользователи (admin) с offset-пагинацией. Ответ не содержит total,
 * поэтому «есть ещё» — эвристика: последняя страница пришла полной
 * (ровно PAGE_SIZE). Если всего записей кратно PAGE_SIZE, будет один
 * лишний запрос, вернувший пустую страницу, — кнопка после этого скроется.
 */
export function useAdminUsers(query: string) {
  return useInfiniteQuery({
    queryKey: qk.adminUsers(query),
    queryFn: ({ pageParam }) => {
      const qs = new URLSearchParams({
        query,
        limit: String(ADMIN_USERS_PAGE_SIZE),
        offset: String(pageParam),
      });
      return api<AdminUsersResponse>(`/api/admin/users?${qs.toString()}`);
    },
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) => {
      if (lastPage.users.length < ADMIN_USERS_PAGE_SIZE) return undefined;
      return allPages.reduce((sum, p) => sum + p.users.length, 0);
    },
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

export const AUDIT_PAGE_SIZE = 50;

export interface AuditParams {
  action?: string;
}

/**
 * Audit-лог (admin) с offset-пагинацией (см. useChats).
 * round4-P1: backend ограничивает limit cap'ом 500 (admin_stats.py
 * _MAX_AUDIT_LIMIT), поэтому догрузка идёт offset'ом, а не ростом limit —
 * иначе после cap hasMore оставался true навсегда, а записи 501+ были недостижимы.
 */
export function useAudit(params: AuditParams = {}) {
  const { action = "" } = params;
  return useInfiniteQuery({
    queryKey: [...qk.audit, "list", { action }] as const,
    queryFn: ({ pageParam }) => {
      const qs = new URLSearchParams({
        limit: String(AUDIT_PAGE_SIZE),
        offset: String(pageParam),
      });
      if (action) qs.set("action", action);
      return api<AuditResponse>(`/api/admin/audit?${qs.toString()}`);
    },
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((sum, p) => sum + p.entries.length, 0);
      return loaded < lastPage.total ? loaded : undefined;
    },
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

export const ADMIN_MEMORIES_PAGE_SIZE = 50;

export interface AdminMemoriesParams {
  telegram_user_id?: number | null;
}

/**
 * Память пользователей (admin) с offset-пагинацией (см. useChats).
 * round4-P1: backend ограничивает limit cap'ом 200 (admin_memory.py
 * _MAX_LIMIT), поэтому догрузка идёт offset'ом, а не ростом limit —
 * иначе после cap hasMore оставался true навсегда.
 */
export function useAdminMemories(params: AdminMemoriesParams = {}) {
  const { telegram_user_id = null } = params;
  return useInfiniteQuery({
    queryKey: [...qk.adminMemory, "list", { telegram_user_id }] as const,
    queryFn: ({ pageParam }) => {
      const qs = new URLSearchParams({
        limit: String(ADMIN_MEMORIES_PAGE_SIZE),
        offset: String(pageParam),
      });
      if (telegram_user_id !== null) qs.set("telegram_user_id", String(telegram_user_id));
      return api<AdminMemoriesResponse>(`/api/admin/memory?${qs.toString()}`);
    },
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((sum, p) => sum + p.memories.length, 0);
      return loaded < lastPage.total ? loaded : undefined;
    },
  });
}
