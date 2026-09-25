import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { qk, useChats } from "../api/hooks";
import type { Chat } from "../api/types";
import {
  Button,
  ConfirmDialog,
  EmptyState,
  Input,
  Modal,
  Section,
  Spinner,
} from "../components";
import { formatDateTime } from "../utils";

interface ChatRowProps {
  chat: Chat;
  disabled: boolean;
  onOpen: (chat: Chat) => void;
  onRename: (chat: Chat) => void;
  onArchive: (chat: Chat) => void;
  onDelete: (chat: Chat) => void;
}

function ChatRow({ chat, disabled, onOpen, onRename, onArchive, onDelete }: ChatRowProps) {
  const archived = chat.archived_at !== null;
  return (
    <div
      className={"list-row" + (archived ? "" : " clickable")}
      onClick={() => {
        if (!archived && !disabled) onOpen(chat);
      }}
    >
      <div className={"radio" + (chat.is_current ? " on" : "")}>{chat.is_current ? "●" : "○"}</div>
      <div className="list-row-main">
        <div className="list-row-title">{chat.title ?? "Без названия"}</div>
        <div className="list-row-subtitle">
          {formatDateTime(chat.updated_at)}
          {chat.model_id ? ` · ${chat.model_id}` : ""}
        </div>
      </div>
      <div className="row-actions" onClick={(e) => e.stopPropagation()}>
        <button
          type="button"
          className="icon-btn"
          title="Переименовать"
          disabled={disabled}
          onClick={() => onRename(chat)}
        >
          ✏️
        </button>
        <button
          type="button"
          className="icon-btn"
          title={archived ? "Разархивировать" : "Архивировать"}
          disabled={disabled}
          onClick={() => onArchive(chat)}
        >
          {archived ? "📤" : "📥"}
        </button>
        <button
          type="button"
          className="icon-btn"
          title="Удалить"
          disabled={disabled}
          onClick={() => onDelete(chat)}
        >
          🗑
        </button>
      </div>
    </div>
  );
}

export function ChatsPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [archiveOpen, setArchiveOpen] = useState(false);

  const chatsQ = useChats();
  const archivedQ = useChats({ include_archived: true, enabled: archiveOpen });
  const invalidate = () => void qc.invalidateQueries({ queryKey: qk.chats });

  const [createOpen, setCreateOpen] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [renameTarget, setRenameTarget] = useState<Chat | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Chat | null>(null);

  const openChat = useMutation({
    mutationFn: (id: string) => api<{ ok: boolean }>(`/api/chats/${id}/open`, { method: "POST" }),
    onSuccess: () => {
      invalidate();
      navigate("/");
    },
  });

  const createChat = useMutation({
    mutationFn: (title: string) =>
      api<{ chat: Chat }>("/api/chats", { method: "POST", body: title ? { title } : {} }),
    onSuccess: () => {
      invalidate();
      setCreateOpen(false);
      setNewTitle("");
      navigate("/");
    },
  });

  const renameChat = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      api<{ chat: Chat }>(`/api/chats/${id}`, { method: "PATCH", body: { title } }),
    onSuccess: () => {
      invalidate();
      setRenameTarget(null);
    },
  });

  const archiveChat = useMutation({
    mutationFn: ({ id, archived }: { id: string; archived: boolean }) =>
      api<{ ok: boolean }>(`/api/chats/${id}/archive`, { method: "POST", body: { archived } }),
    onSuccess: invalidate,
  });

  const deleteChat = useMutation({
    mutationFn: (id: string) => api<{ ok: boolean }>(`/api/chats/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      invalidate();
      setDeleteTarget(null);
    },
  });

  if (chatsQ.isLoading) {
    return (
      <div className="page">
        <Spinner center />
      </div>
    );
  }

  if (chatsQ.error) {
    return (
      <div className="page">
        <EmptyState icon="⚠️" text={errorMessage(chatsQ.error)} />
      </div>
    );
  }

  const activePages = chatsQ.data?.pages ?? [];
  const active = activePages.flatMap((p) => p.chats);
  const activeTotal =
    activePages.length > 0 ? activePages[activePages.length - 1].total : active.length;
  const hasMoreActive = chatsQ.hasNextPage ?? false;

  const archivedPages = archivedQ.data?.pages ?? [];
  const archivedAll = archivedPages.flatMap((p) => p.chats);
  const archivedTotal =
    archivedPages.length > 0 ? archivedPages[archivedPages.length - 1].total : archivedAll.length;
  const archived = archivedAll.filter((c) => c.archived_at !== null);
  const hasMoreArchived = archiveOpen && (archivedQ.hasNextPage ?? false);

  const busy =
    openChat.isPending ||
    createChat.isPending ||
    renameChat.isPending ||
    archiveChat.isPending ||
    deleteChat.isPending;

  const rowProps = {
    disabled: busy,
    onOpen: (c: Chat) => openChat.mutate(c.id),
    onRename: (c: Chat) => {
      setRenameTarget(c);
      setRenameValue(c.title ?? "");
    },
    onArchive: (c: Chat) => archiveChat.mutate({ id: c.id, archived: c.archived_at === null }),
    onDelete: (c: Chat) => setDeleteTarget(c),
  };

  return (
    <div className="page">
      <Button onClick={() => setCreateOpen(true)}>＋ Новый чат</Button>

      {active.length === 0 && (
        <EmptyState icon="💬" text="Чатов пока нет. Создайте первый." />
      )}

      {active.length > 0 && (
        <Section title="Активные" footer="Нажмите на чат, чтобы сделать его текущим.">
          {active.map((c) => (
            <ChatRow key={c.id} chat={c} {...rowProps} />
          ))}
          {hasMoreActive && (
            <div style={{ padding: 12 }}>
              <Button
                size="small"
                variant="secondary"
                loading={chatsQ.isFetchingNextPage}
                onClick={() => void chatsQ.fetchNextPage()}
              >
                Загрузить ещё ({active.length} из {activeTotal})
              </Button>
            </div>
          )}
        </Section>
      )}

      <Section title="Архив">
        {!archiveOpen ? (
          <div style={{ padding: 12 }}>
            <Button size="small" variant="secondary" onClick={() => setArchiveOpen(true)}>
              Показать архивные чаты
            </Button>
          </div>
        ) : archivedQ.isLoading ? (
          <Spinner center />
        ) : archivedQ.error ? (
          <div className="error-text" style={{ padding: 12 }}>{errorMessage(archivedQ.error)}</div>
        ) : archived.length === 0 ? (
          <div className="hint-text" style={{ padding: 12 }}>Архив пуст.</div>
        ) : (
          <>
            {archived.map((c) => (
              <ChatRow key={c.id} chat={c} {...rowProps} />
            ))}
            {hasMoreArchived && (
              <div style={{ padding: 12 }}>
                <Button
                  size="small"
                  variant="secondary"
                  loading={archivedQ.isFetchingNextPage}
                  onClick={() => void archivedQ.fetchNextPage()}
                >
                  Загрузить ещё ({archivedAll.length} из {archivedTotal})
                </Button>
              </div>
            )}
          </>
        )}
      </Section>

      {(openChat.error || archiveChat.error) && (
        <div className="error-text">{errorMessage(openChat.error ?? archiveChat.error)}</div>
      )}

      <Modal open={createOpen} title="Новый чат" onClose={() => setCreateOpen(false)}>
        <Input
          placeholder="Название (необязательно)"
          value={newTitle}
          onChange={(e) => setNewTitle(e.target.value)}
          autoFocus
        />
        {createChat.error && <div className="error-text">{errorMessage(createChat.error)}</div>}
        <div className="modal-actions">
          <Button variant="secondary" onClick={() => setCreateOpen(false)}>
            Отмена
          </Button>
          <Button loading={createChat.isPending} onClick={() => createChat.mutate(newTitle.trim())}>
            Создать
          </Button>
        </div>
      </Modal>

      <Modal open={renameTarget !== null} title="Переименовать чат" onClose={() => setRenameTarget(null)}>
        <Input
          placeholder="Название чата"
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          autoFocus
        />
        {renameChat.error && <div className="error-text">{errorMessage(renameChat.error)}</div>}
        <div className="modal-actions">
          <Button variant="secondary" onClick={() => setRenameTarget(null)}>
            Отмена
          </Button>
          <Button
            loading={renameChat.isPending}
            onClick={() => {
              if (renameTarget) renameChat.mutate({ id: renameTarget.id, title: renameValue.trim() });
            }}
          >
            Сохранить
          </Button>
        </div>
      </Modal>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Удалить чат?"
        message={`«${deleteTarget?.title ?? "Без названия"}» будет удалён вместе с историей. Действие необратимо.`}
        confirmText="Удалить"
        destructive
        loading={deleteChat.isPending}
        error={deleteChat.error ? errorMessage(deleteChat.error) : null}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (deleteTarget) deleteChat.mutate(deleteTarget.id);
        }}
      />
    </div>
  );
}
