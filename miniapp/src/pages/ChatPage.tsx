// web5: экран чата (route /chats/:id) — мобильный AI-чат в духе Qwen.
//
// Поток: user-сообщение оптимистично → POST /messages открывает SSE-стрим →
// дельты конкатенируются в растущий assistant-блок («● ● ●» до первой дельты)
// → done/cancelled/error финализируют → инвалидация кэша истории перекрывает
// optimistic-состояние реальными сообщениями (merge по id/timestamp).
// Обрыв стрима → тост «Соединение прервано» + дозагрузка истории (генерация
// на сервере не отменяется — контракт web5).

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { qk, useChat, useChatMessages } from "../api/hooks";
import { streamChatMessage } from "../api/sse";
import type { ChatStreamEvent, ChatStreamResult } from "../api/sse";
import type { WebMessage } from "../api/types";
import { Markdown } from "../components/Markdown";
import { SideMenu } from "../components/SideMenu";
import { Spinner } from "../components";

// ---------- Optimistic-состояние стриминга ----------

type GenEnd = "done" | "cancelled" | "error" | "break";

interface GenState {
  localId: string;
  /** Date.now() в момент отправки (merge с историей по created_at, ±skew). */
  startedAt: number;
  userText: string;
  /** HTTP-ошибка до старта стрима: user-сообщение не сохранено — не показывать. */
  hideUser: boolean;
  assistantText: string;
  /** id финального assistant-сообщения из done/cancelled. */
  assistantId: string | null;
  ended: GenEnd | null;
  errorMessage: string | null;
  /** Снапшоты последних id на момент отправки — чтобы НЕ покрываться старыми
   *  сообщениями с тем же текстом (повторная отправка «ещё»/«ок»). */
  prevLastUserId: string | null;
  prevLastAssistantId: string | null;
}

function lastIdOf(history: WebMessage[], role: WebMessage["role"]): string | null {
  for (let i = history.length - 1; i >= 0; i -= 1) {
    if (history[i].role === role) return history[i].id;
  }
  return null;
}

/** Покрыт ли optimistic-хвост реальной историей (для merge при рендере). */
function genCoverage(gen: GenState, history: WebMessage[]): { user: boolean; assistant: boolean } {
  const started = gen.startedAt - 15_000; // допуск на расхождение часов
  const userCovered =
    gen.hideUser ||
    history.some(
      (m) =>
        m.role === "user" &&
        m.id !== gen.prevLastUserId &&
        m.text === gen.userText &&
        Date.parse(m.created_at) >= started,
    );
  let assistantCovered = false;
  if (gen.assistantId !== null) {
    assistantCovered = history.some((m) => m.id === gen.assistantId);
  } else if (gen.ended === "break") {
    // Обрыв: генерация продолжается на сервере; assistant-сообщение появится
    // в истории после персистенса (новое, не из снапшота, после отправки).
    assistantCovered = history.some(
      (m) =>
        m.role === "assistant" &&
        m.id !== gen.prevLastAssistantId &&
        Date.parse(m.created_at) >= started,
    );
  }
  return { user: userCovered, assistant: assistantCovered };
}

// ---------- Иконки (inline SVG, stroke 1.6) ----------

function MenuIcon() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
      <path d="M4 7h16" />
      <path d="M4 12h16" />
      <path d="M4 17h16" />
    </svg>
  );
}

function SettingsIcon() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
      <path d="M4 8h5" />
      <path d="M13 8h7" />
      <path d="M4 16h9" />
      <path d="M17 16h3" />
      <circle cx="11" cy="8" r="2.4" fill="#000" />
      <circle cx="15" cy="16" r="2.4" fill="#000" />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 19V5" />
      <path d="M5.5 11.5 12 5l6.5 6.5" />
    </svg>
  );
}

function StopIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <rect x="6.5" y="6.5" width="11" height="11" rx="2.5" />
    </svg>
  );
}

function TypingDots() {
  return (
    <div className="web5-dots" aria-label="Модель отвечает">
      <span />
      <span />
      <span />
    </div>
  );
}

// ---------- Сообщения истории ----------

function HistoryMessage({ message }: { message: WebMessage }) {
  if (message.role === "user") {
    return (
      <div className="web5-msg web5-user-row">
        <div className="web5-user-bubble">{message.text}</div>
        {message.has_image && <div className="web5-photo-hint">📷 Фото</div>}
      </div>
    );
  }
  return (
    <div className="web5-msg web5-assistant">
      {message.text !== "" && <Markdown text={message.text} />}
      {message.status === "cancelled" && <div className="web5-paddle">Ответ прерван</div>}
      {message.status === "failed" && <div className="web5-paddle">Ответ не сформирован</div>}
    </div>
  );
}

// ---------- Экран ----------

const TEXTAREA_MAX_HEIGHT = 110; // ~5 строк

export function ChatPage() {
  const { id } = useParams<{ id: string }>();
  const chatId = id ?? "";
  const navigate = useNavigate();
  const qc = useQueryClient();

  const chatQ = useChat(chatId);
  const messagesQ = useChatMessages(chatId);

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [input, setInput] = useState("");
  const [gens, setGens] = useState<GenState[]>([]);
  const [toast, setToast] = useState<string | null>(null);

  const gensRef = useRef<GenState[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const toastTimer = useRef<number | null>(null);
  /** Актуальная история для снапшотов в момент отправки (стейл-клоуны). */
  const historyRef = useRef<WebMessage[]>([]);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const nearBottomRef = useRef(true);
  const initialScrollRef = useRef(false);
  const pagesCountRef = useRef(0);
  const scrollHeightRef = useRef(0);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  /** История (ASC, дедуп по id — страницы могут перекрываться при refetch). */
  const history = useMemo(() => {
    const seen = new Set<string>();
    const out: WebMessage[] = [];
    for (const page of messagesQ.data?.pages ?? []) {
      for (const m of page.messages) {
        if (seen.has(m.id)) continue;
        seen.add(m.id);
        out.push(m);
      }
    }
    return out;
  }, [messagesQ.data]);
  historyRef.current = history;

  const hasOlder = messagesQ.hasPreviousPage ?? false;
  const streaming = gens.some((g) => g.ended === null);

  const showToast = useCallback((text: string) => {
    setToast(text);
    if (toastTimer.current !== null) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 3200);
  }, []);

  /** Синхронный mirror стейта в ref (актуальный gens внутри async-потока). */
  const updateGen = useCallback((localId: string, patch: (g: GenState) => GenState) => {
    gensRef.current = gensRef.current.map((g) => (g.localId === localId ? patch(g) : g));
    setGens(gensRef.current);
  }, []);

  // unmount → прервать стрим (генерация на сервере продолжит и сохранится)
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      if (toastTimer.current !== null) window.clearTimeout(toastTimer.current);
    };
  }, []);

  // смена чата (route /chats/:id без ремонта компонента): прервать стрим
  // (генерация на сервере продолжит и сохранится), сбросить optimistic-хвост,
  // инвалидировать историю прошлого чата и сбросить прокрутку.
  const prevChatIdRef = useRef(chatId);
  useEffect(() => {
    if (prevChatIdRef.current !== chatId) {
      const prev = prevChatIdRef.current;
      prevChatIdRef.current = chatId;
      abortRef.current?.abort();
      gensRef.current = [];
      setGens([]);
      void qc.invalidateQueries({ queryKey: qk.messages(prev) });
    }
    initialScrollRef.current = false;
    nearBottomRef.current = true;
  }, [chatId, qc]);

  // авто-даунскролл: первичный — всегда; далее — только если юзер у низа (~80px)
  useEffect(() => {
    const el = scrollRef.current;
    if (el === null) return;
    if (!initialScrollRef.current && (messagesQ.data !== undefined || gens.length > 0)) {
      initialScrollRef.current = true;
      nearBottomRef.current = true;
      el.scrollTop = el.scrollHeight;
      return;
    }
    if (nearBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [history, gens, messagesQ.data]);

  // «Загрузить ещё»: prepend старых — удержать вьюпорт (компенсация высоты)
  const pagesCount = messagesQ.data?.pages.length ?? 0;
  useEffect(() => {
    if (pagesCount > pagesCountRef.current) {
      const el = scrollRef.current;
      if (el !== null && scrollHeightRef.current > 0) {
        el.scrollTop += el.scrollHeight - scrollHeightRef.current;
      }
    }
    pagesCountRef.current = pagesCount;
  }, [pagesCount]);

  const loadOlder = useCallback(() => {
    const el = scrollRef.current;
    scrollHeightRef.current = el !== null ? el.scrollHeight : 0;
    void messagesQ.fetchPreviousPage();
  }, [messagesQ]);

  // покрытие optimistic-хвоста историей → сброс покрытых gen'ов
  useEffect(() => {
    const keep = gensRef.current.filter((g) => {
      if (g.ended === null) return true; // стрим ещё идёт
      const cov = genCoverage(g, history);
      if (g.ended === "error") return true; // плашка/partial живут сессию
      return !(cov.user && cov.assistant);
    });
    if (keep.length !== gensRef.current.length) {
      gensRef.current = keep;
      setGens(keep);
    }
  }, [history]);

  // ---------- Отправка ----------

  const send = useCallback(
    async (raw: string): Promise<void> => {
      const text = raw.trim();
      if (text === "" || gensRef.current.some((g) => g.ended === null)) return;
      setInput("");
      nearBottomRef.current = true;

      const localId = `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const gen: GenState = {
        localId,
        startedAt: Date.now(),
        userText: text,
        hideUser: false,
        assistantText: "",
        assistantId: null,
        ended: null,
        errorMessage: null,
        prevLastUserId: lastIdOf(historyRef.current, "user"),
        prevLastAssistantId: lastIdOf(historyRef.current, "assistant"),
      };
      gensRef.current = [...gensRef.current, gen];
      setGens(gensRef.current);

      const onEvent = (ev: ChatStreamEvent): void => {
        if (ev.type === "delta") {
          updateGen(localId, (g) => ({ ...g, assistantText: g.assistantText + ev.text }));
        } else if (ev.type === "done" || ev.type === "cancelled") {
          updateGen(localId, (g) => ({
            ...g,
            ended: ev.type,
            assistantId: ev.messageId !== "" ? ev.messageId : g.assistantId,
          }));
        } else if (ev.type === "error") {
          updateGen(localId, (g) => ({ ...g, ended: "error", errorMessage: ev.message }));
        }
        // meta: модель известна из настроек чата — UI не использует
      };

      const applyResult = (result: ChatStreamResult): void => {
        if (result.kind === "final") {
          updateGen(localId, (g) => ({
            ...g,
            ended: result.status,
            assistantId: result.messageId ?? g.assistantId,
          }));
        } else if (result.kind === "error") {
          updateGen(localId, (g) => ({
            ...g,
            ended: "error",
            errorMessage: result.message,
            hideUser: result.userPersisted ? g.hideUser : true,
          }));
          if (!result.userPersisted) setInput(text); // вернуть текст для повтора
        } else if (result.kind === "interrupted") {
          updateGen(localId, (g) => ({ ...g, ended: "break" }));
          showToast("Соединение прервано");
        }
        // kind === "aborted" (unmount) — тихо
        if (result.kind !== "aborted") {
          void qc.invalidateQueries({ queryKey: qk.messages(chatId) });
          void qc.invalidateQueries({ queryKey: qk.chats });
        }
      };

      const controller = new AbortController();
      abortRef.current = controller;
      try {
        applyResult(await streamChatMessage(chatId, text, onEvent, controller.signal));
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
      }
    },
    [chatId, qc, showToast, updateGen],
  );

  const stop = useCallback(async (): Promise<void> => {
    if (!gensRef.current.some((g) => g.ended === null)) return;
    try {
      await api<{ stopped: boolean }>(`/api/chats/${chatId}/stop`, { method: "POST" });
    } catch (e) {
      showToast(errorMessage(e));
    }
    // финал придёт SSE-событием cancelled; стрим закроется сам
  }, [chatId, showToast]);

  // ---------- Инпут ----------

  const resizeTextarea = useCallback(() => {
    const el = taRef.current;
    if (el === null) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, TEXTAREA_MAX_HEIGHT)}px`;
  }, []);

  useEffect(() => {
    resizeTextarea();
  }, [input, resizeTextarea]);

  const onKeyDown = (e: ReactKeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (!streaming && input.trim() !== "") void send(input);
    }
  };

  const onFeedScroll = (): void => {
    const el = scrollRef.current;
    if (el === null) return;
    nearBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  // ---------- Рендер ----------

  if (messagesQ.isError) {
    return (
      <div className="web5-root">
        <header className="web5-topbar">
          <button type="button" className="web5-icon-btn" onClick={() => navigate("/chats")} aria-label="К списку чатов">
            <MenuIcon />
          </button>
          <div className="web5-title">Чат</div>
          <span className="web5-icon-btn" />
        </header>
        <div className="web5-error-page">
          <div className="web5-empty-title">Не удалось загрузить чат</div>
          <p className="web5-empty-sub">{errorMessage(messagesQ.error)}</p>
          <button type="button" className="web5-error-retry" onClick={() => void messagesQ.refetch()}>
            Повторить
          </button>
          <button type="button" className="web5-error-retry" onClick={() => navigate("/chats")}>
            К списку чатов
          </button>
        </div>
      </div>
    );
  }

  const title = chatQ.data?.chat.title ?? (chatQ.isLoading ? "" : "Без названия");
  const showGreeting =
    !messagesQ.isLoading && history.length === 0 && gens.length === 0;

  return (
    <div className="web5-root">
      <header className="web5-topbar">
        <button
          type="button"
          className="web5-icon-btn"
          onClick={() => setDrawerOpen(true)}
          aria-label="Меню"
        >
          <MenuIcon />
        </button>
        <div className="web5-title">{title}</div>
        <button
          type="button"
          className="web5-icon-btn"
          onClick={() => navigate(`/chats/${chatId}/settings`)}
          aria-label="Настройки чата"
        >
          <SettingsIcon />
        </button>
      </header>

      <div className="web5-feed" ref={scrollRef} onScroll={onFeedScroll}>
        <div className="web5-feed-inner">
          {messagesQ.isLoading && (
            <div className="web5-loading">
              <Spinner />
            </div>
          )}

          {hasOlder && (
            <button
              type="button"
              className="web5-load-more"
              disabled={messagesQ.isFetchingPreviousPage}
              onClick={loadOlder}
            >
              {messagesQ.isFetchingPreviousPage ? "Загружаю…" : "Загрузить ещё"}
            </button>
          )}

          {history.map((m) => (
            <HistoryMessage key={m.id} message={m} />
          ))}

          {gens.map((g) => {
            const cov = genCoverage(g, history);
            return (
              <Fragment key={g.localId}>
                {!cov.user && (
                  <div className="web5-msg web5-user-row">
                    <div className="web5-user-bubble">{g.userText}</div>
                  </div>
                )}
                {!cov.assistant && (g.assistantText !== "" || g.ended === null) && (
                  <div className="web5-msg web5-assistant">
                    {g.assistantText === "" ? (
                      <TypingDots />
                    ) : (
                      <>
                        <Markdown text={g.assistantText} />
                        {g.ended === null && <span className="web5-caret" />}
                      </>
                    )}
                    {g.ended === "cancelled" && <div className="web5-paddle">Ответ прерван</div>}
                  </div>
                )}
                {g.errorMessage !== null && (
                  <div className="web5-msg web5-error-plate" role="alert">{g.errorMessage}</div>
                )}
              </Fragment>
            );
          })}
        </div>

        {showGreeting && (
          <div className="web5-empty">
            <div className="web5-empty-title">Чем помочь?</div>
            <div className="web5-empty-sub">Спросите что угодно — отвечу прямо здесь</div>
          </div>
        )}
      </div>

      <div className="web5-input-wrap">
        <form
          className="web5-input"
          onSubmit={(e) => {
            e.preventDefault();
            if (!streaming && input.trim() !== "") void send(input);
          }}
        >
          <textarea
            ref={taRef}
            rows={1}
            value={input}
            placeholder="Сообщение…"
            disabled={streaming}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
          />
          {streaming ? (
            <button type="button" className="web5-send web5-stop" onClick={() => void stop()} aria-label="Остановить генерацию">
              <StopIcon />
            </button>
          ) : (
            <button type="submit" className="web5-send" disabled={input.trim() === ""} aria-label="Отправить">
              <SendIcon />
            </button>
          )}
        </form>
      </div>

      {toast !== null && <div className="web5-toast">{toast}</div>}

      <SideMenu open={drawerOpen} currentChatId={chatId} onClose={() => setDrawerOpen(false)} />
    </div>
  );
}
