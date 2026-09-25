import { useAdminStats } from "../api/hooks";
import { errorMessage } from "../api/client";
import { Chip, EmptyState, Section, ListRow, Spinner } from "../components";
import { formatDateTime, formatNumber, healthTone } from "../utils";

/** error_rate_today: доля (0..1) → %. */
function formatRate(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

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
  const modelEntries = Object.entries(s.requests_by_model_today ?? {}).sort((a, b) => b[1] - a[1]);

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
        {s.errors_today !== undefined && (
          <div className="stat-card">
            <div className="stat-value">{formatNumber(s.errors_today)}</div>
            <div className="stat-label">Ошибок сегодня</div>
          </div>
        )}
        {s.rate_limit_429_today !== undefined && (
          <div className="stat-card">
            <div className="stat-value">{formatNumber(s.rate_limit_429_today)}</div>
            <div className="stat-label">429 сегодня</div>
          </div>
        )}
        {s.avg_ttft_s !== undefined && (
          <div className="stat-card">
            <div className="stat-value">
              {s.avg_ttft_s === null ? "—" : s.avg_ttft_s.toFixed(1)}
            </div>
            <div className="stat-label">Средний TTFT, с</div>
          </div>
        )}
        {s.error_rate_today !== undefined && (
          <div className="stat-card">
            <div className="stat-value">{formatRate(s.error_rate_today)}</div>
            <div className="stat-label">Error rate сегодня</div>
          </div>
        )}
      </div>

      {modelEntries.length > 0 && (
        <Section title="Запросы по моделям (сегодня)">
          {modelEntries.map(([modelId, count]) => (
            <ListRow key={modelId} title={<span className="mono">{modelId}</span>} right={formatNumber(count)} />
          ))}
        </Section>
      )}

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

      {s.recent_failed_runs && s.recent_failed_runs.length > 0 && (
        <Section title="Последние неудачные генерации">
          <div className="table-wrap" style={{ borderRadius: 0 }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Модель</th>
                  <th>Статус</th>
                  <th>Категория ошибки</th>
                  <th>Время</th>
                </tr>
              </thead>
              <tbody>
                {s.recent_failed_runs.map((r) => (
                  <tr key={r.id}>
                    <td className="mono">{r.model_id ?? "—"}</td>
                    <td>
                      <Chip tone={healthTone(r.status)}>{r.status}</Chip>
                    </td>
                    <td>
                      {r.error_category ?? "—"}
                      {r.error_code ? ` (${r.error_code})` : ""}
                    </td>
                    <td>{formatDateTime(r.started_at)}</td>
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
