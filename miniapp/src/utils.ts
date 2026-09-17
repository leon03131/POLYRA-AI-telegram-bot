// Небольшие форматтеры и мапперы для UI.

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** ISO → значение для <input type="datetime-local"> (в локальной таймзоне). */
export function toInputDateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Значение datetime-local → ISO string; пустая строка → null. */
export function fromInputDateTime(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

export function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("ru-RU");
}

/** Пустая строка → null, иначе число (null если не парсится). */
export function numOrNull(value: string): number | null {
  const t = value.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

const THINKING_LABELS: Record<string, string> = {
  off: "Выкл",
  none: "Нет",
  auto: "Авто",
  minimal: "Минимальный",
  low: "Низкий",
  medium: "Средний",
  high: "Высокий",
  max: "Максимальный",
};

export function thinkingLabel(mode: string): string {
  return THINKING_LABELS[mode] ?? mode;
}

const WEB_MODE_LABELS: Record<string, string> = {
  off: "Выкл",
  auto: "Авто",
  on: "Вкл",
};

export function webModeLabel(mode: string): string {
  return WEB_MODE_LABELS[mode] ?? mode;
}

export type ChipTone = "default" | "ok" | "warn" | "err" | "accent";

export function healthTone(status: string): ChipTone {
  const s = status.toLowerCase();
  if (s === "healthy" || s === "ok" || s === "active") return "ok";
  if (s === "cooldown" || s === "degraded" || s === "suspended") return "warn";
  if (s === "error" || s === "failed" || s === "revoked" || s === "banned" || s === "expired") return "err";
  return "default";
}

const GRANT_STATUS_LABELS: Record<string, string> = {
  active: "активен",
  suspended: "приостановлен",
  revoked: "отозван",
  expired: "истёк",
};

export function grantStatusLabel(status: string | null | undefined): string {
  if (!status) return "нет доступа";
  return GRANT_STATUS_LABELS[status] ?? status;
}

const USER_STATUS_LABELS: Record<string, string> = {
  active: "активен",
  banned: "забанен",
};

export function userStatusLabel(status: string): string {
  return USER_STATUS_LABELS[status] ?? status;
}
