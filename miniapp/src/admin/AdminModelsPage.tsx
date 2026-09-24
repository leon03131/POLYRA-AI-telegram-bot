import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, errorMessage } from "../api/client";
import { qk, useAdminModels } from "../api/hooks";
import type { AdminModel } from "../api/types";
import { Chip, EmptyState, Section, Spinner, Toggle } from "../components";
import { formatNumber, thinkingLabel } from "../utils";

function ModelRow({ model: m }: { model: AdminModel }) {
  const qc = useQueryClient();

  const toggle = useMutation({
    mutationFn: (enabled: boolean) =>
      api<{ ok: boolean }>(`/api/admin/models/${encodeURIComponent(m.model_id)}`, {
        method: "PUT",
        body: { enabled },
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.adminModels }),
  });

  return (
    <tr>
      <td>
        <div>{m.display_name}</div>
        <div className="hint-text mono">{m.model_id}</div>
      </td>
      <td>{m.provider}</td>
      <td>
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
          {m.thinking_modes.length === 0 && <span className="hint-text">—</span>}
          {m.thinking_modes.map((t) => (
            <Chip key={t}>{thinkingLabel(t)}</Chip>
          ))}
        </div>
      </td>
      <td>
        {formatNumber(m.max_context)} / {formatNumber(m.max_output)}
      </td>
      <td>
        {m.internal_only && <Chip tone="warn">internal</Chip>}
        {!m.supports_images && <Chip>text-only</Chip>}
      </td>
      <td>
        <Toggle
          checked={m.enabled}
          disabled={toggle.isPending}
          onChange={(v) => toggle.mutate(v)}
        />
        {toggle.error && <div className="error-text">{errorMessage(toggle.error)}</div>}
      </td>
    </tr>
  );
}

export function AdminModelsPage() {
  const modelsQ = useAdminModels();

  if (modelsQ.isLoading) {
    return (
      <div className="page">
        <Spinner center />
      </div>
    );
  }

  if (modelsQ.error) {
    return (
      <div className="page">
        <EmptyState icon="⚠️" text={errorMessage(modelsQ.error)} />
      </div>
    );
  }

  const models = modelsQ.data?.models ?? [];

  return (
    <div className="page">
      <Section
        title={`Модели (${models.length})`}
        footer="Отключённая модель недоступна пользователям. internal — только для внутренних задач, не показывается в списках."
      >
        {models.length === 0 && (
          <div className="hint-text" style={{ padding: 12 }}>
            Модели не найдены.
          </div>
        )}
        {models.length > 0 && (
          <div className="table-wrap" style={{ borderRadius: 0 }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Модель</th>
                  <th>Провайдер</th>
                  <th>Thinking</th>
                  <th>Контекст / вывод</th>
                  <th>Флаги</th>
                  <th>Включена</th>
                </tr>
              </thead>
              <tbody>
                {models.map((m) => (
                  <ModelRow key={m.model_id} model={m} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}
