import { useMemo, useState } from "react";
import type { IntentCategory, IntentDef, IntentProcess } from "../../api/types";
import { INPUT_CLS, categoryStyle } from "./intentUi";

/** Every action the user may run, as cards grouped by business process (the
 * steps their roles allow, in process order) or by business area. */
export default function IntentGallery({
  intents,
  categories,
  processes = [],
  compact = false,
  techView = false,
  onPick,
}: {
  intents: IntentDef[];
  categories: IntentCategory[];
  processes?: IntentProcess[];
  compact?: boolean;
  techView?: boolean;
  onPick: (code: string) => void;
}) {
  const byCode = useMemo(() => new Map(intents.map((i) => [i.code, i])), [intents]);
  // Only processes with at least one action this user can run (and that is shown).
  const myProcesses = useMemo(() => processes.map((p) => ({ ...p, steps: p.steps.filter((c) => byCode.has(c)) })).filter((p) => p.steps.length), [processes, byCode]);
  const [view, setView] = useState<"process" | "area">(myProcesses.length ? "process" : "area");
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const mode = myProcesses.length ? view : "area";

  const q = query.trim().toLowerCase();
  const matches = (i: IntentDef) => !q || `${i.label} ${i.description} ${i.phrases.join(" ")} ${i.examples.join(" ")}`.toLowerCase().includes(q);
  const inProcess = new Set(myProcesses.flatMap((p) => p.steps));
  const other = intents.filter((i) => !inProcess.has(i.code));

  const chips = mode === "process"
    ? [...myProcesses.map((p) => ({ key: p.key, label: p.label })), ...(other.length ? [{ key: "_other", label: "Other actions" }] : [])]
    : categories;
  const active = chips.some((c) => c.key === filter) ? filter : "all";

  // Each group: a heading and its actions. In process view an action may sit in several processes.
  const groups: { key: string; label: string; color?: string; items: IntentDef[] }[] = mode === "process"
    ? [...myProcesses.map((p) => ({ key: p.key, label: p.label, items: p.steps.map((c) => byCode.get(c)!) })),
      { key: "_other", label: "Other actions", items: other }]
        .filter((g) => active === "all" || g.key === active)
        .map((g) => ({ ...g, items: g.items.filter(matches) }))
        .filter((g) => g.items.length)
    : categories.filter((c) => active === "all" || c.key === active)
        .map((c) => ({ key: c.key, label: c.label, color: categoryStyle(c.key).text, items: intents.filter((i) => i.category === c.key && matches(i)) }))
        .filter((g) => g.items.length);
  const total = new Set(groups.flatMap((g) => g.items.map((i) => i.code))).size;

  const card = (i: IntentDef, step?: number) => {
    const style = categoryStyle(i.category);
    const required = i.fields.filter((f) => f.required).length;
    return (
      <button
        key={i.code}
        onClick={() => onPick(i.code)}
        className={`group flex items-start text-left transition hover:border-slate-600 hover:bg-slate-900 ${
          compact ? "gap-3 rounded-xl border border-slate-800 bg-slate-900/60 p-3" : "h-full gap-4 rounded-xl border border-slate-800 bg-slate-900/40 p-4 hover:-translate-y-0.5"
        }`}
      >
        <span className={`relative flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-xl ${style.bg}`}>
          {i.icon}
          {step !== undefined && <span className="absolute -left-1.5 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-slate-700 px-1 text-[9px] font-bold text-slate-200">{step}</span>}
        </span>
        <span className="min-w-0 space-y-1">
          <span className="block text-sm font-semibold text-slate-100 group-hover:text-white">
            {i.label}
            {i.segment && <span className="ml-1.5 rounded bg-slate-800 px-1.5 py-0.5 align-middle text-[9px] font-bold text-slate-400">{i.segment}</span>}
          </span>
          <span className="block text-xs leading-snug text-slate-400">{i.description}</span>
          <span className="block pt-0.5 text-[11px] text-slate-500">
            {required ? `${required} thing${required > 1 ? "s" : ""} to fill` : "nothing to fill"}
            {techView && <span className="ml-1">· {i.code}</span>}
          </span>
        </span>
      </button>
    );
  };

  return (
    <div className={compact ? "space-y-4" : "space-y-7"}>
      <div className={`flex flex-wrap items-center ${compact ? "gap-2" : "gap-x-4 gap-y-3"}`}>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search actions…" className={`${INPUT_CLS} ${compact ? "" : "max-w-xs"}`} />
        {myProcesses.length > 0 && (
          <div className="flex rounded-lg border border-slate-700 p-0.5 text-xs font-semibold" role="group" aria-label="Group actions">
            {(["process", "area"] as const).map((v) => (
              <button key={v} type="button" aria-pressed={mode === v} onClick={() => { setView(v); setFilter("all"); }}
                className={`rounded-md px-2.5 py-1 ${mode === v ? "bg-slate-700 text-white" : "text-slate-400 hover:text-slate-200"}`}>
                {v === "process" ? "By process" : "By area"}
              </button>
            ))}
          </div>
        )}
        <div className="flex flex-wrap gap-1.5">
          {[{ key: "all", label: "All" }, ...chips].map((c) => (
            <button
              key={c.key}
              onClick={() => setFilter(c.key)}
              className={`rounded-full px-3 py-1 text-xs font-semibold transition ${
                active === c.key ? "bg-slate-100 text-slate-900" : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"
              }`}
            >
              {mode === "area" && c.key !== "all" && <span className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full ${categoryStyle(c.key).dot}`} />}
              {c.label}
            </button>
          ))}
        </div>
      </div>

      {groups.map((g) => (
        <div key={g.key}>
          <p className={`text-xs font-semibold uppercase tracking-[0.1em] ${compact ? "mb-2" : "mb-3"} ${g.color ?? "text-slate-400"}`}>
            {g.label}
            {mode === "process" && g.key !== "_other" && <span className="ml-2 font-normal normal-case tracking-normal text-slate-500">{g.items.length} step{g.items.length === 1 ? "" : "s"} you can run</span>}
          </p>
          <div className={`grid ${compact ? "gap-2" : "gap-4 sm:grid-cols-2 2xl:grid-cols-3"}`}>
            {g.items.map((i, n) => card(i, mode === "process" && g.key !== "_other" ? n + 1 : undefined))}
          </div>
        </div>
      ))}
      {total === 0 && <p className="text-sm text-slate-500">No action matches - try describing it in the box above.</p>}
    </div>
  );
}
