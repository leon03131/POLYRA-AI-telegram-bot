import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, errorMessage } from "../api/client";
import { qk, useSearchBackends } from "../api/hooks";
import type { SearchBackend } from "../api/types";
import { Button, Chip, EmptyState, Input, Section, Spinner, Toggle } from "../components";
import { healthTone } from "../utils";

function BackendCard({ backend: b }: { backend: SearchBackend }) {
  const qc = useQueryClient();
  const invalidate = () => void qc.invalidateQueries({ queryKey: qk.searchBackends });

  const [priority, setPriority] = useState(b.priority.toString());
  const [apiKey, setApiKey] = useState("");
  const [testResult, setTestResult] = useState<{ ok: boolean; error: string | null } | null>(null);

  const update = useMutation({
    mutationFn: (body: { enabled?: boolean; priority?: number }) =>
      api<{ ok: boolean }>(`/api/admin/search/backends/${b.backend_id}`, {
        method: "PUT",
        body,
      }),
    onSuccess: invalidate,
  });

  const setKey = useMutation({
    mutationFn: () =>
      api<{ ok: boolean }>(`/api/admin/search/backends/${b.backend_id}/key`, {
        method: "POST",
        body: { api_key: apiKey.trim() },
      }),
    onSuccess: () => {
      setApiKey("");
      invalidate();
    },
  });

  const test = useMutation({
    mutationFn: () =>
      api<{ ok: boolean; error: string | null }>(
        `/api/admin/search/backends/${b.backend_id}/test`,
        { method: "POST" },
      ),
    onSuccess: (data) => setTestResult(data),
    onError: () => setTestResult({ ok: false, error: "Запрос не выполнен" }),
  });

  const busy = update.isPending || setKey.isPending || test.isPending;

  return (
    <div className="card">
      <div className="row-between">
        <strong className="mono">{b.backend_id}</strong>
        <div className="row-between" style={{ gap: 10 }}>
          <Chip tone={healthTone(b.health_status)}>{b.health_status}</Chip>
          <Toggle
            checked={b.enabled}
            disabled={busy}
            onChange={(v) => update.mutate({ enabled: v })}
          />
        </div>
      </div>

      <div className="hint-text mono">ключ: {b.key_hint ?? "—"}</div>
      {b.last_error && <div className="error-text">{b.last_error}</div>}

      <div className="form-row" style={{ padding: 0 }}>
        <label className="form-label">Приоритет (меньше = раньше)</label>
        <div className="row-between">
          <Input
            type="number"
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
            style={{ width: 100 }}
          />
          <Button
            size="small"
            variant="secondary"
            disabled={busy || priority.trim() === b.priority.toString() || !priority.trim()}
            onClick={() => update.mutate({ priority: Number(priority) })}
          >
            Сохранить
          </Button>
        </div>
      </div>

      <div className="form-row" style={{ padding: 0 }}>
        <label className="form-label">Новый API-ключ</label>
        <div className="row-between">
          <Input
            type="password"
            placeholder="оставить пустым, если без изменений"
            value={apiKey}
            autoComplete="off"
            onChange={(e) => setApiKey(e.target.value)}
          />
          <Button
            size="small"
            variant="secondary"
            disabled={busy || !apiKey.trim()}
            onClick={() => setKey.mutate()}
          >
            Установить
          </Button>
        </div>
      </div>

      <div className="row-between">
        <Button size="small" variant="secondary" loading={test.isPending} onClick={() => test.mutate()}>
          🔍 Тест
        </Button>
        {testResult && (
          <span className={testResult.ok ? "hint-text" : "error-text"}>
            {testResult.ok ? "✅ OK" : `❌ ${testResult.error ?? "ошибка"}`}
          </span>
        )}
      </div>

      {(update.error || setKey.error || test.error) && (
        <div className="error-text">{errorMessage(update.error ?? setKey.error ?? test.error)}</div>
      )}
    </div>
  );
}

export function AdminSearchPage() {
  const backendsQ = useSearchBackends();

  if (backendsQ.isLoading) {
    return (
      <div className="page">
        <Spinner center />
      </div>
    );
  }

  if (backendsQ.error) {
    return (
      <div className="page">
        <EmptyState icon="⚠️" text={errorMessage(backendsQ.error)} />
      </div>
    );
  }

  const backends = backendsQ.data?.backends ?? [];

  return (
    <div className="page">
      <Section
        title="Поисковые бэкенды"
        footer="Порядок определяется приоритетом. Отключённые бэкенды не используются."
      >
        <div className="hint-text" style={{ padding: "10px 16px" }}>
          Бэкендов: {backends.length}, включено: {backends.filter((b) => b.enabled).length}
        </div>
      </Section>

      {backends.length === 0 && <EmptyState icon="🔍" text="Поисковые бэкенды не настроены." />}

      <div className="gap-list">
        {backends.map((b) => (
          <BackendCard key={b.backend_id} backend={b} />
        ))}
      </div>
    </div>
  );
}
