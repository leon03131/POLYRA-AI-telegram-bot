import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, errorMessage } from "../api/client";
import { qk, useAdminUsers, useModels } from "../api/hooks";
import type { AdminUser, GrantBody } from "../api/types";
import {
  Button,
  Chip,
  ConfirmDialog,
  EmptyState,
  Input,
  Modal,
  Section,
  Spinner,
  Toggle,
} from "../components";
import { formatDateTime, formatNumber, fromInputDateTime, grantStatusLabel, healthTone, numOrNull, toInputDateTime, userStatusLabel } from "../utils";

type AccessAction = "suspend" | "revoke" | "ban" | "unban";

const ACTION_TEXT: Record<AccessAction, { title: string; message: string; confirm: string }> = {
  suspend: {
    title: "Приостановить доступ?",
    message: "Генерации будут остановлены до возобновления доступа (новый grant).",
    confirm: "Приостановить",
  },
  revoke: {
    title: "Отозвать доступ?",
    message: "Доступ будет полностью отозван. Потребуется новый grant.",
    confirm: "Отозвать",
  },
  ban: {
    title: "Забанить пользователя?",
    message: "Пользователь не сможет пользоваться ботом и Mini App.",
    confirm: "Забанить",
  },
  unban: {
    title: "Разбанить пользователя?",
    message: "Бан будет снят. Доступ по grant восстановится, если он активен.",
    confirm: "Разбанить",
  },
};

// ---------- Grant / Extend ----------

interface GrantModalProps {
  user: AdminUser;
  mode: "grant" | "extend";
  onClose: () => void;
}

function GrantModal({ user, mode, onClose }: GrantModalProps) {
  const qc = useQueryClient();
  const grant = user.grant;

  const [permanent, setPermanent] = useState(mode === "grant" ? grant?.expires_at == null : false);
  const [expiresLocal, setExpiresLocal] = useState(
    toInputDateTime(grant?.expires_at) || "",
  );
  const [rpd, setRpd] = useState(grant?.requests_per_day?.toString() ?? "");
  const [tokenLimit, setTokenLimit] = useState(grant?.token_limit?.toString() ?? "");
  const [maxConc, setMaxConc] = useState(grant?.max_concurrent_generations?.toString() ?? "");
  const [web, setWeb] = useState(grant?.can_use_web_search ?? true);
  const [memory, setMemory] = useState(grant?.can_use_memory ?? true);
  const [note, setNote] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (body: object) =>
      api<{ ok: boolean }>(
        mode === "grant" ? "/api/admin/access/grant" : "/api/admin/access/extend",
        { method: "POST", body },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.adminUsersAll });
      onClose();
    },
  });

  const submit = () => {
    setFormError(null);
    const expires_at = permanent ? null : fromInputDateTime(expiresLocal);
    if (!permanent && !expires_at) {
      setFormError("Укажите дату окончания или отметьте «Бессрочно»");
      return;
    }
    if (mode === "extend") {
      if (!expires_at) {
        setFormError("Укажите новую дату окончания");
        return;
      }
      save.mutate({ telegram_user_id: user.telegram_user_id, expires_at });
      return;
    }
    const body: GrantBody = {
      telegram_user_id: user.telegram_user_id,
      expires_at,
      requests_per_day: numOrNull(rpd),
      token_limit: numOrNull(tokenLimit),
      max_concurrent_generations: numOrNull(maxConc),
      can_use_web_search: web,
      can_use_memory: memory,
    };
    if (note.trim()) body.note = note.trim();
    save.mutate(body);
  };

  return (
    <Modal
      open
      title={mode === "grant" ? "Выдать доступ" : "Продлить доступ"}
      onClose={onClose}
    >
      <div className="hint-text">
        {user.first_name ?? "—"} {user.username ? `(@${user.username})` : ""} · id{" "}
        {user.telegram_user_id}
      </div>

      <div className="form-row inline" style={{ padding: 0 }}>
        <span>Бессрочно</span>
        <Toggle checked={permanent} onChange={setPermanent} />
      </div>

      {!permanent && (
        <div className="form-row" style={{ padding: 0 }}>
          <label className="form-label">Действует до</label>
          <Input
            type="datetime-local"
            value={expiresLocal}
            onChange={(e) => setExpiresLocal(e.target.value)}
          />
        </div>
      )}

      {mode === "grant" && (
        <>
          <div className="form-row" style={{ padding: 0 }}>
            <label className="form-label">Запросов в день (пусто — без лимита)</label>
            <Input type="number" min={1} value={rpd} onChange={(e) => setRpd(e.target.value)} />
          </div>
          <div className="form-row" style={{ padding: 0 }}>
            <label className="form-label">Токен-лимит (пусто — без лимита)</label>
            <Input
              type="number"
              min={1}
              value={tokenLimit}
              onChange={(e) => setTokenLimit(e.target.value)}
            />
          </div>
          <div className="form-row" style={{ padding: 0 }}>
            <label className="form-label">Макс. параллельных генераций</label>
            <Input type="number" min={1} value={maxConc} onChange={(e) => setMaxConc(e.target.value)} />
          </div>
          <div className="form-row inline" style={{ padding: 0 }}>
            <span>Веб-поиск</span>
            <Toggle checked={web} onChange={setWeb} />
          </div>
          <div className="form-row inline" style={{ padding: 0 }}>
            <span>Память</span>
            <Toggle checked={memory} onChange={setMemory} />
          </div>
          <div className="form-row" style={{ padding: 0 }}>
            <label className="form-label">Заметка</label>
            <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="необязательно" />
          </div>
        </>
      )}

      {formError && <div className="error-text">{formError}</div>}
      {save.error && <div className="error-text">{errorMessage(save.error)}</div>}

      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>
          Отмена
        </Button>
        <Button loading={save.isPending} onClick={submit}>
          {mode === "grant" ? "Выдать" : "Продлить"}
        </Button>
      </div>
    </Modal>
  );
}

// ---------- Model permissions ----------

function ModelsModal({ user, onClose }: { user: AdminUser; onClose: () => void }) {
  const qc = useQueryClient();
  const modelsQ = useModels();
  const userModelsQ = useQuery({
    queryKey: qk.userModels(user.telegram_user_id),
    queryFn: () =>
      api<{ allowed_models: string[] | null }>(`/api/admin/users/${user.telegram_user_id}/models`),
  });

  const [allowAll, setAllowAll] = useState<boolean | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (userModelsQ.data && allowAll === null) {
      const am = userModelsQ.data.allowed_models;
      setAllowAll(am === null);
      setSelected(new Set(am ?? (modelsQ.data?.models ?? []).map((m) => m.model_id)));
    }
  }, [userModelsQ.data, allowAll, modelsQ.data]);

  const save = useMutation({
    mutationFn: () =>
      api<{ ok: boolean }>(`/api/admin/users/${user.telegram_user_id}/models`, {
        method: "PUT",
        body: { allowed_models: allowAll ? null : Array.from(selected) },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.adminUsersAll });
      onClose();
    },
  });

  const toggleModel = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const models = modelsQ.data?.models ?? [];

  return (
    <Modal open title="Доступные модели" onClose={onClose}>
      <div className="hint-text">
        {user.first_name ?? "—"} {user.username ? `(@${user.username})` : ""}
      </div>

      {userModelsQ.isLoading || allowAll === null ? (
        <Spinner center />
      ) : (
        <>
          <div className="form-row inline" style={{ padding: 0 }}>
            <span>Все модели</span>
            <Toggle checked={allowAll} onChange={setAllowAll} />
          </div>
          {!allowAll && (
            <div>
              {models.length === 0 && <div className="hint-text">Список моделей пуст.</div>}
              {models.map((m) => (
                <label key={m.model_id} className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={selected.has(m.model_id)}
                    onChange={() => toggleModel(m.model_id)}
                  />
                  <span>
                    {m.display_name} <span className="hint-text">({m.provider})</span>
                  </span>
                </label>
              ))}
            </div>
          )}
          {save.error && <div className="error-text">{errorMessage(save.error)}</div>}
          <div className="modal-actions">
            <Button variant="secondary" onClick={onClose}>
              Отмена
            </Button>
            <Button loading={save.isPending} onClick={() => save.mutate()}>
              Сохранить
            </Button>
          </div>
        </>
      )}
    </Modal>
  );
}

// ---------- User card ----------

interface UserCardProps {
  user: AdminUser;
  onGrant: () => void;
  onExtend: () => void;
  onAction: (action: AccessAction) => void;
  onModels: () => void;
}

function UserCard({ user, onGrant, onExtend, onAction, onModels }: UserCardProps) {
  const g = user.grant;
  const banned = user.status === "banned";
  return (
    <div className="card">
      <div className="row-between">
        <div>
          <strong>{user.first_name ?? "—"}</strong>{" "}
          {user.username && <span className="hint-text">@{user.username}</span>}
          {user.is_owner && <Chip tone="accent">owner</Chip>}
        </div>
        <Chip tone={healthTone(user.status)}>{userStatusLabel(user.status)}</Chip>
      </div>

      <div className="hint-text mono">id: {user.telegram_user_id}</div>

      <div className="hint-text">
        Доступ: <Chip tone={healthTone(g?.status ?? "")}>{grantStatusLabel(g?.status)}</Chip>
        {g?.expires_at && <> до {formatDateTime(g.expires_at)}</>}
        {g && g.expires_at === null && g.status === "active" && <> · бессрочно</>}
      </div>

      {g && (
        <div className="hint-text">
          Лимиты: {g.requests_per_day === null ? "∞" : formatNumber(g.requests_per_day)} зап/день
          {" · "}токены {g.token_limit === null ? "∞" : formatNumber(g.token_limit)}
          {" · "}параллель {g.max_concurrent_generations ?? "—"}
          {" · "}веб {g.can_use_web_search ? "✅" : "❌"} · память{" "}
          {g.can_use_memory ? "✅" : "❌"}
        </div>
      )}

      <div className="hint-text">
        Модели: {user.allowed_models === null ? "все" : `${user.allowed_models.length} шт.`}
        {" · "}активен {formatDateTime(user.last_seen_at)}
      </div>

      {!user.is_owner && (
        <div className="row-between wrap" style={{ justifyContent: "flex-start" }}>
          <Button size="small" onClick={onGrant}>
            Grant
          </Button>
          <Button size="small" variant="secondary" onClick={onExtend}>
            Продлить
          </Button>
          <Button size="small" variant="secondary" onClick={() => onAction("suspend")}>
            Suspend
          </Button>
          <Button size="small" variant="secondary" onClick={() => onAction("revoke")}>
            Revoke
          </Button>
          <Button size="small" variant="secondary" onClick={onModels}>
            Модели
          </Button>
          {banned ? (
            <Button size="small" variant="secondary" onClick={() => onAction("unban")}>
              Unban
            </Button>
          ) : (
            <Button size="small" variant="danger" onClick={() => onAction("ban")}>
              Ban
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

// ---------- Page ----------

export function AdminUsersPage() {
  const qc = useQueryClient();
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");

  useEffect(() => {
    const t = setTimeout(() => setDebounced(query.trim()), 300);
    return () => clearTimeout(t);
  }, [query]);

  const usersQ = useAdminUsers(debounced);

  const [grantById, setGrantById] = useState("");
  const [grantTarget, setGrantTarget] = useState<{ user: AdminUser; mode: "grant" | "extend" } | null>(null);
  const [modelsTarget, setModelsTarget] = useState<AdminUser | null>(null);
  const [confirm, setConfirm] = useState<{ user: AdminUser; action: AccessAction } | null>(null);

  const actionMut = useMutation({
    mutationFn: ({ action, tgId }: { action: AccessAction; tgId: number }) => {
      const path =
        action === "ban" || action === "unban"
          ? `/api/admin/users/${action}`
          : `/api/admin/access/${action}`;
      return api<{ ok: boolean }>(path, { method: "POST", body: { telegram_user_id: tgId } });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.adminUsersAll });
      setConfirm(null);
    },
  });

  const users = usersQ.data?.users ?? [];

  const openGrantById = () => {
    const tgId = Number(grantById.trim());
    if (!Number.isInteger(tgId) || tgId <= 0) return;
    // Пользователя может ещё не быть в БД — backend создаст его при grant.
    const stub: AdminUser = {
      id: "",
      telegram_user_id: tgId,
      username: null,
      first_name: null,
      status: "active",
      is_owner: false,
      first_seen_at: "",
      last_seen_at: "",
      grant: null,
      allowed_models: null,
    };
    setGrantTarget({ user: stub, mode: "grant" });
  };

  return (
    <div className="page">
      <Section title="Выдать доступ по Telegram ID">
        <div className="hint-text">
          Пользователю необязательно писать боту заранее — запись будет создана автоматически.
        </div>
        <div className="row-inline">
          <Input
            placeholder="например, 795063564"
            inputMode="numeric"
            value={grantById}
            onChange={(e) => setGrantById(e.target.value)}
          />
          <Button onClick={openGrantById} disabled={!grantById.trim()}>
            Выдать
          </Button>
        </div>
      </Section>

      <div className="search-input">
        <Input
          placeholder="Поиск: telegram id или username"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {usersQ.isLoading && <Spinner center />}
      {usersQ.error && <EmptyState icon="⚠️" text={errorMessage(usersQ.error)} />}
      {!usersQ.isLoading && !usersQ.error && users.length === 0 && (
        <EmptyState icon="👤" text="Пользователи не найдены." />
      )}

      <div className="gap-list">
        {users.map((u) => (
          <UserCard
            key={u.telegram_user_id}
            user={u}
            onGrant={() => setGrantTarget({ user: u, mode: "grant" })}
            onExtend={() => setGrantTarget({ user: u, mode: "extend" })}
            onAction={(action) => setConfirm({ user: u, action })}
            onModels={() => setModelsTarget(u)}
          />
        ))}
      </div>

      {grantTarget && (
        <GrantModal
          user={grantTarget.user}
          mode={grantTarget.mode}
          onClose={() => setGrantTarget(null)}
        />
      )}

      {modelsTarget && <ModelsModal user={modelsTarget} onClose={() => setModelsTarget(null)} />}

      {confirm && (
        <ConfirmDialog
          open
          title={ACTION_TEXT[confirm.action].title}
          message={`${ACTION_TEXT[confirm.action].message} (${confirm.user.first_name ?? ""} · id ${confirm.user.telegram_user_id})`}
          confirmText={ACTION_TEXT[confirm.action].confirm}
          destructive={confirm.action !== "unban"}
          loading={actionMut.isPending}
          error={actionMut.error ? errorMessage(actionMut.error) : null}
          onCancel={() => setConfirm(null)}
          onConfirm={() =>
            actionMut.mutate({ action: confirm.action, tgId: confirm.user.telegram_user_id })
          }
        />
      )}
    </div>
  );
}
