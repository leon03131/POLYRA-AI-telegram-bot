// Fetch-wrapper: Bearer-токен (память + sessionStorage), 401 → один повторный auth.

const TOKEN_STORAGE_KEY = "aibot.session_token";

let memoryToken: string | null = null;
let reauthHandler: (() => Promise<string>) | null = null;
let reauthPromise: Promise<string> | null = null;

function readStoredToken(): string | null {
  try {
    return window.sessionStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredToken(token: string | null): void {
  try {
    if (token) window.sessionStorage.setItem(TOKEN_STORAGE_KEY, token);
    else window.sessionStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    // sessionStorage может быть недоступен — работаем с токеном в памяти
  }
}

export function getSessionToken(): string | null {
  return memoryToken ?? readStoredToken();
}

export function setSessionToken(token: string | null): void {
  memoryToken = token;
  writeStoredToken(token);
}

/** Регистрируется AuthProvider'ом: как получить новый session_token по initData. */
export function setReauthHandler(handler: (() => Promise<string>) | null): void {
  reauthHandler = handler;
  reauthPromise = null;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Человекочитаемое сообщение об ошибке (detail из ответа FastAPI). */
export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 0) return "Нет соединения с сервером";
    if (e.status === 401) return "Сессия истекла — обновите страницу";
    if (e.status === 403) return "Недостаточно прав";
    return e.message;
  }
  if (e instanceof Error) return e.message;
  return "Неизвестная ошибка";
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
}

async function rawRequest<T>(path: string, options: RequestOptions): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = getSessionToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let body: string | undefined;
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  let res: Response;
  try {
    res = await fetch(path, { method: options.method ?? "GET", headers, body });
  } catch {
    throw new ApiError(0, "network error");
  }

  if (!res.ok) {
    let detail = `Ошибка запроса (${res.status})`;
    try {
      const data = (await res.json()) as { detail?: unknown };
      if (typeof data.detail === "string" && data.detail) detail = data.detail;
    } catch {
      // тело не JSON — оставляем общее сообщение
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

async function reauthenticate(): Promise<string> {
  if (!reauthHandler) throw new ApiError(401, "Сессия истекла");
  if (!reauthPromise) {
    reauthPromise = reauthHandler().finally(() => {
      reauthPromise = null;
    });
  }
  return reauthPromise;
}

/**
 * Базовый метод API. baseURL "" (тот же origin; в dev — vite proxy на 127.0.0.1:8080).
 * При 401 выполняет ОДИН повторный auth и повторяет запрос один раз.
 */
export async function api<T>(path: string, options: RequestOptions = {}, allowRetry = true): Promise<T> {
  try {
    return await rawRequest<T>(path, options);
  } catch (e) {
    if (e instanceof ApiError && e.status === 401 && allowRetry && reauthHandler) {
      const token = await reauthenticate();
      setSessionToken(token);
      return rawRequest<T>(path, options);
    }
    throw e;
  }
}
