import type { Chat, ChatPatch, ModelInfo, UserSettings } from "../api/types";
import { thinkingLabel, webModeLabel } from "../utils";
import { Select } from "./Select";

interface ChatSettingsFormProps {
  chat: Chat;
  models: ModelInfo[];
  settings: UserSettings | null;
  busy: boolean;
  onPatch: (body: ChatPatch) => void;
}

function modelDisplay(models: ModelInfo[], id: string | null | undefined): string {
  if (!id) return "не задана";
  return models.find((m) => m.model_id === id)?.display_name ?? id;
}

/**
 * Общие per-chat контролы: модель / thinking / интернет / память.
 * Пустое значение селекта = null = наследовать из пользовательских дефолтов.
 */
export function ChatSettingsForm({ chat, models, settings, busy, onPatch }: ChatSettingsFormProps) {
  const effectiveModelId = chat.model_id ?? settings?.default_model_id ?? null;
  const effectiveModel = models.find((m) => m.model_id === effectiveModelId) ?? null;
  const thinkingModes = effectiveModel?.thinking_modes ?? [];

  const onModelChange = (value: string) => {
    const model_id = value === "" ? null : value;
    const nextModel = models.find((m) => m.model_id === (model_id ?? settings?.default_model_id));
    const keepThinking =
      chat.thinking_setting !== null && nextModel?.thinking_modes.includes(chat.thinking_setting);
    onPatch({ model_id, ...(keepThinking ? {} : { thinking_setting: null }) });
  };

  const inheritWeb = settings ? webModeLabel(settings.web_mode) : "Авто";
  const inheritMemory = settings ? (settings.memory_enabled ? "Вкл" : "Выкл") : "Вкл";

  return (
    <>
      <div className="form-row">
        <label className="form-label">Модель</label>
        <Select
          value={chat.model_id ?? ""}
          disabled={busy}
          onChange={onModelChange}
          options={[
            { value: "", label: `По умолчанию (${modelDisplay(models, settings?.default_model_id)})` },
            ...models.map((m) => ({ value: m.model_id, label: m.display_name })),
          ]}
        />
      </div>
      <div className="form-row">
        <label className="form-label">Thinking</label>
        <Select
          value={chat.thinking_setting ?? ""}
          disabled={busy || thinkingModes.length === 0}
          onChange={(v) => onPatch({ thinking_setting: v === "" ? null : v })}
          options={[
            {
              value: "",
              label: `По умолчанию${
                effectiveModel?.default_thinking
                  ? ` (${thinkingLabel(effectiveModel.default_thinking)})`
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
          value={chat.web_mode ?? ""}
          disabled={busy}
          onChange={(v) => onPatch({ web_mode: v === "" ? null : v })}
          options={[
            { value: "", label: `По умолчанию (${inheritWeb})` },
            { value: "off", label: "Выкл" },
            { value: "auto", label: "Авто" },
            { value: "on", label: "Вкл" },
          ]}
        />
      </div>
      <div className="form-row">
        <label className="form-label">Память</label>
        <Select
          value={chat.memory_enabled === null ? "" : chat.memory_enabled ? "1" : "0"}
          disabled={busy}
          onChange={(v) => onPatch({ memory_enabled: v === "" ? null : v === "1" })}
          options={[
            { value: "", label: `По умолчанию (${inheritMemory})` },
            { value: "1", label: "Вкл" },
            { value: "0", label: "Выкл" },
          ]}
        />
      </div>
    </>
  );
}
