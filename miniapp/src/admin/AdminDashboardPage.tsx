import { useAdminStats } from "../api/hooks";
import { errorMessage } from "../api/client";
import { Chip, EmptyState, Section, ListRow, Spinner } from "../components";
import { formatNumber } from "../utils";

export function AdminDashboardPage() {
  const statsQ = useAdminStats();

  if (statsQ.isLoading) {
    return (
      <div className="page">
        <Spinner center />
      </div>
    );
  }

  if (statsQ.error || !statsQ.data) {
    return (
      <div className="page">
        <EmptyState icon="⚠️" text={errorMessage(statsQ.error)} />
      </div>
    );
  }

  const s = statsQ.data;
  const statusEntries = Object.entries(s.generations_by_status ?? {});

  return (
    <div className="page">
      <div className="stat-grid">
        <div className="stat-card">
          <div className="stat-value">{formatNumber(s.users_total)}</div>
          <div className="stat-label">Пользователей всего</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{formatNumber(s.users_active_7d)}</div>
          <div className="stat-label">Активны за 7 дней</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{formatNumber(s.generations_today)}</div>
          <div className="stat-label">Генераций сегодня</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{formatNumber(s.tool_calls_today)}</div>
          <div className="stat-label">Вызовов инструментов</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">
            {formatNumber(s.tokens_today.input)} / {formatNumber(s.tokens_today.output)}
          </div>
          <div className="stat-label">Токены in / out сегодня</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">
            {formatNumber(s.gemini_projects.healthy)} / {formatNumber(s.gemini_projects.enabled)} /{" "}
            {formatNumber(s.gemini_projects.total)}
          </div>
          <div className="stat-label">Gemini: healthy / enabled / всего</div>
        </div>
      </div>

      {statusEntries.length > 0 && (
        <Section title="Генерации по статусам">
          {statusEntries.map(([status, count]) => (
            <ListRow
              key={status}
              title={<Chip tone={status === "ok" || status === "success" ? "ok" : status === "error" ? "err" : "default"}>{status}</Chip>}
              right={formatNumber(count)}
            />
          ))}
        </Section>
      )}
    </div>
  );
}
