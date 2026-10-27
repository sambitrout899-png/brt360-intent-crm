import { useCallback, useEffect, useMemo, useState } from "react";
import { invApi } from "../api/client";
import { useIntentRefresh } from "../workspace";
import { BTN, BTN_DANGER, BTN_PRIMARY, BTN_WARN, CARD, Empty, Field, INPUT, Panel, Section, Stat, Tabs } from "./fieldservice/ui";

/* ---------------------------------------------------------------- shapes (backend: crm_core/inventory_service.py) */

type Kind = "location" | "physical" | "logical" | "service";
type Brief = {
  id: number; brt_id: string; type_code: string; type_name: string; kind: Kind; identifier: string; name: string;
  lifecycle: string; oper_status: string; location: string | null; parent: string | null; order_id: number | null;
};
type Item = Brief & {
  parent_id: number | null; location_id: number | null; pool_id: number | null; serial: string; vendor: string; model: string;
  attributes: Record<string, unknown>; sources: Record<string, string>; party_id: number | null; service_item_id: number | null;
  reference: string; reserved_until: string | null; source: string; last_verified_at: string | null; notes: string; next_lifecycle: string[];
};
type Attr = { key: string; label: string; type: string; required?: boolean; unique?: boolean; values?: string[] };
type ItemType = {
  id: number; code: string; name: string; kind: Kind; description: string; parent_code: string; attributes: Attr[]; all_attributes: Attr[];
  contained_in: string[]; naming: string; resource_type: string; is_active: boolean; builtin: boolean; items: number;
};
type Detail = {
  item: Item; type: ItemType; path: Brief[]; children: Brief[]; children_by_lifecycle: Record<string, number>;
  relationships: { id: number; rel_type: string; wording: string; direction: "in" | "out"; other: Brief | null; note: string }[];
  history: { action: string; changes: Record<string, unknown>; source: string; reference: string; at: string }[];
  services: Brief[];
};
type Pool = {
  id: number; name: string; type_code: string; type_name: string; pool_type: string; location: string | null; range_start: string; range_end: string;
  block_size: number; format: string; threshold_pct: number; capacity: number; used: number; free: number; used_pct: number; over_threshold: boolean;
};
type Capacity = {
  location: string | null;
  equipment: { resource_type: string; total: number; available: number; reserved: number; in_use: number; maintenance: number; used_pct: number }[];
  pools: Pool[];
};
type Health = {
  items: number; by_kind: Record<string, number>; by_lifecycle: Record<string, number>; score: number; types: number; pools: number; in_use: boolean;
  checks: { key: string; label: string; bad: number; examples: string[] }[];
};
type Overview = {
  health: Health; capacity: Capacity; locations: (Brief & { parent_id: number | null; equipment: number })[]; ai: string | null;
  lifecycle: string[]; oper_statuses: string[]; relationships: Record<string, { forward: string; reverse: string }>; attribute_types: string[];
};
type Impact = { summary: string; affected_items: number; services: Brief[]; customers: { id: number; name: string }[]; orders: number[]; protected: string[] };
type Asked = { filter: Record<string, unknown>; read_as: string; source: "llm" | "rules"; model: string | null; results: Brief[] };

const LIFE_CLS: Record<string, string> = {
  planned: "bg-slate-500/15 text-slate-300", available: "bg-emerald-500/15 text-emerald-300", reserved: "bg-amber-500/15 text-amber-300",
  in_use: "bg-sky-500/15 text-sky-300", maintenance: "bg-violet-500/15 text-violet-300", retired: "bg-slate-700/40 text-slate-500",
};
const OPER_DOT: Record<string, string> = { up: "#10b981", down: "#ef4444", degraded: "#f59e0b", unknown: "#64748b" };
const KIND_ICON: Record<Kind, string> = { location: "📍", physical: "🔌", logical: "🔢", service: "🛰️" };

function errorText(e: unknown, fallback: string) {
  if (!(e instanceof Error)) return fallback;
  const m = e.message.match(/^\d{3} [^:]*: (.*)$/s);
  if (m) {
    try {
      const detail = JSON.parse(m[1]).detail;
      if (typeof detail === "string") return detail;
    } catch {
      /* not JSON */
    }
  }
  return e.message || fallback;
}

function Life({ value }: { value: string }) {
  return <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold ${LIFE_CLS[value] ?? LIFE_CLS.planned}`}>{value.replace("_", " ")}</span>;
}

function Oper({ value }: { value: string }) {
  return (
    <span className="inline-flex shrink-0 items-center gap-1 text-[11px] text-slate-400" title="Operational status">
      <span className="h-2 w-2 rounded-full" style={{ background: OPER_DOT[value] ?? OPER_DOT.unknown }} />
      {value}
    </span>
  );
}

function Bar({ pct, warn }: { pct: number; warn?: boolean }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
      <div className={`h-full ${warn ? "bg-amber-400" : "bg-sky-500"}`} style={{ width: `${Math.min(100, pct)}%` }} />
    </div>
  );
}

function Row({ b, onOpen, active }: { b: Brief; onOpen: (id: number) => void; active?: boolean }) {
  return (
    <button type="button" onClick={() => onOpen(b.id)}
      className={`flex w-full min-w-0 items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm transition ${active ? "border-sky-500 bg-sky-500/5" : "border-slate-800 hover:border-slate-600"}`}>
      <span aria-hidden="true">{KIND_ICON[b.kind]}</span>
      <span className="min-w-0 flex-1">
        <span className="block truncate font-semibold text-slate-100">{b.identifier}</span>
        <span className="block truncate text-[11px] text-slate-500">{b.type_name}{b.location ? ` · ${b.location}` : ""}{b.order_id ? ` · order ${b.order_id}` : ""}</span>
      </span>
      <Oper value={b.oper_status} />
      <Life value={b.lifecycle} />
    </button>
  );
}

/* ---------------------------------------------------------------- page */

type View = "overview" | "explore" | "pools" | "types" | "reconcile";

/**
 * Network Inventory - the network as recorded: locations, equipment and its containment,
 * logical resources from pools, customer services and what they use. Fulfillment's SOM
 * steps allocate from here; ask it anything in plain English.
 */
export default function InventoryPage({ onRequestIntent }: { onRequestIntent: (text: string) => void }) {
  const [view, setView] = useState<View>("overview");
  const [o, setO] = useState<Overview | null>(null);
  const [focus, setFocus] = useState<number | null>(null);
  const [filter, setFilter] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    invApi<Overview>("/overview").then(setO).catch((e) => setError(errorText(e, "Could not load the inventory")));
  }, []);
  useEffect(load, [load, view]);
  useIntentRefresh(load);

  const open = (id: number) => {
    setFocus(id);
    setView("explore");
  };

  async function loadSample() {
    setBusy(true);
    setError(null);
    try {
      await invApi("/sample", "POST");
      load();
    } catch (e) {
      setError(errorText(e, "Could not load the sample network"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page space-y-6">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold text-slate-100">Network Inventory</h1>
          <p className="mt-1 hidden text-sm text-slate-400 sm:block">
            Locations, equipment, logical resources and customer services - what Fulfillment allocates from, and what an outage takes down.
          </p>
        </div>
        <button type="button" onClick={() => onRequestIntent("Find inventory: ")} className="rounded-lg border border-sky-500/40 bg-sky-500/10 px-3 py-2 text-xs font-semibold text-sky-300 hover:bg-sky-500/20">
          ✨ Ask in the Command Bar
        </button>
        <button type="button" onClick={load} className={BTN}>↻ Refresh</button>
      </div>
      {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</p>}
      {o && !o.health.in_use && (
        <div className={`${CARD} flex flex-wrap items-center gap-3 border-sky-500/30 p-4`}>
          <p className="min-w-0 flex-1 text-sm text-slate-300">
            No network is recorded yet - Fulfillment uses the integrated inventory system's answers until you record locations, equipment or pools here.
          </p>
          <button type="button" disabled={busy} onClick={loadSample} className={BTN_PRIMARY}>Load a sample fibre network</button>
        </div>
      )}

      <Tabs value={view} onChange={setView} items={[
        { id: "overview", label: "Overview" },
        { id: "explore", label: "Explore", count: o?.health.items },
        { id: "pools", label: "Pools", count: o?.capacity.pools.filter((p) => p.over_threshold).length || undefined },
        { id: "types", label: "Item types", count: o?.health.types },
        { id: "reconcile", label: "Reconcile" },
      ]} />

      {view === "overview" && o && <OverviewView o={o} onOpen={open} onFilter={(f) => { setFilter(f); setFocus(null); setView("explore"); }} />}
      {view === "explore" && o && <Explore o={o} focus={focus} setFocus={setFocus} preset={filter} onChanged={load} />}
      {view === "pools" && o && <Pools o={o} onChanged={load} />}
      {view === "types" && o && <Types o={o} />}
      {view === "reconcile" && <Reconcile onOpen={open} />}
    </div>
  );
}

/* ---------------------------------------------------------------- overview */

function OverviewView({ o, onOpen, onFilter }: { o: Overview; onOpen: (id: number) => void; onFilter: (f: Record<string, unknown>) => void }) {
  const h = o.health;
  const roots = o.locations.filter((l) => !o.locations.some((p) => p.id === l.parent_id));
  const childrenOf = (id: number) => o.locations.filter((l) => l.parent_id === id);
  const tree = (l: Overview["locations"][number], depth: number): React.ReactNode => (
    <div key={l.id}>
      <button type="button" onClick={() => onOpen(l.id)} className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm hover:bg-slate-800/60" style={{ paddingLeft: 8 + depth * 16 }}>
        <span className="text-slate-500">📍</span>
        <span className="min-w-0 flex-1 truncate text-slate-200">{l.identifier} <span className="text-slate-500">· {l.name}</span></span>
        <span className="text-[11px] text-slate-500">{l.equipment} item(s)</span>
      </button>
      {childrenOf(l.id).map((c) => tree(c, depth + 1))}
    </div>
  );
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Data health" value={`${h.score}%`} tone={h.score >= 90 ? "text-emerald-300" : h.score >= 70 ? "text-amber-300" : "text-red-300"} sub="complete, current, placed, unique" />
        <Stat label="Locations" value={h.by_kind.location ?? 0} onClick={() => onFilter({ kind: "location" })} />
        <Stat label="Equipment" value={h.by_kind.physical ?? 0} sub={`${h.by_lifecycle.available ?? 0} available overall`} onClick={() => onFilter({ kind: "physical" })} />
        <Stat label="Logical in use" value={h.by_kind.logical ?? 0} onClick={() => onFilter({ kind: "logical" })} />
        <Stat label="Services" value={h.by_kind.service ?? 0} onClick={() => onFilter({ kind: "service" })} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Data health checks">
          <ul className="space-y-2">
            {h.checks.map((c) => (
              <li key={c.key} className="flex items-start gap-2 text-sm">
                <span className={c.bad ? "text-amber-300" : "text-emerald-400"}>{c.bad ? "▲" : "✓"}</span>
                <span className="min-w-0 flex-1 text-slate-300">
                  {c.label}
                  {c.bad > 0 && <span className="block truncate text-[11px] text-slate-500">{c.bad} to look at - e.g. {c.examples.join(", ")}</span>}
                </span>
              </li>
            ))}
          </ul>
        </Panel>
        <Panel title="Capacity - equipment">
          {o.capacity.equipment.length === 0 ? <p className="text-sm text-slate-500">No equipment recorded.</p> : (
            <div className="space-y-3">
              {o.capacity.equipment.map((r) => (
                <button key={r.resource_type} type="button" onClick={() => onFilter({ resource_type: r.resource_type, lifecycle: "available" })} className="block w-full text-left">
                  <div className="flex justify-between text-xs text-slate-300">
                    <span className="font-semibold">{r.resource_type.replace("_", " ")}</span>
                    <span>{r.available} free · {r.reserved} reserved · {r.in_use} in use of {r.total}</span>
                  </div>
                  <Bar pct={r.used_pct} warn={r.used_pct >= 80} />
                </button>
              ))}
            </div>
          )}
        </Panel>
        <Panel title="Pools">
          {o.capacity.pools.length === 0 ? <p className="text-sm text-slate-500">No pools yet.</p> : (
            <div className="space-y-3">
              {o.capacity.pools.map((p) => (
                <div key={p.id}>
                  <div className="flex justify-between text-xs text-slate-300">
                    <span className="truncate font-semibold">{p.name}</span>
                    <span>{p.used} / {p.capacity}{p.over_threshold ? " ⚠" : ""}</span>
                  </div>
                  <Bar pct={p.used_pct} warn={p.over_threshold} />
                </div>
              ))}
            </div>
          )}
        </Panel>
        <Panel title="Locations">
          {roots.length === 0 ? <p className="text-sm text-slate-500">No locations yet.</p> : <div className="max-h-80 overflow-y-auto">{roots.map((r) => tree(r, 0))}</div>}
        </Panel>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- explore */

function Explore({ o, focus, setFocus, preset, onChanged }: {
  o: Overview; focus: number | null; setFocus: (id: number | null) => void; preset: Record<string, unknown> | null; onChanged: () => void;
}) {
  const [types, setTypes] = useState<ItemType[]>([]);
  const [f, setF] = useState<Record<string, unknown>>(preset ?? {});
  const [rows, setRows] = useState<Brief[]>([]);
  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState<Asked | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState<"item" | "receive" | null>(null);

  useEffect(() => {
    invApi<ItemType[]>("/types").then(setTypes).catch(() => setTypes([]));
  }, []);
  const search = useCallback((filter: Record<string, unknown>) => {
    invApi<Brief[]>("/items/search", "POST", filter).then((r) => { setRows(r); setError(null); }).catch((e) => setError(errorText(e, "Search failed")));
  }, []);
  useEffect(() => search(f), [f, search]);

  async function ask() {
    if (!question.trim()) return;
    try {
      const a = await invApi<Asked>("/ask", "POST", { question });
      setAsked(a);
      setRows(a.results);
      setError(null);
    } catch (e) {
      setError(errorText(e, "Could not answer that"));
    }
  }

  const set = (k: string, v: unknown) => { setAsked(null); setF((cur) => { const n = { ...cur }; if (v === "" || v == null) delete n[k]; else n[k] = v; return n; }); };
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-violet-500/30 bg-violet-500/5 p-3">
        <div className="flex flex-wrap gap-2">
          <input value={question} onChange={(e) => setQuestion(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ask()}
            placeholder="✨ Ask: free GPON ports at BLR-EX-01 · ONTs in stock in the Bangalore store · what does order 12 use · devices that are down"
            className={`${INPUT} flex-1`} />
          <button type="button" onClick={ask} className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-bold text-white hover:bg-violet-500">Ask</button>
        </div>
        <p className="mt-1 text-[11px] text-slate-500">
          {asked ? <>Read {asked.source === "llm" ? <>by <b className="text-slate-300">{asked.model}</b></> : "by the built-in reader"} as: <span className="text-violet-300">{asked.read_as || JSON.stringify(asked.filter)}</span></>
            : <>Answered by {o.ai ? <b className="text-slate-300">{o.ai}</b> : "the built-in reader (set up your own model or an API under Admin → AI)"}.</>}
        </p>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-6">
        <input placeholder="Identifier, name, serial, BRT_ID" value={String(f.text ?? "")} onChange={(e) => set("text", e.target.value)} className={`${INPUT} lg:col-span-2`} />
        <select value={String(f.type ?? "")} onChange={(e) => set("type", e.target.value)} className={INPUT}>
          <option value="">Any type</option>
          {types.map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}
        </select>
        <select value={String(f.lifecycle ?? "")} onChange={(e) => set("lifecycle", e.target.value)} className={INPUT}>
          <option value="">Any lifecycle</option>
          {o.lifecycle.map((l) => <option key={l} value={l}>{l.replace("_", " ")}</option>)}
        </select>
        <select value={String(f.location ?? "")} onChange={(e) => set("location", e.target.value)} className={INPUT}>
          <option value="">Anywhere</option>
          {o.locations.map((l) => <option key={l.id} value={l.identifier}>{l.identifier}</option>)}
        </select>
        <div className="flex gap-2">
          <button type="button" onClick={() => setCreating(creating === "item" ? null : "item")} className={BTN}>+ Item</button>
          <button type="button" onClick={() => setCreating(creating === "receive" ? null : "receive")} className={BTN}>+ Receive</button>
        </div>
      </div>
      {error && <p className="text-xs text-red-400">{error}</p>}
      {creating === "item" && <NewItem types={types} onDone={(id) => { setCreating(null); setFocus(id); search(f); onChanged(); }} />}
      {creating === "receive" && <ReceiveStock types={types} o={o} onDone={() => { setCreating(null); search(f); onChanged(); }} />}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
        <Section title="Items" count={rows.length}>
          {rows.length === 0 ? <Empty>Nothing matches.</Empty> : (
            <div className="max-h-[70vh] space-y-1.5 overflow-y-auto pr-1">{rows.map((r) => <Row key={r.id} b={r} onOpen={setFocus} active={r.id === focus} />)}</div>
          )}
        </Section>
        {focus ? <ItemDetail key={focus} id={focus} o={o} onOpen={setFocus} onChanged={() => { search(f); onChanged(); }} /> : <Empty>Pick an item to see what it holds, what it relates to and what it would take down.</Empty>}
      </div>
    </div>
  );
}

function ItemDetail({ id, o, onOpen, onChanged }: { id: number; o: Overview; onOpen: (id: number) => void; onChanged: () => void }) {
  const [d, setD] = useState<Detail | null>(null);
  const [impact, setImpact] = useState<Impact | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [edit, setEdit] = useState<Record<string, string> | null>(null);
  const [rel, setRel] = useState({ target: "", rel_type: "depends_on" });

  const load = useCallback(() => {
    invApi<Detail>(`/items/${id}`).then(setD).catch((e) => setError(errorText(e, "Could not load the item")));
  }, [id]);
  useEffect(load, [load]);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      const r = await fn();
      if (r && typeof r === "object" && "item" in (r as object)) setD(r as Detail);
      else load();
      onChanged();
    } catch (e) {
      setError(errorText(e, "That didn't work"));
    }
  }

  if (!d) return <Empty>{error ?? "Loading…"}</Empty>;
  const it = d.item;
  return (
    <div className={`${CARD} space-y-4 p-4`}>
      <div className="flex flex-wrap items-center gap-1 text-[11px] text-slate-500">
        {d.path.map((p) => <button key={p.id} type="button" onClick={() => onOpen(p.id)} className="hover:text-sky-300">{p.identifier} ›</button>)}
        <span className="text-slate-300">{it.identifier}</span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-lg" aria-hidden="true">{KIND_ICON[it.kind]}</span>
        <h2 className="min-w-0 flex-1 truncate text-lg font-bold text-slate-100">{it.name || it.identifier}</h2>
        <Oper value={it.oper_status} />
        <Life value={it.lifecycle} />
      </div>
      <p className="text-xs text-slate-500">
        {d.type.name} · {it.brt_id}{it.serial ? ` · S/N ${it.serial}` : ""}{it.vendor ? ` · ${it.vendor} ${it.model}` : ""}
        {it.order_id ? ` · held for order ${it.order_id}` : ""}{it.reserved_until ? ` until ${it.reserved_until.slice(0, 10)}` : ""}
        {` · verified ${it.last_verified_at ? it.last_verified_at.slice(0, 10) : "never"}`}
      </p>
      {error && <p className="rounded border border-red-500/30 bg-red-500/10 px-2 py-1 text-xs text-red-300">{error}</p>}

      <div className="flex flex-wrap items-end gap-2">
        {it.next_lifecycle.map((to) => (
          <button key={to} type="button" onClick={() => act(() => invApi(`/items/${id}/lifecycle`, "POST", { to, note }))}
            className={to === "retired" ? BTN_DANGER : to === "maintenance" ? BTN_WARN : BTN}>→ {to.replace("_", " ")}</button>
        ))}
        <select value={it.oper_status} onChange={(e) => act(() => invApi(`/items/${id}/oper-status`, "POST", { status: e.target.value, note }))} className={`${INPUT} w-32`}>
          {o.oper_statuses.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <button type="button" onClick={() => act(() => invApi(`/items/${id}/verify`, "POST"))} className={BTN}>✓ Verified</button>
        <button type="button" onClick={() => invApi<Impact>(`/impact/${id}`).then(setImpact).catch((e) => setError(errorText(e, "Impact failed")))} className={BTN_WARN}>💥 Impact</button>
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why (needed for maintenance on what services use)" className={`${INPUT} min-w-48 flex-1`} />
      </div>

      {impact && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-200">
          <p className="font-semibold">{impact.summary}</p>
          {impact.services.length > 0 && <p className="mt-1">Services: {impact.services.map((s) => s.identifier).join(", ")}</p>}
          {impact.customers.length > 0 && <p>Customers: {impact.customers.map((c) => c.name).join(", ")}</p>}
          {impact.orders.length > 0 && <p>Orders: {impact.orders.join(", ")}</p>}
          {impact.protected.length > 0 && <p>Protected: {impact.protected.join(", ")}</p>}
        </div>
      )}

      <Panel title="Attributes" action={edit ? (
        <span className="flex gap-2">
          <button type="button" className={BTN_PRIMARY} onClick={() => act(async () => { const r = await invApi(`/items/${id}`, "PUT", { attributes: edit }); setEdit(null); return r; })}>Save</button>
          <button type="button" className={BTN} onClick={() => setEdit(null)}>Cancel</button>
        </span>
      ) : <button type="button" className={BTN} onClick={() => setEdit(Object.fromEntries(d.type.all_attributes.map((a) => [a.key, String(it.attributes[a.key] ?? "")])))}>Edit</button>}>
        <div className="grid gap-2 sm:grid-cols-2">
          {d.type.all_attributes.map((a) => (
            <div key={a.key} className="min-w-0 text-sm">
              <p className="text-[11px] uppercase tracking-wider text-slate-500">{a.label}{a.required ? " *" : ""}</p>
              {edit ? (a.values ? (
                <select value={edit[a.key] ?? ""} onChange={(e) => setEdit({ ...edit, [a.key]: e.target.value })} className={INPUT}>
                  <option value="" />{a.values.map((v) => <option key={v} value={v}>{v}</option>)}
                </select>
              ) : <input value={edit[a.key] ?? ""} onChange={(e) => setEdit({ ...edit, [a.key]: e.target.value })} className={INPUT} />) : (
                <p className="truncate text-slate-200">
                  {String(it.attributes[a.key] ?? "") || <span className="text-slate-600">-</span>}
                  {it.sources[a.key] && <span className="ml-1 rounded bg-slate-800 px-1 text-[10px] text-slate-400" title="Where the value came from">{it.sources[a.key]}</span>}
                </p>
              )}
            </div>
          ))}
          {d.type.all_attributes.length === 0 && <p className="text-sm text-slate-500">This type records no attributes.</p>}
        </div>
      </Panel>

      {d.children.length > 0 && (
        <Panel title={`Holds ${d.children.length}`} action={<span className="text-[11px] text-slate-500">{Object.entries(d.children_by_lifecycle).map(([k, v]) => `${v} ${k.replace("_", " ")}`).join(" · ")}</span>}>
          <div className="grid max-h-64 gap-1.5 overflow-y-auto sm:grid-cols-2">{d.children.map((c) => <Row key={c.id} b={c} onOpen={onOpen} />)}</div>
        </Panel>
      )}

      <Panel title="Relationships">
        <div className="space-y-1.5">
          {d.relationships.map((r) => (
            <div key={r.id} className="flex items-center gap-2 text-sm">
              <span className="text-slate-400">{r.wording}</span>
              {r.other && <button type="button" onClick={() => onOpen(r.other!.id)} className="truncate font-semibold text-sky-300 hover:underline">{r.other.identifier}</button>}
              <span className="flex-1" />
              <button type="button" onClick={() => act(() => invApi(`/relationships/${r.id}`, "DELETE"))} className="text-[11px] text-slate-500 hover:text-red-400">remove</button>
            </div>
          ))}
          {d.relationships.length === 0 && <p className="text-sm text-slate-500">None.</p>}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <select value={rel.rel_type} onChange={(e) => setRel({ ...rel, rel_type: e.target.value })} className={`${INPUT} w-40`}>
            {Object.entries(o.relationships).map(([k, v]) => <option key={k} value={k}>{v.forward}</option>)}
          </select>
          <input value={rel.target} onChange={(e) => setRel({ ...rel, target: e.target.value })} placeholder="Item identifier" className={`${INPUT} flex-1`} />
          <button type="button" disabled={!rel.target.trim()} onClick={() => act(async () => { await invApi("/relationships", "POST", { source: id, target: rel.target, rel_type: rel.rel_type }); setRel({ ...rel, target: "" }); })} className={BTN}>Link</button>
        </div>
      </Panel>

      {d.services.length > 0 && (
        <Panel title="Services on it">
          <div className="grid gap-1.5 sm:grid-cols-2">{d.services.map((s) => <Row key={s.id} b={s} onOpen={onOpen} />)}</div>
        </Panel>
      )}

      <Panel title="History">
        <ol className="max-h-56 space-y-1 overflow-y-auto text-xs">
          {d.history.map((h, i) => (
            <li key={i} className="flex gap-2">
              <span className="w-28 shrink-0 text-slate-500">{h.at.slice(0, 16).replace("T", " ")}</span>
              <span className="w-20 shrink-0 font-semibold text-slate-300">{h.action.replace("_", " ")}</span>
              <span className="min-w-0 flex-1 truncate text-slate-400" title={JSON.stringify(h.changes)}>{JSON.stringify(h.changes)}</span>
              <span className="shrink-0 text-slate-600">{h.source}{h.reference ? ` · ${h.reference}` : ""}</span>
            </li>
          ))}
        </ol>
      </Panel>
    </div>
  );
}

function NewItem({ types, onDone }: { types: ItemType[]; onDone: (id: number) => void }) {
  const [v, setV] = useState({ type_code: "", identifier: "", name: "", parent: "", serial: "", vendor: "", model: "" });
  const [attrs, setAttrs] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const t = types.find((x) => x.code === v.type_code);
  async function save() {
    try {
      const d = await invApi<Detail>("/items", "POST", { ...v, attributes: attrs, verified: true });
      onDone(d.item.id);
    } catch (e) {
      setError(errorText(e, "Could not add the item"));
    }
  }
  return (
    <Panel title="New item">
      <div className="grid gap-2 sm:grid-cols-3">
        <Field label="Type">
          <select value={v.type_code} onChange={(e) => setV({ ...v, type_code: e.target.value })} className={INPUT}>
            <option value="">Choose…</option>{types.filter((x) => x.is_active).map((x) => <option key={x.code} value={x.code}>{x.name}</option>)}
          </select>
        </Field>
        <Field label="Identifier" hint={t?.naming ? `Blank = named ${t.naming}` : "Blank = named automatically"}><input value={v.identifier} onChange={(e) => setV({ ...v, identifier: e.target.value })} className={INPUT} /></Field>
        <Field label="Sits in" hint={t?.contained_in.length ? `A ${t.contained_in.join(" / ")}` : undefined}><input value={v.parent} onChange={(e) => setV({ ...v, parent: e.target.value })} placeholder="Parent's identifier" className={INPUT} /></Field>
        <Field label="Name"><input value={v.name} onChange={(e) => setV({ ...v, name: e.target.value })} className={INPUT} /></Field>
        <Field label="Serial"><input value={v.serial} onChange={(e) => setV({ ...v, serial: e.target.value })} className={INPUT} /></Field>
        <Field label="Vendor / model"><span className="flex gap-1"><input value={v.vendor} onChange={(e) => setV({ ...v, vendor: e.target.value })} className={INPUT} /><input value={v.model} onChange={(e) => setV({ ...v, model: e.target.value })} className={INPUT} /></span></Field>
        {t?.all_attributes.map((a) => (
          <Field key={a.key} label={`${a.label}${a.required ? " *" : ""}`}>
            {a.values ? (
              <select value={attrs[a.key] ?? ""} onChange={(e) => setAttrs({ ...attrs, [a.key]: e.target.value })} className={INPUT}><option value="" />{a.values.map((x) => <option key={x}>{x}</option>)}</select>
            ) : <input value={attrs[a.key] ?? ""} onChange={(e) => setAttrs({ ...attrs, [a.key]: e.target.value })} className={INPUT} />}
          </Field>
        ))}
      </div>
      {error && <p className="mt-2 text-xs text-red-400">{error}</p>}
      <button type="button" disabled={!v.type_code} onClick={save} className={`${BTN_PRIMARY} mt-3`}>Add</button>
    </Panel>
  );
}

function ReceiveStock({ types, o, onDone }: { types: ItemType[]; o: Overview; onDone: () => void }) {
  const [v, setV] = useState({ type_code: "", quantity: "10", location: "", vendor: "", model: "", serial_prefix: "" });
  const [error, setError] = useState<string | null>(null);
  async function save() {
    try {
      await invApi("/items/receive", "POST", { ...v, quantity: Number(v.quantity) });
      onDone();
    } catch (e) {
      setError(errorText(e, "Could not receive the stock"));
    }
  }
  return (
    <Panel title="Receive equipment">
      <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <Field label="Type"><select value={v.type_code} onChange={(e) => setV({ ...v, type_code: e.target.value })} className={INPUT}><option value="">Choose…</option>{types.filter((t) => t.kind === "physical").map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}</select></Field>
        <Field label="Quantity"><input type="number" min={1} max={500} value={v.quantity} onChange={(e) => setV({ ...v, quantity: e.target.value })} className={INPUT} /></Field>
        <Field label="Into"><select value={v.location} onChange={(e) => setV({ ...v, location: e.target.value })} className={INPUT}><option value="">Choose…</option>{o.locations.map((l) => <option key={l.id} value={l.identifier}>{l.identifier}</option>)}</select></Field>
        <Field label="Vendor"><input value={v.vendor} onChange={(e) => setV({ ...v, vendor: e.target.value })} className={INPUT} /></Field>
        <Field label="Model"><input value={v.model} onChange={(e) => setV({ ...v, model: e.target.value })} className={INPUT} /></Field>
        <Field label="Serial prefix" hint="Blank = no serials"><input value={v.serial_prefix} onChange={(e) => setV({ ...v, serial_prefix: e.target.value })} className={INPUT} /></Field>
      </div>
      {error && <p className="mt-2 text-xs text-red-400">{error}</p>}
      <button type="button" disabled={!v.type_code || !v.location} onClick={save} className={`${BTN_PRIMARY} mt-3`}>Receive</button>
    </Panel>
  );
}

/* ---------------------------------------------------------------- pools */

function Pools({ o, onChanged }: { o: Overview; onChanged: () => void }) {
  const [pools, setPools] = useState<Pool[]>([]);
  const [v, setV] = useState({ type_code: "vlan", pool_type: "range", range_start: "", range_end: "", block_size: "29", format: "{n}", location: "", name: "", threshold_pct: "80" });
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => { invApi<Pool[]>("/pools").then(setPools).catch(() => setPools([])); }, []);
  useEffect(load, [load]);
  async function save() {
    try {
      await invApi("/pools", "POST", { ...v, block_size: Number(v.block_size), threshold_pct: Number(v.threshold_pct), location: v.location || null });
      setError(null);
      load();
      onChanged();
    } catch (e) {
      setError(errorText(e, "Could not create the pool"));
    }
  }
  const ip = v.pool_type === "ip";
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-2">
        {pools.map((p) => (
          <div key={p.id} className={`${CARD} space-y-2 p-4`}>
            <div className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate font-semibold text-slate-100">{p.name}</span>
              {p.over_threshold && <span className="rounded bg-amber-500/15 px-1.5 text-[10px] font-bold text-amber-300">OVER {p.threshold_pct}%</span>}
            </div>
            <p className="text-xs text-slate-500">{p.type_name} · {p.pool_type === "ip" ? `${p.range_start} in /${p.block_size}s` : `${p.range_start}-${p.range_end}${p.format !== "{n}" ? ` as ${p.format}` : ""}`} · {p.location ?? "shared"}</p>
            <Bar pct={p.used_pct} warn={p.over_threshold} />
            <p className="text-xs text-slate-400">{p.used} used · {p.free} free of {p.capacity}</p>
          </div>
        ))}
        {pools.length === 0 && <Empty>No pools yet.</Empty>}
      </div>
      <Panel title="New pool">
        <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-5">
          <Field label="Hands out"><select value={v.type_code} onChange={(e) => setV({ ...v, type_code: e.target.value, pool_type: ["ip_block", "ipv6_prefix"].includes(e.target.value) ? "ip" : v.pool_type === "ip" ? "range" : v.pool_type })} className={INPUT}>
            {["vlan", "ip_block", "ipv6_prefix", "phone_number", "circuit_id", "service_id", "sip_trunk"].map((c) => <option key={c} value={c}>{c.replace("_", " ")}</option>)}
          </select></Field>
          <Field label="Kind"><select value={v.pool_type} onChange={(e) => setV({ ...v, pool_type: e.target.value })} className={INPUT}><option value="range">Number range</option><option value="sequence">Formatted sequence</option><option value="ip">IP subnets</option></select></Field>
          {ip ? <>
            <Field label="Network"><input value={v.range_start} onChange={(e) => setV({ ...v, range_start: e.target.value })} placeholder="10.30.0.0/16" className={INPUT} /></Field>
            <Field label="Hand out blocks of /"><input type="number" value={v.block_size} onChange={(e) => setV({ ...v, block_size: e.target.value })} className={INPUT} /></Field>
          </> : <>
            <Field label="From"><input value={v.range_start} onChange={(e) => setV({ ...v, range_start: e.target.value })} placeholder="100" className={INPUT} /></Field>
            <Field label="To"><input value={v.range_end} onChange={(e) => setV({ ...v, range_end: e.target.value })} placeholder="999" className={INPUT} /></Field>
          </>}
          {v.pool_type === "sequence" && <Field label="Reads as" hint="{n} or {n:06d}"><input value={v.format} onChange={(e) => setV({ ...v, format: e.target.value })} className={INPUT} /></Field>}
          <Field label="For location"><select value={v.location} onChange={(e) => setV({ ...v, location: e.target.value })} className={INPUT}><option value="">Shared - every location</option>{o.locations.map((l) => <option key={l.id} value={l.identifier}>{l.identifier}</option>)}</select></Field>
          <Field label="Warn at %"><input type="number" value={v.threshold_pct} onChange={(e) => setV({ ...v, threshold_pct: e.target.value })} className={INPUT} /></Field>
          <Field label="Name" hint="Blank = named from the range"><input value={v.name} onChange={(e) => setV({ ...v, name: e.target.value })} className={INPUT} /></Field>
        </div>
        {error && <p className="mt-2 text-xs text-red-400">{error}</p>}
        <button type="button" disabled={!v.range_start} onClick={save} className={`${BTN_PRIMARY} mt-3`}>Create pool</button>
      </Panel>
    </div>
  );
}

/* ---------------------------------------------------------------- item types */

const BLANK_TYPE: Omit<ItemType, "id" | "all_attributes" | "builtin" | "items"> = {
  code: "", name: "", kind: "physical", description: "", parent_code: "", attributes: [], contained_in: [], naming: "", resource_type: "", is_active: true,
};

function Types({ o }: { o: Overview }) {
  const [types, setTypes] = useState<ItemType[]>([]);
  const [editing, setEditing] = useState<(typeof BLANK_TYPE & { id?: number }) | null>(null);
  const [describe, setDescribe] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => { invApi<ItemType[]>("/types").then(setTypes).catch(() => setTypes([])); }, []);
  useEffect(load, [load]);
  const grouped = useMemo(() => (["location", "physical", "logical", "service"] as Kind[]).map((k) => [k, types.filter((t) => t.kind === k)] as const), [types]);

  async function draft() {
    try {
      const r = await invApi<{ type: typeof BLANK_TYPE; source: string; model: string | null; notes: string }>("/types/draft", "POST", { description: describe });
      setEditing({ ...BLANK_TYPE, ...r.type });
      setNotes(r.source === "llm" ? `Drafted by ${r.model}.` : r.notes);
      setError(null);
    } catch (e) {
      setError(errorText(e, "Could not draft it"));
    }
  }
  async function save() {
    if (!editing) return;
    try {
      await invApi(editing.id ? `/types/${editing.id}` : "/types", editing.id ? "PUT" : "POST", editing);
      setEditing(null);
      setNotes("");
      load();
    } catch (e) {
      setError(errorText(e, "Could not save the type"));
    }
  }
  const setAttr = (i: number, patch: Partial<Attr>) => editing && setEditing({ ...editing, attributes: editing.attributes.map((a, j) => (j === i ? { ...a, ...patch } : a)) });

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
      <div className="space-y-4">
        <div className="rounded-xl border border-violet-500/30 bg-violet-500/5 p-3">
          <p className="mb-2 text-xs font-bold text-violet-300">✨ Describe a kind of equipment, place or logical resource - AI drafts the type</p>
          <textarea value={describe} onChange={(e) => setDescribe(e.target.value)} rows={3} className={INPUT}
            placeholder="e.g. A DSLAM is a network device that sits in an exchange or cabinet, with a port count (required), firmware and a line type (ADSL, VDSL)" />
          <button type="button" disabled={describe.trim().length < 8} onClick={draft} className="mt-2 rounded-lg bg-violet-600 px-4 py-1.5 text-sm font-bold text-white hover:bg-violet-500 disabled:opacity-50">Draft the type</button>
          <p className="mt-1 text-[11px] text-slate-500">Drafted by {o.ai ?? "the built-in reader"} - nothing is saved until you Save.</p>
        </div>
        {grouped.map(([kind, list]) => (
          <Section key={kind} title={`${KIND_ICON[kind]} ${kind}`} count={list.length}>
            <div className="space-y-1">
              {list.map((t) => (
                <button key={t.id} type="button" onClick={() => { setEditing({ ...t }); setNotes(""); }} className="flex w-full items-center gap-2 rounded-lg border border-slate-800 px-3 py-2 text-left text-sm hover:border-slate-600">
                  <span className="min-w-0 flex-1 truncate text-slate-200">{t.name} <span className="text-slate-500">· {t.code}</span></span>
                  {t.resource_type && <span className="rounded bg-sky-500/10 px-1.5 text-[10px] text-sky-300">SOM: {t.resource_type}</span>}
                  <span className="text-[11px] text-slate-500">{t.items}</span>
                </button>
              ))}
            </div>
          </Section>
        ))}
      </div>
      {editing ? (
        <Panel title={editing.id ? `Item type · ${editing.code}` : "New item type"} action={<button type="button" onClick={() => setEditing(null)} className={BTN}>Close</button>}>
          {notes && <p className="mb-2 text-xs text-violet-300">{notes}</p>}
          <div className="grid gap-2 sm:grid-cols-2">
            <Field label="Name"><input value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} className={INPUT} /></Field>
            <Field label="Code"><input value={editing.code} onChange={(e) => setEditing({ ...editing, code: e.target.value })} className={INPUT} /></Field>
            <Field label="Kind"><select value={editing.kind} onChange={(e) => setEditing({ ...editing, kind: e.target.value as Kind })} className={INPUT}>{["location", "physical", "logical", "service"].map((k) => <option key={k}>{k}</option>)}</select></Field>
            <Field label="Inherits from"><select value={editing.parent_code} onChange={(e) => setEditing({ ...editing, parent_code: e.target.value })} className={INPUT}><option value="">-</option>{types.filter((t) => t.kind === editing.kind && t.code !== editing.code).map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}</select></Field>
            <Field label="Sits in (type codes, comma-separated)" hint="Blank = anywhere"><input value={editing.contained_in.join(", ")} onChange={(e) => setEditing({ ...editing, contained_in: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} className={INPUT} /></Field>
            <Field label="Naming rule" hint="{location}, {type}, {n:02d}"><input value={editing.naming} onChange={(e) => setEditing({ ...editing, naming: e.target.value })} className={INPUT} /></Field>
            <Field label="Fulfillment resource type" hint="What SOM steps ask for to get one (port, ont, vlan...)"><input value={editing.resource_type} onChange={(e) => setEditing({ ...editing, resource_type: e.target.value })} className={INPUT} /></Field>
            <Field label="Description"><input value={editing.description} onChange={(e) => setEditing({ ...editing, description: e.target.value })} className={INPUT} /></Field>
          </div>
          <p className="mb-1 mt-4 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Attributes</p>
          <div className="space-y-2">
            {editing.attributes.map((a, i) => (
              <div key={i} className="grid grid-cols-2 gap-1 sm:grid-cols-[1.2fr_1fr_1.4fr_auto_auto_auto]">
                <input value={a.label} onChange={(e) => setAttr(i, { label: e.target.value, key: a.key || e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "_") })} placeholder="Label" className={INPUT} />
                <select value={a.type} onChange={(e) => setAttr(i, { type: e.target.value })} className={INPUT}>{o.attribute_types.map((t) => <option key={t}>{t}</option>)}</select>
                <input value={(a.values ?? []).join(", ")} disabled={a.type !== "picklist"} onChange={(e) => setAttr(i, { values: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} placeholder="Values (picklist)" className={INPUT} />
                <label className="flex items-center gap-1 text-[11px] text-slate-400"><input type="checkbox" checked={!!a.required} onChange={(e) => setAttr(i, { required: e.target.checked })} />req.</label>
                <label className="flex items-center gap-1 text-[11px] text-slate-400"><input type="checkbox" checked={!!a.unique} onChange={(e) => setAttr(i, { unique: e.target.checked })} />unique</label>
                <button type="button" onClick={() => setEditing({ ...editing, attributes: editing.attributes.filter((_, j) => j !== i) })} className="text-xs text-slate-500 hover:text-red-400">✕</button>
              </div>
            ))}
            <button type="button" onClick={() => setEditing({ ...editing, attributes: [...editing.attributes, { key: "", label: "", type: "text" }] })} className={BTN}>+ Attribute</button>
          </div>
          {error && <p className="mt-2 text-xs text-red-400">{error}</p>}
          <button type="button" onClick={save} className={`${BTN_PRIMARY} mt-4`}>Save type</button>
        </Panel>
      ) : (
        <Empty>
          <button type="button" onClick={() => { setEditing({ ...BLANK_TYPE }); setNotes(""); }} className={BTN}>+ New item type</button>
          <p className="mt-2">or pick one to change it. Types inherit attributes, say where their items may sit, how they are named, and which Fulfillment resource they answer to.</p>
        </Empty>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- reconcile */

const RECON_EXAMPLE = JSON.stringify({
  source: "nms-scan", full_scan_of: "BLR-EX-02",
  items: [
    { type: "olt", identifier: "BLR-EX-02-OLT-01", attributes: { software: "R022" }, oper_status: "up" },
    { type: "card", identifier: "BLR-EX-02-OLT-01/3", parent: "BLR-EX-02-OLT-01", model: "GPHF", oper_status: "up" },
  ],
}, null, 2);

type ReconResult = { run_id: number; dry_run: boolean; source: string; summary: Record<string, number>; details: { result: string; identifier: string | null; item_id?: number; note: string }[] };

function Reconcile({ onOpen }: { onOpen: (id: number) => void }) {
  const [text, setText] = useState(RECON_EXAMPLE);
  const [result, setResult] = useState<ReconResult | null>(null);
  const [runs, setRuns] = useState<{ id: number; source: string; dry_run: boolean; summary: Record<string, number>; at: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const loadRuns = useCallback(() => { invApi<typeof runs>("/reconcile/runs").then(setRuns).catch(() => setRuns([])); }, []);
  useEffect(loadRuns, [loadRuns]);
  async function run(apply: boolean) {
    try {
      const payload = JSON.parse(text);
      setResult(await invApi<ReconResult>("/reconcile", "POST", { ...payload, apply }));
      setError(null);
      loadRuns();
    } catch (e) {
      setError(e instanceof SyntaxError ? "That isn't valid JSON" : errorText(e, "Reconciliation failed"));
    }
  }
  const tone: Record<string, string> = { created: "text-emerald-300", updated: "text-sky-300", unchanged: "text-slate-400", conflicts: "text-amber-300", missing: "text-amber-300", rejected: "text-red-300" };
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Panel title="A discovery report">
        <p className="mb-2 text-xs text-slate-500">
          What a network scan, an element manager or a spreadsheet says is out there. Items are found by type + identifier, else serial; values set by hand
          are never overwritten by discovery; with <code>full_scan_of</code>, what is recorded there but not reported is flagged - never deleted. Dry run first.
        </p>
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={16} className={`${INPUT} font-mono text-xs`} />
        {error && <p className="mt-2 text-xs text-red-400">{error}</p>}
        <div className="mt-2 flex gap-2">
          <button type="button" onClick={() => run(false)} className={BTN}>Dry run</button>
          <button type="button" onClick={() => run(true)} className={BTN_WARN}>Apply</button>
        </div>
      </Panel>
      <div className="space-y-4">
        {result && (
          <Panel title={`${result.dry_run ? "Dry run" : "Applied"} · ${result.source}`}>
            <p className="mb-2 text-xs text-slate-300">{Object.entries(result.summary).map(([k, v]) => `${v} ${k}`).join(" · ")}</p>
            <ul className="max-h-80 space-y-1 overflow-y-auto text-xs">
              {result.details.map((d, i) => (
                <li key={i} className="flex gap-2">
                  <span className={`w-20 shrink-0 font-semibold ${tone[d.result] ?? ""}`}>{d.result}</span>
                  {d.item_id && !result.dry_run ? <button type="button" onClick={() => onOpen(d.item_id!)} className="shrink-0 text-sky-300 hover:underline">{d.identifier}</button> : <span className="shrink-0 text-slate-300">{d.identifier}</span>}
                  <span className="min-w-0 flex-1 truncate text-slate-500">{d.note}</span>
                </li>
              ))}
            </ul>
          </Panel>
        )}
        <Panel title="Recent runs">
          <ul className="space-y-1 text-xs">
            {runs.map((r) => (
              <li key={r.id} className="flex gap-2 text-slate-400">
                <span className="w-32 shrink-0 text-slate-500">{r.at.slice(0, 16).replace("T", " ")}</span>
                <span className="w-28 shrink-0 truncate text-slate-300">{r.source}</span>
                <span className="w-14 shrink-0">{r.dry_run ? "dry run" : "applied"}</span>
                <span className="truncate">{Object.entries(r.summary).filter(([, v]) => v).map(([k, v]) => `${v} ${k}`).join(" · ")}</span>
              </li>
            ))}
            {runs.length === 0 && <li className="text-slate-500">None yet.</li>}
          </ul>
        </Panel>
      </div>
    </div>
  );
}
