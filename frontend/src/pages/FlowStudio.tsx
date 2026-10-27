import { useEffect, useRef, useState } from "react";
import {
  activateFlow,
  createFlow,
  deactivateFlow,
  deleteFlow,
  flowCatalog,
  flowFieldCatalog,
  generateFlow,
  listFlows,
  reviewFlow,
  simulateFlow,
  updateFlow,
} from "../api/client";
import type { FlowTriggerInfo } from "../api/client";
import { ENTITY_LABELS } from "../api/types";
import type {
  Flow,
  FlowCatalog,
  FlowCorrection,
  FlowFieldCatalog,
  FlowGraph,
  FlowInput,
  FlowIssue,
  FlowNode,
  FlowReview,
  FlowSimulateResult,
} from "../api/types";
import NodeConfigFields from "../components/FlowConfigFields";
import FlowList from "../components/FlowList";
import { editableFlowGraph } from "../flowGraph";

const INPUT = "mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200";
const SMALL_BTN = "rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300";
const NODE_W = 180;
const NODE_H = 52;

const NODE_COLORS: Record<string, string> = {
  start: "#10b981",
  end: "#64748b",
  condition: "#f59e0b",
  ai_decision: "#a855f7",
  set_field: "#0ea5e9",
  transition: "#6366f1",
  assign: "#ec4899",
  notify: "#14b8a6",
  reject: "#ef4444",
  action: "#22c55e",
  record: "#38bdf8",
  map: "#c084fc",
  catch: "#f97316",
  throw: "#dc2626",
};

// how a step ended on the last simulation - painted on the canvas
const STATUS_STROKE: Record<string, string> = { failed: "#ef4444", caught: "#f59e0b", skipped: "#94a3b8", rejected: "#ef4444" };
const OUTPUT_LABEL = (o: string) => (o === "" ? "next" : o === "error" ? "error (if it fails)" : o);

function blankFlow(): FlowInput {
  return {
    name: "",
    description: "",
    entity_type: null,
    trigger_type: "intent",
    trigger_intent_type: null,
    timing: "after",
    trigger_state: null,
    trigger_phrases: null,
    required_fields: null,
    on_error: "fail",
    graph: {
      nodes: [
        { id: "n1", type: "start", label: "Start", x: 80, y: 40, config: {} },
        { id: "n2", type: "end", label: "End", x: 80, y: 280, config: {} },
      ],
      edges: [{ id: "e1", source: "n1", target: "n2", label: "" }],
    },
  };
}

function toInput(f: Flow): FlowInput {
  const { id: _id, version: _v, is_active: _a, intent_code: _c, validation_errors: _e, updated_at: _u, ...input } = f;
  return { ...input, graph: editableFlowGraph(input.graph) };
}

function nextId(prefix: string, taken: string[]) {
  let i = taken.length + 1;
  while (taken.includes(`${prefix}${i}`)) i++;
  return `${prefix}${i}`;
}

function errorText(e: unknown, fallback: string) {
  return e instanceof Error ? e.message : fallback;
}

// ---------- canvas ----------

function FlowCanvas({
  graph,
  selected,
  visited,
  flagged,
  statuses,
  onSelect,
  onMove,
}: {
  graph: FlowGraph;
  selected: string | null;
  visited: Set<string>;
  flagged: Set<string>;
  /** node id -> how it ended on the last simulation (failed / caught / skipped / rejected) */
  statuses: Map<string, string>;
  onSelect: (id: string | null) => void;
  onMove: (id: string, x: number, y: number) => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const drag = useRef<{ id: string; dx: number; dy: number } | null>(null);

  const width = Math.max(640, ...graph.nodes.map((n) => n.x + NODE_W + 40));
  const height = Math.max(360, ...graph.nodes.map((n) => n.y + NODE_H + 40));
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));

  function point(e: React.PointerEvent) {
    const rect = svgRef.current!.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  return (
    <div className="overflow-auto rounded-xl border border-slate-700 bg-slate-950">
      <svg
        ref={svgRef}
        width={width}
        height={height}
        onPointerMove={(e) => {
          if (!drag.current) return;
          const p = point(e);
          onMove(drag.current.id, Math.max(0, Math.round(p.x - drag.current.dx)), Math.max(0, Math.round(p.y - drag.current.dy)));
        }}
        onPointerUp={() => (drag.current = null)}
        onPointerLeave={() => (drag.current = null)}
        onPointerDown={(e) => e.target === svgRef.current && onSelect(null)}
        className="select-none"
      >
        <defs>
          <marker id="flow-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0,0 L10,5 L0,10 z" fill="#64748b" />
          </marker>
        </defs>
        {graph.edges.map((e) => {
          const s = byId.get(e.source);
          const t = byId.get(e.target);
          if (!s || !t) return null;
          const x1 = s.x + NODE_W / 2;
          const y1 = s.y + NODE_H;
          const x2 = t.x + NODE_W / 2;
          const y2 = t.y;
          const mid = (y1 + y2) / 2;
          const hot = visited.has(e.source) && visited.has(e.target);
          const isError = e.label === "error";
          return (
            <g key={e.id}>
              <path
                d={`M${x1},${y1} C${x1},${mid} ${x2},${mid} ${x2},${y2}`}
                fill="none"
                stroke={hot ? (isError ? "#f59e0b" : "#38bdf8") : isError ? "#b91c1c" : "#475569"}
                strokeWidth={hot ? 2.5 : 1.5}
                strokeDasharray={isError ? "6 4" : undefined}
                markerEnd="url(#flow-arrow)"
              />
              {e.label && (
                <text x={(x1 + x2) / 2} y={mid - 4} textAnchor="middle" fontSize="11" fill={e.label === "true" ? "#34d399" : isError ? "#fb923c" : "#f87171"}>
                  {isError ? "⚠ error" : e.label}
                </text>
              )}
            </g>
          );
        })}
        {/* failures jump to a Catch errors step - drawn as a hint from the flow's start */}
        {graph.nodes.filter((n) => n.type === "catch").map((n) => (
          <text key={`hint-${n.id}`} x={n.x + NODE_W / 2} y={Math.max(10, n.y - 6)} textAnchor="middle" fontSize="10" fill="#fb923c">
            ⚡ any failed step jumps here
          </text>
        ))}
        {graph.nodes.map((n) => {
          const color = NODE_COLORS[n.type] ?? "#64748b";
          const isSelected = n.id === selected;
          const ended = statuses.get(n.id);
          return (
            <g
              key={n.id}
              transform={`translate(${n.x},${n.y})`}
              onPointerDown={(e) => {
                e.stopPropagation();
                const p = point(e);
                drag.current = { id: n.id, dx: p.x - n.x, dy: p.y - n.y };
                onSelect(n.id);
              }}
              style={{ cursor: "grab" }}
            >
              <rect
                width={NODE_W}
                height={NODE_H}
                rx={10}
                fill="#0f172a"
                stroke={isSelected ? "#38bdf8" : ended && STATUS_STROKE[ended] ? STATUS_STROKE[ended] : flagged.has(n.id) ? "#f59e0b" : visited.has(n.id) ? "#38bdf8" : color}
                strokeWidth={isSelected ? 2.5 : ended || flagged.has(n.id) || visited.has(n.id) ? 2 : 1.5}
                strokeDasharray={flagged.has(n.id) && !isSelected && !ended ? "5 3" : undefined}
              />
              {flagged.has(n.id) && !ended && (
                <text x={NODE_W - 16} y={18} fontSize="13" fill="#f59e0b" fontWeight="bold">
                  !
                </text>
              )}
              {ended && STATUS_STROKE[ended] && (
                <text x={NODE_W - 8} y={18} fontSize="10" fill={STATUS_STROKE[ended]} fontWeight="bold" textAnchor="end">
                  {ended === "caught" ? "caught" : ended === "failed" ? "✕ failed" : ended}
                </text>
              )}
              <rect width={6} height={NODE_H} rx={3} fill={color} />
              <text x={16} y={20} fontSize="10" fill={color} fontWeight="bold">
                {n.type.toUpperCase()}
              </text>
              <text x={16} y={38} fontSize="12" fill="#e2e8f0">
                {n.label.length > 24 ? `${n.label.slice(0, 23)}…` : n.label}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// ---------- node inspector ----------

function NodeInspector({
  node,
  graph,
  catalog,
  fieldCatalog,
  issues,
  onChange,
  onDelete,
  simulation,
}: {
  node: FlowNode;
  graph: FlowGraph;
  catalog: FlowCatalog;
  fieldCatalog: FlowFieldCatalog | null;
  issues: FlowIssue[];
  simulation?: FlowSimulateResult | null;
  onChange: (graph: FlowGraph) => void;
  onDelete: () => void;
}) {
  const spec = catalog.node_types.find((t) => t.type === node.type);
  const outputs = spec?.outputs ?? [];
  const [connect, setConnect] = useState({ output: outputs[0] ?? "", target: "" });
  const outgoing = graph.edges.filter((e) => e.source === node.id);
  const ancestors = new Set<string>();
  const collect = (id: string) => graph.edges.filter((e) => e.target === id).forEach((e) => {
    if (e.source !== node.id && !ancestors.has(e.source)) { ancestors.add(e.source); collect(e.source); }
  });
  collect(node.id);
  const mappedCatalog = fieldCatalog ? { ...fieldCatalog, fields: [...fieldCatalog.fields] } : null;
  for (const prev of graph.nodes.filter((n) => ancestors.has(n.id))) {
    const fields = catalog.record_entities?.find((e) => e.name === prev.config.entity)?.fields ?? [];
    const outputKeys = prev.type === "record" ? ["id", "brt_id", "created", "deleted", "record", ...fields.map((f) => `record.${f.name}`)]
      : prev.type === "map" ? Object.keys(prev.config.values ?? {}) : ["order_id", "party_id", "quote_id", "outcome"];
    const inputs = prev.type === "action" ? Object.keys(prev.config.entities ?? {}) : Object.keys(prev.config);
    for (const [scope, keys] of [["input", inputs], ["output", outputKeys]] as const) {
      for (const key of ["", ...keys]) {
        const path = `steps.${prev.id}.${scope}${key ? `.${key}` : ""}`;
        mappedCatalog?.fields.push({ path, key: path, label: `${prev.label || prev.id} · ${scope}${key ? ` · ${key}` : " (all)"}`, data_type: "text", source: "result", values: [], settable: false, recent: [] });
      }
    }
  }
  const addObserved = (path: string, value: unknown, depth = 0) => {
    if (!mappedCatalog || depth > 5) return;
    if (!mappedCatalog.fields.some((f) => f.path === path)) mappedCatalog.fields.push({ path, key: path,
      label: `Observed: ${path.replace(/^trigger.output/, "Trigger outputs").replace(/^trigger.input/, "Trigger inputs")}`,
      data_type: typeof value === "number" ? "number" : typeof value === "boolean" ? "boolean" : "text", source: "result", values: [], settable: false, recent: [] });
    if (Array.isArray(value)) value.slice(0, 1).forEach((v, i) => addObserved(`${path}.${i}`, v, depth + 1));
    else if (value && typeof value === "object") Object.entries(value).slice(0, 100).forEach(([k, v]) => addObserved(`${path}.${k}`, v, depth + 1));
  };
  for (const entry of simulation?.run?.trace ?? []) {
    if (ancestors.has(entry.node_id)) {
      addObserved(`steps.${entry.node_id}.input`, entry.input);
      addObserved(`steps.${entry.node_id}.output`, entry.output);
    }
  }
  if (simulation?.run?.trigger) {
    addObserved("trigger.input", simulation.run.trigger.input);
    addObserved("trigger.output", simulation.run.trigger.output);
  }

  function patchNode(patch: Partial<FlowNode>) {
    onChange({ ...graph, nodes: graph.nodes.map((n) => (n.id === node.id ? { ...n, ...patch } : n)) });
  }

  function addEdge() {
    if (!connect.target) return;
    const edges = graph.edges.filter((e) => !(e.source === node.id && e.label === connect.output));
    edges.push({ id: nextId("e", graph.edges.map((e) => e.id)), source: node.id, target: connect.target, label: connect.output });
    onChange({ ...graph, edges });
  }

  return (
    <div className="space-y-3 rounded-xl border border-slate-700 bg-slate-900/60 p-4 text-xs">
      <div className="flex items-center justify-between">
        <span className="font-bold uppercase tracking-wide" style={{ color: NODE_COLORS[node.type] }}>
          {spec?.label ?? node.type} · {node.id}
        </span>
        {node.type !== "start" && (
          <button onClick={onDelete} className={`${SMALL_BTN} hover:border-red-500 hover:text-red-400`}>
            Delete node
          </button>
        )}
      </div>
      <label className="block text-slate-400">
        Label
        <input value={node.label} onChange={(e) => patchNode({ label: e.target.value })} className={INPUT} />
      </label>
      {node.type !== "start" && node.type !== "catch" && !graph.edges.some((e) => e.target === node.id) && <p role="alert" className="rounded border border-amber-600 p-2 text-amber-300">This step is disconnected and will not run. Link a previous step to it using Outputs.</p>}
      {node.type === "catch" && (
        <p className="rounded border border-orange-500/40 bg-orange-500/5 p-2 text-orange-200">
          Error handler for the whole flow. When a step fails and has no “error” output of its own, the flow jumps here
          (its changes undone) - nothing links into this step. Link what should happen next: notify someone, set a field,
          or end the flow quietly. Use {"{{error.message}}"} and {"{{error.code}}"} in later steps. Leave it unlinked to
          swallow the error.
        </p>
      )}
      {node.type === "throw" && (
        <p className="rounded border border-red-500/40 bg-red-500/5 p-2 text-red-200">
          Raises an error of your own. A Catch errors step can handle it by its code; if none does, the flow fails with this
          message. Unlike Reject, which always turns the request down, an error can be caught.
        </p>
      )}
      {outputs.includes("error") && (
        <p className="text-[11px] text-slate-500">
          Optional: link the <b>error</b> output to handle a failure of this step right here - its changes are undone and the flow goes on down that path.
        </p>
      )}
      {!!fieldCatalog?.handoff?.length && <details className="rounded border border-sky-900 p-2 text-slate-300">
        <summary>Available flow input parameters ({fieldCatalog.handoff.length})</summary>
        <p className="my-2 text-slate-400">Passed automatically from the triggering intent. Select these under “Flow inputs” in the expression builder.</p>
        <div className="max-h-48 overflow-auto">{fieldCatalog.handoff.map((f) => <div key={f.path} className="border-t border-slate-800 py-1">{f.label} <span className="text-slate-500">· {f.data_type}</span></div>)}</div>
      </details>}
      <NodeConfigFields
        node={node}
        configKeys={spec?.config ?? []}
        catalog={mappedCatalog}
        recordEntities={catalog.record_entities}
        errorCodes={catalog.error_codes ?? []}
        hasCatch={graph.nodes.some((n) => n.type === "catch")}
        issues={issues}
        onConfig={(config) => patchNode({ config })}
      />
      <p className="text-[11px] text-slate-400">Choose Trigger outputs to use the result of the intent that called this flow. Choose Previous steps to use a connected step's result. Trigger outputs are available after the intent finishes.</p>

      {outputs.length > 0 && (
        <div className="space-y-2 border-t border-slate-800 pt-3">
          <p className="font-bold text-slate-400">Outputs</p>
          {outgoing.map((e) => (
            <div key={e.id} className="flex items-center gap-2 text-slate-300">
              <span className={`font-mono ${e.label === "error" ? "text-orange-400" : ""}`}>{OUTPUT_LABEL(e.label || "")}</span>→<span>{graph.nodes.find((n) => n.id === e.target)?.label ?? e.target}</span>
              <button
                onClick={() => onChange({ ...graph, edges: graph.edges.filter((x) => x.id !== e.id) })}
                className="ml-auto text-slate-500 hover:text-red-400"
              >
                ✕
              </button>
            </div>
          ))}
          <div className="flex gap-2">
            {outputs.length > 1 && (
              <select value={connect.output} onChange={(e) => setConnect((c) => ({ ...c, output: e.target.value }))} className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200">
                {outputs.map((o) => (
                  <option key={o} value={o}>
                    {OUTPUT_LABEL(o)}
                  </option>
                ))}
              </select>
            )}
            <select value={connect.target} onChange={(e) => setConnect((c) => ({ ...c, target: e.target.value }))} className="flex-1 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200">
              <option value="">connect to…</option>
              {graph.nodes
                .filter((n) => n.id !== node.id && n.type !== "start" && n.type !== "catch")
                .map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.label} ({n.id})
                  </option>
                ))}
            </select>
            <button onClick={addEdge} className={`${SMALL_BTN} hover:border-sky-500 hover:text-sky-400`}>
              Link
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------- studio ----------

export default function FlowStudioSection() {
  const [catalog, setCatalog] = useState<FlowCatalog | null>(null);
  const [flows, setFlows] = useState<Flow[]>([]);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<FlowInput | null>(null);
  const [validation, setValidation] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [aiNotes, setAiNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [changingId, setChangingId] = useState<number | null>(null);
  const changingFlow = useRef(false);
  const [stateNotice, setStateNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sim, setSim] = useState({ entityId: "", entities: "{}", result: "{}" });
  const [simResult, setSimResult] = useState<FlowSimulateResult | null>(null);
  // Field intelligence: what this flow can reference, and a live review of
  // what it does reference (auto-fixes + suggestions + warnings).
  const [fieldCatalog, setFieldCatalog] = useState<FlowFieldCatalog | null>(null);
  const [review, setReview] = useState<{ for: string; result: FlowReview } | null>(null);
  const [applied, setApplied] = useState<FlowCorrection[]>([]);

  const trigger: FlowTriggerInfo | null = form
    ? {
        entity_type: form.entity_type,
        trigger_type: form.trigger_type,
        trigger_intent_type: form.trigger_type === "intent" ? form.trigger_intent_type : null,
        required_fields: form.trigger_type === "custom_intent" ? form.required_fields : null,
      }
    : null;
  const triggerKey = JSON.stringify(trigger);
  const graphKey = form ? JSON.stringify(form.graph.nodes.map((n) => [n.id, n.type, n.config])) : "";

  useEffect(() => {
    if (!trigger) return;
    const handle = setTimeout(() => flowFieldCatalog(trigger).then(setFieldCatalog).catch(() => setFieldCatalog(null)), 250);
    return () => clearTimeout(handle);
    // trigger is derived from triggerKey
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [triggerKey]);

  useEffect(() => {
    if (!trigger || !form) return;
    const key = triggerKey + graphKey;
    const handle = setTimeout(() => {
      reviewFlow(trigger, form.graph)
        .then((result) => setReview({ for: key, result }))
        .catch(() => setReview(null));
    }, 600);
    return () => clearTimeout(handle);
    // the keys capture everything the review depends on
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [triggerKey, graphKey, fieldCatalog]);

  const liveReview = review && review.for === triggerKey + graphKey ? review.result : null;

  function applyFixes() {
    if (!liveReview || !form) return;
    // keep canvas positions; take the corrected settings
    const fixed = new Map(liveReview.graph.nodes.map((n) => [n.id, n.config]));
    setGraph({ ...form.graph, nodes: form.graph.nodes.map((n) => ({ ...n, config: fixed.get(n.id) ?? n.config })) });
    setApplied(liveReview.corrections);
  }

  function applySuggestion(issue: FlowIssue) {
    if (!form || !issue.suggestion) return;
    setGraph({
      ...form.graph,
      nodes: form.graph.nodes.map((n) => (n.id === issue.node_id ? { ...n, config: { ...n.config, [issue.key]: issue.suggestion! } } : n)),
    });
  }

  function load() {
    listFlows().then(setFlows);
  }

  useEffect(() => {
    flowCatalog().then(setCatalog);
    load();
  }, []);

  function open(flow: Flow | null) {
    setEditingId(flow?.id ?? null);
    setForm(flow ? toInput(flow) : blankFlow());
    setValidation(flow?.validation_errors ?? []);
    setSelected(null);
    setAiNotes("");
    setSimResult(null);
    setError(null);
    setApplied([]);
    setReview(null);
  }

  function patch(p: Partial<FlowInput>) {
    setForm((f) => f && { ...f, ...p });
  }

  function setGraph(graph: FlowGraph) {
    patch({ graph: editableFlowGraph(graph) });
    setSimResult(null);
  }

  function addNode(type: string) {
    if (!form || !catalog) return;
    const id = nextId("n", form.graph.nodes.map((n) => n.id));
    const label = catalog.node_types.find((t) => t.type === type)?.label ?? type;
    const bottom = Math.max(0, ...form.graph.nodes.map((n) => n.y));
    setGraph({ ...form.graph, nodes: [...form.graph.nodes, { id, type, label, x: 340, y: bottom + 40, config: {} }] });
    setSelected(id);
  }

  function deleteNode(id: string) {
    if (!form) return;
    setGraph({ nodes: form.graph.nodes.filter((n) => n.id !== id), edges: form.graph.edges.filter((e) => e.source !== id && e.target !== id) });
    setSelected(null);
  }

  async function save(): Promise<Flow | null> {
    if (!form) return null;
    if (!form.name.trim()) {
      setError("Flow name is required.");
      return null;
    }
    setError(null);
    setBusy(true);
    try {
      const payload = { ...form, name: form.name.trim() };
      const flow = editingId ? await updateFlow(editingId, payload) : await createFlow(payload);
      setEditingId(flow.id);
      setValidation(flow.validation_errors);
      setApplied(flow.corrections);
      // show what the server corrected, keeping the canvas layout
      if (flow.corrections.length) {
        const fixed = new Map(flow.graph.nodes.map((n) => [n.id, n.config]));
        setForm((f) => f && { ...f, graph: { ...f.graph, nodes: f.graph.nodes.map((n) => ({ ...n, config: fixed.get(n.id) ?? n.config })) } });
      }
      load();
      return flow;
    } catch (e) {
      setError(errorText(e, "Failed to save flow."));
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function toggleActive(flow: Flow) {
    if (changingFlow.current) return;
    changingFlow.current = true;
    setChangingId(flow.id);
    setError(null);
    setStateNotice(null);
    try {
      const updated = await (flow.is_active ? deactivateFlow(flow.id) : activateFlow(flow.id));
      setFlows((previous) => previous.map((entry) => entry.id === updated.id ? updated : entry));
      setStateNotice(`“${updated.name}” ${updated.is_active ? "activated" : "deactivated"}.`);
    } catch (e) {
      setError(errorText(e, "Failed to change flow state."));
    } finally {
      changingFlow.current = false;
      setChangingId(null);
    }
  }

  async function remove(id: number) {
    await deleteFlow(id);
    if (editingId === id) {
      setEditingId(null);
      setForm(null);
    }
    load();
  }

  async function generate() {
    if (!form || prompt.trim().length < 3) return;
    setBusy(true);
    setError(null);
    try {
      const result = await generateFlow(prompt.trim(), trigger!);
      setGraph(result.graph);
      setValidation(result.validation_errors);
      setApplied(result.corrections);
      // the description said when it runs ("When the order gets approved ...") - set the trigger to match
      const said = result.trigger;
      if (said) {
        patch({
          trigger_type: said.trigger_type,
          entity_type: (said.entity_type ?? form.entity_type) as FlowInput["entity_type"],
          ...(said.trigger_type === "state_enter" ? { trigger_state: said.trigger_state ?? null } : {}),
          ...(said.trigger_type === "intent" ? { trigger_intent_type: said.trigger_intent_type ?? null, timing: (said.timing ?? "after") as FlowInput["timing"] } : {}),
        });
      }
      setAiNotes(
        `${result.source === "llm" ? "Generated by the LLM" : "Generated by the built-in reader (set up a model under Admin → AI for richer drafts)"}` +
          `${result.notes ? ` - ${result.notes}` : ""}` +
          (said ? ` Trigger set to: ${said.summary}.` : "") +
          (result.corrections.length ? ` Matched ${result.corrections.length} reference(s) to your real fields and values.` : "")
      );
      if (!form.description) patch({ description: prompt.trim() });
      if (!form.name.trim()) patch({ name: prompt.trim().replace(/[.,;:].*$/, "").slice(0, 80) });
    } catch (e) {
      setError(errorText(e, "Generation failed."));
    } finally {
      setBusy(false);
    }
  }

  async function simulate() {
    let entities: Record<string, unknown>;
    let result: Record<string, unknown>;
    try {
      entities = JSON.parse(sim.entities || "{}");
      result = JSON.parse(sim.result || "{}");
      if (![entities, result].every((v) => v && typeof v === "object" && !Array.isArray(v))) throw new Error();
    } catch {
      setError("Simulation inputs and outputs must be valid JSON objects.");
      return;
    }
    const flow = await save(); // simulate exactly what is on screen
    if (!flow) return;
    setBusy(true);
    try {
      setSimResult(await simulateFlow(flow.id, sim.entityId ? Number(sim.entityId) : null, entities, result));
    } catch (e) {
      setError(errorText(e, "Simulation failed."));
    } finally {
      setBusy(false);
    }
  }

  const selectedNode = form?.graph.nodes.find((n) => n.id === selected) ?? null;
  const visited = new Set(simResult?.run?.trace.map((t) => t.node_id) ?? []);
  const statuses = new Map((simResult?.run?.trace ?? []).filter((t) => t.status && t.status !== "ok").map((t) => [t.node_id, t.status as string]));
  const issues = liveReview?.issues ?? [];
  const flagged = new Set([...issues.map((i) => i.node_id), ...(liveReview?.corrections ?? []).map((c) => c.node_id)]);
  const current = flows.find((f) => f.id === editingId);
  const intentLabels = Object.fromEntries((catalog?.intents ?? []).map((i) => [i.code, i.label]));

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        Flows are small decision graphs that run with your intents: <b>before</b> a built-in intent (and can reject it),{" "}
        <b>after</b> it (to enrich, route or notify), when a record <b>enters a state</b>, or as a brand-new{" "}
        <b>custom intent</b> users can invoke by phrase. Describe what you want and let AI draft the graph, then adjust it,
        simulate it against a real record (changes are rolled back), and activate it.
      </p>

      {stateNotice && <p role="status" className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-300">{stateNotice}</p>}

      {!form && (
        <>
          {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</p>}
          <FlowList
            flows={flows}
            intentLabels={intentLabels}
            onOpen={(f) => open(f)}
            onToggle={toggleActive}
            changingId={changingId}
            onDelete={remove}
            onNew={() => open(null)}
          />
        </>
      )}

      {form && catalog && (
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <button onClick={() => setForm(null)} className={`${SMALL_BTN} hover:border-sky-500 hover:text-sky-400`}>
              ← All flows
            </button>
            <h3 className="text-base font-bold text-slate-100">{editingId ? `Edit flow #${editingId}` : "New flow"}</h3>
            {current && (
              <span className={`rounded-md border px-2 py-0.5 text-xs ${current.is_active ? "border-emerald-500/40 text-emerald-400" : "border-slate-700 text-slate-500"}`}>
                v{current.version} · {current.is_active ? "active" : "inactive"}
              </span>
            )}
          </div>

          <div className="grid gap-3 rounded-xl border border-slate-700 bg-slate-900/60 p-5 md:grid-cols-3">
            <label className="text-xs text-slate-400">
              Name
              <input value={form.name} onChange={(e) => patch({ name: e.target.value })} className={INPUT} />
            </label>
            <label className="text-xs text-slate-400">
              Trigger
              <select value={form.trigger_type} onChange={(e) => patch({ trigger_type: e.target.value as FlowInput["trigger_type"] })} className={INPUT}>
                <option value="intent">Around a built-in intent</option>
                <option value="state_enter">When a record enters a state</option>
                <option value="custom_intent">As a new custom intent</option>
                <option value="validation">Validation - checks a page before it saves (Page Designer)</option>
              </select>
            </label>
            <label className="text-xs text-slate-400">
              Record type{form.trigger_type === "intent" ? " (optional)" : ""}
              <select value={form.entity_type ?? ""} onChange={(e) => patch({ entity_type: (e.target.value || null) as FlowInput["entity_type"] })} className={INPUT}>
                <option value="">—</option>
                {catalog.entity_types.map((t) => (
                  <option key={t} value={t}>
                    {ENTITY_LABELS[t]}
                  </option>
                ))}
              </select>
            </label>

            {form.trigger_type === "intent" && (
              <>
                <label className="text-xs text-slate-400">
                  Intent
                  <select value={form.trigger_intent_type ?? ""} onChange={(e) => patch({ trigger_intent_type: e.target.value || null })} className={INPUT}>
                    <option value="">—</option>
                    {catalog.intents.map((i) => (
                      <option key={i.code} value={i.code}>
                        {i.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="text-xs text-slate-400">
                  Timing
                  <select value={form.timing} onChange={(e) => patch({ timing: e.target.value as FlowInput["timing"] })} className={INPUT}>
                    <option value="before">before - can reject the intent</option>
                    <option value="after">after - runs on the result</option>
                    <option value="error">on error - runs when the intent fails</option>
                  </select>
                </label>
              </>
            )}
            {(form.trigger_type === "state_enter" || (form.trigger_type === "intent" && form.timing !== "error")) && (
              <label className="text-xs text-slate-400">
                If this flow fails (an error no step catches)
                <select value={form.on_error ?? "fail"} onChange={(e) => patch({ on_error: e.target.value as FlowInput["on_error"] })} className={INPUT}>
                  <option value="fail">Stop - fail the action too and undo everything (safest)</option>
                  <option value="continue">Carry on - undo only this flow, let the action complete and warn</option>
                </select>
              </label>
            )}
            {form.trigger_type === "state_enter" && (
              <label className="text-xs text-slate-400">
                State
                <input value={form.trigger_state ?? ""} onChange={(e) => patch({ trigger_state: e.target.value || null })} placeholder="e.g. won" className={INPUT} />
              </label>
            )}
            {form.trigger_type === "custom_intent" && (
              <>
                <label className="text-xs text-slate-400">
                  Trigger phrases (comma-separated)
                  <input value={form.trigger_phrases ?? ""} onChange={(e) => patch({ trigger_phrases: e.target.value || null })} placeholder="escalate this deal" className={INPUT} />
                </label>
                <label className="text-xs text-slate-400">
                  Required fields (comma-separated)
                  <input value={form.required_fields ?? ""} onChange={(e) => patch({ required_fields: e.target.value || null })} placeholder="opportunity_id" className={INPUT} />
                </label>
              </>
            )}
            <label className="text-xs text-slate-400 md:col-span-3">
              Description
              <input value={form.description} onChange={(e) => patch({ description: e.target.value })} className={INPUT} />
            </label>
          </div>

          {form.trigger_type === "intent" && <section className="rounded-xl border border-sky-800 bg-slate-900/60 p-4">
            <h3 className="font-bold text-sky-300">Triggering intent → Flow input parameters</h3>
            <p className="mt-1 text-sm text-slate-400">{catalog.intents.find((i) => i.code === form.trigger_intent_type)?.label ?? "Select an intent"} passes its input data and returned results into this flow automatically. The same parameters appear in every node's expression builder under “Flow inputs”.</p>
            {form.timing === "before" && <p className="mt-2 text-amber-300">This flow runs before the intent: only input data is available. Returned output parameters require “after” timing.</p>}
            {form.timing === "error" && (
              <p className="mt-2 text-orange-300">
                This flow runs when the intent fails - after everything it did has been undone. Use it to notify someone, open a
                ticket or log the failure. What went wrong is available as {"{{error.message}}"}, {"{{error.code}}"},
                {" {{error.category}}"} and {"{{error.node}}"}; the intent's inputs are available, its outputs are not. Its own
                failure never hides the original one.
              </p>
            )}
            <p className="my-2 text-xs text-slate-400">Customer and product details are available when the intent identifies those records. Eligible products are a list, not an automatically selected product. Values are populated during execution; this table shows the parameter definitions.</p>
            <div className="max-h-72 overflow-auto"><table className="w-full text-left text-xs"><thead><tr className="text-slate-400"><th className="p-2">Intent data available to the flow</th><th className="p-2">Type</th><th className="p-2">Flow input reference</th></tr></thead>
              <tbody>{(fieldCatalog?.handoff ?? []).map((f) => <tr key={f.path} className="border-t border-slate-800"><td className="p-2 text-slate-200">{f.label}</td><td className="p-2 text-slate-400">{f.data_type}</td><td className="break-all p-2 font-mono text-sky-300">{f.path}</td></tr>)}</tbody></table></div>
            {!fieldCatalog?.handoff?.length && <p className="text-xs text-slate-400">No parameter definitions loaded yet. Choose a triggering intent above.</p>}
          </section>}

          <div className="rounded-xl border border-violet-500/30 bg-violet-500/5 p-4">
            <p className="mb-2 text-xs font-bold text-violet-300">✨ Describe the flow - AI drafts the graph (replaces the current one)</p>
            <div className="flex gap-2">
              <input
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && generate()}
                placeholder="If total price is greater than 5000, assign to Deal Desk. Otherwise notify the owner"
                className="flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
              />
              <button onClick={generate} disabled={busy || prompt.trim().length < 3} className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-bold text-white hover:bg-violet-500 disabled:opacity-50">
                Generate
              </button>
            </div>
            {aiNotes && <p className="mt-2 text-xs text-violet-300/80">{aiNotes}</p>}
          </div>

          <div className="flex flex-wrap gap-1.5">
            {catalog.node_types
              .filter((t) => t.type !== "start")
              .map((t) => (
                <button
                  key={t.type}
                  onClick={() => addNode(t.type)}
                  className="rounded-md border px-2.5 py-1 text-xs font-bold hover:bg-slate-800"
                  style={{ borderColor: NODE_COLORS[t.type], color: NODE_COLORS[t.type] }}
                >
                  + {t.label}
                </button>
              ))}
          </div>

          <div className="grid gap-4 lg:grid-cols-[1fr_280px]">
            <FlowCanvas
              graph={form.graph}
              selected={selected}
              visited={visited}
              flagged={flagged}
              statuses={statuses}
              onSelect={setSelected}
              onMove={(id, x, y) => patch({ graph: { ...form.graph, nodes: form.graph.nodes.map((n) => (n.id === id ? { ...n, x, y } : n)) } })}
            />
            {selectedNode ? (
              <NodeInspector
                key={selectedNode.id}
                node={selectedNode}
                graph={form.graph}
                catalog={catalog}
                fieldCatalog={fieldCatalog}
                simulation={simResult}
                issues={issues.filter((i) => i.node_id === selectedNode.id)}
                onChange={setGraph}
                onDelete={() => deleteNode(selectedNode.id)}
              />
            ) : (
              <p className="text-xs text-slate-500">Select a node to edit its settings and connect its outputs. Drag nodes to arrange them.</p>
            )}
          </div>

          {liveReview && (liveReview.corrections.length > 0 || liveReview.issues.length > 0) && (
            <div className="space-y-2 rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 text-xs">
              <div className="flex items-center gap-3">
                <p className="font-bold text-amber-300">✨ Flow assistant</p>
                {liveReview.corrections.length > 0 && (
                  <button onClick={applyFixes} className="rounded-md bg-amber-500/20 px-2.5 py-1 font-bold text-amber-200 hover:bg-amber-500/30">
                    Apply {liveReview.corrections.length} fix{liveReview.corrections.length > 1 ? "es" : ""}
                  </button>
                )}
                <span className="text-slate-500">Sure fixes are also applied automatically when you save.</span>
              </div>
              {liveReview.corrections.map((c, i) => (
                <p key={`c${i}`} className="text-slate-300">
                  <button onClick={() => setSelected(c.node_id)} className="font-semibold text-amber-200 hover:underline">
                    {c.node}
                  </button>{" "}
                  {c.key}: <span className="font-mono text-red-300 line-through">{String(c.before)}</span> →{" "}
                  <span className="font-mono text-emerald-300">{String(c.after)}</span> <span className="text-slate-500">({c.reason})</span>
                </p>
              ))}
              {liveReview.issues.map((issue, i) => (
                <p key={`i${i}`} className="flex flex-wrap items-center gap-2 text-amber-200">
                  <button onClick={() => setSelected(issue.node_id)} className="font-semibold hover:underline">
                    {issue.node}
                  </button>
                  ⚠ {issue.message}
                  {issue.suggestion && (
                    <button onClick={() => applySuggestion(issue)} className="rounded border border-amber-500/40 px-1.5 hover:bg-amber-500/10">
                      use {fieldCatalog?.fields.find((f) => f.path === issue.suggestion)?.label ?? issue.suggestion}
                    </button>
                  )}
                </p>
              ))}
            </div>
          )}
          {applied.length > 0 && (
            <p className="text-xs text-emerald-400">
              ✓ Auto-corrected {applied.length}: {applied.map((c) => `${String(c.before)} → ${String(c.after)}`).join(" · ")}
            </p>
          )}
          {validation.length > 0 && (
            <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">
              {validation.map((v) => (
                <p key={v}>• {v}</p>
              ))}
            </div>
          )}
          {error && <p className="text-xs text-red-400">{error}</p>}

          <div className="flex flex-wrap gap-2">
            <button onClick={save} disabled={busy} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500 disabled:opacity-50">
              {busy ? "Working…" : "Save Flow"}
            </button>
            {current && (
              <button disabled={changingId !== null || busy} onClick={() => toggleActive(current)} className="rounded-lg border border-slate-700 px-4 py-2 text-sm font-bold text-slate-300 hover:border-emerald-500 hover:text-emerald-400 disabled:opacity-50">
                {changingId === current.id ? "Saving…" : current.is_active ? "Deactivate" : "Activate"}
              </button>
            )}
          </div>

          <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-5">
            <h3 className="mb-3 text-base font-bold text-slate-100">Simulate</h3>
            <div className="grid gap-3 md:grid-cols-[160px_1fr_auto] md:items-end">
              <label className="text-xs text-slate-400">
                Record id
                <input value={sim.entityId} onChange={(e) => setSim((s) => ({ ...s, entityId: e.target.value }))} placeholder="e.g. 12" className={INPUT} />
              </label>
              <label className="text-xs text-slate-400">
                Trigger inputs (JSON)
                <input value={sim.entities} onChange={(e) => setSim((s) => ({ ...s, entities: e.target.value }))} className={`${INPUT} font-mono`} />
              </label>
              <label className="text-xs text-slate-400">Trigger outputs (JSON, for after-intent flows)
                <input value={sim.result} onChange={(e) => setSim((s) => ({ ...s, result: e.target.value }))} className={`${INPUT} font-mono`} />
              </label>
              <button onClick={simulate} disabled={busy} className="rounded-lg border border-sky-500/40 bg-sky-500/10 px-4 py-2 text-sm font-bold text-sky-400 hover:bg-sky-500/20 disabled:opacity-50">
                Save & Run
              </button>
            </div>
            {form.timing === "error" && (
              <p className="mt-2 text-[11px] text-slate-500">
                To try a specific failure, put it in Trigger outputs: {'{"error": {"code": "ACTION_FAILED", "message": "…"}}'}
              </p>
            )}
            {simResult && (
              <div className="mt-4 space-y-1 text-xs">
                <p className={simResult.ok ? (simResult.run?.outcome === "handled" ? "text-amber-300" : "text-emerald-400") : "text-red-400"}>
                  {simResult.ok
                    ? simResult.run?.outcome === "handled"
                      ? `✓ Flow completed - ${simResult.run.errors?.length ?? 0} error(s) caught and handled (rolled back)`
                      : "✓ Flow completed (rolled back)"
                    : `✕ ${simResult.error}`}
                </p>
                {simResult.error_detail && (
                  <div role="alert" className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-red-200">
                    <p className="font-mono text-[11px]">
                      {simResult.error_detail.code} · {simResult.error_detail.category}
                      {simResult.error_detail.ref ? ` · reference ${simResult.error_detail.ref}` : ""}
                      {simResult.error_detail.retryable ? " · retryable" : ""}
                    </p>
                    {simResult.error_detail.node_id && (
                      <button onClick={() => setSelected(simResult.error_detail!.node_id)} className="mt-1 font-semibold underline hover:text-red-100">
                        Go to the step that failed: {simResult.error_detail.node}
                      </button>
                    )}
                    {simResult.error_detail.code !== "REJECTED" && (
                      <p className="mt-1 text-red-200/70">
                        Nothing caught it. Link that step's <b>error</b> output, or add a <b>Catch errors</b> step
                        {form.on_error !== "continue" && form.timing !== "error" ? ", or set “If this flow fails” to carry on" : ""}.
                      </p>
                    )}
                  </div>
                )}
                {simResult.run?.trace.map((t, i) => {
                  const { node_id, type, label, status, error: stepError, caught_by, attempts, ...rest } = t;
                  return (
                    <div key={i} className={`flex flex-wrap gap-2 rounded-md bg-slate-950 px-3 py-1.5 ${status && status !== "ok" ? "border-l-2" : ""}`}
                      style={status && STATUS_STROKE[status] ? { borderColor: STATUS_STROKE[status] } : undefined}>
                      <span className="font-bold" style={{ color: NODE_COLORS[type] }}>
                        {type}
                      </span>
                      <span className="text-slate-300">{label}</span>
                      <span className="text-slate-600">{node_id}</span>
                      {status && status !== "ok" && (
                        <span className="font-semibold" style={{ color: STATUS_STROKE[status] ?? "#94a3b8" }}>
                          {status === "caught" ? `caught${caught_by ? ` → ${caught_by}` : " → error path"}` : status}
                        </span>
                      )}
                      {attempts ? <span className="text-slate-500">{attempts} attempt(s)</span> : null}
                      {stepError && (
                        <span className="w-full text-slate-400">
                          <span className="font-mono text-[11px] text-orange-300">{stepError.code}</span> {stepError.message}
                        </span>
                      )}
                      {Object.keys(rest).length > 0 && <details className="w-full text-slate-400"><summary>Inspect inputs and outputs</summary><pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all pt-2 font-mono">{JSON.stringify(rest, null, 2)}</pre></details>}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
