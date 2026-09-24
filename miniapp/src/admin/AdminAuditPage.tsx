import { useEffect, useState } from "react";
import { errorMessage } from "../api/client";
import { useAudit } from "../api/hooks";
import { Button, EmptyState, Input, Section, Spinner } from "../components";
import { formatDateTime } from "../utils";

const PAGE_SIZE = 50;

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
  const [action, setAction] = useState("");
  const [debouncedAction, setDebouncedAction] = useState("");
  const [limit, setLimit] = useState(PAGE_SIZE);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedAction(action.trim()), 300);
    return () => clearTimeout(t);
  }, [action]);

  // При смене фильтра начинаем с первой страницы.
  useEffect(() => {
    setLimit(PAGE_SIZE);
  }, [debouncedAction]);

  const auditQ = useAudit({ limit, action: debouncedAction || undefined });

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
  const total = auditQ.data?.total ?? entries.length;
  const hasMore = entries.length < total;

  return (
    <div className="page">
      <div className="row-between">
        <span className="hint-text">Событий: {total}</span>
        <Button size="small" variant="secondary" onClick={() => void auditQ.refetch()}>
          Обновить
        </Button>
      </div>

      <div className="search-input">
        <Input
          placeholder="Фильтр по action (например, grant_access)"
          value={action}
          onChange={(e) => setAction(e.target.value)}
        />
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
          {hasMore && (
            <div style={{ padding: 12 }}>
              <Button
                size="small"
                variant="secondary"
                loading={auditQ.isFetching}
                onClick={() => setLimit((v) => v + PAGE_SIZE)}
              >
                Загрузить ещё ({entries.length} из {total})
              </Button>
            </div>
          )}
        </Section>
      )}
    </div>
  );
}
