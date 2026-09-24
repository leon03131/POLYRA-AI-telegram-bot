import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { qk, useChat, useModels, useSettings } from "../api/hooks";
import type { Chat, ChatPatch } from "../api/types";
import {
  Button,
  ChatSettingsForm,
  ConfirmDialog,
  EmptyState,
  Input,
  Section,
  Spinner,
  Textarea,
} from "../components";

export function ChatSettingsPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const chatQ = useChat(id);
  const modelsQ = useModels();
  const settingsQ = useSettings();

  const chatId = id ?? "";
  const chat = chatQ.data?.chat ?? null;

  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [promptLoaded, setPromptLoaded] = useState(false);

  useEffect(() => {
    if (chat && !promptLoaded) {
      setTitle(chat.title ?? "");
      setPrompt(chat.system_prompt_override ?? "");
      setPromptLoaded(true);
    }
  }, [chat, promptLoaded]);

  const invalidate = () => void qc.invalidateQueries({ queryKey: qk.chats });

  const patchChat = useMutation({
    mutationFn: (body: ChatPatch) =>
      api<{ chat: Chat }>(`/api/chats/${chatId}`, { method: "PATCH", body }),
    onSuccess: invalidate,
  });

  const archiveChat = useMutation({
    mutationFn: (archived: boolean) =>
      api<{ ok: boolean }>(`/api/chats/${chatId}/archive`, { method: "POST", body: { archived } }),
    onSuccess: () => {
      invalidate();
      navigate("/chats");
    },
  });

  const deleteChat = useMutation({
    mutationFn: () => api<{ ok: boolean }>(`/api/chats/${chatId}`, { method: "DELETE" }),
    onSuccess: () => {
      invalidate();
      navigate("/chats");
    },
  });

  const [deleteOpen, setDeleteOpen] = useState(false);

  if (chatQ.isLoading || modelsQ.isLoading || settingsQ.isLoading) {
    return (
      <div className="page">
        <Spinner center />
      </div>
    );
  }

  if (chatQ.error) {
    return (
      <div className="page">
        <EmptyState icon="⚠️" text={errorMessage(chatQ.error)}>
          <Button variant="secondary" onClick={() => navigate("/chats")}>
            К списку чатов
          </Button>
        </EmptyState>
      </div>
    );
  }

  if (!chat) {
    return (
      <div className="page">
        <EmptyState icon="🤷" text="Чат не найден.">
          <Button variant="secondary" onClick={() => navigate("/chats")}>
            К списку чатов
          </Button>
        </EmptyState>
      </div>
    );
  }

  const models = modelsQ.data?.models ?? [];
  const settings = settingsQ.data ?? null;
  const archived = chat.archived_at !== null;

  return (
    <div className="page">
      <Section title="Название">
        <div className="form-row">
          <Input
            placeholder="Название чата"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>
        <div className="form-row">
          <Button
            size="small"
            variant="secondary"
            disabled={patchChat.isPending || title.trim() === (chat.title ?? "")}
            onClick={() => patchChat.mutate({ title: title.trim() })}
          >
            Сохранить название
          </Button>
        </div>
      </Section>

      <Section title="Модель и поведение">
        <ChatSettingsForm
          chat={chat}
          models={models}
          settings={settings}
          busy={patchChat.isPending}
          onPatch={(body) => patchChat.mutate(body)}
        />
      </Section>

      <Section
        title="Системный промпт"
        footer="Переопределяет системный промпт только для этого чата. Пустое поле — наследовать."
      >
        <div className="form-row">
          <Textarea
            placeholder="Не задан"
            value={prompt}
            rows={4}
            onChange={(e) => setPrompt(e.target.value)}
          />
        </div>
        <div className="form-row">
          <Button
            size="small"
            variant="secondary"
            disabled={patchChat.isPending}
            onClick={() =>
              patchChat.mutate({ system_prompt_override: prompt.trim() === "" ? null : prompt })
            }
          >
            Сохранить промпт
          </Button>
        </div>
      </Section>

      {patchChat.error && <div className="error-text">{errorMessage(patchChat.error)}</div>}

      <Section title="Действия">
        <div className="form-row inline">
          <span>{archived ? "Разархивировать чат" : "Архивировать чат"}</span>
          <Button
            size="small"
            variant="secondary"
            disabled={archiveChat.isPending}
            onClick={() => archiveChat.mutate(!archived)}
          >
            {archived ? "Разархивировать" : "В архив"}
          </Button>
        </div>
        <div className="form-row inline">
          <span>Удалить чат</span>
          <Button size="small" variant="danger" onClick={() => setDeleteOpen(true)}>
            Удалить
          </Button>
        </div>
      </Section>

      <ConfirmDialog
        open={deleteOpen}
        title="Удалить чат?"
        message={`«${chat.title ?? "Без названия"}» будет удалён вместе с историей. Действие необратимо.`}
        confirmText="Удалить"
        destructive
        loading={deleteChat.isPending}
        error={deleteChat.error ? errorMessage(deleteChat.error) : null}
        onCancel={() => setDeleteOpen(false)}
        onConfirm={() => deleteChat.mutate()}
      />
    </div>
  );
}
