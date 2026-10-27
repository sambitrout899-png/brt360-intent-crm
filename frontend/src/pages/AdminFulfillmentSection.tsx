import { useEffect, useMemo, useRef, useState } from "react";
import {
  createFulfillmentPattern,
  createFulfillmentPatternFromTemplate,
  deleteFulfillmentPattern,
  getFulfillmentOrder,
  listFulfillmentOrders,
  listFulfillmentPatterns,
  fulfillmentCatalog,
  generateFulfillmentPattern,
  postInventoryResponse,
  previewFulfillmentPattern,
  resolveFulfillmentTask,
  setFulfillmentPatternActive,
  submitOrderToFulfillment,
  updateFulfillmentPattern,
} from "../api/client";
import type {
  FulfillmentCatalog,
  FulfillmentDraft,
  FulfillmentFunction,
  FulfillmentGraph,
  FulfillmentLayer,
  FulfillmentNode,
  FulfillmentOrderSummary,
  FulfillmentOrderView,
  FulfillmentPattern,
  FulfillmentPatternInput,
  FulfillmentPreview,
  FulfillmentTask,
  FulfillmentTriggerIntent,
} from "../api/types";

const LAYERS: FulfillmentLayer[] = ["COM", "SOM", "TOM"];
const LANE_H = 170;
const NODE_W = 178;
const NODE_H = 56;
const LAYER_COLOR: Record<FulfillmentLayer, string> = { COM: "#38bdf8", SOM: "#a78bfa", TOM: "#f59e0b" };
const STATUS_COLOR: Record<string, string> = {
  pending: "#475569", running: "#38bdf8", waiting: "#f59e0b", completed: "#10b981", skipped: "#64748b", failed: "#ef4444",
};
const INPUT = "mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-sm text-slate-200";
const BTN = "rounded-md border border-slate-700 px-3 py-1.5 text-xs font-bold text-slate-300 hover:border-sky-500 hover:text-sky-300 disabled:opacity-50";
const SELECTS: Record<string, string[]> = {
  frequency: ["monthly", "quarterly", "annual"],
  payment_method: ["bill_me", "credit_card", "auto_debit"],
  bill_media: ["email", "paper", "portal", "sms"],
  payment_type: ["postpaid", "prepaid"],
  response: ["sync", "async"],
};
const KEY_LABELS: Record<string, string> = {
  resources: "Resources to ask inventory for", response: "Inventory answers", kind: "Work order kind", template: "Field Service template",
  subject: "Job title", skills: "Skills needed (comma-separated)", duration_minutes: "Duration (minutes)", instructions: "Instructions for the engineer",
  rate_plan: "Rate plan", frequency: "Bill frequency", payment_method: "Payment method", bill_media: "Bill media", payment_type: "Payment type",
  label: "Milestone text",
};

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

function toInput(p: FulfillmentPattern): FulfillmentPatternInput {
  const { name, description, products, segment, order_type, priority, mode, trigger_intents, graph } = p;
  return { name, description, products, segment, order_type, priority, mode, trigger_intents, graph };
}

function codesOf(triggers: string): string[] {
  return triggers.split(",").map((c) => c.trim()).filter(Boolean);
}

const EXAMPLES = [
  "For Fibre 500 business orders: register the customer, a billing account and a monthly billing profile. Then design the service, assign a VLAN and a static IP and reserve a port and an ONT. Install (3 hours), then test the connection and get the acceptance signed. Activate the service, start billing, create the installed assets and complete the order.",
  "When Upgrade bandwidth runs on an order: assign a VLAN, activate the service, then start billing.",
  "Site survey only if the order has an address, then install and test the connection in parallel. Let the AI decide the steps per order.",
];

// ---------------------------------------------------------------- describe it - AI drafts the pattern

function DescribeBox({ catalog, replacing, sourcePattern, onDraft }: { catalog: FulfillmentCatalog; replacing: boolean; sourcePattern?: FulfillmentPatternInput; onDraft: (d: FulfillmentDraft) => Promise<void> | void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<FulfillmentDraft | null>(null);

  async function draft() {
    if (text.trim().length < 10) return;
    setBusy(true);
    setError(null);
    try {
      const d = await generateFulfillmentPattern(text.trim(), sourcePattern);
      setResult(d);
      if (d.validation_errors.length) {
        setError(d.validation_errors.join("; "));
        return;
      }
      await onDraft(d);
    } catch (e) {
      setError(errorText(e, "Could not draft the pattern"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-violet-500/30 bg-violet-500/5 p-4">
      <p className="text-xs font-bold text-violet-300">
        ✨ Describe the fulfillment process - AI drafts the COM / SOM / TOM pattern{replacing ? " (replaces the lanes and settings below - nothing is saved until you Save)" : " as a new draft"}
      </p>
      <p className="mb-2 text-[11px] text-slate-500">
        Configured model: {catalog.ai ? <b className="text-slate-300">{catalog.ai}</b> : <>built-in reader - set up your own model or an API under Admin → AI for richer drafts</>}.
        Say the steps in billing, inventory and the field, for which products, and which intents start it.
      </p>
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3}
        onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) draft(); }}
        placeholder="e.g. Register the customer and a billing account, reserve a port and an ONT, install, test the connection, then start billing"
        className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" />
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button onClick={draft} disabled={busy || text.trim().length < 10}
          className="rounded-lg bg-violet-600 px-4 py-1.5 text-sm font-bold text-white hover:bg-violet-500 disabled:opacity-50">
          {busy ? "Drafting…" : "Draft the pattern"}
        </button>
        {EXAMPLES.map((x, i) => (
          <button key={x} onClick={() => setText(x)} title={x}
            className="rounded-full border border-slate-700 px-2 py-0.5 text-[10px] text-slate-400 hover:border-violet-500 hover:text-violet-300">
            Example {i + 1}
          </button>
        ))}
      </div>
      {error && <p className="mt-2 text-xs text-red-400">{error}</p>}
      {result && (
        <div className="mt-2 space-y-0.5 text-xs text-violet-300/90">
          <p>{result.source === "llm" ? `Drafted by ${result.model}.` : ""} {result.notes}</p>
          {result.fixes.map((f) => <p key={f} className="text-slate-400">• {f}</p>)}
          <p className="text-slate-500">Review the steps, then Save and Activate - a drafted pattern never goes live on its own.</p>
        </div>
      )}
    </div>
  );
}

function TriggerPicker({ value, catalog, onChange }: { value: string; catalog: FulfillmentCatalog; onChange: (v: string) => void }) {
  const chosen = codesOf(value);
  const toggle = (code: string) => onChange((chosen.includes(code) ? chosen.filter((c) => c !== code) : [...chosen, code]).join(", "));
  const groups: [string, FulfillmentTriggerIntent[]][] = [
    ["Vanilla", catalog.trigger_intents.filter((i) => i.kind !== "custom")],
    ["Custom", catalog.trigger_intents.filter((i) => i.kind === "custom")],
  ];
  return (
    <div className="md:col-span-4">
      <p>Started by - the intents whose completion sends their order here {chosen.length === 0 && <span className="text-slate-500">(none chosen = Order Control approval, {catalog.default_trigger})</span>}</p>
      {groups.map(([title, items]) => (
        <div key={title} className="mt-1 flex flex-wrap items-center gap-1">
          <span className="w-16 text-[10px] font-bold uppercase text-slate-500">{title}</span>
          {items.map((i) => (
            <button key={i.code} type="button" onClick={() => toggle(i.code)} title={i.code}
              className={`rounded border px-2 py-0.5 text-[11px] ${chosen.includes(i.code) ? "border-violet-400 bg-violet-500/15 text-violet-200" : "border-slate-700 text-slate-400 hover:border-slate-500"}`}>
              {i.label} <span className="text-slate-500">{i.code}</span>
            </button>
          ))}
          {items.length === 0 && <span className="text-[11px] text-slate-600">{title === "Custom" ? "No custom intents yet - make one in the Flow Studio or Admin → Intents" : "-"}</span>}
        </div>
      ))}
    </div>
  );
}

function laneTop(layer: FulfillmentLayer) {
  return LAYERS.indexOf(layer) * LANE_H;
}

function clampY(layer: FulfillmentLayer, y: number) {
  const top = laneTop(layer);
  return Math.min(Math.max(y, top + 30), top + LANE_H - NODE_H - 10);
}

// ---------------------------------------------------------------- swimlane canvas (designer + monitor)

function SwimlaneCanvas({
  graph,
  selected,
  statuses,
  onSelect,
  onMove,
}: {
  graph: FulfillmentGraph;
  selected?: string | null;
  /** node id -> task status (the monitor) */
  statuses?: Record<string, string>;
  onSelect?: (id: string | null) => void;
  onMove?: (id: string, x: number, y: number) => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const drag = useRef<{ id: string; dx: number; dy: number } | null>(null);
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const width = Math.max(900, ...graph.nodes.map((n) => n.x + NODE_W + 60));
  const height = LANE_H * LAYERS.length;
  const point = (e: React.PointerEvent) => {
    const r = svgRef.current!.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };
  const routes = graph.nodes.filter((n) => n.function === "route_to_som" || n.function === "route_to_tom");

  return (
    <div className="overflow-auto rounded-xl border border-slate-700 bg-slate-950">
      <svg
        ref={svgRef}
        width={width}
        height={height}
        className="select-none"
        onPointerMove={(e) => {
          if (!drag.current || !onMove) return;
          const p = point(e);
          const node = byId.get(drag.current.id);
          if (!node) return;
          onMove(node.id, Math.max(10, Math.round(p.x - drag.current.dx)), clampY(node.layer, Math.round(p.y - drag.current.dy)));
        }}
        onPointerUp={() => (drag.current = null)}
        onPointerLeave={() => (drag.current = null)}
        onPointerDown={(e) => e.target === svgRef.current && onSelect?.(null)}
      >
        <defs>
          <marker id="fulfillment-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0,0 L10,5 L0,10 z" fill="#64748b" />
          </marker>
          <marker id="fulfillment-route" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0,0 L10,5 L0,10 z" fill="#e879f9" />
          </marker>
        </defs>
        {LAYERS.map((layer, i) => (
          <g key={layer}>
            <rect x={0} y={i * LANE_H} width={width} height={LANE_H} fill={i % 2 ? "#0b1222" : "#0f172a"} />
            <rect x={0} y={i * LANE_H} width={4} height={LANE_H} fill={LAYER_COLOR[layer]} />
            <text x={12} y={i * LANE_H + 18} fontSize="11" fontWeight="bold" fill={LAYER_COLOR[layer]}>
              {layer} · {layer === "COM" ? "Customer Order Management" : layer === "SOM" ? "Service Order Management" : "Technical Order Management"}
            </text>
          </g>
        ))}
        {graph.edges.map((e) => {
          const s = byId.get(e.source);
          const t = byId.get(e.target);
          if (!s || !t) return null;
          const x1 = s.x + NODE_W;
          const y1 = s.y + NODE_H / 2;
          const x2 = t.x;
          const y2 = t.y + NODE_H / 2;
          const mid = (x1 + x2) / 2;
          return <path key={e.id} d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`} fill="none" stroke="#475569" strokeWidth={1.5} markerEnd="url(#fulfillment-arrow)" />;
        })}
        {routes.map((r) => {
          const next = r.function === "route_to_som" ? "SOM" : "TOM";
          const x = r.x + NODE_W / 2;
          return (
            <g key={`route-${r.id}`}>
              <path d={`M${x},${r.y + NODE_H} L${x},${laneTop(next as FulfillmentLayer) + 26}`} stroke="#e879f9" strokeDasharray="5 4" strokeWidth={1.5} markerEnd="url(#fulfillment-route)" />
              <text x={x + 6} y={laneTop(next as FulfillmentLayer) - 6} fontSize="10" fill="#e879f9">
                decompose → {next} · completes back ↑
              </text>
            </g>
          );
        })}
        {graph.nodes.map((n) => {
          const color = LAYER_COLOR[n.layer];
          const status = statuses?.[n.id];
          const isSelected = n.id === selected;
          const conditional = !!n.config?.when_field;
          return (
            <g
              key={n.id}
              transform={`translate(${n.x},${n.y})`}
              style={{ cursor: onMove ? "grab" : onSelect ? "pointer" : "default" }}
              onPointerDown={(e) => {
                e.stopPropagation();
                onSelect?.(n.id);
                if (onMove) {
                  const p = point(e);
                  drag.current = { id: n.id, dx: p.x - n.x, dy: p.y - n.y };
                }
              }}
            >
              <rect width={NODE_W} height={NODE_H} rx={9} fill="#0f172a"
                stroke={isSelected ? "#f8fafc" : status ? STATUS_COLOR[status] ?? color : color}
                strokeWidth={isSelected || status ? 2.5 : 1.5} strokeDasharray={status === "skipped" ? "4 3" : undefined} />
              <rect width={5} height={NODE_H} rx={2} fill={color} />
              <text x={12} y={18} fontSize="9" fill={color} fontWeight="bold">
                {n.function.replace(/_/g, " ").toUpperCase()}{n.mandatory ? " · REQUIRED" : ""}
              </text>
              <text x={12} y={36} fontSize="12" fill="#e2e8f0">
                {n.label.length > 24 ? `${n.label.slice(0, 23)}…` : n.label}
              </text>
              {(status || conditional) && (
                <text x={12} y={50} fontSize="9" fill={status ? STATUS_COLOR[status] ?? "#94a3b8" : "#94a3b8"}>
                  {status ? status.toUpperCase() : `only when ${n.config.when_field}`}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// ---------------------------------------------------------------- inspector

function ConfigField({ k, value, catalog, onChange }: { k: string; value: string; catalog: FulfillmentCatalog; onChange: (v: string) => void }) {
  if (k === "resources") {
    const chosen = value.split(",").map((s) => s.trim()).filter(Boolean);
    const toggle = (r: string) => onChange((chosen.includes(r) ? chosen.filter((c) => c !== r) : [...chosen, r]).join(", "));
    return (
      <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 rounded-md border border-slate-700 bg-slate-950 p-2 text-[11px]">
        {[["logical", catalog.logical_resources], ["physical", catalog.physical_resources]].map(([kind, list]) => (
          <div key={kind as string}>
            <p className="font-bold uppercase text-slate-500">{kind as string}</p>
            {(list as string[]).map((r) => (
              <label key={r} className="flex items-center gap-1.5 text-slate-300">
                <input type="checkbox" checked={chosen.includes(r)} onChange={() => toggle(r)} />
                {r.replace(/_/g, " ")}
              </label>
            ))}
          </div>
        ))}
      </div>
    );
  }
  if (k === "kind")
    return (
      <select value={value || "installation"} onChange={(e) => onChange(e.target.value)} className={INPUT}>
        {catalog.work_order_kinds.map((o) => <option key={o} value={o}>{o.replace(/_/g, " ")}</option>)}
      </select>
    );
  if (k === "template")
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)} className={INPUT}>
        <option value="">— none (subject, skills and duration below) —</option>
        {catalog.templates.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  if (SELECTS[k])
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)} className={INPUT}>
        <option value="">— default —</option>
        {SELECTS[k].map((o) => <option key={o} value={o}>{k === "response" && o === "async" ? "async - inventory answers later (callback)" : o.replace(/_/g, " ")}</option>)}
      </select>
    );
  if (k === "instructions") return <textarea value={value} onChange={(e) => onChange(e.target.value)} rows={3} className={INPUT} />;
  return <input value={value} onChange={(e) => onChange(e.target.value)} type={k === "duration_minutes" ? "number" : "text"} className={INPUT} />;
}

function NodeInspector({ node, graph, catalog, onChange, onDelete }: {
  node: FulfillmentNode; graph: FulfillmentGraph; catalog: FulfillmentCatalog; onChange: (g: FulfillmentGraph) => void; onDelete: () => void;
}) {
  const spec = catalog.functions.find((f) => f.function === node.function);
  const lane = graph.nodes.filter((n) => n.id !== node.id && (n.layer === node.layer ||
    (n.layer !== "COM" && node.layer !== "COM" && !n.function.startsWith("route_") && !node.function.startsWith("route_"))));
  const waitsFor = new Set(graph.edges.filter((e) => e.target === node.id).map((e) => e.source));
  const patch = (p: Partial<FulfillmentNode>) => onChange({ ...graph, nodes: graph.nodes.map((n) => (n.id === node.id ? { ...n, ...p } : n)) });
  const setConfig = (k: string, v: string) => patch({ config: { ...node.config, [k]: v } });
  const toggleWait = (source: string) =>
    onChange({
      ...graph,
      edges: waitsFor.has(source)
        ? graph.edges.filter((e) => !(e.source === source && e.target === node.id))
        : [...graph.edges, { id: `e-${source}-${node.id}`, source, target: node.id }],
    });

  return (
    <div className="space-y-3 rounded-xl border border-slate-700 bg-slate-900/60 p-4 text-xs">
      <div className="flex items-center justify-between">
        <span className="font-bold uppercase" style={{ color: LAYER_COLOR[node.layer] }}>{node.layer} · {spec?.label ?? node.function}</span>
        <button onClick={onDelete} className="text-slate-500 hover:text-red-400">Delete</button>
      </div>
      {spec && <p className="text-slate-400">{spec.description}<br /><span className="text-slate-500">eTOM: {spec.etom}</span></p>}
      <label className="block text-slate-400">Label<input value={node.label} onChange={(e) => patch({ label: e.target.value })} className={INPUT} /></label>
      <label className="flex items-center gap-2 text-slate-300">
        <input type="checkbox" checked={!!node.mandatory} onChange={(e) => patch({ mandatory: e.target.checked })} />
        Required - an AI plan may never leave it out
      </label>
      {(spec?.config ?? []).map((k) => (
        <label key={k} className="block text-slate-400">
          {KEY_LABELS[k] ?? k}
          <ConfigField k={k} value={node.config?.[k] ?? ""} catalog={catalog} onChange={(v) => setConfig(k, v)} />
        </label>
      ))}
      <div className="rounded-md border border-slate-800 p-2">
        <p className="font-bold text-slate-400">Only when (optional)</p>
        <div className="mt-1 grid grid-cols-[1fr_80px_1fr] gap-1.5">
          <input list="fulfillment-condition-fields" value={node.config?.when_field ?? ""} onChange={(e) => setConfig("when_field", e.target.value)}
            placeholder="order.segment" className={INPUT} />
          <select value={node.config?.when_operator ?? "eq"} onChange={(e) => setConfig("when_operator", e.target.value)} className={INPUT}>
            {catalog.operators.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
          <input value={node.config?.when_value ?? ""} onChange={(e) => setConfig("when_value", e.target.value)} placeholder="B2B" className={INPUT} />
        </div>
        <datalist id="fulfillment-condition-fields">{catalog.condition_fields.map((f) => <option key={f} value={f} />)}</datalist>
        <p className="mt-1 text-[11px] text-slate-500">Blank = always. A step whose condition is false is skipped for that order.</p>
      </div>
      <div>
        <p className="font-bold text-slate-400">Waits for (SOM and TOM may depend on each other's tasks)</p>
        {lane.length === 0 && <p className="text-slate-500">No other step in this lane yet.</p>}
        {lane.map((n) => (
          <label key={n.id} className="flex items-center gap-2 text-slate-300">
            <input type="checkbox" checked={waitsFor.has(n.id)} onChange={() => toggleWait(n.id)} />
            {n.label} <span className="text-slate-600">{n.layer} · {n.id}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- designer

function PatternDesigner({ catalog }: { catalog: FulfillmentCatalog }) {
  const [patterns, setPatterns] = useState<FulfillmentPattern[]>([]);
  const [editing, setEditing] = useState<FulfillmentPattern | null>(null);
  const [form, setForm] = useState<FulfillmentPatternInput | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [preview, setPreview] = useState<FulfillmentPreview | null>(null);
  const [previewOrder, setPreviewOrder] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = () => listFulfillmentPatterns().then(setPatterns).catch((e) => setError(errorText(e, "Could not load patterns")));
  useEffect(() => {
    load();
  }, []);

  function open(p: FulfillmentPattern) {
    setEditing(p);
    setForm(toInput(p));
    setSelected(null);
    setPreview(null);
    setError(null);
  }

  async function run<T>(fn: () => Promise<T>, done?: (v: T) => void) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const v = await fn();
      done?.(v);
    } catch (e) {
      setError(errorText(e, "That didn't work"));
    } finally {
      setBusy(false);
    }
  }

  function addNode(layer: FulfillmentLayer, fn: FulfillmentFunction, preset: { label: string; config: Record<string, string> }) {
    if (!form) return;
    const taken = form.graph.nodes.map((n) => n.id);
    let i = taken.length + 1;
    const prefix = layer[0].toLowerCase();
    while (taken.includes(`${prefix}${i}`)) i++;
    const inLane = form.graph.nodes.filter((n) => n.layer === layer);
    const node: FulfillmentNode = {
      id: `${prefix}${i}`, layer, function: fn.function, label: preset.label, config: { ...preset.config },
      x: inLane.length ? Math.max(...inLane.map((n) => n.x)) + 200 : 30, y: laneTop(layer) + 50,
      mandatory: !!fn.route || fn.function === "complete_order",
    };
    const last = inLane.length ? inLane.reduce((a, b) => (a.x > b.x ? a : b)) : null;
    setForm({ ...form, graph: { nodes: [...form.graph.nodes, node], edges: last ? [...form.graph.edges, { id: `e-${last.id}-${node.id}`, source: last.id, target: node.id }] : form.graph.edges } });
    setSelected(node.id);
  }

  const selectedNode = form?.graph.nodes.find((n) => n.id === selected) ?? null;

  if (!form || !editing) {
    return (
      <div className="space-y-4">
        {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</p>}
        <div className="flex flex-wrap gap-2">
          <button disabled={busy} onClick={() => run(() => createFulfillmentPatternFromTemplate(), (p) => { load(); open(p); })}
            className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500 disabled:opacity-50">
            + New from the eTOM template (fixed broadband)
          </button>
          <button disabled={busy} onClick={() => run(() => createFulfillmentPattern({ name: "New fulfillment pattern", description: "", products: "", segment: "", order_type: "new", priority: 100, mode: "deterministic", trigger_intents: "", graph: { nodes: [], edges: [] } }), (p) => { load(); open(p); })}
            className={BTN}>
            + Blank pattern
          </button>
        </div>
        <DescribeBox catalog={catalog} replacing={false} onDraft={async (d) => {
          const p = await createFulfillmentPattern(d.pattern);  // saved as a draft - inactive until someone activates it
          load();
          open(p);
          setNotice(`Drafted "${p.name}" ${d.source === "llm" ? `by ${d.model}` : "by the built-in reader"} - review the steps, then Save and Activate.`);
        }} />
        <div className="grid gap-3 md:grid-cols-2">
          {patterns.map((p) => (
            <button key={p.id} onClick={() => open(p)} className="rounded-xl border border-slate-700 bg-slate-900/60 p-4 text-left hover:border-sky-500">
              <div className="flex items-center gap-2">
                <span className="font-bold text-slate-100">{p.name}</span>
                <span className={`rounded px-1.5 text-[10px] font-bold ${p.is_active ? "bg-emerald-500/15 text-emerald-300" : "bg-slate-700/40 text-slate-400"}`}>{p.is_active ? "ACTIVE" : "DRAFT"}</span>
                {p.mode === "llm" && <span className="rounded bg-violet-500/15 px-1.5 text-[10px] font-bold text-violet-300">AI-PLANNED</span>}
                {p.validation_errors.length > 0 && <span className="rounded bg-red-500/15 px-1.5 text-[10px] font-bold text-red-300">{p.validation_errors.length} ISSUE(S)</span>}
              </div>
              <p className="mt-1 text-xs text-slate-400">{p.products ? `Products: ${p.products}` : "Any product"} · {p.segment || "any segment"} · {p.order_type || "any"} orders · priority {p.priority} · v{p.version}</p>
              <p className="mt-1 text-xs text-slate-500">{LAYERS.map((l) => `${l} ${p.graph.nodes.filter((n) => n.layer === l).length}`).join(" · ")} steps</p>
              <p className="mt-1 text-xs text-slate-500">
                Started by: {p.started_by.map((c) => catalog.trigger_intents.find((i) => i.code === c)?.label ?? c).join(", ")}
              </p>
            </button>
          ))}
          {patterns.length === 0 && <p className="text-sm text-slate-400">No fulfillment patterns yet - start from the eTOM template.</p>}
        </div>
      </div>
    );
  }

  const errors = editing.validation_errors;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={() => { setEditing(null); setForm(null); load(); }} className={BTN}>← All patterns</button>
        <span className="text-sm font-bold text-slate-100">{editing.name}</span>
        <span className={`rounded px-1.5 text-[10px] font-bold ${editing.is_active ? "bg-emerald-500/15 text-emerald-300" : "bg-slate-700/40 text-slate-400"}`}>{editing.is_active ? "ACTIVE" : "DRAFT"} · v{editing.version}</span>
      </div>
      <div className="grid gap-3 rounded-xl border border-slate-700 bg-slate-900/60 p-4 text-xs text-slate-400 md:grid-cols-4">
        <label className="md:col-span-2">Name<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={INPUT} /></label>
        <label>Planned by
          <select value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value as FulfillmentPatternInput["mode"] })} className={INPUT}>
            <option value="deterministic">Deterministic - the plan as drawn</option>
            <option value="llm">AI - picks the steps each order needs, within this design</option>
          </select>
        </label>
        <label>Priority (highest match wins)<input type="number" value={form.priority} onChange={(e) => setForm({ ...form, priority: Number(e.target.value) })} className={INPUT} /></label>
        <label className="md:col-span-2">Products it fulfils (comma-separated - blank = any)
          <input list="fulfillment-products" value={form.products} onChange={(e) => setForm({ ...form, products: e.target.value })} className={INPUT} />
          <datalist id="fulfillment-products">{catalog.products.map((p) => <option key={p} value={p} />)}</datalist>
        </label>
        <label>Segment
          <select value={form.segment} onChange={(e) => setForm({ ...form, segment: e.target.value })} className={INPUT}>
            {["", "B2C", "B2B", "B2B-SMB", "B2B-Enterprise"].map((s) => <option key={s} value={s}>{s || "any"}</option>)}
          </select>
        </label>
        <label>Order type
          <select value={form.order_type} onChange={(e) => setForm({ ...form, order_type: e.target.value })} className={INPUT}>
            {["new", "change", "cancel", "suspend", ""].map((s) => <option key={s} value={s}>{s || "any"}</option>)}
          </select>
        </label>
        <label className="md:col-span-4">Description<input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className={INPUT} /></label>
        <TriggerPicker value={form.trigger_intents} catalog={catalog} onChange={(trigger_intents) => setForm({ ...form, trigger_intents })} />
      </div>

      <DescribeBox catalog={catalog} replacing sourcePattern={form.graph.nodes.length > 1 ? form : undefined} onDraft={(d) => {
        setForm({ ...d.pattern, name: form.name.trim() && form.name !== "New fulfillment pattern" ? form.name : d.pattern.name });
        setSelected(null);
        setPreview(null);
      }} />

      <div className="grid gap-2 md:grid-cols-3">
        {LAYERS.map((layer) => (
          <div key={layer} className="rounded-xl border border-slate-800 bg-slate-900/40 p-3">
            <p className="text-[11px] font-bold" style={{ color: LAYER_COLOR[layer] }}>+ Add to {layer}</p>
            <p className="mb-2 text-[10px] text-slate-500">{catalog.layers.find((l) => l.layer === layer)?.etom}</p>
            <div className="flex flex-wrap gap-1">
              {catalog.functions.filter((f) => f.layer === layer || f.layer === "*").flatMap((f) => f.presets.map((p) => (
                <button key={`${f.function}-${p.label}`} title={`${f.description} (eTOM ${f.etom})`} onClick={() => addNode(layer, f, p)}
                  className="rounded border px-2 py-0.5 text-[11px] hover:bg-slate-800" style={{ borderColor: LAYER_COLOR[layer], color: LAYER_COLOR[layer] }}>
                  {p.label}
                </button>
              )))}
            </div>
          </div>
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
        <SwimlaneCanvas graph={form.graph} selected={selected} onSelect={setSelected}
          onMove={(id, x, y) => setForm({ ...form, graph: { ...form.graph, nodes: form.graph.nodes.map((n) => (n.id === id ? { ...n, x, y } : n)) } })} />
        {selectedNode ? (
          <NodeInspector key={selectedNode.id} node={selectedNode} graph={form.graph} catalog={catalog} onChange={(graph) => setForm({ ...form, graph })}
            onDelete={() => { setForm({ ...form, graph: { nodes: form.graph.nodes.filter((n) => n.id !== selectedNode.id), edges: form.graph.edges.filter((e) => e.source !== selectedNode.id && e.target !== selectedNode.id) } }); setSelected(null); }} />
        ) : (
          <p className="text-xs text-slate-500">
            Each lane is one order layer. Add steps from the palette, drag them within their lane and pick a step to set what it does, what it waits for,
            and when it applies. A lane hands over to the next through its Route step; the child order completing completes that Route step.
          </p>
        )}
      </div>

      {errors.length > 0 && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {errors.map((e) => <p key={e}>• {e}</p>)}
        </div>
      )}
      {error && <p className="text-xs text-red-400">{error}</p>}
      {notice && <p className="text-xs text-emerald-400">{notice}</p>}

      <div className="flex flex-wrap items-end gap-2">
        <button disabled={busy} onClick={() => run(() => updateFulfillmentPattern(editing.id, form), (p) => { setEditing(p); setForm(toInput(p)); setNotice(`Saved v${p.version}`); load(); })}
          className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500 disabled:opacity-50">Save</button>
        <button disabled={busy} onClick={() => run(() => setFulfillmentPatternActive(editing.id, !editing.is_active), (p) => { setEditing(p); setNotice(p.is_active ? "Active - matching orders are orchestrated with it from Order Control approval on" : "Deactivated"); load(); })}
          className={BTN}>{editing.is_active ? "Deactivate" : "Activate"}</button>
        <label className="text-xs text-slate-400">Preview for CRM order #
          <input value={previewOrder} onChange={(e) => setPreviewOrder(e.target.value.replace(/\D/g, ""))} placeholder="optional" className={`${INPUT} w-28`} />
        </label>
        <button disabled={busy} onClick={() => run(() => previewFulfillmentPattern(editing.id, previewOrder ? Number(previewOrder) : null), setPreview)} className={BTN}>Preview the plan</button>
        {!editing.is_active && (
          <button disabled={busy} onClick={() => run(() => deleteFulfillmentPattern(editing.id), () => { setEditing(null); setForm(null); load(); })}
            className={`${BTN} ml-auto hover:border-red-500 hover:text-red-400`}>Delete</button>
        )}
      </div>

      {preview && (
        <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-4 text-xs">
          <p className="font-bold text-slate-200">Orchestration plan{preview.order && Object.keys(preview.order).length ? ` for order #${String(preview.order.id)}` : ""} - saved version</p>
          {preview.note && <p className="mt-1 text-violet-300">{preview.note}</p>}
          <div className="mt-2 grid gap-3 md:grid-cols-3">
            {preview.layers.map((l) => (
              <div key={l.layer}>
                <p className="font-bold" style={{ color: LAYER_COLOR[l.layer] }}>{l.layer}</p>
                <ol className="mt-1 space-y-1">
                  {l.steps.map((s, i) => (
                    <li key={s.id} className={s.runs ? "text-slate-300" : "text-slate-600 line-through"} title={`eTOM ${s.etom}`}>
                      {i + 1}. {s.label}{s.depends_on.length ? <span className="text-slate-500"> ← {s.depends_on.join(", ")}</span> : null}
                      {!s.runs && <span className="ml-1 no-underline">(skipped for this order)</span>}
                    </li>
                  ))}
                </ol>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- monitor

function layout(view: FulfillmentOrderView): { graph: FulfillmentGraph; statuses: Record<string, string> } {
  const nodes: FulfillmentNode[] = [];
  const edges: FulfillmentGraph["edges"] = [];
  const statuses: Record<string, string> = {};
  const nodeLayers = new Map(flatten(view).flatMap((v) => v.tasks.map((t) => [t.node_id, v.layer] as const)));
  const walk = (v: FulfillmentOrderView) => {
    v.tasks.forEach((t, i) => {
      const id = `${v.layer}:${t.node_id}`;
      nodes.push({ id, layer: v.layer, function: t.function, label: t.label, x: 30 + i * 200, y: laneTop(v.layer) + 50, config: t.config });
      statuses[id] = t.status;
      t.depends_on.forEach((d) => edges.push({ id: `${id}<${d}`, source: `${nodeLayers.get(d) ?? v.layer}:${d}`, target: id }));
    });
    (v.children ?? []).forEach(walk);
  };
  walk(view);
  return { graph: { nodes, edges }, statuses };
}

function flatten(view: FulfillmentOrderView): FulfillmentOrderView[] {
  return [view, ...(view.children ?? []).flatMap(flatten)];
}

function TaskRow({ task, onAction }: { task: FulfillmentTask; onAction: (action: "retry" | "skip" | "complete" | "inventory", task: FulfillmentTask) => void }) {
  const routes = task.function.startsWith("route_"); // completes with its child order - resolve that order instead
  return (
    <div className="rounded-md bg-slate-950 px-3 py-2 text-xs" style={{ borderLeft: `3px solid ${STATUS_COLOR[task.status]}` }}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-slate-200">{task.sequence}. {task.label}</span>
        <span className="font-bold" style={{ color: STATUS_COLOR[task.status] }}>{task.status}</span>
        {task.work_order_id && <span className="text-slate-500">work order #{task.work_order_id}</span>}
        {task.external_ref && <span className="font-mono text-slate-500">{task.external_ref}</span>}
        {task.attempts > 1 && <span className="text-slate-500">{task.attempts} attempts</span>}
        <span className="ml-auto flex gap-1">
          {task.status === "failed" && <button onClick={() => onAction("retry", task)} className={BTN}>Retry</button>}
          {(task.status === "failed" || task.status === "waiting") && !routes && <button onClick={() => onAction("skip", task)} className={BTN}>Skip</button>}
          {(task.status === "failed" || task.status === "waiting") && !routes && <button onClick={() => onAction("complete", task)} className={BTN}>Done by hand</button>}
          {task.status === "waiting" && task.external_ref.startsWith("INVREQ-") && <button onClick={() => onAction("inventory", task)} className={BTN}>Post inventory answer</button>}
        </span>
      </div>
      {task.note && <p className="mt-1 text-slate-400">{task.note}</p>}
      {task.error && <p className="mt-1 text-red-300"><span className="font-mono text-[11px]">{task.error.code}</span> {task.error.message}</p>}
      {(Object.keys(task.request).length > 0 || Object.keys(task.response).length > 0) && (
        <details className="mt-1 text-slate-500">
          <summary>Request / response{task.etom ? ` · eTOM ${task.etom}` : ""}</summary>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all pt-1 font-mono text-[11px]">{JSON.stringify({ request: task.request, response: task.response }, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}

function OrderMonitor() {
  const [filter, setFilter] = useState("");
  const [orders, setOrders] = useState<FulfillmentOrderSummary[]>([]);
  const [view, setView] = useState<FulfillmentOrderView | null>(null);
  const [submitId, setSubmitId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => listFulfillmentOrders(filter || undefined).then(setOrders).catch((e) => setError(errorText(e, "Could not load orders")));
  useEffect(() => {
    load();
    // the filter decides what loads
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  async function run(fn: () => Promise<FulfillmentOrderView>) {
    setBusy(true);
    setError(null);
    try {
      setView(await fn());
      load();
    } catch (e) {
      setError(errorText(e, "That didn't work"));
    } finally {
      setBusy(false);
    }
  }

  function act(action: "retry" | "skip" | "complete" | "inventory", task: FulfillmentTask) {
    if (action === "inventory") {
      const raw = window.prompt(`Inventory answer for ${task.external_ref} - resources as type=identifier, comma-separated (blank = refused)`, "port=OLT-01/1/1/1, ont=HWTC00000001");
      if (raw === null) return;
      const resources = raw.split(",").map((p) => p.split("=").map((s) => s.trim())).filter((p) => p.length === 2 && p[0] && p[1]).map(([type, identifier]) => ({ type, identifier }));
      run(() => postInventoryResponse(resources.length ? { request_id: task.external_ref, status: "completed", resources } : { request_id: task.external_ref, status: "failed", message: "Refused by inventory" }));
      return;
    }
    const note = window.prompt(action === "retry" ? "What was fixed? (optional)" : action === "skip" ? "Why skip this step?" : "What was done instead?", "") ?? null;
    if (note === null) return;
    run(() => resolveFulfillmentTask(task.id, action, note));
  }

  const drawn = useMemo(() => (view ? layout(view) : null), [view]);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs text-slate-400">Show
          <select value={filter} onChange={(e) => setFilter(e.target.value)} className={INPUT}>
            <option value="">all</option>
            <option value="fallout">fallout</option>
            <option value="waiting">waiting</option>
            <option value="in_progress">in progress</option>
            <option value="completed">completed</option>
          </select>
        </label>
        <button onClick={load} className={BTN}>Refresh</button>
        <label className="ml-auto text-xs text-slate-400">Send CRM order #
          <input value={submitId} onChange={(e) => setSubmitId(e.target.value.replace(/\D/g, ""))} className={`${INPUT} w-28`} />
        </label>
        <button disabled={busy || !submitId} onClick={() => run(() => submitOrderToFulfillment(Number(submitId)))} className={BTN}>to Fulfillment</button>
      </div>
      {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</p>}
      <div className="overflow-auto rounded-xl border border-slate-700">
        <table className="w-full text-left text-xs">
          <thead className="bg-slate-900 text-slate-400">
            <tr><th className="p-2">Customer order</th><th className="p-2">CRM order</th><th className="p-2">Pattern</th><th className="p-2">Stage</th><th className="p-2">Progress</th><th className="p-2">Attention</th></tr>
          </thead>
          <tbody>
            {orders.map((o) => (
              <tr key={o.id} onClick={() => run(() => getFulfillmentOrder(o.id))} className="cursor-pointer border-t border-slate-800 hover:bg-slate-900/60">
                <td className="p-2 font-mono text-sky-300">{o.number}</td>
                <td className="p-2 text-slate-300">#{o.crm_order_id}</td>
                <td className="p-2 text-slate-300">{o.pattern}{o.planned_by !== "deterministic" ? <span className="ml-1 text-violet-300">({o.planned_by.replace("_", " ")})</span> : null}</td>
                <td className="p-2"><span style={{ color: LAYER_COLOR[o.stage] }}>{o.stage}</span> · <span style={{ color: STATUS_COLOR[o.stage_status === "fallout" ? "failed" : o.stage_status] ?? "#94a3b8" }}>{o.stage_status.replace("_", " ")}</span></td>
                <td className="p-2 text-slate-400">{o.tasks_done}/{o.tasks_total}</td>
                <td className="p-2">{o.fallout.length ? <span className="text-red-300">fallout: {o.fallout.join(", ")}</span> : o.waiting.length ? <span className="text-amber-300">waiting: {o.waiting.join(", ")}</span> : <span className="text-slate-600">—</span>}</td>
              </tr>
            ))}
            {orders.length === 0 && <tr><td colSpan={6} className="p-3 text-slate-500">No orders in orchestration{filter ? ` (${filter})` : ""}.</td></tr>}
          </tbody>
        </table>
      </div>

      {view && drawn && (
        <div className="space-y-3">
          <p className="text-sm font-bold text-slate-100">
            {view.number} · CRM order #{view.crm_order_id} · {view.pattern} v{view.pattern_version}
            <span className="ml-2 text-xs font-normal text-slate-400">{view.status.replace("_", " ")}</span>
          </p>
          <SwimlaneCanvas graph={drawn.graph} statuses={drawn.statuses} />
          <div className="grid gap-3 lg:grid-cols-3">
            {flatten(view).map((v) => (
              <div key={v.id} className="space-y-1.5">
                <p className="text-xs font-bold" style={{ color: LAYER_COLOR[v.layer] }}>
                  {v.number} · {v.status.replace("_", " ")}
                  {v.planned_by !== "deterministic" && <span className="ml-1 font-normal text-violet-300">{v.planned_by.replace("_", " ")}</span>}
                </p>
                {v.plan_note && <p className="text-[11px] text-violet-300/80">{v.plan_note}</p>}
                {v.tasks.map((t) => <TaskRow key={t.id} task={t} onAction={act} />)}
              </div>
            ))}
          </div>
          <div className="grid gap-3 lg:grid-cols-3 text-xs">
            <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-3">
              <p className="mb-1 font-bold text-sky-300">Billing (COM)</p>
              {Object.entries(view.billing).map(([k, v]) => <p key={k} className="text-slate-300"><span className="text-slate-500">{k.replace(/_/g, " ")}:</span> {String(v)}</p>)}
              {Object.keys(view.billing).length === 0 && <p className="text-slate-500">Not registered yet.</p>}
            </div>
            <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-3">
              <p className="mb-1 font-bold text-violet-300">Inventory responses (SOM)</p>
              {view.resources.map((r) => (
                <p key={`${r.type}-${r.identifier}`} className="text-slate-300">
                  <span className={r.kind === "physical" ? "text-amber-300" : "text-sky-300"}>{r.kind}</span> {r.type.replace(/_/g, " ")}: <span className="font-mono">{r.identifier}</span> <span className="text-slate-500">{r.status}</span>
                </p>
              ))}
              {view.resources.length === 0 && <p className="text-slate-500">No resources yet.</p>}
            </div>
            <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-3">
              <p className="mb-1 font-bold text-amber-300">Field results (TOM)</p>
              {Object.entries(view.work).map(([kind, w]) => (
                <p key={kind} className="text-slate-300">{kind.replace(/_/g, " ")}: WO #{String(w.work_order_id ?? "")} · {String(w.technician ?? "")}{w.signature ? ` · signed “${String(w.signature)}”` : ""}</p>
              ))}
              {Object.keys(view.work).length === 0 && <p className="text-slate-500">No work orders closed yet.</p>}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- section

export default function AdminFulfillmentSection() {
  const [tab, setTab] = useState<"design" | "monitor">("design");
  const [catalog, setCatalog] = useState<FulfillmentCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fulfillmentCatalog().then(setCatalog).catch((e) => setError(errorText(e, "Could not load the orchestration catalog")));
  }, []);
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-400">
        Fulfillment orchestrates orders in layers, on eTOM: an approved CRM order is received by <b className="text-sky-300">COM</b> (billing
        registration: customer, billing account, billing profile), decomposed to <b className="text-violet-300">SOM</b> (technical actions to the
        inventory system - logical and physical resources) and on to <b className="text-amber-300">TOM</b> (Field Service work orders - installation,
        connectivity test, acceptance). As the engineers close their jobs the order moves back up: billing starts, installed assets are created, the
        order completes. Orders no active pattern fits keep the classic fulfillment chain.
      </p>
      <div className="flex gap-1">
        {(["design", "monitor"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`rounded-lg px-4 py-1.5 text-sm font-bold ${tab === t ? "bg-sky-600 text-white" : "border border-slate-700 text-slate-300"}`}>
            {t === "design" ? "Fulfillment patterns" : "Order monitor & fallout"}
          </button>
        ))}
      </div>
      {error && <p className="text-xs text-red-400">{error}</p>}
      {tab === "design" && catalog && <PatternDesigner catalog={catalog} />}
      {tab === "monitor" && <OrderMonitor />}
    </div>
  );
}
