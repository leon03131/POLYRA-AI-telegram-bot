// web5: боковое меню чата (drawer, 85% ширины) — список чатов + «Новый чат».
//
// Затемнение rgba(0,0,0,.6) закрывает по тапу; текущий чат подсвечен.
// Иконки — inline SVG (stroke 1.6), без библиотек.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { qk, useChats } from "../api/hooks";
import type { Chat } from "../api/types";
import { formatDateTime } from "../utils";

interface SideMenuProps {
  open: boolean;
  /** id чата, открытого на экране (подсветка строки). */
  currentChatId: string;
  onClose: () => void;
}

function PlusIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </svg>
  );
}

function ChatRow({ chat, current, onClick }: { chat: Chat; current: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      className={"web5-chat-row" + (current ? " current" : "")}
      onClick={onClick}
    >
      <span className="web5-chat-title">{chat.title ?? "Без названия"}</span>
      <span className="web5-chat-sub">
        {formatDateTime(chat.updated_at)}
        {chat.model_id ? ` · ${chat.model_id}` : ""}
      </span>
    </button>
  );
}

export function SideMenu({ open, currentChatId, onClose }: SideMenuProps) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const chatsQ = useChats();

  const createChat = useMutation({
    mutationFn: () => api<{ chat: Chat }>("/api/chats", { method: "POST", body: {} }),
    onSuccess: ({ chat }) => {
      void qc.invalidateQueries({ queryKey: qk.chats });
      onClose();
      navigate(`/chats/${chat.id}`);
    },
  });

  if (!open) return null;

  const chats = chatsQ.data?.pages.flatMap((p) => p.chats) ?? [];
  const pages = chatsQ.data?.pages ?? [];
  const total = pages.length > 0 ? pages[pages.length - 1].total : 0;
  const hasMore = chatsQ.hasNextPage ?? false;

  return (
    <div className="web5-drawer-root" aria-hidden={!open}>
      <div className="web5-drawer-backdrop" onClick={onClose} />
      <aside className="web5-drawer">
        <div className="web5-drawer-header">Чаты</div>
        <button
          type="button"
          className="web5-new-chat"
          disabled={createChat.isPending}
          onClick={() => createChat.mutate()}
        >
          <PlusIcon />
          Новый чат
        </button>
        {createChat.error && (
          <div className="web5-drawer-error">{errorMessage(createChat.error)}</div>
        )}
        <div className="web5-drawer-list">
          {chatsQ.isLoading ? (
            <div className="web5-drawer-hint">Загружаю…</div>
          ) : chatsQ.isError ? (
            <div className="web5-drawer-error">{errorMessage(chatsQ.error)}</div>
          ) : chats.length === 0 ? (
            <div className="web5-drawer-hint">Чатов пока нет</div>
          ) : (
            <>
              {chats.map((c) => (
                <ChatRow
                  key={c.id}
                  chat={c}
                  current={c.id === currentChatId}
                  onClick={() => {
                    onClose();
                    navigate(`/chats/${c.id}`);
                  }}
                />
              ))}
              {hasMore && (
                <button
                  type="button"
                  className="web5-load-more"
                  disabled={chatsQ.isFetchingPreviousPage}
                  onClick={() => void chatsQ.fetchPreviousPage()}
                >
                  {chatsQ.isFetchingPreviousPage
                    ? "Загружаю…"
                    : `Загрузить ещё (${chats.length} из ${total})`}
                </button>
              )}
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
