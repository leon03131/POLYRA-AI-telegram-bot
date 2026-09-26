import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, errorMessage } from "../api/client";
import { qk, useModels, useSystemSettings } from "../api/hooks";
import type { SystemSettings } from "../api/types";
import { Button, EmptyState, Input, Section, Select, Spinner, Textarea } from "../components";
import { thinkingLabel } from "../utils";

export function AdminSystemPage() {
  const qc = useQueryClient();
  const systemQ = useSystemSettings();
  const modelsQ = useModels();

  const [form, setForm] = useState<SystemSettings | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (systemQ.data && form === null) setForm(systemQ.data);
  }, [systemQ.data, form]);

  const save = useMutation({
    mutationFn: (body: SystemSettings) =>
      api<{ ok: boolean }>("/api/admin/system", { method: "PUT", body }),
    onSuccess: () => {
      setSaved(true);
      void qc.invalidateQueries({ queryKey: qk.system });
    },
  });

  if (systemQ.isLoading || modelsQ.isLoading || !form) {
    return (
      <div className="page">
        {systemQ.error ? <EmptyState icon="⚠️" text={errorMessage(systemQ.error)} /> : <Spinner center />}
      </div>
    );
  }

  const models = modelsQ.data?.models ?? [];
  const selectedModel = models.find((m) => m.model_id === form.default_model) ?? null;
  const thinkingModes = selectedModel?.thinking_modes ?? [];

  const set = <K extends keyof SystemSettings>(key: K, value: SystemSettings[K]) => {
    setSaved(false);
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));
  };

  const onModelChange = (value: string) => {
    const model = value === "" ? null : value;
    const next = models.find((m) => m.model_id === model);
    const keep = form.default_thinking !== null && next?.thinking_modes.includes(form.default_thinking);
    setForm((prev) =>
      prev
        ? { ...prev, default_model: model, default_thinking: keep ? prev.default_thinking : null }
        : prev,
    );
    setSaved(false);
  };

  return (
    <div className="page">
      <Section title="Модель по умолчанию">
        <div className="form-row">
          <label className="form-label">Модель</label>
          <Select
            value={form.default_model ?? ""}
            onChange={onModelChange}
            options={[
              { value: "", label: "Не задана" },
              ...models.map((m) => ({ value: m.model_id, label: m.display_name })),
            ]}
          />
        </div>
        <div className="form-row">
          <label className="form-label">Thinking</label>
          <Select
            value={form.default_thinking ?? ""}
            disabled={thinkingModes.length === 0}
            onChange={(v) => set("default_thinking", v === "" ? null : v)}
            options={[
              { value: "", label: "Не задан" },
              ...thinkingModes.map((m) => ({ value: m, label: thinkingLabel(m) })),
            ]}
          />
        </div>
      </Section>

      <Section title="Системный промпт по умолчанию">
        <div className="form-row">
          <Textarea
            rows={6}
            value={form.default_system_prompt ?? ""}
            placeholder="Не задан"
            onChange={(e) => set("default_system_prompt", e.target.value === "" ? null : e.target.value)}
          />
        </div>
      </Section>

      <Section title="Лимиты и контекст">
        <div className="form-row">
          <label className="form-label">Макс. итераций инструментов (max_tool_iterations)</label>
          <Input
            type="number"
            min={1}
            value={form.max_tool_iterations}
            onChange={(e) => set("max_tool_iterations", Number(e.target.value))}
          />
        </div>
        <div className="form-row">
          <label className="form-label">Недавних сообщений в контексте (context_keep_recent)</label>
          <Input
            type="number"
            min={1}
            value={form.context_keep_recent}
            onChange={(e) => set("context_keep_recent", Number(e.target.value))}
          />
        </div>
        <div className="form-row">
          <label className="form-label">Порог сжатия контекста, 0–1 (context_trigger_ratio)</label>
          <Input
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={form.context_trigger_ratio}
            onChange={(e) => set("context_trigger_ratio", Number(e.target.value))}
          />
        </div>
        <div className="form-row">
          <label className="form-label">Лимит извлечения памяти (memory_retrieval_limit)</label>
          <Input
            type="number"
            min={1}
            value={form.memory_retrieval_limit}
            onChange={(e) => set("memory_retrieval_limit", Number(e.target.value))}
          />
        </div>
        <div className="form-row">
          <label className="form-label">
            Мин. объём сообщения для извлечения памяти (memory_extraction_min_chars)
          </label>
          <Input
            type="number"
            min={1}
            value={form.memory_extraction_min_chars}
            onChange={(e) => set("memory_extraction_min_chars", Number(e.target.value))}
          />
        </div>
      </Section>

      {save.error && <div className="error-text">{errorMessage(save.error)}</div>}
      {saved && !save.error && <div className="hint-text">✅ Сохранено.</div>}

      <Button loading={save.isPending} onClick={() => save.mutate(form)}>
        Сохранить
      </Button>
    </div>
  );
}
