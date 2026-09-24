import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, errorMessage } from "../api/client";
import { qk, useMemories } from "../api/hooks";
import type { MemoryItem, MemoryPatch } from "../api/types";
import {
  Button,
  Chip,
  ConfirmDialog,
  EmptyState,
  Input,
  Modal,
  Section,
  Select,
  Spinner,
  Textarea,
} from "../components";
import { formatDateTime } from "../utils";

const IMPORTANCE_OPTIONS = Array.from({ length: 10 }, (_, i) => ({
  value: String(i + 1),
  label: `${i + 1}`,
}));

const PAGE_SIZE = 50;

export function MemoryPage() {
  const qc = useQueryClient();
  const [limit, setLimit] = useState(PAGE_SIZE);
  const memoriesQ = useMemories({ limit });
  const invalidate = () => void qc.invalidateQueries({ queryKey: qk.memories });

  const [editTarget, setEditTarget] = useState<MemoryItem | null>(null);
  const [editText, setEditText] = useState("");
  const [editCategory, setEditCategory] = useState("");
  const [editImportance, setEditImportance] = useState("5");
  const [deleteTarget, setDeleteTarget] = useState<MemoryItem | null>(null);

  const updateMemory = useMutation({
    mutationFn: ({ id, body }: { id: string; body: MemoryPatch }) =>
      api<{ memory: MemoryItem }>(`/api/memory/${id}`, { method: "PATCH", body }),
    onSuccess: () => {
      invalidate();
      setEditTarget(null);
    },
  });

  const deleteMemory = useMutation({
    mutationFn: (id: string) => api<{ ok: boolean }>(`/api/memory/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      invalidate();
      setDeleteTarget(null);
    },
  });

  if (memoriesQ.isLoading) {
    return (
      <div className="page">
        <Spinner center />
      </div>
    );
  }

  if (memoriesQ.error) {
    return (
      <div className="page">
        <EmptyState icon="⚠️" text={errorMessage(memoriesQ.error)} />
      </div>
    );
  }

  const memories = memoriesQ.data?.memories ?? [];
  const total = memoriesQ.data?.total ?? memories.length;
  const hasMore = memories.length < total;

  const openEdit = (m: MemoryItem) => {
    setEditTarget(m);
    setEditText(m.text);
    setEditCategory(m.category);
    setEditImportance(String(m.importance));
  };

  return (
    <div className="page">
      <p className="hint-text">
        Память формируется автоматически: ассистент запоминает важное из диалогов. Здесь можно
        просматривать, править и удалять записи.
      </p>

      {memories.length === 0 && (
        <EmptyState icon="🧠" text="Память пока пуста. Она пополнится по мере общения с ботом." />
      )}

      {memories.length > 0 && (
        <Section title={`Записей: ${total}`}>
          {memories.map((m) => (
            <div key={m.id} className="list-row">
              <div className="list-row-main">
                <div className="list-row-title" style={{ whiteSpace: "normal" }}>
                  {m.text}
                </div>
                <div
                  className="list-row-subtitle"
                  style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6 }}
                >
                  <Chip>{m.category}</Chip>
                  <Chip tone="accent">★ {m.importance}</Chip>
                  <Chip>{formatDateTime(m.updated_at)}</Chip>
                </div>
              </div>
              <div className="row-actions">
                <button
                  type="button"
                  className="icon-btn"
                  title="Редактировать"
                  onClick={() => openEdit(m)}
                >
                  ✏️
                </button>
                <button
                  type="button"
                  className="icon-btn"
                  title="Удалить"
                  onClick={() => setDeleteTarget(m)}
                >
                  🗑
                </button>
              </div>
            </div>
          ))}
          {hasMore && (
            <div style={{ padding: 12 }}>
              <Button
                size="small"
                variant="secondary"
                loading={memoriesQ.isFetching}
                onClick={() => setLimit((v) => v + PAGE_SIZE)}
              >
                Загрузить ещё ({memories.length} из {total})
              </Button>
            </div>
          )}
        </Section>
      )}

      <Modal
        open={editTarget !== null}
        title="Редактировать запись"
        onClose={() => setEditTarget(null)}
      >
        <div className="form-row" style={{ padding: 0 }}>
          <label className="form-label">Текст</label>
          <Textarea value={editText} rows={4} onChange={(e) => setEditText(e.target.value)} />
        </div>
        <div className="form-row" style={{ padding: 0 }}>
          <label className="form-label">Категория</label>
          <Input value={editCategory} onChange={(e) => setEditCategory(e.target.value)} />
        </div>
        <div className="form-row" style={{ padding: 0 }}>
          <label className="form-label">Важность (1–10)</label>
          <Select value={editImportance} onChange={setEditImportance} options={IMPORTANCE_OPTIONS} />
        </div>
        {updateMemory.error && <div className="error-text">{errorMessage(updateMemory.error)}</div>}
        <div className="modal-actions">
          <Button variant="secondary" onClick={() => setEditTarget(null)}>
            Отмена
          </Button>
          <Button
            loading={updateMemory.isPending}
            onClick={() => {
              if (!editTarget) return;
              updateMemory.mutate({
                id: editTarget.id,
                body: {
                  text: editText.trim(),
                  category: editCategory.trim(),
                  importance: Number(editImportance),
                },
              });
            }}
          >
            Сохранить
          </Button>
        </div>
      </Modal>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Удалить запись?"
        message="Запись памяти будет удалена безвозвратно."
        confirmText="Удалить"
        destructive
        loading={deleteMemory.isPending}
        error={deleteMemory.error ? errorMessage(deleteMemory.error) : null}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (deleteTarget) deleteMemory.mutate(deleteTarget.id);
        }}
      />
    </div>
  );
}
