import { errorMessage } from "../api/client";
import { useAudit } from "../api/hooks";
import { Button, EmptyState, Section, Spinner } from "../components";
import { formatDateTime } from "../utils";

function metadataPreview(metadata: unknown): string {
  if (metadata === null || metadata === undefined) return "—";
  try {
    const s = JSON.stringify(metadata);
    return s.length > 80 ? s.slice(0, 80) + "…" : s;
  } catch {
    return "—";
  }
}

export function AdminAuditPage() {
  const auditQ = useAudit(100);

  if (auditQ.isLoading) {
    return (
      <div className="page">
        <Spinner center />
      </div>
    );
  }

  if (auditQ.error) {
    return (
      <div className="page">
        <EmptyState icon="⚠️" text={errorMessage(auditQ.error)} />
      </div>
    );
  }

  const entries = auditQ.data?.entries ?? [];

  return (
    <div className="page">
      <div className="row-between">
        <span className="hint-text">Последние события (до 100)</span>
        <Button size="small" variant="secondary" onClick={() => void auditQ.refetch()}>
          Обновить
        </Button>
      </div>

      {entries.length === 0 && <EmptyState icon="📋" text="Событий пока нет." />}

      {entries.length > 0 && (
        <Section>
          <div className="table-wrap" style={{ borderRadius: 0 }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Время</th>
                  <th>Актор</th>
                  <th>Действие</th>
                  <th>Цель</th>
                  <th>Детали</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((e) => (
                  <tr key={e.id}>
                    <td>{formatDateTime(e.created_at)}</td>
                    <td className="mono">{e.actor_telegram_id}</td>
                    <td>{e.action}</td>
                    <td className="mono">
                      {e.target_type}
                      {e.target_id ? `:${e.target_id}` : ""}
                    </td>
                    <td className="mono" title={metadataPreview(e.metadata)}>
                      {metadataPreview(e.metadata)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}
