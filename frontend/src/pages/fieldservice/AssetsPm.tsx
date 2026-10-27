import { useCallback, useEffect, useState } from "react";
import { fsApi, listParties } from "../../api/client";
import type { Party } from "../../api/types";
import type { FsAsset, PmAction, PmPlan, PmTrigger, Region, Template } from "./types";
import { BTN, BTN_GO, BTN_PRIMARY, CARD, Chip, Drawer, Empty, Field, INPUT, Notice, Panel, Section, StatusPill, Tabs } from "./ui";
import { fmtDate, fmtWhen, todayIso, useAction } from "./style";

export default function AssetsPm({ onOpenRequest }: { onOpenRequest: (id: number) => void }) {
  const [tab, setTab] = useState<"assets" | "plans" | "actions">("assets");
  return (
    <div className="space-y-4">
      <Tabs
        value={tab}
        onChange={setTab}
        items={[
          { id: "assets", label: "Installed base" },
          { id: "plans", label: "Maintenance plans" },
          { id: "actions", label: "Maintenance actions" },
        ]}
      />
      {tab === "assets" && <Assets onOpenRequest={onOpenRequest} />}
      {tab === "plans" && <Plans />}
      {tab === "actions" && <Actions onOpenRequest={onOpenRequest} />}
    </div>
  );
}

function Assets({ onOpenRequest }: { onOpenRequest: (id: number) => void }) {
  const [rows, setRows] = useState<FsAsset[]>([]);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const load = useCallback(() => {
    fsApi<FsAsset[]>(`/assets?q=${encodeURIComponent(q)}`).then(setRows).catch(() => setRows([]));
  }, [q]);
  useEffect(() => {
    const t = setTimeout(load, 200);
    return () => clearTimeout(t);
  }, [load]);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <input className={`${INPUT} flex-1`} placeholder="Search by product, serial or customer…" value={q} onChange={(e) => setQ(e.target.value)} />
        <button type="button" className={BTN_PRIMARY} onClick={() => setAdding(true)}>
          + Register asset
        </button>
      </div>
      {rows.length === 0 ? (
        <Empty>No assets yet. Assets arrive with fulfilled orders, or register a customer's equipment here.</Empty>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {rows.map((a) => (
            <button key={a.id} type="button" onClick={() => setOpen(a.id)} className={`${CARD} flex h-full flex-col p-4 text-left transition hover:border-slate-600 ${a.status !== "active" ? "opacity-60" : ""}`}>
              <div className="flex items-start gap-2">
                <p className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-100">{a.product_name}</p>
                {a.status !== "active" && <Chip>{a.status}</Chip>}
              </div>
              <p className="truncate font-mono text-xs text-slate-400">{a.serial_number || a.identifier}</p>
              <p className="mt-1 truncate text-xs text-slate-400">{a.party_name ?? "—"}</p>
              <div className="mt-auto flex flex-wrap gap-1.5 pt-3">
                {a.warranty ? (
                  <Chip tone={a.warranty.days_left < 30 ? "amber" : "emerald"}>warranty · {a.warranty.days_left}d left</Chip>
                ) : (
                  <Chip>no warranty</Chip>
                )}
                {a.installed_on && <Chip>installed {fmtDate(a.installed_on)}</Chip>}
              </div>
            </button>
          ))}
        </div>
      )}
      <Drawer open={open !== null} onClose={() => setOpen(null)} title="Asset" wide>
        {open && <AssetDetail id={open} onChanged={load} onOpenRequest={onOpenRequest} />}
      </Drawer>
      <Drawer open={adding} onClose={() => setAdding(false)} title="Register an asset" subtitle="Equipment at a customer's site - warranties are attached automatically.">
        {adding && <RegisterAsset onDone={() => { setAdding(false); load(); }} />}
      </Drawer>
    </div>
  );
}

function RegisterAsset({ onDone }: { onDone: () => void }) {
  const [parties, setParties] = useState<Party[]>([]);
  const [partyQ, setPartyQ] = useState("");
  const [regions, setRegions] = useState<Region[]>([]);
  const [f, setF] = useState({ party_id: "", product_name: "", serial_number: "", installed_on: todayIso(), address: "", region_id: "" });
  const act = useAction();
  useEffect(() => {
    const t = setTimeout(() => listParties(partyQ || undefined).then((p) => setParties(p.slice(0, 50))).catch(() => setParties([])), 200);
    return () => clearTimeout(t);
  }, [partyQ]);
  useEffect(() => {
    fsApi<Region[]>("/regions").then(setRegions).catch(() => setRegions([]));
  }, []);
  return (
    <div className="space-y-3">
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <Field label="Customer">
        <input className={INPUT} placeholder="Search…" value={partyQ} onChange={(e) => setPartyQ(e.target.value)} />
        <select className={`${INPUT} mt-2`} value={f.party_id} onChange={(e) => setF({ ...f, party_id: e.target.value })}>
          <option value="">— choose —</option>
          {parties.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Product">
          <input className={INPUT} value={f.product_name} onChange={(e) => setF({ ...f, product_name: e.target.value })} />
        </Field>
        <Field label="Serial number">
          <input className={INPUT} value={f.serial_number} onChange={(e) => setF({ ...f, serial_number: e.target.value })} />
        </Field>
        <Field label="Installed on">
          <input className={INPUT} type="date" value={f.installed_on} onChange={(e) => setF({ ...f, installed_on: e.target.value })} />
        </Field>
        <Field label="Service region">
          <select className={INPUT} value={f.region_id} onChange={(e) => setF({ ...f, region_id: e.target.value })}>
            <option value="">—</option>
            {regions.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Field label="Site address">
        <input className={INPUT} value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} />
      </Field>
      <button
        type="button"
        className={BTN_PRIMARY}
        disabled={!f.party_id || !f.product_name.trim() || act.busy}
        onClick={async () => (await act.run(() => fsApi("/assets", "POST", { ...f, party_id: Number(f.party_id), region_id: f.region_id ? Number(f.region_id) : null }))) && onDone()}
      >
        Register
      </button>
    </div>
  );
}

function AssetDetail({ id, onChanged, onOpenRequest }: { id: number; onChanged: () => void; onOpenRequest: (id: number) => void }) {
  const [a, setA] = useState<FsAsset | null>(null);
  const [m, setM] = useState({ name: "", unit: "", kind: "counter", max_value: "", lower_limit: "", upper_limit: "" });
  const [reading, setReading] = useState<Record<number, string>>({});
  const load = useCallback(() => {
    fsApi<FsAsset>(`/assets/${id}`).then(setA);
  }, [id]);
  useEffect(load, [load]);
  const act = useAction(() => {
    load();
    onChanged();
  });
  if (!a) return <p className="text-sm text-slate-500">Loading…</p>;
  const num = (v: string) => (v === "" ? null : Number(v));
  return (
    <div className="space-y-4">
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <div className={`${CARD} p-4`}>
        <div className="flex flex-wrap items-center gap-2">
          <p className="min-w-0 flex-1 truncate text-lg font-bold text-slate-100">{a.product_name}</p>
          <Chip>{a.status}</Chip>
        </div>
        <div className="mt-2 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
          {[
            ["Serial", a.serial_number || a.identifier],
            ["Customer", a.party_name ?? "—"],
            ["Installed", fmtDate(a.installed_on)],
            ["Site", a.address || "—"],
          ].map(([k, v]) => (
            <div key={k} className="min-w-0">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{k}</p>
              <p className="truncate text-slate-200">{v}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Warranties" className="h-full" action={<button type="button" className={BTN} onClick={() => act.run(() => fsApi(`/assets/${a.id}/warranties`, "POST"))}>Get warranties</button>}>
          {(a.warranties ?? []).length === 0 ? (
            <p className="text-sm text-slate-500">None attached.</p>
          ) : (
            (a.warranties ?? []).map((w) => (
              <div key={w.id} className="flex items-center gap-2 py-1 text-sm">
                <span className="min-w-0 flex-1 truncate text-slate-200">{w.name}</span>
                <span className={`text-xs ${w.active ? "text-emerald-300" : "text-slate-500"}`}>until {fmtDate(w.ends_on)}</span>
              </div>
            ))
          )}
        </Panel>
        <Panel title="History" className="h-full">
          {(a.service_requests ?? []).length === 0 && (a.repairs ?? []).length === 0 && <p className="text-sm text-slate-500">No service history.</p>}
          {(a.service_requests ?? []).map((s) => (
            <button key={s.id} type="button" onClick={() => onOpenRequest(s.id)} className="flex w-full items-center gap-2 py-1 text-left text-sm hover:text-sky-300">
              <span className="font-mono text-xs text-slate-500">SR {s.id}</span>
              <span className="min-w-0 flex-1 truncate text-slate-200">{s.description}</span>
              <StatusPill status={s.status} />
            </button>
          ))}
          {(a.repairs ?? []).map((r) => (
            <p key={r.id} className="py-1 text-sm text-slate-300">
              {r.repair_number} · {r.status.replace("_", " ")}
            </p>
          ))}
        </Panel>
      </div>

      <Panel title="Measurements & readings">
        <div className="grid gap-3 md:grid-cols-2">
          {(a.measurements ?? []).map((x) => (
            <div key={x.id} className={`rounded-xl border px-3 py-2 ${x.alarm ? "border-red-500/40 bg-red-500/5" : "border-slate-800 bg-slate-900/40"}`}>
              <div className="flex items-baseline gap-2">
                <p className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-100">{x.name}</p>
                <span className="text-lg font-bold text-slate-100">{x.last_value ?? "—"}</span>
                <span className="text-xs text-slate-500">{x.unit}</span>
              </div>
              <p className="text-[11px] text-slate-500">
                {x.kind}
                {x.lower_limit !== null && ` · min ${x.lower_limit}`}
                {x.upper_limit !== null && ` · max ${x.upper_limit}`}
                {x.last_read_at && ` · ${fmtWhen(x.last_read_at)}`}
              </p>
              <Spark values={x.readings.map((r) => r.value).reverse()} />
              <div className="mt-2 flex gap-2">
                <input className={`${INPUT} py-1`} type="number" placeholder="New reading" value={reading[x.id] ?? ""} onChange={(e) => setReading({ ...reading, [x.id]: e.target.value })} />
                <button type="button" className={BTN} disabled={!reading[x.id]} onClick={() => act.run(() => fsApi(`/measurements/${x.id}/readings`, "POST", { value: Number(reading[x.id]) })).then(() => setReading({ ...reading, [x.id]: "" }))}>
                  Save
                </button>
              </div>
            </div>
          ))}
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 border-t border-slate-800 pt-3 sm:grid-cols-6">
          <input className={`${INPUT} col-span-2`} placeholder="Measurement, e.g. Copies" value={m.name} onChange={(e) => setM({ ...m, name: e.target.value })} />
          <select className={INPUT} value={m.kind} onChange={(e) => setM({ ...m, kind: e.target.value })}>
            <option value="counter">Counter</option>
            <option value="gauge">Gauge</option>
          </select>
          <input className={INPUT} placeholder="Unit" value={m.unit} onChange={(e) => setM({ ...m, unit: e.target.value })} />
          {m.kind === "counter" ? (
            <input className={`${INPUT} col-span-2`} type="number" placeholder="Rolls over after" value={m.max_value} onChange={(e) => setM({ ...m, max_value: e.target.value })} />
          ) : (
            <>
              <input className={INPUT} type="number" placeholder="Low limit" value={m.lower_limit} onChange={(e) => setM({ ...m, lower_limit: e.target.value })} />
              <input className={INPUT} type="number" placeholder="High limit" value={m.upper_limit} onChange={(e) => setM({ ...m, upper_limit: e.target.value })} />
            </>
          )}
        </div>
        <button
          type="button"
          className={`${BTN_GO} mt-2`}
          disabled={!m.name.trim() || act.busy}
          onClick={() =>
            act
              .run(() => fsApi(`/assets/${a.id}/measurements`, "POST", { name: m.name, unit: m.unit, kind: m.kind, max_value: num(m.max_value), lower_limit: num(m.lower_limit), upper_limit: num(m.upper_limit) }))
              .then(() => setM({ name: "", unit: "", kind: "counter", max_value: "", lower_limit: "", upper_limit: "" }))
          }
        >
          + Add measurement
        </button>
      </Panel>

      {(a.pm_actions ?? []).length > 0 && (
        <Panel title="Maintenance">
          {(a.pm_actions ?? []).map((p) => (
            <div key={p.id} className="flex items-center gap-2 py-1 text-sm">
              <span className="w-28 text-slate-300">{fmtDate(p.due_on)}</span>
              <span className="min-w-0 flex-1 truncate text-slate-400">{p.trigger_kind} trigger</span>
              <Chip tone={p.status === "done" ? "emerald" : "sky"}>{p.status}</Chip>
            </div>
          ))}
        </Panel>
      )}
    </div>
  );
}

function Spark({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * 100},${28 - ((v - min) / (max - min || 1)) * 24}`).join(" ");
  return (
    <svg viewBox="0 0 100 30" className="mt-1 h-8 w-full" preserveAspectRatio="none">
      <polyline points={pts} fill="none" stroke="#38bdf8" strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

const TRIGGER_TEXT = (t: PmTrigger) =>
  t.type === "interval"
    ? `every ${t.every_days} days${t.valid_start ? ` from day ${t.valid_start}` : ""}${t.valid_end ? ` to day ${t.valid_end}` : ""}`
    : t.type === "usage"
      ? `every ${t.every} ${t.measurement}`
      : t.type === "threshold"
        ? `${t.measurement} outside ${t.lower ?? "−∞"}…${t.upper ?? "∞"}`
        : `${t.requests} service requests within ${t.within_days} days`;

function Plans() {
  const [plans, setPlans] = useState<PmPlan[]>([]);
  const [editing, setEditing] = useState<PmPlan | "new" | null>(null);
  const [until, setUntil] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() + 30);
    return d.toISOString().slice(0, 10);
  });
  const [result, setResult] = useState<{ due_on: string; plan: string; asset: string; trigger: string }[] | null>(null);
  const load = useCallback(() => {
    fsApi<PmPlan[]>("/pm/plans").then(setPlans).catch(() => setPlans([]));
  }, []);
  useEffect(load, [load]);
  const act = useAction(load);
  return (
    <div className="space-y-4">
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <div className={`${CARD} flex flex-wrap items-end gap-3 p-4`}>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-slate-100">Run the maintenance engine</p>
          <p className="text-xs text-slate-400">Creates a service request and an activity for every maintenance that falls due, up to the date.</p>
        </div>
        <input type="date" className={`${INPUT} w-auto`} value={until} onChange={(e) => setUntil(e.target.value)} />
        <button
          type="button"
          className={BTN_PRIMARY}
          disabled={act.busy}
          onClick={async () => {
            const out = await act.run(() => fsApi<{ generated: { due_on: string; plan: string; asset: string; trigger: string }[] }>("/pm/run", "POST", { until }));
            if (out) setResult(out.generated);
          }}
        >
          Run
        </button>
      </div>
      {result && (
        <Notice
          tone={result.length ? "emerald" : "amber"}
          text={result.length ? `${result.length} maintenance visit${result.length === 1 ? "" : "s"} created: ${result.slice(0, 6).map((g) => `${g.asset} on ${fmtDate(g.due_on)}`).join(", ")}${result.length > 6 ? "…" : ""}` : "Nothing falls due up to that date."}
          onClose={() => setResult(null)}
        />
      )}
      <Section title="Plans" count={plans.length} action={<button type="button" className={BTN} onClick={() => setEditing("new")}>+ Plan</button>}>
        {plans.length === 0 ? (
          <Empty>No maintenance plans.</Empty>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {plans.map((p) => (
              <div key={p.id} className={`${CARD} flex h-full flex-col p-4 ${p.active ? "" : "opacity-60"}`}>
                <div className="flex items-start gap-2">
                  <p className="min-w-0 flex-1 text-sm font-semibold text-slate-100">{p.name}</p>
                  <Chip tone={p.active ? "emerald" : "slate"}>{p.active ? "active" : "off"}</Chip>
                </div>
                <p className="mt-1 text-xs text-slate-400">
                  {p.products.join(", ") || "named assets"} · {p.asset_count} asset{p.asset_count === 1 ? "" : "s"}
                </p>
                <ul className="mt-2 space-y-0.5">
                  {p.triggers.map((t, i) => (
                    <li key={i} className="text-xs text-slate-300">
                      • {TRIGGER_TEXT(t)}
                    </li>
                  ))}
                </ul>
                <div className="mt-auto flex items-center gap-2 border-t border-slate-800 pt-3 text-xs" style={{ marginTop: "auto" }}>
                  <span className="text-slate-500">
                    {p.scheduled} due · {p.done} done
                  </span>
                  <span className="flex-1" />
                  <button type="button" className={BTN} onClick={() => act.run(() => fsApi(`/pm/plans/${p.id}`, "PUT", { active: !p.active }))}>
                    {p.active ? "Switch off" : "Switch on"}
                  </button>
                  <button type="button" className={BTN} onClick={() => setEditing(p)}>
                    Edit
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>
      <Drawer open={editing !== null} onClose={() => setEditing(null)} title={editing === "new" ? "New maintenance plan" : "Maintenance plan"}>
        {editing && <PlanEditor plan={editing === "new" ? null : editing} onDone={() => { setEditing(null); load(); }} />}
      </Drawer>
    </div>
  );
}

function PlanEditor({ plan, onDone }: { plan: PmPlan | null; onDone: () => void }) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [f, setF] = useState({
    name: plan?.name ?? "",
    priority: plan?.priority ?? 3,
    optional: plan?.optional ?? false,
    active: plan?.active ?? true,
    products: (plan?.products ?? []).join(", "),
    template_id: plan?.template_id ? String(plan.template_id) : "",
    triggers: plan?.triggers ?? ([{ type: "interval", every_days: 90 }] as PmTrigger[]),
  });
  const act = useAction();
  useEffect(() => {
    fsApi<Template[]>("/templates").then(setTemplates).catch(() => setTemplates([]));
  }, []);
  const setT = (i: number, patch: Partial<PmTrigger>) => setF({ ...f, triggers: f.triggers.map((t, j) => (j === i ? { ...t, ...patch } : t)) });
  const n = (v: string) => (v === "" ? null : Number(v));
  return (
    <div className="space-y-3">
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <Field label="Name">
        <input className={INPUT} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      </Field>
      <Field label="Products it covers" hint="Comma separated - every active asset of these products.">
        <input className={INPUT} value={f.products} onChange={(e) => setF({ ...f, products: e.target.value })} />
      </Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Job template">
          <select className={INPUT} value={f.template_id} onChange={(e) => setF({ ...f, template_id: e.target.value })}>
            <option value="">—</option>
            {templates.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Priority">
          <select className={INPUT} value={f.priority} onChange={(e) => setF({ ...f, priority: Number(e.target.value) })}>
            {[1, 2, 3, 4].map((p) => (
              <option key={p} value={p}>
                {["", "1 - ASAP", "2 - High", "3 - Medium", "4 - Low"][p]}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className="flex flex-wrap gap-4 text-sm text-slate-300">
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Active
        </label>
        <label className="flex items-center gap-2" title="An optional plan skips a day a higher-priority plan already serves">
          <input type="checkbox" checked={f.optional} onChange={(e) => setF({ ...f, optional: e.target.checked })} /> Optional
        </label>
      </div>
      <div className="space-y-2">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Triggers</p>
        {f.triggers.map((t, i) => (
          <div key={i} className="grid grid-cols-2 gap-2 rounded-xl border border-slate-800 p-2 sm:grid-cols-4">
            <select className={INPUT} value={t.type} onChange={(e) => setT(i, { type: e.target.value as PmTrigger["type"] })}>
              <option value="interval">Every N days</option>
              <option value="usage">Usage</option>
              <option value="threshold">Threshold</option>
              <option value="event">Repeat failures</option>
            </select>
            {t.type === "interval" && (
              <>
                <input className={INPUT} type="number" placeholder="days" value={t.every_days ?? ""} onChange={(e) => setT(i, { every_days: Number(e.target.value) })} />
                <input className={INPUT} type="number" placeholder="from day" value={t.valid_start ?? ""} onChange={(e) => setT(i, { valid_start: n(e.target.value) })} />
                <input className={INPUT} type="number" placeholder="to day" value={t.valid_end ?? ""} onChange={(e) => setT(i, { valid_end: n(e.target.value) })} />
              </>
            )}
            {(t.type === "usage" || t.type === "threshold") && <input className={INPUT} placeholder="measurement" value={t.measurement ?? ""} onChange={(e) => setT(i, { measurement: e.target.value })} />}
            {t.type === "usage" && <input className={INPUT} type="number" placeholder="every" value={t.every ?? ""} onChange={(e) => setT(i, { every: Number(e.target.value) })} />}
            {t.type === "threshold" && (
              <>
                <input className={INPUT} type="number" placeholder="below" value={t.lower ?? ""} onChange={(e) => setT(i, { lower: n(e.target.value) })} />
                <input className={INPUT} type="number" placeholder="above" value={t.upper ?? ""} onChange={(e) => setT(i, { upper: n(e.target.value) })} />
              </>
            )}
            {t.type === "event" && (
              <>
                <input className={INPUT} type="number" placeholder="requests" value={t.requests ?? ""} onChange={(e) => setT(i, { requests: Number(e.target.value) })} />
                <input className={INPUT} type="number" placeholder="within days" value={t.within_days ?? ""} onChange={(e) => setT(i, { within_days: Number(e.target.value) })} />
              </>
            )}
            <button type="button" className={`${BTN} col-span-2 sm:col-span-4`} onClick={() => setF({ ...f, triggers: f.triggers.filter((_, j) => j !== i) })}>
              Remove trigger
            </button>
          </div>
        ))}
        <button type="button" className={BTN} onClick={() => setF({ ...f, triggers: [...f.triggers, { type: "interval", every_days: 30 }] })}>
          + Trigger
        </button>
      </div>
      <button
        type="button"
        className={BTN_PRIMARY}
        disabled={!f.name.trim() || act.busy}
        onClick={async () => {
          const body = { ...f, products: f.products.split(",").map((s) => s.trim()).filter(Boolean), template_id: f.template_id ? Number(f.template_id) : null };
          if (await act.run(() => (plan ? fsApi(`/pm/plans/${plan.id}`, "PUT", body) : fsApi("/pm/plans", "POST", body)))) onDone();
        }}
      >
        Save plan
      </button>
    </div>
  );
}

function Actions({ onOpenRequest }: { onOpenRequest: (id: number) => void }) {
  const [rows, setRows] = useState<PmAction[]>([]);
  const [status, setStatus] = useState<"scheduled" | "done" | "">("scheduled");
  useEffect(() => {
    fsApi<PmAction[]>(`/pm/actions${status ? `?status=${status}` : ""}`).then(setRows).catch(() => setRows([]));
  }, [status]);
  return (
    <div className="space-y-3">
      <Tabs
        value={status}
        onChange={setStatus}
        items={[
          { id: "scheduled", label: "Due" },
          { id: "done", label: "Done" },
          { id: "", label: "All" },
        ]}
      />
      {rows.length === 0 ? (
        <Empty>No maintenance actions.</Empty>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {rows.map((r) => (
            <button key={r.id} type="button" disabled={!r.ticket_id} onClick={() => r.ticket_id && onOpenRequest(r.ticket_id)} className={`${CARD} p-4 text-left hover:border-slate-600`}>
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-sky-300">{fmtDate(r.due_on)}</span>
                <span className="flex-1" />
                <Chip tone={r.status === "done" ? "emerald" : "sky"}>{r.status}</Chip>
              </div>
              <p className="mt-1 truncate text-sm text-slate-100">{r.asset_label}</p>
              <p className="truncate text-xs text-slate-400">
                {r.plan_name} · {r.trigger_kind}
                {r.reading_value !== null && ` at ${r.reading_value}`}
              </p>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
