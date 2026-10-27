import { useCallback, useEffect, useState } from "react";
import { fsApi, listParties } from "../../api/client";
import type { Party } from "../../api/types";
import type { Engineer, Entitlement, Region, Template, Warranty } from "./types";
import { BTN, BTN_DANGER, BTN_PRIMARY, CARD, Chip, Drawer, Empty, Field, INPUT, Notice, Tabs } from "./ui";
import { CATEGORY_LABEL, browserOffset, fmtDate, fmtWhen, fromLocalInput, money, todayIso, useAction } from "./style";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
type Tab = "engineers" | "regions" | "templates" | "entitlements" | "warranties";

export default function Setup({ onChanged }: { onChanged: () => void }) {
  const [tab, setTab] = useState<Tab>("engineers");
  const act = useAction(onChanged);
  return (
    <div className="space-y-4">
      <div className={`${CARD} flex flex-wrap items-center gap-3 p-4`}>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-slate-100">Starter setup</p>
          <p className="text-xs text-slate-400">Adds whatever is missing: two service regions, a warehouse with stock, a trunk per engineer, a repair center, job templates, a warranty and a maintenance plan.</p>
        </div>
        <button type="button" className={BTN_PRIMARY} disabled={act.busy} onClick={() => act.run(() => fsApi<{ created: string[] }>("/setup/sample", "POST", { timezone: browserOffset() }).then((r) => {
          act.setDone(r.created.length ? `Added: ${r.created.join(", ")}` : "Everything was already set up.");
          return r;
        }))}>
          Load starter setup
        </button>
      </div>
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <Notice text={act.done} tone="emerald" onClose={() => act.setDone(null)} />
      <Tabs
        value={tab}
        onChange={setTab}
        items={[
          { id: "engineers", label: "Engineers" },
          { id: "regions", label: "Service regions" },
          { id: "templates", label: "Job templates" },
          { id: "entitlements", label: "Entitlements" },
          { id: "warranties", label: "Warranties" },
        ]}
      />
      {tab === "engineers" && <Engineers key={act.done ?? ""} />}
      {tab === "regions" && <Regions key={act.done ?? ""} />}
      {tab === "templates" && <Templates key={act.done ?? ""} />}
      {tab === "entitlements" && <Entitlements />}
      {tab === "warranties" && <Warranties key={act.done ?? ""} />}
    </div>
  );
}

function useList<T>(path: string) {
  const [rows, setRows] = useState<T[]>([]);
  const load = useCallback(() => {
    fsApi<T[]>(path).then(setRows).catch(() => setRows([]));
  }, [path]);
  useEffect(load, [load]);
  return [rows, load] as const;
}

function Grid({ children }: { children: React.ReactNode }) {
  return <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{children}</div>;
}

function Card({ title, sub, chips, onClick }: { title: string; sub?: string; chips?: React.ReactNode; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className={`${CARD} flex h-full flex-col p-4 text-left transition hover:border-slate-600`}>
      <p className="truncate text-sm font-semibold text-slate-100">{title}</p>
      {sub && <p className="mt-0.5 line-clamp-2 text-xs text-slate-400">{sub}</p>}
      {chips && <div className="mt-auto flex flex-wrap gap-1.5 pt-3">{chips}</div>}
    </button>
  );
}

// ------------------------------------------------------------------ engineers

function Engineers() {
  const [rows, load] = useList<Engineer>("/engineers");
  const [regions] = useList<Region>("/regions");
  const [open, setOpen] = useState<Engineer | "new" | null>(null);
  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <button type="button" className={BTN} onClick={() => setOpen("new")}>
          + Engineer
        </button>
      </div>
      <Grid>
        {rows.map((e) => (
          <Card
            key={e.technician_id}
            title={e.name}
            sub={`${e.region_name ?? "Any region"} · ${e.day_start}–${e.day_end} ${e.work_days.map((d) => DAYS[d]).join(" ")} · up to ${e.max_jobs_per_day} jobs/day`}
            onClick={() => setOpen(e)}
            chips={
              <>
                {e.skills.map((s) => (
                  <Chip key={s.skill} tone="violet">
                    {s.skill} {"●".repeat(s.level)}
                  </Chip>
                ))}
                {e.trunk_name ? <Chip tone="sky">trunk</Chip> : <Chip tone="amber">no trunk</Chip>}
              </>
            }
          />
        ))}
      </Grid>
      <Drawer open={open !== null} onClose={() => setOpen(null)} title={open === "new" ? "New engineer" : open?.name ?? ""}>
        {open && <EngineerEditor e={open === "new" ? null : open} regions={regions} onDone={() => { setOpen(null); load(); }} />}
      </Drawer>
    </div>
  );
}

function EngineerEditor({ e, regions, onDone }: { e: Engineer | null; regions: Region[]; onDone: () => void }) {
  const [f, setF] = useState({
    name: e?.name ?? "",
    region_id: e?.region_id ? String(e.region_id) : "",
    skills: e?.skills ?? [],
    own_hours: e?.own_hours ?? false,
    day_start: e?.day_start ?? "08:00",
    day_end: e?.day_end ?? "17:00",
    work_days: e?.work_days ?? [0, 1, 2, 3, 4],
    max_jobs_per_day: e?.max_jobs_per_day ?? 6,
    cost_per_hour: e?.cost_per_hour ?? 0,
    phone: e?.phone ?? "",
    status: e?.status ?? "available",
  });
  const [skill, setSkill] = useState("");
  const [absences, setAbsences] = useState<{ id: number; starts_at: string; ends_at: string; reason: string }[]>([]);
  const [away, setAway] = useState({ starts_at: "", ends_at: "", reason: "" });
  const act = useAction();
  const loadAway = useCallback(() => {
    if (e) fsApi<typeof absences>(`/engineers/${e.technician_id}/absences`).then(setAbsences).catch(() => setAbsences([]));
  }, [e]);
  useEffect(loadAway, [loadAway]);

  const save = async (createTrunk = false) => {
    const body = {
      name: f.name,
      region_id: f.region_id ? Number(f.region_id) : null,
      skills: f.skills,
      day_start: f.own_hours ? f.day_start : null,
      day_end: f.own_hours ? f.day_end : null,
      work_days: f.own_hours ? f.work_days : [],
      max_jobs_per_day: f.max_jobs_per_day,
      cost_per_hour: f.cost_per_hour,
      phone: f.phone,
      status: f.status,
      create_trunk: createTrunk,
    };
    if (await act.run(() => (e ? fsApi(`/engineers/${e.technician_id}`, "PUT", body) : fsApi("/engineers", "POST", body)))) onDone();
  };

  return (
    <div className="space-y-3">
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <div className="grid grid-cols-2 gap-2">
        <Field label="Name">
          <input className={INPUT} value={f.name} onChange={(x) => setF({ ...f, name: x.target.value })} />
        </Field>
        <Field label="Service region">
          <select className={INPUT} value={f.region_id} onChange={(x) => setF({ ...f, region_id: x.target.value })}>
            <option value="">Any region</option>
            {regions.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Phone">
          <input className={INPUT} value={f.phone} onChange={(x) => setF({ ...f, phone: x.target.value })} />
        </Field>
        <Field label="Status">
          <select className={INPUT} value={f.status} onChange={(x) => setF({ ...f, status: x.target.value })}>
            <option value="available">Available</option>
            <option value="busy">Busy</option>
          </select>
        </Field>
        <Field label="Max jobs a day">
          <input className={INPUT} type="number" min={1} value={f.max_jobs_per_day} onChange={(x) => setF({ ...f, max_jobs_per_day: Number(x.target.value) })} />
        </Field>
        <Field label="Cost per hour">
          <input className={INPUT} type="number" min={0} value={f.cost_per_hour} onChange={(x) => setF({ ...f, cost_per_hour: Number(x.target.value) })} />
        </Field>
      </div>

      <Field label="Skills" hint="● novice · ●● intermediate · ●●● expert - experts score higher when jobs are assigned.">
        <div className="flex flex-wrap gap-1.5">
          {f.skills.map((s, i) => (
            <span key={s.skill} className="inline-flex items-center gap-1 rounded-md bg-violet-500/10 px-2 py-1 text-xs text-violet-200">
              {s.skill}
              <select className="bg-transparent text-xs" value={s.level} onChange={(x) => setF({ ...f, skills: f.skills.map((y, j) => (j === i ? { ...y, level: Number(x.target.value) } : y)) })}>
                <option value={1}>●</option>
                <option value={2}>●●</option>
                <option value={3}>●●●</option>
              </select>
              <button type="button" onClick={() => setF({ ...f, skills: f.skills.filter((_, j) => j !== i) })}>
                ✕
              </button>
            </span>
          ))}
        </div>
        <div className="mt-2 flex gap-2">
          <input className={INPUT} placeholder="e.g. fiber_install" value={skill} onChange={(x) => setSkill(x.target.value)} />
          <button
            type="button"
            className={BTN}
            disabled={!skill.trim()}
            onClick={() => {
              setF({ ...f, skills: [...f.skills.filter((s) => s.skill !== skill.trim().toLowerCase()), { skill: skill.trim().toLowerCase(), level: 2 }] });
              setSkill("");
            }}
          >
            Add
          </button>
        </div>
      </Field>

      <label className="flex items-center gap-2 text-sm text-slate-300">
        <input type="checkbox" checked={f.own_hours} onChange={(x) => setF({ ...f, own_hours: x.target.checked })} /> Own working hours (instead of the region's)
      </label>
      {f.own_hours && (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <input className={INPUT} type="time" value={f.day_start} onChange={(x) => setF({ ...f, day_start: x.target.value })} />
            <input className={INPUT} type="time" value={f.day_end} onChange={(x) => setF({ ...f, day_end: x.target.value })} />
          </div>
          <DayPicker value={f.work_days} onChange={(v) => setF({ ...f, work_days: v })} />
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <button type="button" className={BTN_PRIMARY} disabled={!f.name.trim() || act.busy} onClick={() => save()}>
          Save
        </button>
        {e && !e.trunk_location_id && (
          <button type="button" className={BTN} disabled={act.busy} onClick={() => save(true)}>
            Save & give a trunk
          </button>
        )}
      </div>

      {e && (
        <div className="space-y-2 border-t border-slate-800 pt-3">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Away (leave, training, vehicle down)</p>
          {absences.map((a) => (
            <div key={a.id} className="flex items-center gap-2 text-xs text-slate-300">
              <span className="min-w-0 flex-1 truncate">
                {fmtWhen(a.starts_at)} → {fmtWhen(a.ends_at)} · {a.reason || "away"}
              </span>
              <button type="button" className={BTN_DANGER} onClick={() => act.run(() => fsApi(`/absences/${a.id}`, "DELETE")).then(loadAway)}>
                Remove
              </button>
            </div>
          ))}
          <div className="grid grid-cols-2 gap-2">
            <input className={INPUT} type="datetime-local" value={away.starts_at} onChange={(x) => setAway({ ...away, starts_at: x.target.value })} />
            <input className={INPUT} type="datetime-local" value={away.ends_at} onChange={(x) => setAway({ ...away, ends_at: x.target.value })} />
            <input className={`${INPUT} col-span-2`} placeholder="Reason" value={away.reason} onChange={(x) => setAway({ ...away, reason: x.target.value })} />
          </div>
          <button
            type="button"
            className={BTN}
            disabled={!away.starts_at || !away.ends_at || act.busy}
            onClick={() =>
              act
                .run(() => fsApi(`/engineers/${e.technician_id}/absences`, "POST", { starts_at: fromLocalInput(away.starts_at), ends_at: fromLocalInput(away.ends_at), reason: away.reason }))
                .then(() => {
                  setAway({ starts_at: "", ends_at: "", reason: "" });
                  loadAway();
                })
            }
          >
            + Add absence
          </button>
        </div>
      )}
    </div>
  );
}

function DayPicker({ value, onChange }: { value: number[]; onChange: (v: number[]) => void }) {
  return (
    <div className="flex flex-wrap gap-1">
      {DAYS.map((d, i) => (
        <button
          key={d}
          type="button"
          onClick={() => onChange(value.includes(i) ? value.filter((x) => x !== i) : [...value, i].sort())}
          className={`rounded-md px-2.5 py-1 text-xs font-semibold ${value.includes(i) ? "bg-sky-600 text-white" : "bg-slate-800 text-slate-400"}`}
        >
          {d}
        </button>
      ))}
    </div>
  );
}

// -------------------------------------------------------------------- regions

function Regions() {
  const [rows, load] = useList<Region>("/regions");
  const [open, setOpen] = useState<Region | "new" | null>(null);
  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <button type="button" className={BTN} onClick={() => setOpen("new")}>
          + Region
        </button>
      </div>
      {rows.length === 0 ? (
        <Empty>No service regions yet.</Empty>
      ) : (
        <Grid>
          {rows.map((r) => (
            <Card
              key={r.id}
              title={r.name}
              sub={`${r.day_start}–${r.day_end} · ${r.work_days.map((d) => DAYS[d]).join(" ")} · ${r.timezone}`}
              onClick={() => setOpen(r)}
              chips={
                <>
                  <Chip tone="sky">{money(r.labor_rate)}/h labor</Chip>
                  <Chip>{r.travel_minutes} min travel</Chip>
                  {r.holidays.length > 0 && <Chip tone="amber">{r.holidays.length} holidays</Chip>}
                </>
              }
            />
          ))}
        </Grid>
      )}
      <Drawer open={open !== null} onClose={() => setOpen(null)} title={open === "new" ? "New service region" : open?.name ?? ""} subtitle="Its service calendar counts response times and limits appointment slots.">
        {open && <RegionEditor r={open === "new" ? null : open} onDone={() => { setOpen(null); load(); }} />}
      </Drawer>
    </div>
  );
}

function RegionEditor({ r, onDone }: { r: Region | null; onDone: () => void }) {
  const [f, setF] = useState({
    name: r?.name ?? "",
    timezone: r?.timezone ?? browserOffset(),
    day_start: r?.day_start ?? "08:00",
    day_end: r?.day_end ?? "17:00",
    work_days: r?.work_days ?? [0, 1, 2, 3, 4],
    holidays: (r?.holidays ?? []).join(", "),
    labor_rate: r?.labor_rate ?? 0,
    travel_minutes: r?.travel_minutes ?? 30,
  });
  const act = useAction();
  return (
    <div className="space-y-3">
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <div className="grid grid-cols-2 gap-2">
        <Field label="Name">
          <input className={INPUT} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        </Field>
        <Field label="Time zone" hint='e.g. "UTC+05:30"'>
          <input className={INPUT} value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })} />
        </Field>
        <Field label="Day starts">
          <input className={INPUT} type="time" value={f.day_start} onChange={(e) => setF({ ...f, day_start: e.target.value })} />
        </Field>
        <Field label="Day ends">
          <input className={INPUT} type="time" value={f.day_end} onChange={(e) => setF({ ...f, day_end: e.target.value })} />
        </Field>
        <Field label="Labor rate (per hour)">
          <input className={INPUT} type="number" min={0} value={f.labor_rate} onChange={(e) => setF({ ...f, labor_rate: Number(e.target.value) })} />
        </Field>
        <Field label="Travel between jobs (min)">
          <input className={INPUT} type="number" min={0} value={f.travel_minutes} onChange={(e) => setF({ ...f, travel_minutes: Number(e.target.value) })} />
        </Field>
      </div>
      <Field label="Working days">
        <DayPicker value={f.work_days} onChange={(v) => setF({ ...f, work_days: v })} />
      </Field>
      <Field label="Holidays" hint="Dates, comma separated: 2026-12-25, 2027-01-01">
        <input className={INPUT} value={f.holidays} onChange={(e) => setF({ ...f, holidays: e.target.value })} />
      </Field>
      <button
        type="button"
        className={BTN_PRIMARY}
        disabled={!f.name.trim() || act.busy}
        onClick={async () => {
          const body = { ...f, holidays: f.holidays.split(",").map((s) => s.trim()).filter(Boolean) };
          if (await act.run(() => (r ? fsApi(`/regions/${r.id}`, "PUT", body) : fsApi("/regions", "POST", body)))) onDone();
        }}
      >
        Save
      </button>
    </div>
  );
}

// ------------------------------------------------------------------ templates

function Templates() {
  const [rows, load] = useList<Template>("/templates");
  const [open, setOpen] = useState<Template | "new" | null>(null);
  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <button type="button" className={BTN} onClick={() => setOpen("new")}>
          + Template
        </button>
      </div>
      {rows.length === 0 ? (
        <Empty>No job templates yet.</Empty>
      ) : (
        <Grid>
          {rows.map((t) => (
            <Card
              key={t.id}
              title={t.name}
              sub={`${CATEGORY_LABEL[t.category] ?? t.category} · ${t.duration_minutes} min · ${t.steps.length} steps`}
              onClick={() => setOpen(t)}
              chips={
                <>
                  {t.skills.map((s) => (
                    <Chip key={s} tone="violet">
                      {s}
                    </Chip>
                  ))}
                  {t.items.map((i) => (
                    <Chip key={i.product}>
                      {i.quantity}× {i.product}
                    </Chip>
                  ))}
                  {!t.active && <Chip tone="amber">inactive</Chip>}
                </>
              }
            />
          ))}
        </Grid>
      )}
      <Drawer open={open !== null} onClose={() => setOpen(null)} title={open === "new" ? "New job template" : open?.name ?? ""} subtitle="The steps, parts and skills one kind of job needs.">
        {open && <TemplateEditor t={open === "new" ? null : open} onDone={() => { setOpen(null); load(); }} />}
      </Drawer>
    </div>
  );
}

function TemplateEditor({ t, onDone }: { t: Template | null; onDone: () => void }) {
  const [f, setF] = useState({
    name: t?.name ?? "",
    category: t?.category ?? "repair",
    duration_minutes: t?.duration_minutes ?? 60,
    priority: t?.priority ?? 3,
    skills: (t?.skills ?? []).join(", "),
    steps: (t?.steps ?? []).join("\n"),
    items: (t?.items ?? []).map((i) => `${i.quantity} x ${i.product}${i.tool ? " (tool)" : ""}`).join("\n"),
    instructions: t?.instructions ?? "",
    billable: t?.billable ?? true,
    active: t?.active ?? true,
  });
  const act = useAction();
  const parseItems = (text: string) =>
    text
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean)
      .map((l) => {
        const m = l.match(/^(\d+(?:\.\d+)?)\s*[x×]\s*(.+)$/i);
        const rest = (m ? m[2] : l).trim();
        const tool = /\(tool\)\s*$/i.test(rest);
        return { quantity: m ? Number(m[1]) : 1, product: rest.replace(/\(tool\)\s*$/i, "").trim(), tool };
      });
  return (
    <div className="space-y-3">
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <Field label="Name">
        <input className={INPUT} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      </Field>
      <div className="grid grid-cols-3 gap-2">
        <Field label="Type">
          <select className={INPUT} value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>
            {Object.entries(CATEGORY_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Minutes">
          <input className={INPUT} type="number" min={15} step={15} value={f.duration_minutes} onChange={(e) => setF({ ...f, duration_minutes: Number(e.target.value) })} />
        </Field>
        <Field label="Priority">
          <select className={INPUT} value={f.priority} onChange={(e) => setF({ ...f, priority: Number(e.target.value) })}>
            {[1, 2, 3, 4].map((p) => (
              <option key={p} value={p}>
                {["", "ASAP", "High", "Medium", "Low"][p]}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Field label="Skills needed" hint="Comma separated">
        <input className={INPUT} value={f.skills} onChange={(e) => setF({ ...f, skills: e.target.value })} />
      </Field>
      <Field label="Steps" hint="One per line">
        <textarea className={INPUT} rows={5} value={f.steps} onChange={(e) => setF({ ...f, steps: e.target.value })} />
      </Field>
      <Field label="Parts & tools" hint='One per line: "2 x Fiber patch cord", "1 x Splicer (tool)"'>
        <textarea className={INPUT} rows={3} value={f.items} onChange={(e) => setF({ ...f, items: e.target.value })} />
      </Field>
      <Field label="Instructions">
        <textarea className={INPUT} rows={2} value={f.instructions} onChange={(e) => setF({ ...f, instructions: e.target.value })} />
      </Field>
      <div className="flex gap-4 text-sm text-slate-300">
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={f.billable} onChange={(e) => setF({ ...f, billable: e.target.checked })} /> Billable
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Active
        </label>
      </div>
      <button
        type="button"
        className={BTN_PRIMARY}
        disabled={!f.name.trim() || act.busy}
        onClick={async () => {
          const body = {
            ...f,
            skills: f.skills.split(",").map((s) => s.trim()).filter(Boolean),
            steps: f.steps.split("\n").map((s) => s.trim()).filter(Boolean),
            items: parseItems(f.items),
          };
          if (await act.run(() => (t ? fsApi(`/templates/${t.id}`, "PUT", body) : fsApi("/templates", "POST", body)))) onDone();
        }}
      >
        Save
      </button>
    </div>
  );
}

// --------------------------------------------------------------- entitlements

function Entitlements() {
  const [rows, load] = useList<Entitlement>("/entitlements");
  const [open, setOpen] = useState<Entitlement | "new" | null>(null);
  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <button type="button" className={BTN} onClick={() => setOpen("new")}>
          + Entitlement
        </button>
      </div>
      {rows.length === 0 ? (
        <Empty>No customer entitlements yet. An entitlement sets a customer's response time, coverage hours and discounts.</Empty>
      ) : (
        <Grid>
          {rows.map((e) => (
            <Card
              key={e.id}
              title={e.name}
              sub={`${e.party_name ?? "?"}${e.asset_label ? ` · ${e.asset_label}` : e.product ? ` · ${e.product}` : " · all assets"}`}
              onClick={() => setOpen(e)}
              chips={
                <>
                  <Chip tone="sky">
                    {e.response_hours}h {e.coverage === "24x7" ? "24x7" : "business hrs"}
                  </Chip>
                  {e.labor_discount_pct > 0 && <Chip tone="emerald">−{e.labor_discount_pct}% labor</Chip>}
                  {e.parts_discount_pct > 0 && <Chip tone="emerald">−{e.parts_discount_pct}% parts</Chip>}
                  {e.visits_allowed !== null && <Chip>{`${e.visits_used}/${e.visits_allowed} visits`}</Chip>}
                  {!e.current && <Chip tone="amber">not current</Chip>}
                  {e.ends_on && <Chip>until {fmtDate(e.ends_on)}</Chip>}
                </>
              }
            />
          ))}
        </Grid>
      )}
      <Drawer open={open !== null} onClose={() => setOpen(null)} title={open === "new" ? "New entitlement" : open?.name ?? ""}>
        {open && <EntitlementEditor e={open === "new" ? null : open} onDone={() => { setOpen(null); load(); }} />}
      </Drawer>
    </div>
  );
}

function EntitlementEditor({ e, onDone }: { e: Entitlement | null; onDone: () => void }) {
  const [parties, setParties] = useState<Party[]>([]);
  const [q, setQ] = useState("");
  const [f, setF] = useState(() => ({
    party_id: e?.party_id ? String(e.party_id) : "",
    name: e?.name ?? "",
    product: e?.product ?? "",
    coverage: e?.coverage ?? "business",
    response_hours: e?.response_hours ?? 8,
    resolution_hours: e?.resolution_hours ?? "",
    labor_discount_pct: e?.labor_discount_pct ?? 0,
    parts_discount_pct: e?.parts_discount_pct ?? 0,
    expense_discount_pct: e?.expense_discount_pct ?? 0,
    visits_allowed: e?.visits_allowed ?? "",
    starts_on: e?.starts_on ?? todayIso(),
    ends_on: e?.ends_on ?? "",
    active: e?.active ?? true,
  }));
  const act = useAction();
  useEffect(() => {
    if (e) return;
    const t = setTimeout(() => listParties(q || undefined).then((p) => setParties(p.slice(0, 50))).catch(() => setParties([])), 200);
    return () => clearTimeout(t);
  }, [q, e]);
  return (
    <div className="space-y-3">
      <Notice text={act.error} onClose={() => act.setError(null)} />
      {!e && (
        <Field label="Customer">
          <input className={INPUT} placeholder="Search…" value={q} onChange={(x) => setQ(x.target.value)} />
          <select className={`${INPUT} mt-2`} value={f.party_id} onChange={(x) => setF({ ...f, party_id: x.target.value })}>
            <option value="">— choose —</option>
            {parties.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </Field>
      )}
      <div className="grid grid-cols-2 gap-2">
        <Field label="Name" className="col-span-2">
          <input className={INPUT} placeholder="e.g. Gold support" value={f.name} onChange={(x) => setF({ ...f, name: x.target.value })} />
        </Field>
        <Field label="Product (empty = all)">
          <input className={INPUT} value={f.product} onChange={(x) => setF({ ...f, product: x.target.value })} />
        </Field>
        <Field label="Coverage">
          <select className={INPUT} value={f.coverage} onChange={(x) => setF({ ...f, coverage: x.target.value as "business" | "24x7" })}>
            <option value="business">Business hours</option>
            <option value="24x7">24x7</option>
          </select>
        </Field>
        <Field label="Response (hours)">
          <input className={INPUT} type="number" min={0} step={0.5} value={f.response_hours} onChange={(x) => setF({ ...f, response_hours: Number(x.target.value) })} />
        </Field>
        <Field label="Visits allowed (empty = unlimited)">
          <input className={INPUT} type="number" min={0} value={f.visits_allowed} onChange={(x) => setF({ ...f, visits_allowed: x.target.value })} />
        </Field>
        <Field label="Labor discount %">
          <input className={INPUT} type="number" min={0} max={100} value={f.labor_discount_pct} onChange={(x) => setF({ ...f, labor_discount_pct: Number(x.target.value) })} />
        </Field>
        <Field label="Parts discount %">
          <input className={INPUT} type="number" min={0} max={100} value={f.parts_discount_pct} onChange={(x) => setF({ ...f, parts_discount_pct: Number(x.target.value) })} />
        </Field>
        <Field label="Starts">
          <input className={INPUT} type="date" value={f.starts_on} onChange={(x) => setF({ ...f, starts_on: x.target.value })} />
        </Field>
        <Field label="Ends">
          <input className={INPUT} type="date" value={f.ends_on} onChange={(x) => setF({ ...f, ends_on: x.target.value })} />
        </Field>
      </div>
      <label className="flex items-center gap-2 text-sm text-slate-300">
        <input type="checkbox" checked={f.active} onChange={(x) => setF({ ...f, active: x.target.checked })} /> Active
      </label>
      <button
        type="button"
        className={BTN_PRIMARY}
        disabled={act.busy || (!e && !f.party_id) || !f.name.trim()}
        onClick={async () => {
          const body = {
            ...f,
            party_id: Number(f.party_id),
            resolution_hours: f.resolution_hours === "" ? null : Number(f.resolution_hours),
            visits_allowed: f.visits_allowed === "" ? null : Number(f.visits_allowed),
            ends_on: f.ends_on || null,
          };
          if (await act.run(() => (e ? fsApi(`/entitlements/${e.id}`, "PUT", body) : fsApi("/entitlements", "POST", body)))) onDone();
        }}
      >
        Save
      </button>
    </div>
  );
}

// ----------------------------------------------------------------- warranties

function Warranties() {
  const [rows, load] = useList<Warranty>("/warranties");
  const [open, setOpen] = useState<Warranty | "new" | null>(null);
  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <button type="button" className={BTN} onClick={() => setOpen("new")}>
          + Warranty
        </button>
      </div>
      {rows.length === 0 ? (
        <Empty>No warranties defined.</Empty>
      ) : (
        <Grid>
          {rows.map((w) => (
            <Card
              key={w.id}
              title={w.name}
              sub={`${w.duration_days} days · ${w.products.join(", ") || "any product"}${w.provider ? ` · ${w.provider}` : ""}`}
              onClick={() => setOpen(w)}
              chips={
                <>
                  {w.covers_parts && <Chip tone="emerald">parts</Chip>}
                  {w.covers_labor && <Chip tone="emerald">labor</Chip>}
                  {w.covers_expenses && <Chip tone="emerald">expenses</Chip>}
                  {w.usage_limit !== null && <Chip tone="amber">{`≤ ${w.usage_limit} ${w.measurement}`}</Chip>}
                  {w.transferable && <Chip>moves on swap</Chip>}
                </>
              }
            />
          ))}
        </Grid>
      )}
      <Drawer open={open !== null} onClose={() => setOpen(null)} title={open === "new" ? "New warranty" : open?.name ?? ""}>
        {open && <WarrantyEditor w={open === "new" ? null : open} onDone={() => { setOpen(null); load(); }} />}
      </Drawer>
    </div>
  );
}

function WarrantyEditor({ w, onDone }: { w: Warranty | null; onDone: () => void }) {
  const [f, setF] = useState({
    name: w?.name ?? "",
    products: (w?.products ?? []).join(", "),
    duration_days: w?.duration_days ?? 365,
    covers_parts: w?.covers_parts ?? true,
    covers_labor: w?.covers_labor ?? true,
    covers_expenses: w?.covers_expenses ?? false,
    measurement: w?.measurement ?? "",
    usage_limit: w?.usage_limit ?? "",
    transferable: w?.transferable ?? true,
    provider: w?.provider ?? "",
  });
  const act = useAction();
  return (
    <div className="space-y-3">
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <Field label="Name">
        <input className={INPUT} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      </Field>
      <Field label="Products" hint="Comma separated, empty = any product">
        <input className={INPUT} value={f.products} onChange={(e) => setF({ ...f, products: e.target.value })} />
      </Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Days">
          <input className={INPUT} type="number" min={1} value={f.duration_days} onChange={(e) => setF({ ...f, duration_days: Number(e.target.value) })} />
        </Field>
        <Field label="Provider">
          <input className={INPUT} value={f.provider} onChange={(e) => setF({ ...f, provider: e.target.value })} />
        </Field>
        <Field label="Usage measurement (optional)">
          <input className={INPUT} placeholder="e.g. Copies" value={f.measurement} onChange={(e) => setF({ ...f, measurement: e.target.value })} />
        </Field>
        <Field label="Usage limit">
          <input className={INPUT} type="number" value={f.usage_limit} onChange={(e) => setF({ ...f, usage_limit: e.target.value })} />
        </Field>
      </div>
      <div className="flex flex-wrap gap-4 text-sm text-slate-300">
        {(["covers_parts", "covers_labor", "covers_expenses", "transferable"] as const).map((k) => (
          <label key={k} className="flex items-center gap-2">
            <input type="checkbox" checked={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.checked })} />
            {{ covers_parts: "Parts", covers_labor: "Labor", covers_expenses: "Expenses", transferable: "Moves to a swapped-in asset" }[k]}
          </label>
        ))}
      </div>
      <button
        type="button"
        className={BTN_PRIMARY}
        disabled={!f.name.trim() || act.busy}
        onClick={async () => {
          const body = { ...f, products: f.products.split(",").map((s) => s.trim()).filter(Boolean), usage_limit: f.usage_limit === "" ? null : Number(f.usage_limit) };
          if (await act.run(() => (w ? fsApi(`/warranties/${w.id}`, "PUT", body) : fsApi("/warranties", "POST", body)))) onDone();
        }}
      >
        Save
      </button>
    </div>
  );
}
