import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, errorMessage } from "../api/client";
import { qk, useGeminiProjects, useGeminiQuotas } from "../api/hooks";
import type { GeminiProject, GeminiQuota } from "../api/types";
import {
  Button,
  Chip,
  ConfirmDialog,
  EmptyState,
  Input,
  Modal,
  Section,
  Spinner,
  Textarea,
  Toggle,
} from "../components";
import { formatDateTime, healthTone, numOrNull } from "../utils";

// ---------- Add / Bulk import ----------

function AddProjectModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [apiKey, setApiKey] = useState("");

  const save = useMutation({
    mutationFn: () =>
      api<{ ok: boolean; id: number }>("/api/admin/gemini/projects", {
        method: "POST",
        body: { name: name.trim(), api_key: apiKey.trim() },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.geminiProjects });
      onClose();
    },
  });

  return (
    <Modal open title="Добавить проект" onClose={onClose}>
      <div className="form-row" style={{ padding: 0 }}>
        <label className="form-label">Название</label>
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="my-gemini-project" />
      </div>
      <div className="form-row" style={{ padding: 0 }}>
        <label className="form-label">API-ключ</label>
        <Input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder="AIza..."
          autoComplete="off"
        />
      </div>
      {save.error && <div className="error-text">{errorMessage(save.error)}</div>}
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>
          Отмена
        </Button>
        <Button
          loading={save.isPending}
          disabled={!name.trim() || !apiKey.trim()}
          onClick={() => save.mutate()}
        >
          Добавить
        </Button>
      </div>
    </Modal>
  );
}

function BulkImportModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [keys, setKeys] = useState("");
  const [prefix, setPrefix] = useState("gemini");
  const [result, setResult] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => {
      const api_keys = keys
        .split("\n")
        .map((k) => k.trim())
        .filter((k) => k.length > 0);
      return api<{ added: number; skipped: number }>("/api/admin/gemini/projects/bulk", {
        method: "POST",
        body: { api_keys, name_prefix: prefix.trim() || undefined },
      });
    },
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: qk.geminiProjects });
      setResult(`Добавлено: ${data.added}, пропущено (дубликаты): ${data.skipped}`);
    },
  });

  return (
    <Modal open title="Массовый импорт" onClose={onClose}>
      <div className="form-row" style={{ padding: 0 }}>
        <label className="form-label">Ключи — по одному на строку</label>
        <Textarea
          value={keys}
          onChange={(e) => setKeys(e.target.value)}
          rows={6}
          placeholder={"AIza...\nAIza...\nAIza..."}
        />
      </div>
      <div className="form-row" style={{ padding: 0 }}>
        <label className="form-label">Префикс имён</label>
        <Input value={prefix} onChange={(e) => setPrefix(e.target.value)} />
      </div>
      {result && <div className="hint-text">{result}</div>}
      {save.error && <div className="error-text">{errorMessage(save.error)}</div>}
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>
          Закрыть
        </Button>
        <Button loading={save.isPending} disabled={!keys.trim()} onClick={() => save.mutate()}>
          Импортировать
        </Button>
      </div>
    </Modal>
  );
}

// ---------- Project card ----------

interface ProjectCardProps {
  project: GeminiProject;
  isFirst: boolean;
  isLast: boolean;
  onDelete: () => void;
}

function ProjectCard({ project: p, isFirst, isLast, onDelete }: ProjectCardProps) {
  const qc = useQueryClient();
  const invalidate = () => void qc.invalidateQueries({ queryKey: qk.geminiProjects });

  const toggle = useMutation({
    mutationFn: () =>
      api<{ ok: boolean }>(`/api/admin/gemini/projects/${p.id}/${p.enabled ? "disable" : "enable"}`, {
        method: "POST",
      }),
    onSuccess: invalidate,
  });

  const move = useMutation({
    mutationFn: (direction: -1 | 1) =>
      api<{ ok: boolean }>(`/api/admin/gemini/projects/${p.id}/move`, {
        method: "POST",
        body: { direction },
      }),
    onSuccess: invalidate,
  });

  const busy = toggle.isPending || move.isPending;
  const cooldownActive = p.cooldown_until !== null && new Date(p.cooldown_until).getTime() > Date.now();

  return (
    <div className="card">
      <div className="row-between">
        <strong>{p.name}</strong>
        <div className="row-between" style={{ gap: 10 }}>
          <Chip tone={healthTone(p.health_status)}>{p.health_status}</Chip>
          <Toggle checked={p.enabled} disabled={busy} onChange={() => toggle.mutate()} />
        </div>
      </div>

      <div className="hint-text mono">ключ: {p.key_hint}</div>

      <div className="hint-text">
        Порядок ротации: {p.rotation_order}
        {p.last_success_at && <> · успех {formatDateTime(p.last_success_at)}</>}
        {cooldownActive && (
          <>
            {" · "}
            <Chip tone="warn">cooldown до {formatDateTime(p.cooldown_until)}</Chip>
          </>
        )}
      </div>

      {p.last_error_code && (
        <div className="error-text" title={p.last_error_message ?? ""}>
          {p.last_error_code}: {p.last_error_message ?? "—"}
        </div>
      )}

      {(toggle.error || move.error) && (
        <div className="error-text">{errorMessage(toggle.error ?? move.error)}</div>
      )}

      <div className="row-between wrap" style={{ justifyContent: "flex-start" }}>
        <Button size="small" variant="secondary" disabled={busy || isFirst} onClick={() => move.mutate(-1)}>
          ↑ Вверх
        </Button>
        <Button size="small" variant="secondary" disabled={busy || isLast} onClick={() => move.mutate(1)}>
          ↓ Вниз
        </Button>
        <Button size="small" variant="danger" disabled={busy} onClick={onDelete}>
          Удалить
        </Button>
      </div>
    </div>
  );
}

// ---------- Quotas ----------

function QuotaRow({ quota }: { quota: GeminiQuota }) {
  const qc = useQueryClient();
  const [rpm, setRpm] = useState(quota.rpm?.toString() ?? "");
  const [tpm, setTpm] = useState(quota.tpm?.toString() ?? "");
  const [rpd, setRpd] = useState(quota.rpd?.toString() ?? "");

  const save = useMutation({
    mutationFn: () =>
      api<{ ok: boolean }>("/api/admin/gemini/quotas", {
        method: "PUT",
        body: {
          model_id: quota.model_id,
          rpm: numOrNull(rpm),
          tpm: numOrNull(tpm),
          rpd: numOrNull(rpd),
        },
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.geminiQuotas }),
  });

  return (
    <tr>
      <td className="mono">{quota.model_id}</td>
      <td>
        <Input type="number" min={0} value={rpm} onChange={(e) => setRpm(e.target.value)} placeholder="—" />
      </td>
      <td>
        <Input type="number" min={0} value={tpm} onChange={(e) => setTpm(e.target.value)} placeholder="—" />
      </td>
      <td>
        <Input type="number" min={0} value={rpd} onChange={(e) => setRpd(e.target.value)} placeholder="—" />
      </td>
      <td>
        <Button size="small" variant="secondary" loading={save.isPending} onClick={() => save.mutate()}>
          💾
        </Button>
        {save.error && <div className="error-text">{errorMessage(save.error)}</div>}
      </td>
    </tr>
  );
}

// ---------- Page ----------

export function AdminGeminiPage() {
  const qc = useQueryClient();
  const projectsQ = useGeminiProjects();
  const quotasQ = useGeminiQuotas();

  const [addOpen, setAddOpen] = useState(false);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<GeminiProject | null>(null);

  const deleteProject = useMutation({
    mutationFn: (id: number) =>
      api<{ ok: boolean }>(`/api/admin/gemini/projects/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.geminiProjects });
      setDeleteTarget(null);
    },
  });

  const projects = projectsQ.data?.projects ?? [];
  const quotas = quotasQ.data?.quotas ?? [];

  return (
    <div className="page">
      <div className="row-between wrap">
        <Button size="small" onClick={() => setAddOpen(true)}>
          ＋ Добавить проект
        </Button>
        <Button size="small" variant="secondary" onClick={() => setBulkOpen(true)}>
          📥 Массовый импорт
        </Button>
      </div>

      {projectsQ.isLoading && <Spinner center />}
      {projectsQ.error && <EmptyState icon="⚠️" text={errorMessage(projectsQ.error)} />}
      {!projectsQ.isLoading && !projectsQ.error && projects.length === 0 && (
        <EmptyState icon="🔑" text="Проектов нет. Добавьте первый или импортируйте пачку ключей." />
      )}

      <div className="gap-list">
        {projects.map((p, i) => (
          <ProjectCard
            key={p.id}
            project={p}
            isFirst={i === 0}
            isLast={i === projects.length - 1}
            onDelete={() => setDeleteTarget(p)}
          />
        ))}
      </div>

      <Section
        title="Квоты по моделям"
        footer="Лимиты на проект: запросы/мин (rpm), токены/мин (tpm), запросы/день (rpd). Пусто — без лимита."
      >
        {quotasQ.isLoading && <Spinner center />}
        {quotasQ.error && <div className="error-text" style={{ padding: 12 }}>{errorMessage(quotasQ.error)}</div>}
        {quotas.length > 0 && (
          <div className="table-wrap" style={{ borderRadius: 0 }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Модель</th>
                  <th>rpm</th>
                  <th>tpm</th>
                  <th>rpd</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {quotas.map((q) => (
                  <QuotaRow key={q.model_id} quota={q} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {addOpen && <AddProjectModal onClose={() => setAddOpen(false)} />}
      {bulkOpen && <BulkImportModal onClose={() => setBulkOpen(false)} />}

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Удалить проект?"
        message={`Проект «${deleteTarget?.name}» (${deleteTarget?.key_hint}) будет удалён из пула.`}
        confirmText="Удалить"
        destructive
        loading={deleteProject.isPending}
        error={deleteProject.error ? errorMessage(deleteProject.error) : null}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (deleteTarget) deleteProject.mutate(deleteTarget.id);
        }}
      />
    </div>
  );
}
