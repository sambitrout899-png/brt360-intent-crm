import { useCallback, useEffect, useMemo, useState } from "react";
import { fsApi, listParties } from "../../api/client";
import type { Party } from "../../api/types";
import { useIntentRefresh } from "../../workspace";
import ActivityPanel, { ChargeLines } from "./ActivityPanel";
import type { FsAsset, FsRequest, Template } from "./types";
import { BTN, BTN_GO, BTN_PRIMARY, CARD, Chip, Drawer, Empty, Field, INPUT, Notice, Panel, PriorityTag, SlaBadge, StatusPill, Tabs } from "./ui";
import { fmtWhen, money, useAction } from "./style";

const TICKET_PRIORITY: Record<string, number> = { P1: 1, P2: 2, P3: 3, P4: 4 };

/** `openId` opens one request on arrival (from the overview, an asset or a maintenance action). */
export default function Requests({ onRequestIntent, openId }: { onRequestIntent: (t: string) => void; openId?: number | null }) {
  const [filter, setFilter] = useState<"open" | "closed" | "all">("open");
  const [rows, setRows] = useState<FsRequest[]>([]);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<number | null>(openId ?? null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(() => {
    fsApi<FsRequest[]>(`/requests?status=${filter}`).then(setRows).catch(() => setRows([]));
  }, [filter]);
  useEffect(load, [load]);
  useIntentRefresh(load);

  const shown = useMemo(() => {
    const k = q.trim().toLowerCase();
    return k ? rows.filter((r) => `${r.id} ${r.description} ${r.party_name ?? ""} ${r.asset_label ?? ""}`.toLowerCase().includes(k)) : rows;
  }, [rows, q]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Tabs
          value={filter}
          onChange={setFilter}
          items={[
            { id: "open", label: "Open" },
            { id: "closed", label: "Resolved & closed" },
            { id: "all", label: "All" },
          ]}
        />
        <span className="flex-1" />
        <input className={`${INPUT} w-full sm:w-64`} placeholder="Search requests…" value={q} onChange={(e) => setQ(e.target.value)} />
        <button type="button" className={BTN_PRIMARY} onClick={() => setCreating(true)}>
          + Service request
        </button>
      </div>

      {shown.length === 0 ? (
        <Empty>No service requests here.</Empty>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {shown.map((r) => (
            <button key={r.id} type="button" onClick={() => setSelected(r.id)} className={`${CARD} flex h-full flex-col p-4 text-left transition hover:border-slate-600`}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs text-slate-500">SR {r.id}</span>
                <PriorityTag priority={TICKET_PRIORITY[r.priority] ?? 3} label={r.priority} />
                <span className="flex-1" />
                <SlaBadge sla={r.verified ? r.sla : "none"} committed={r.committed_at} />
              </div>
              <p className="mt-2 line-clamp-2 text-sm font-semibold text-slate-100">{r.description}</p>
              <p className="mt-1 truncate text-xs text-slate-400">
                {r.party_name ?? "No customer"} {r.asset_label && <>· {r.asset_label}</>}
              </p>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {r.source === "pm" && <Chip tone="violet">preventive</Chip>}
                {r.entitlement_name && <Chip tone="sky">{r.entitlement_name}</Chip>}
                {r.warranty_name && <Chip tone="emerald">warranty</Chip>}
                {r.verified && !r.entitlement_name && <Chip tone="amber">billable</Chip>}
              </div>
              <div className="mt-auto flex items-center gap-2 border-t border-slate-800 pt-3 text-xs text-slate-400" style={{ marginTop: "auto" }}>
                <StatusPill status={r.status} />
                <span className="flex-1" />
                <span>
                  {r.open_activities}/{r.activity_count} open activit{r.activity_count === 1 ? "y" : "ies"}
                </span>
              </div>
            </button>
          ))}
        </div>
      )}

      <Drawer open={selected !== null} onClose={() => setSelected(null)} title={selected ? `Service request ${selected}` : ""} wide>
        {selected && <RequestDetail id={selected} onChanged={load} onRequestIntent={onRequestIntent} />}
      </Drawer>
      <Drawer open={creating} onClose={() => setCreating(false)} title="New service request" subtitle="Who called, about what - it is verified straight away.">
        {creating && (
          <NewRequest
            onRequestIntent={onRequestIntent}
            onCreated={(id) => {
              setCreating(false);
              load();
              setSelected(id);
            }}
          />
        )}
      </Drawer>
    </div>
  );
}

function NewRequest({ onCreated, onRequestIntent }: { onCreated: (id: number) => void; onRequestIntent: (t: string) => void }) {
  const [parties, setParties] = useState<Party[]>([]);
  const [partyQ, setPartyQ] = useState("");
  const [partyId, setPartyId] = useState<number | "">("");
  const [assets, setAssets] = useState<FsAsset[]>([]);
  const [form, setForm] = useState({ description: "", asset_id: "", severity: "" });
  const act = useAction();
  useEffect(() => {
    const t = setTimeout(() => listParties(partyQ || undefined).then((p) => setParties(p.slice(0, 50))).catch(() => setParties([])), 200);
    return () => clearTimeout(t);
  }, [partyQ]);
  const pickParty = (id: number | "") => {
    setPartyId(id);
    setAssets([]);
    setForm((f) => ({ ...f, asset_id: "" }));
    if (id) fsApi<FsAsset[]>(`/assets?party_id=${id}`).then(setAssets).catch(() => setAssets([]));
  };

  return (
    <div className="space-y-3">
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <Field label="Customer">
        <input className={INPUT} placeholder="Search customers…" value={partyQ} onChange={(e) => setPartyQ(e.target.value)} />
        <select className={`${INPUT} mt-2`} value={partyId} onChange={(e) => pickParty(e.target.value ? Number(e.target.value) : "")}>
          <option value="">— choose —</option>
          {parties.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Asset" hint={partyId && !assets.length ? "This customer has no registered assets yet." : undefined}>
        <select className={INPUT} value={form.asset_id} onChange={(e) => setForm({ ...form, asset_id: e.target.value })}>
          <option value="">— none —</option>
          {assets.map((a) => (
            <option key={a.id} value={a.id}>
              {a.label} {a.warranty ? "· under warranty" : ""}
            </option>
          ))}
        </select>
      </Field>
      <Field label="What's wrong">
        <textarea className={INPUT} rows={3} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="e.g. Internet down at the branch since 9am" />
      </Field>
      <Field label="Severity" hint="Left empty, it is read from the description.">
        <select className={INPUT} value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value })}>
          <option value="">Automatic</option>
          {["critical", "high", "medium", "low"].map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </Field>
      <div className="flex flex-wrap gap-2 pt-2">
        <button
          type="button"
          className={BTN_PRIMARY}
          disabled={!form.description.trim() || act.busy}
          onClick={async () => {
            const r = await act.run(() =>
              fsApi<FsRequest>("/requests", "POST", { description: form.description, party_id: partyId || null, asset_id: form.asset_id ? Number(form.asset_id) : null, severity: form.severity || null })
            );
            if (r) onCreated(r.id);
          }}
        >
          Log & verify
        </button>
        <button type="button" className={BTN} onClick={() => onRequestIntent("Log & classify this complaint. description: ")}>
          Use the intent bar instead
        </button>
      </div>
    </div>
  );
}

function RequestDetail({ id, onChanged, onRequestIntent }: { id: number; onChanged: () => void; onRequestIntent: (t: string) => void }) {
  const [r, setR] = useState<FsRequest | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [activity, setActivity] = useState<number | null>(null);
  const [assetId, setAssetId] = useState<string>("");
  const [entId, setEntId] = useState<string>("");
  const [newAct, setNewAct] = useState({ template_id: "", subject: "" });
  const [closing, setClosing] = useState({ resolution_code: "", notes: "" });
  const load = useCallback(() => {
    fsApi<FsRequest>(`/requests/${id}`).then((x) => {
      setR(x);
      setAssetId(x.asset_id ? String(x.asset_id) : "");
    });
  }, [id]);
  useEffect(load, [load]);
  useEffect(() => {
    fsApi<Template[]>("/templates").then((t) => setTemplates(t.filter((x) => x.active))).catch(() => setTemplates([]));
  }, []);
  const after = useCallback(() => {
    load();
    onChanged();
  }, [load, onChanged]);
  const act = useAction(after);

  if (!r) return <p className="text-sm text-slate-500">Loading…</p>;
  if (activity) {
    return (
      <div className="space-y-3">
        <button type="button" className={BTN} onClick={() => setActivity(null)}>
          ← Service request {r.id}
        </button>
        <h3 className="text-lg font-bold text-slate-100">{r.activities?.find((a) => a.id === activity)?.subject}</h3>
        <ActivityPanel activityId={activity} onChanged={after} onRequestIntent={onRequestIntent} />
      </div>
    );
  }
  const closed = r.status === "closed";
  return (
    <div className="space-y-4">
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <Notice text={act.done} tone="emerald" onClose={() => act.setDone(null)} />
      <div className={`${CARD} p-4`}>
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill status={r.status} />
          <PriorityTag priority={TICKET_PRIORITY[r.priority] ?? 3} label={`${r.priority} · ${r.severity}`} />
          <span className="flex-1" />
          <SlaBadge sla={r.verified ? r.sla : "none"} committed={r.committed_at} />
        </div>
        <p className="mt-3 text-base font-semibold text-slate-100">{r.description}</p>
        <div className="mt-3 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
          <Info label="Customer" value={r.party_name ?? "—"} />
          <Info label="Opened" value={fmtWhen(r.opened_at)} />
          <Info label="Committed" value={r.committed_at ? fmtWhen(r.committed_at) : "—"} />
          <Info label="Source" value={r.source === "pm" ? "Preventive maintenance" : "Customer call"} />
        </div>
        {r.root_cause && (
          <p className="mt-3 rounded-lg bg-slate-950/60 px-3 py-2 text-xs text-slate-400">
            <span className="font-semibold text-slate-300">Diagnosis:</span> {r.root_cause}
          </p>
        )}
        {!r.root_cause && !closed && (
          <button type="button" className={`${BTN} mt-3`} onClick={() => onRequestIntent(`Diagnose the root cause. ticket_id: ${r.id}`)}>
            Try known fixes (diagnose)…
          </button>
        )}
      </div>

      <Panel title="1 · Verify - asset, entitlement, warranty">
        {r.verify_note && <p className="mb-3 text-sm text-slate-300">{r.verify_note}</p>}
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label="Asset">
            <select className={INPUT} value={assetId} onChange={(e) => setAssetId(e.target.value)} disabled={closed}>
              <option value="">— none —</option>
              {(r.customer_assets ?? []).map((a) => (
                <option key={a.id} value={a.id}>
                  {a.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Entitlement">
            <select className={INPUT} value={entId} onChange={(e) => setEntId(e.target.value)} disabled={closed}>
              <option value="">Best available</option>
              {(r.candidates ?? []).map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name} · {e.response_hours}h {e.coverage === "24x7" ? "24x7" : "business hrs"}
                </option>
              ))}
            </select>
          </Field>
        </div>
        {(r.candidates ?? []).length === 0 && r.party_id && <p className="mt-2 text-xs text-amber-300">No current entitlement covers this - the work is billable.</p>}
        <button
          type="button"
          className={`${BTN_PRIMARY} mt-3`}
          disabled={act.busy || closed}
          onClick={() => act.run(() => fsApi(`/requests/${r.id}/verify`, "POST", { asset_id: assetId ? Number(assetId) : null, entitlement_id: entId ? Number(entId) : null }), "Verified")}
        >
          {r.verified ? "Verify again" : "Verify"}
        </button>
      </Panel>

      <Panel
        title={`2 · Activities (${r.activities?.length ?? 0})`}
        action={r.field_dispatch_required ? <Chip tone="amber">diagnosis says: dispatch an engineer</Chip> : undefined}
      >
        <div className="space-y-2">
          {(r.activities ?? []).map((a) => (
            <button key={a.id} type="button" onClick={() => setActivity(a.id)} className="flex w-full items-center gap-3 rounded-xl border border-slate-800 bg-slate-900/40 px-3 py-2.5 text-left hover:border-slate-600">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-slate-100">{a.subject}</p>
                <p className="truncate text-xs text-slate-400">
                  {a.technician_name ?? "No engineer"} · {a.planned_start ? fmtWhen(a.planned_start) : "not scheduled"}
                  {a.steps_total > 0 && ` · ${a.steps_done}/${a.steps_total} steps`}
                </p>
              </div>
              <StatusPill status={a.status} />
            </button>
          ))}
          {(r.activities ?? []).length === 0 && <p className="text-sm text-slate-500">No activity yet. Add one from a template.</p>}
        </div>
        {!closed && (
          <div className="mt-3 grid gap-2 border-t border-slate-800 pt-3 sm:grid-cols-[1fr_1fr_auto]">
            <select className={INPUT} value={newAct.template_id} onChange={(e) => setNewAct({ ...newAct, template_id: e.target.value })}>
              <option value="">From template…</option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} · {t.duration_minutes} min
                </option>
              ))}
            </select>
            <input className={INPUT} placeholder="…or describe the job" value={newAct.subject} onChange={(e) => setNewAct({ ...newAct, subject: e.target.value })} />
            <button
              type="button"
              className={BTN_GO}
              disabled={act.busy || (!newAct.template_id && !newAct.subject.trim())}
              onClick={async () => {
                const created = await act.run(() =>
                  fsApi<{ id: number }>(`/requests/${r.id}/activities`, "POST", { template_id: newAct.template_id ? Number(newAct.template_id) : null, subject: newAct.subject })
                );
                if (created) {
                  setNewAct({ template_id: "", subject: "" });
                  setActivity(created.id);
                }
              }}
            >
              + Add
            </button>
          </div>
        )}
      </Panel>

      <Panel
        title="3 · Charges"
        action={
          (r.charges?.pending ?? 0) > 0 || (r.charges?.lines ?? []).some((l) => l.status === "pending") ? (
            <button type="button" className={BTN_GO} disabled={act.busy} onClick={() => act.run(() => fsApi(`/requests/${r.id}/invoice`, "POST"), "Invoiced")}>
              Invoice {money(r.charges?.pending ?? 0)}
            </button>
          ) : undefined
        }
      >
        {(r.charges?.lines.length ?? 0) > 0 ? <ChargeLines charges={r.charges!} /> : <p className="text-sm text-slate-500">Charges appear when an activity is completed.</p>}
      </Panel>

      {!closed && (
        <Panel title="4 · Close">
          <div className="grid gap-2 sm:grid-cols-[1fr_2fr_auto]">
            <input className={INPUT} placeholder="Resolution code" value={closing.resolution_code} onChange={(e) => setClosing({ ...closing, resolution_code: e.target.value })} />
            <input className={INPUT} placeholder="Notes" value={closing.notes} onChange={(e) => setClosing({ ...closing, notes: e.target.value })} />
            <button type="button" className={BTN_PRIMARY} disabled={act.busy || r.open_activities > 0} onClick={() => act.run(() => fsApi(`/requests/${r.id}/close`, "POST", closing), "Closed")}>
              Close request
            </button>
          </div>
          {r.open_activities > 0 && <p className="mt-2 text-xs text-slate-500">Complete or cancel the open activities first.</p>}
        </Panel>
      )}
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{label}</p>
      <p className="truncate text-slate-200">{value}</p>
    </div>
  );
}
