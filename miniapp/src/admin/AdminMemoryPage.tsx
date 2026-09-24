import { useEffect, useState } from "react";
import { errorMessage } from "../api/client";
import { useAdminMemories } from "../api/hooks";
import { Button, Chip, EmptyState, Input, Section, Spinner } from "../components";
import { formatDateTime } from "../utils";

const PAGE_SIZE = 50;

export function AdminMemoryPage() {
  const [filter, setFilter] = useState("");
  const [debounced, setDebounced] = useState<number | null>(null);
  const [limit, setLimit] = useState(PAGE_SIZE);

  useEffect(() => {
    const t = setTimeout(() => {
      const trimmed = filter.trim();
      if (!trimmed) {
        setDebounced(null);
        return;
      }
      const n = Number(trimmed);
      setDebounced(Number.isInteger(n) && n > 0 ? n : null);
    }, 300);
    return () => clearTimeout(t);
  }, [filter]);

  // При смене фильтра начинаем с первой страницы.
  useEffect(() => {
    setLimit(PAGE_SIZE);
  }, [debounced]);

  const memoriesQ = useAdminMemories({ telegram_user_id: debounced, limit });

  const memories = memoriesQ.data?.memories ?? [];
  const total = memoriesQ.data?.total ?? memories.length;
  const hasMore = memories.length < total;

  return (
    <div className="page">
      <Section title="Фильтр">
        <div className="form-row">
          <label className="form-label">Telegram user id (пусто — все пользователи)</label>
          <Input
            inputMode="numeric"
            placeholder="например, 795063564"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
      </Section>

      {memoriesQ.isLoading && <Spinner center />}
      {memoriesQ.error && <EmptyState icon="⚠️" text={errorMessage(memoriesQ.error)} />}
      {!memoriesQ.isLoading && !memoriesQ.error && memories.length === 0 && (
        <EmptyState icon="🧠" text="Записей памяти не найдено." />
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
                  {m.last_used_at && <Chip>исп. {formatDateTime(m.last_used_at)}</Chip>}
                </div>
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
    </div>
  );
}
