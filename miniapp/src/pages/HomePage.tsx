import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { qk, useChats, useModels, useSettings } from "../api/hooks";
import type { Chat, ChatPatch } from "../api/types";
import { Button, ChatSettingsForm, EmptyState, ListRow, Section, Spinner } from "../components";

export function HomePage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const chatsQ = useChats();
  const modelsQ = useModels();
  const settingsQ = useSettings();

  const patchChat = useMutation({
    mutationFn: ({ id, body }: { id: string; body: ChatPatch }) =>
      api<{ chat: Chat }>(`/api/chats/${id}`, { method: "PATCH", body }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.chats }),
  });

  const createChat = useMutation({
    mutationFn: () => api<{ chat: Chat }>("/api/chats", { method: "POST", body: {} }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.chats }),
  });

  if (chatsQ.isLoading || modelsQ.isLoading) {
    return (
      <div className="page">
        <Spinner center />
      </div>
    );
  }

  if (chatsQ.error || modelsQ.error) {
    return (
      <div className="page">
        <EmptyState icon="⚠️" text={errorMessage(chatsQ.error ?? modelsQ.error)} />
      </div>
    );
  }

  const chats = chatsQ.data?.chats ?? [];
  const models = modelsQ.data?.models ?? [];
  const settings = settingsQ.data ?? null;
  const chat = chats.find((c) => c.is_current) ?? null;

  if (!chat) {
    return (
      <div className="page">
        <EmptyState icon="💬" text="Нет активного чата. Создайте новый — он станет текущим." />
        <Button onClick={() => createChat.mutate()} loading={createChat.isPending}>
          Новый чат
        </Button>
        {createChat.error && <div className="error-text">{errorMessage(createChat.error)}</div>}
        <p className="hint-text center">
          Пишите в чат с ботом — ответы приходят там. Mini App нужен только для настройки.
        </p>
      </div>
    );
  }

  return (
    <div className="page">
      <Section title="Текущий чат">
        <ListRow
          title={chat.title ?? "Без названия"}
          subtitle="Нажмите, чтобы открыть настройки чата"
          chevron
          onClick={() => navigate(`/chats/${chat.id}`)}
        />
      </Section>

      <Section
        title="Быстрые настройки"
        footer="Изменения применяются сразу и действуют только на текущий чат."
      >
        <ChatSettingsForm
          chat={chat}
          models={models}
          settings={settings}
          busy={patchChat.isPending}
          onPatch={(body) => patchChat.mutate({ id: chat.id, body })}
        />
      </Section>

      {patchChat.error && <div className="error-text">{errorMessage(patchChat.error)}</div>}

      <p className="hint-text center">
        💬 Пишите в чат с ботом — ответы приходят там. Mini App нужен только для настройки.
      </p>
    </div>
  );
}
