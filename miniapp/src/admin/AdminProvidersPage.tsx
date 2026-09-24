import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, errorMessage } from "../api/client";
import { qk, useAlibabaStatus } from "../api/hooks";
import type { ProviderTestResult } from "../api/types";
import { Button, Chip, EmptyState, Input, Section, Spinner } from "../components";

export function AdminProvidersPage() {
  const qc = useQueryClient();
  const alibabaQ = useAlibabaStatus();
  const [apiKey, setApiKey] = useState("");
  const [saved, setSaved] = useState(false);
  const [smokeResult, setSmokeResult] = useState<ProviderTestResult | null>(null);

  const setKey = useMutation({
    mutationFn: () =>
      api<{ ok: boolean }>("/api/admin/providers/alibaba/key", {
        method: "POST",
        body: { api_key: apiKey.trim() },
      }),
    onSuccess: () => {
      setApiKey("");
      setSaved(true);
      void qc.invalidateQueries({ queryKey: qk.alibaba });
    },
  });

  const smoke = useMutation({
    mutationFn: () =>
      api<ProviderTestResult>("/api/admin/providers/alibaba/smoke", { method: "POST" }),
    onSuccess: (data) => setSmokeResult(data),
    onError: (e) => setSmokeResult({ ok: false, latency_ms: 0, error: errorMessage(e) }),
  });

  if (alibabaQ.isLoading) {
    return (
      <div className="page">
        <Spinner center />
      </div>
    );
  }

  if (alibabaQ.error || !alibabaQ.data) {
    return (
      <div className="page">
        <EmptyState icon="⚠️" text={errorMessage(alibabaQ.error)} />
      </div>
    );
  }

  const a = alibabaQ.data;

  return (
    <div className="page">
      <Section title="Alibaba (DashScope)">
        <div className="form-row inline">
          <span>Статус</span>
          <Chip tone={a.configured ? "ok" : "err"}>
            {a.configured ? "настроен" : "не настроен"}
          </Chip>
        </div>
        <div className="form-row inline">
          <span>Ключ</span>
          <span className="mono hint-text">{a.key_hint ?? "—"}</span>
        </div>
        <div className="form-row inline">
          <span>Включён</span>
          <span>{a.enabled ? "✅" : "❌"}</span>
        </div>
        <div className="form-row">
          <label className="form-label">Base URL (только чтение)</label>
          <Input value={a.base_url} readOnly />
        </div>
        <div className="form-row">
          <div className="row-between">
            <Button
              size="small"
              variant="secondary"
              loading={smoke.isPending}
              disabled={!a.configured}
              onClick={() => smoke.mutate()}
            >
              🔍 Run Smoke Test
            </Button>
            {smokeResult && (
              <span className={smokeResult.ok ? "hint-text" : "error-text"}>
                {smokeResult.ok
                  ? `✅ OK · ${smokeResult.latency_ms} мс`
                  : `❌ ${smokeResult.error ?? "ошибка"}`}
              </span>
            )}
          </div>
        </div>
      </Section>

      <Section
        title="Установить ключ"
        footer="Полный ключ никогда не отображается — только подсказка (первые/последние символы)."
      >
        <div className="form-row">
          <Input
            type="password"
            placeholder="sk-..."
            value={apiKey}
            autoComplete="off"
            onChange={(e) => {
              setApiKey(e.target.value);
              setSaved(false);
            }}
          />
        </div>
        <div className="form-row">
          <Button
            size="small"
            disabled={!apiKey.trim()}
            loading={setKey.isPending}
            onClick={() => setKey.mutate()}
          >
            Сохранить ключ
          </Button>
        </div>
      </Section>

      {setKey.error && <div className="error-text">{errorMessage(setKey.error)}</div>}
      {saved && !setKey.error && <div className="hint-text">✅ Ключ сохранён.</div>}
    </div>
  );
}
