import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ApiError, setReauthHandler, setSessionToken } from "./client";
import { getInitData } from "../telegram/webapp";
import type { AuthResponse, TelegramUser } from "./types";

export type AuthStatus = "loading" | "ready" | "no-telegram" | "error";

export interface AuthState {
  status: AuthStatus;
  user: TelegramUser | null;
  isOwner: boolean;
  error: string | null;
  retry: () => void;
}

const initialState: Omit<AuthState, "retry"> = {
  status: "loading",
  user: null,
  isOwner: false,
  error: null,
};

const AuthContext = createContext<AuthState>({ ...initialState, retry: () => {} });

async function requestAuthToken(): Promise<AuthResponse> {
  const initData = getInitData();
  if (!initData) throw new ApiError(0, "Нет initData Telegram");

  let res: Response;
  try {
    res = await fetch("/api/auth/telegram", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ init_data: initData }),
    });
  } catch {
    throw new ApiError(0, "Нет соединения с сервером");
  }

  if (!res.ok) {
    let detail = `Ошибка авторизации (${res.status})`;
    try {
      const data = (await res.json()) as { detail?: unknown };
      if (typeof data.detail === "string" && data.detail) detail = data.detail;
    } catch {
      // оставляем общее сообщение
    }
    throw new ApiError(res.status, detail);
  }

  const data = (await res.json()) as AuthResponse;
  setSessionToken(data.session_token);
  return data;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<Omit<AuthState, "retry">>(initialState);
  const inFlight = useRef<Promise<AuthResponse> | null>(null);

  // Дедупликация параллельных auth-вызовов (StrictMode, 401-retry во время старта).
  const authenticate = useCallback((): Promise<AuthResponse> => {
    if (!inFlight.current) {
      inFlight.current = requestAuthToken().finally(() => {
        inFlight.current = null;
      });
    }
    return inFlight.current;
  }, []);

  // Хук повторной авторизации для client.ts (401 → новый токен).
  useEffect(() => {
    setReauthHandler(async () => (await authenticate()).session_token);
    return () => setReauthHandler(null);
  }, [authenticate]);

  const start = useCallback(async () => {
    setState({ status: "loading", user: null, isOwner: false, error: null });
    if (!getInitData()) {
      setState({ status: "no-telegram", user: null, isOwner: false, error: null });
      return;
    }
    try {
      const data = await authenticate();
      setState({ status: "ready", user: data.user, isOwner: data.is_owner, error: null });
    } catch (e) {
      const message = e instanceof Error ? e.message : "Неизвестная ошибка";
      setState({ status: "error", user: null, isOwner: false, error: message });
    }
  }, [authenticate]);

  useEffect(() => {
    void start();
  }, [start]);

  return (
    <AuthContext.Provider value={{ ...state, retry: () => void start() }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  return useContext(AuthContext);
}
