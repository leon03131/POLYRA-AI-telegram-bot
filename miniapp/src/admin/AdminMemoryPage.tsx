import { useEffect, useState } from "react";
import { errorMessage } from "../api/client";
import { useAdminMemories } from "../api/hooks";
import { Button, Chip, EmptyState, Input, Section, Spinner } from "../components";
import { formatDateTime } from "../utils";

export function AdminMemoryPage() {
  const [filter, setFilter] = useState("");
  const [debounced, setDebounced] = useState<number | null>(null);

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

  // round4-P1: offset-пагинация — смена фильтра меняет queryKey и автоматически
  // сбрасывает догрузку на первую страницу (setLimit больше не нужен).
  const memoriesQ = useAdminMemories({ telegram_user_id: debounced });

  const pages = memoriesQ.data?.pages ?? [];
  const memories = pages.flatMap((p) => p.memories);
  const total = pages.length > 0 ? pages[pages.length - 1].total : memories.length;
  const hasMore = memoriesQ.hasNextPage ?? false;

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
      {/* round4-P1: при ошибке с накопленными страницами список не стираем. */}
      {memoriesQ.error && memories.length === 0 && (
        <EmptyState icon="⚠️" text={errorMessage(memoriesQ.error)} />
      )}
      {memoriesQ.error && memories.length > 0 && (
        <div className="error-text" style={{ padding: 12 }}>
          {errorMessage(memoriesQ.error)}
        </div>
      )}
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
                loading={memoriesQ.isFetchingNextPage}
                onClick={() => void memoriesQ.fetchNextPage()}
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
