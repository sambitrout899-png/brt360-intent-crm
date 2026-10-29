import { useEffect, useMemo, useRef, useState } from "react";
import { ENTITY_LABELS } from "../api/types";
import type { EntityType, Flow } from "../api/types";

type Status = "all" | "active" | "off" | "errors";
type Kind = "all" | Flow["trigger_type"];
type Sort = "name" | "recent" | "trigger";

const KINDS: [Kind, string][] = [
  ["all", "Any trigger"],
  ["intent", "Before / after an action"],
  ["state_enter", "On a stage change"],
  ["custom_intent", "Custom action"],
  ["validation", "Page validation"],
];
const STEP_ICON: Record<string, string> = {
  condition: "◇", ai_decision: "🧠", set_field: "✎", transition: "➜", assign: "👤", notify: "🔔", reject: "⛔", action: "⚡",
  wait: "⏳", wait_approval: "✋",
};
const STEP_WORD: Record<string, string> = {
  condition: "check", ai_decision: "AI decision", set_field: "set field", transition: "change stage", assign: "assign", notify: "notify",
  reject: "reject", action: "run action", wait: "wait", wait_approval: "approval",
};
const FILTERS_KEY = "brt360.flowlist";

function ago(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso.endsWith("Z") ? iso : `${iso}Z`).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 1440) return `${Math.round(minutes / 60)} h ago`;
  return `${Math.round(minutes / 1440)} d ago`;
}

function readFilters(): { status: Status; kind: Kind; entity: string; sort: Sort; grouped: boolean } {
  const fallback = { status: "all" as Status, kind: "all" as Kind, entity: "all", sort: "name" as Sort, grouped: true };
  try {
    return { ...fallback, ...JSON.parse(localStorage.getItem(FILTERS_KEY) || "{}") };
  } catch {
    return fallback;
  }
}

/** "B2C process: confirm installation" belongs to "B2C process" */
function groupOf(name: string): string | null {
  const m = name.match(/^(.{3,60}?):\s+\S/);
  return m ? m[1].trim() : null;
}

/** Every flow, findable: search across names, triggers, codes and what the
 * steps inside do; filter by status, trigger and record; grouped by process. */
export default function FlowList({
  flows,
  intentLabels,
  onOpen,
  onToggle,
  onDelete,
  onNew,
  changingId,
}: {
  flows: Flow[];
  intentLabels: Record<string, string>;
  onOpen: (flow: Flow) => void;
  onToggle: (flow: Flow) => void;
  onDelete: (id: number) => void;
  onNew: () => void;
  changingId: number | null;
}) {
  const initial = readFilters();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<Status>(initial.status);
  const [kind, setKind] = useState<Kind>(initial.kind);
  const [entity, setEntity] = useState(initial.entity);
  const [sort, setSort] = useState<Sort>(initial.sort);
  const [grouped, setGrouped] = useState(initial.grouped);
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState<number | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    try {
      localStorage.setItem(FILTERS_KEY, JSON.stringify({ status, kind, entity, sort, grouped }));
    } catch {
      /* private window - the filters last for this visit */
    }
  }, [status, kind, entity, sort, grouped]);

  // "/" jumps to the search box
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName);
      if (e.key === "/" && !typing) {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const trigger = (f: Flow): string => {
    if (f.trigger_type === "intent") {
      const code = f.trigger_intent_type ?? "";
      return `${f.timing} ${intentLabels[code] ? `${intentLabels[code]} (${code})` : code || "any action"}`;
    }
    if (f.trigger_type === "state_enter") return `when ${ENTITY_LABELS[f.entity_type as EntityType]?.toLowerCase() ?? f.entity_type ?? "a record"} → ${(f.trigger_state ?? "?").replace(/_/g, " ")}`;
    if (f.trigger_type === "validation") return `validates the ${ENTITY_LABELS[f.entity_type as EntityType]?.toLowerCase() ?? "record"} page`;
    return `custom action ${f.intent_code ?? ""}`.trim();
  };

  const steps = (f: Flow) => f.graph.nodes.filter((n) => n.type !== "start" && n.type !== "end");

  // what each flow can be found by - and where a match came from
  const indexed = useMemo(
    () =>
      flows.map((f) => {
        const parts: [string, string][] = [
          ["name", f.name],
          ["description", f.description || ""],
          ["trigger", `${trigger(f)} ${f.trigger_type.replace("_", " ")} ${f.trigger_phrases ?? ""} ${f.intent_code ?? ""} FLOW-${f.id}`],
          ["record", `${f.entity_type ?? ""} ${ENTITY_LABELS[f.entity_type as EntityType] ?? ""}`],
          ...steps(f).map((n): [string, string] => [
            `step “${n.label || STEP_WORD[n.type] || n.type}”`,
            `${n.label} ${n.type} ${Object.values(n.config ?? {}).map((v) => (typeof v === "object" ? JSON.stringify(v) : String(v ?? ""))).join(" ")} ${
              intentLabels[String(n.config?.intent ?? "")] ?? ""
            }`,
          ]),
        ];
        return { flow: f, parts: parts.map(([where, text]) => [where, text.toLowerCase()] as [string, string]) };
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- trigger() only reads intentLabels
    [flows, intentLabels]
  );

  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const matchOf = (parts: [string, string][]) => {
    if (!terms.length) return { ok: true, where: "" };
    const all = parts.map(([, t]) => t).join(" ");
    if (!terms.every((t) => all.includes(t))) return { ok: false, where: "" };
    // the name says it all - no need to say where; else the first other place that holds a term
    if (terms.every((t) => parts[0][1].includes(t))) return { ok: true, where: "" };
    const where = parts.find(([w, t]) => w !== "name" && terms.some((term) => t.includes(term)));
    return { ok: true, where: where ? where[0] : "" };
  };

  const counts = {
    all: flows.length,
    active: flows.filter((f) => f.is_active).length,
    off: flows.filter((f) => !f.is_active).length,
    errors: flows.filter((f) => f.validation_errors.length > 0).length,
  };
  const entities = [...new Set(flows.map((f) => f.entity_type).filter(Boolean))] as string[];

  const shown = indexed
    .map(({ flow, parts }) => ({ flow, ...matchOf(parts) }))
    .filter(({ flow, ok }) =>
      ok &&
      (status === "all" || (status === "active" ? flow.is_active : status === "off" ? !flow.is_active : flow.validation_errors.length > 0)) &&
      (kind === "all" || flow.trigger_type === kind) &&
      (entity === "all" || flow.entity_type === entity)
    )
    .sort((a, b) =>
      sort === "recent"
        ? b.flow.updated_at.localeCompare(a.flow.updated_at)
        : sort === "trigger"
          ? trigger(a.flow).localeCompare(trigger(b.flow)) || a.flow.name.localeCompare(b.flow.name)
          : a.flow.name.localeCompare(b.flow.name)
    );

  const groups: [string, typeof shown][] = [];
  if (grouped) {
    const byName = new Map<string, typeof shown>();
    for (const row of shown) {
      const g = groupOf(row.flow.name);
      const key = g && shown.filter((r) => groupOf(r.flow.name) === g).length > 1 ? g : "";
      byName.set(key, [...(byName.get(key) ?? []), row]);
    }
    for (const [key, rows] of [...byName.entries()].sort(([a], [b]) => (a === "" ? 1 : b === "" ? -1 : a.localeCompare(b)))) groups.push([key, rows]);
  } else {
    groups.push(["", shown]);
  }

  const chip = (on: boolean) =>
    `rounded-full px-3 py-1 text-xs font-semibold transition ${on ? "bg-slate-100 text-slate-900" : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"}`;

  return (
    <div className="space-y-4">
      {/* ---------- header ---------- */}
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="text-base font-semibold text-slate-100">
          Flows <span className="ml-1 text-sm font-normal text-slate-500">{counts.all} · {counts.active} active</span>
        </h3>
        <button onClick={onNew} className="ml-auto rounded-lg bg-sky-600 px-4 py-2 text-sm font-semibold text-white hover:bg-sky-500">
          + New flow
        </button>
      </div>

      {/* ---------- search + filters ---------- */}
      <div className="space-y-3 rounded-xl border border-slate-800 bg-slate-900/40 p-3">
        <div className="relative">
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500">⌕</span>
          <input
            ref={searchRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && setQuery("")}
            placeholder="Search flows - names, triggers, INT / FLOW codes, and what their steps do (messages, positions, actions)…"
            className="h-10 w-full rounded-lg border border-slate-700 bg-slate-950/80 pl-9 pr-16 text-sm text-slate-100 outline-none placeholder:text-slate-500 focus:border-sky-500"
          />
          {query ? (
            <button onClick={() => setQuery("")} className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-400 hover:text-slate-200">
              Clear
            </button>
          ) : (
            <kbd className="absolute right-3 top-1/2 -translate-y-1/2 rounded border border-slate-700 bg-slate-800 px-1.5 text-[10px] text-slate-400">/</kbd>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <div className="flex flex-wrap gap-1">
            {(
              [
                ["all", "All"],
                ["active", "Active"],
                ["off", "Inactive"],
                ["errors", "With errors"],
              ] as [Status, string][]
            ).map(([k, l]) => (
              <button key={k} onClick={() => setStatus(k)} className={chip(status === k)}>
                {l} <span className={status === k ? "text-slate-500" : "text-slate-600"}>{counts[k]}</span>
              </button>
            ))}
          </div>
          <select value={kind} onChange={(e) => setKind(e.target.value as Kind)} className="h-8 rounded-lg border border-slate-700 bg-slate-950 px-2 text-xs text-slate-200">
            {KINDS.map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </select>
          <select value={entity} onChange={(e) => setEntity(e.target.value)} className="h-8 rounded-lg border border-slate-700 bg-slate-950 px-2 text-xs text-slate-200">
            <option value="all">Any record</option>
            {entities.map((e) => (
              <option key={e} value={e}>
                {ENTITY_LABELS[e as EntityType] ?? e}
              </option>
            ))}
          </select>
          <span className="ml-auto flex items-center gap-3 text-xs text-slate-400">
            <label className="flex cursor-pointer items-center gap-1.5">
              <input type="checkbox" checked={grouped} onChange={(e) => setGrouped(e.target.checked)} className="accent-sky-500" />
              Group by process
            </label>
            <select value={sort} onChange={(e) => setSort(e.target.value as Sort)} className="h-8 rounded-lg border border-slate-700 bg-slate-950 px-2 text-xs text-slate-200">
              <option value="name">Sort: name</option>
              <option value="recent">Sort: recently changed</option>
              <option value="trigger">Sort: trigger</option>
            </select>
          </span>
        </div>
      </div>

      {/* ---------- the flows ---------- */}
      {shown.length === 0 && (
        <p className="rounded-xl border border-dashed border-slate-800 p-8 text-center text-sm text-slate-500">
          {flows.length === 0 ? "No flows yet - create one, or describe a process in the Intent Command Bar's “Build a process”." : "No flow matches."}
        </p>
      )}
      {groups.map(([group, rows]) => {
        const isClosed = group !== "" && closed.has(group) && !terms.length;
        const on = rows.filter((r) => r.flow.is_active).length;
        return (
          <section key={group || "_"} className="overflow-hidden rounded-xl border border-slate-800">
            {group !== "" && (
              <button
                onClick={() => setClosed((s) => {
                  const n = new Set(s);
                  if (n.has(group)) n.delete(group);
                  else n.add(group);
                  return n;
                })}
                className="flex w-full items-center gap-3 border-b border-slate-800 bg-slate-900/70 px-4 py-2.5 text-left hover:bg-slate-900"
              >
                <span className="text-xs text-slate-500">{isClosed ? "▸" : "▾"}</span>
                <span className="text-sm font-semibold text-slate-100">{group}</span>
                <span className="text-xs text-slate-500">
                  {rows.length} flows · {on} active
                </span>
              </button>
            )}
            {!isClosed && (
              <ul className="divide-y divide-slate-800/80">
                {rows.map(({ flow: f, where }) => {
                  const inner = steps(f);
                  const kinds = [...new Set(inner.map((n) => n.type))];
                  const shortName = group && f.name.startsWith(`${group}:`) ? f.name.slice(group.length + 1).trim() : f.name;
                  return (
                    <li key={f.id} className={`flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 transition hover:bg-slate-900/50 ${f.is_active ? "" : "opacity-60"}`}>
                      <button
                        role="switch"
                        aria-checked={f.is_active}
                        aria-label={`${f.is_active ? "Deactivate" : "Activate"} ${f.name}`}
                        disabled={changingId !== null}
                        title={f.is_active ? "Deactivate flow" : "Activate flow"}
                        onClick={() => onToggle(f)}
                        className={`relative h-5 w-9 shrink-0 rounded-full transition ${f.is_active ? "bg-emerald-500" : "bg-slate-700"}`}
                      >
                        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${f.is_active ? "left-[18px]" : "left-0.5"}`} />
                      </button>
                      <button onClick={() => onOpen(f)} className="min-w-0 flex-1 text-left">
                        <span className="flex flex-wrap items-center gap-2">
                          <span className="truncate text-sm font-semibold text-slate-100 hover:text-sky-300">{shortName}</span>
                          <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">{f.intent_code ?? `FLOW-${f.id}`}</span>
                          <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${f.is_active ? "bg-emerald-500/15 text-emerald-300" : "bg-slate-800 text-slate-400"}`}>
                            {f.is_active ? "Active" : "Inactive"}
                          </span>
                          {f.validation_errors.length > 0 && (
                            <span className="rounded bg-red-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-red-300" title={f.validation_errors.join("\n")}>
                              {f.validation_errors.length} error{f.validation_errors.length > 1 ? "s" : ""}
                            </span>
                          )}
                          {f.warnings.length > 0 && f.validation_errors.length === 0 && (
                            <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300" title={f.warnings.join("\n")}>
                              {f.warnings.length} warning{f.warnings.length > 1 ? "s" : ""}
                            </span>
                          )}
                        </span>
                        <span className="mt-0.5 block truncate text-xs text-slate-500">
                          <span className="text-slate-400">{trigger(f)}</span>
                          {inner.length > 0 && (
                            <span title={inner.map((n) => n.label || STEP_WORD[n.type]).join(" → ")}>
                              {" · "}
                              {kinds.map((k) => STEP_ICON[k] ?? "•").join(" ")} {inner.length} step{inner.length > 1 ? "s" : ""} ({kinds.map((k) => STEP_WORD[k] ?? k).join(", ")})
                            </span>
                          )}
                          {where && <span className="text-sky-400"> · found in {where}</span>}
                        </span>
                      </button>
                      <span className="hidden text-[11px] text-slate-500 md:inline" title={f.updated_at}>
                        v{f.version} · {ago(f.updated_at)}
                      </span>
                      {confirming === f.id ? (
                        <span className="flex items-center gap-2 text-xs">
                          <span className="text-red-300">Delete?</span>
                          <button onClick={() => (setConfirming(null), onDelete(f.id))} className="rounded-md bg-red-600 px-2.5 py-1 font-semibold text-white hover:bg-red-500">
                            Yes
                          </button>
                          <button onClick={() => setConfirming(null)} className="rounded-md border border-slate-700 px-2.5 py-1 text-slate-300 hover:border-slate-500">
                            No
                          </button>
                        </span>
                      ) : (
                        <span className="flex items-center gap-1">
                          <button
                            disabled={changingId !== null}
                            onClick={() => onToggle(f)}
                            aria-label={`${f.is_active ? "Deactivate" : "Activate"} ${f.name}`}
                            className={`rounded-md border px-3 py-1 text-xs font-semibold disabled:cursor-wait disabled:opacity-50 ${f.is_active ? "border-amber-500/40 text-amber-300 hover:bg-amber-500/10" : "border-emerald-500/40 text-emerald-300 hover:bg-emerald-500/10"}`}
                          >
                            {changingId === f.id ? "Saving…" : f.is_active ? "Deactivate" : "Activate"}
                          </button>
                          <button onClick={() => onOpen(f)} className="rounded-md border border-slate-700 px-3 py-1 text-xs font-semibold text-slate-200 hover:border-sky-500 hover:text-sky-300">
                            Edit
                          </button>
                          <button onClick={() => setConfirming(f.id)} title="Delete" aria-label={`Delete ${f.name}`} className="rounded-md px-2 py-1 text-xs text-slate-500 hover:bg-red-500/10 hover:text-red-400">
                            🗑
                          </button>
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}
