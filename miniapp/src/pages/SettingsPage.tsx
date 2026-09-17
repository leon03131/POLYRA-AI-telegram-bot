import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, errorMessage } from "../api/client";
import { qk, useMe, useModels, useSettings } from "../api/hooks";
import type { SettingsPatch, UserSettings, WebMode } from "../api/types";
import { EmptyState, ListRow, Section, Select, Spinner, Toggle } from "../components";
import { formatNumber, thinkingLabel } from "../utils";

export function SettingsPage() {
  const qc = useQueryClient();
  const settingsQ = useSettings();
  const modelsQ = useModels();
  const meQ = useMe();

  const patchSettings = useMutation({
    mutationFn: (body: SettingsPatch) =>
      api<{ settings: UserSettings }>("/api/settings", { method: "PATCH", body }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.settings }),
  });

  if (settingsQ.isLoading || modelsQ.isLoading) {
    return (
      <div className="page">
        <Spinner center />
      </div>
    );
  }

  if (settingsQ.error || modelsQ.error || !settingsQ.data) {
    return (
      <div className="page">
        <EmptyState icon="⚠️" text={errorMessage(settingsQ.error ?? modelsQ.error)} />
      </div>
    );
  }

  const settings = settingsQ.data;
  const models = modelsQ.data?.models ?? [];
  const me = meQ.data ?? null;

  const defaultModel = models.find((m) => m.model_id === settings.default_model_id) ?? null;
  const thinkingModes = defaultModel?.thinking_modes ?? [];

  const patch = (body: SettingsPatch) => patchSettings.mutate(body);

  const onModelChange = (value: string) => {
    const default_model_id = value === "" ? null : value;
    const next = models.find((m) => m.model_id === default_model_id);
    const keep =
      settings.default_thinking !== null &&
      next?.thinking_modes.includes(settings.default_thinking);
    patch({ default_model_id, ...(keep ? {} : { default_thinking: null }) });
  };

  const permissions = me?.permissions ?? null;

  return (
    <div className="page">
      <Section
        title="Настройки по умолчанию"
        footer="Применяются к новым чатам и к чатам, где параметр не переопределён."
      >
        <div className="form-row">
          <label className="form-label">Модель по умолчанию</label>
          <Select
            value={settings.default_model_id ?? ""}
            disabled={patchSettings.isPending}
            onChange={onModelChange}
            options={[
              { value: "", label: "Системная" },
              ...models.map((m) => ({ value: m.model_id, label: m.display_name })),
            ]}
          />
        </div>
        <div className="form-row">
          <label className="form-label">Thinking по умолчанию</label>
          <Select
            value={settings.default_thinking ?? ""}
            disabled={patchSettings.isPending || thinkingModes.length === 0}
            onChange={(v) => patch({ default_thinking: v === "" ? null : v })}
            options={[
              {
                value: "",
                label: `Системный${
                  defaultModel?.default_thinking
                    ? ` (${thinkingLabel(defaultModel.default_thinking)})`
                    : ""
                }`,
              },
              ...thinkingModes.map((m) => ({ value: m, label: thinkingLabel(m) })),
            ]}
          />
        </div>
        <div className="form-row">
          <label className="form-label">Интернет</label>
          <Select
            value={settings.web_mode}
            disabled={patchSettings.isPending}
            onChange={(v) => patch({ web_mode: v as WebMode })}
            options={[
              { value: "off", label: "Выкл" },
              { value: "auto", label: "Авто" },
              { value: "on", label: "Вкл" },
            ]}
          />
        </div>
        <div className="form-row inline">
          <span>Память</span>
          <Toggle
            checked={settings.memory_enabled}
            disabled={patchSettings.isPending}
            onChange={(v) => patch({ memory_enabled: v })}
          />
        </div>
      </Section>

      {patchSettings.error && <div className="error-text">{errorMessage(patchSettings.error)}</div>}

      {permissions && (
        <Section title="Ваш доступ">
          <ListRow
            title="Модели"
            right={
              permissions.allowed_models === null
                ? "все"
                : `${permissions.allowed_models.length} шт.`
            }
          />
          <ListRow title="Веб-поиск" right={permissions.can_use_web_search ? "✅" : "❌"} />
          <ListRow title="Память" right={permissions.can_use_memory ? "✅" : "❌"} />
          <ListRow
            title="Запросов в день"
            right={
              permissions.requests_per_day === null ? "без лимита" : formatNumber(permissions.requests_per_day)
            }
          />
          <ListRow
            title="Токен-лимит"
            right={permissions.token_limit === null ? "без лимита" : formatNumber(permissions.token_limit)}
          />
          <ListRow
            title="Параллельные генерации"
            right={formatNumber(permissions.max_concurrent_generations)}
          />
        </Section>
      )}
    </div>
  );
}
