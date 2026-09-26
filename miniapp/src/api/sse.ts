// web5: SSE-стриминг ответа модели — POST /api/chats/{id}/messages (text/event-stream).
//
// EventSource не подходит (POST + Authorization), поэтому fetch с теми же
// заголовками, что api() из client.ts (Bearer из session; 401 → ОДИН
// повторный auth и повтор запроса), тело читается response.body.getReader()'ом
// и кормится инкрементальным парсером кадров «event: <name>\ndata: <json>\n\n».
// Контракт бэкенда: app/api/routes/chat_web.py.

import { ApiError, getSessionToken, reauthenticate, setSessionToken } from "./client";

/** Типизированное SSE-событие web-чата (кадры meta/delta/error/done/cancelled). */
export type ChatStreamEvent =
  | { type: "meta"; chatId: string; modelHint: string | null }
  | { type: "delta"; text: string }
  | { type: "error"; message: string }
  | { type: "done"; messageId: string }
  | { type: "cancelled"; messageId: string };

/**
 * Исход стрима.
 * - final: ровно один done/cancelled (стрим корректно завершён).
 * - error: пользовательская ошибка — error-событие (user-сообщение уже
 *   персистентно) либо HTTP-ошибка ДО старта стрима (не сохранено ничего).
 * - interrupted: обрыв без финала → тост «Соединение прервано» + дозагрузка
 *   истории (генерация на сервере продолжает жить и будет сохранена).
 * - aborted: прервано клиентом (unmount).
 */
export type ChatStreamResult =
  | { kind: "final"; status: "done" | "cancelled"; messageId: string | null }
  | { kind: "error"; message: string; userPersisted: boolean }
  | { kind: "interrupted" }
  | { kind: "aborted" };

interface SseFrame {
  event: string;
  data: string;
}

/** Разобрать один кадр (без завершающего \n\n): «event:…\ndata:…». */
function parseFrame(raw: string): SseFrame | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice("event:".length).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice("data:".length).trimStart());
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}

/**
 * Инкрементальный парсер SSE-кадров: feed(chunk) вызывает onFrame для каждого
 * готового кадра; незавершённый хвост живёт в буфере. \r\n нормализуется
 * ПОСЛЕ склейки с буфером, чтобы разделитель \n\n не ломался на границе чанков.
 */
export function createSseParser(onFrame: (frame: SseFrame) => void): (chunk: string) => void {
  let buffer = "";
  return (chunk: string): void => {
    buffer = (buffer + chunk).replace(/\r\n/g, "\n");
    let sep = buffer.indexOf("\n\n");
    while (sep !== -1) {
      const raw = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const frame = parseFrame(raw);
      if (frame !== null) onFrame(frame);
      sep = buffer.indexOf("\n\n");
    }
  };
}

function safeJson(text: string): Record<string, unknown> | null {
  try {
    const value: unknown = JSON.parse(text);
    return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function isAbortError(e: unknown): boolean {
  return e instanceof Error && e.name === "AbortError";
}

/** Открыть SSE-запрос; 401 → один re-auth + повтор (образец api() в client.ts). */
async function fetchStream(chatId: string, text: string, signal: AbortSignal): Promise<Response> {
  const headers: Record<string, string> = {
    Accept: "text/event-stream",
    "Content-Type": "application/json",
  };
  const token = getSessionToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const body = JSON.stringify({ text });
  const url = `/api/chats/${chatId}/messages`;

  let res: Response;
  try {
    res = await fetch(url, { method: "POST", headers, body, signal });
  } catch (e) {
    if (isAbortError(e)) throw e;
    throw new ApiError(0, "network error");
  }

  if (res.status === 401) {
    const fresh = await reauthenticate();
    setSessionToken(fresh);
    try {
      res = await fetch(url, {
        method: "POST",
        headers: { ...headers, Authorization: `Bearer ${fresh}` },
        body,
        signal,
      });
    } catch (e) {
      if (isAbortError(e)) throw e;
      throw new ApiError(0, "network error");
    }
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
  if (res.body === null) throw new ApiError(0, "Пустой ответ сервера");
  return res;
}

/**
 * Отправить сообщение и прочитать SSE-стрим ответа.
 * onEvent вызывается по мере кадров; возврат — итог стрима. Отмена (signal)
 * НЕ отменяет генерацию на сервере — ответ будет сохранён (контракт web5).
 */
export async function streamChatMessage(
  chatId: string,
  text: string,
  onEvent: (event: ChatStreamEvent) => void,
  signal: AbortSignal,
): Promise<ChatStreamResult> {
  let res: Response;
  try {
    res = await fetchStream(chatId, text, signal);
  } catch (e) {
    if (isAbortError(e)) return { kind: "aborted" };
    if (e instanceof ApiError && e.status === 0) return { kind: "interrupted" };
    // HTTP-ошибка до старта стрима (400/403/404/503…): user-сообщение не сохранено.
    return {
      kind: "error",
      message: e instanceof ApiError ? e.message : "Не удалось отправить сообщение",
      userPersisted: false,
    };
  }

  // Состояние стрима: мутабельный объект, замкнутый в парсере (CFA-safe).
  const seen: {
    error: string | null;
    status: "done" | "cancelled" | null;
    messageId: string | null;
  } = { error: null, status: null, messageId: null };

  const feed = createSseParser((frame) => {
    if (frame.event === "delta") {
      const data = safeJson(frame.data);
      if (data !== null && typeof data.text === "string") {
        onEvent({ type: "delta", text: data.text });
      }
    } else if (frame.event === "error") {
      const data = safeJson(frame.data);
      const message =
        data !== null && typeof data.message === "string" && data.message !== ""
          ? data.message
          : "Ошибка генерации";
      seen.error = message;
      onEvent({ type: "error", message });
    } else if (frame.event === "done" || frame.event === "cancelled") {
      const data = safeJson(frame.data);
      const messageId = data !== null && typeof data.message_id === "string" ? data.message_id : null;
      const status = frame.event as "done" | "cancelled";
      seen.status = status;
      seen.messageId = messageId;
      onEvent({ type: status, messageId: messageId ?? "" });
    } else if (frame.event === "meta") {
      const data = safeJson(frame.data);
      onEvent({
        type: "meta",
        chatId: data !== null && typeof data.chat_id === "string" ? data.chat_id : chatId,
        modelHint: data !== null && typeof data.model_hint === "string" ? data.model_hint : null,
      });
    }
  });

  const body = res.body;
  if (body === null) return { kind: "interrupted" };
  const reader = body.getReader();
  const decoder = new TextDecoder();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      feed(decoder.decode(value, { stream: true }));
    }
  } catch (e) {
    if (isAbortError(e)) return { kind: "aborted" };
    return { kind: "interrupted" };
  }

  if (seen.status !== null) {
    return { kind: "final", status: seen.status, messageId: seen.messageId };
  }
  if (seen.error !== null) {
    return { kind: "error", message: seen.error, userPersisted: true };
  }
  return { kind: "interrupted" };
}
