import { useState } from "react";
import type { C360ConnectorDraft, C360Layout, C360Pane, C360Studio, C360Widget, C360Width } from "../../api/c360Types";
import { compatibleTypes, move, newId, TYPE_ICONS, TYPE_LABELS, WIDTH_LABELS } from "./studioUtil";

/** Visual editing: headline numbers, panes (order, width, colour, name) and
 * the widgets inside them (chart type, title, data source). */
export default function LayoutTab({
  studio,
  draft,
  setDraft,
  pending,
  onFocusPane,
}: {
  studio: C360Studio;
  draft: C360Layout;
  setDraft: (next: C360Layout) => void;
  pending: C360ConnectorDraft[];
  onFocusPane: (id: string | null) => void;
}) {
  const [open, setOpen] = useState<string | null>(draft.panes[0]?.id ?? null);
  const [picker, setPicker] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const cat = studio.catalog;
  const recipeTitle = (id: string) => cat.recipes.find((r) => r.id === id)?.title ?? id;
  const datasetLabel = (key: string | null) => cat.datasets.find((d) => d.key === key)?.label ?? "";

  const setPanes = (panes: C360Pane[]) => setDraft({ ...draft, panes });
  const updatePane = (id: string, patch: Partial<C360Pane>) => setPanes(draft.panes.map((p) => (p.id === id ? { ...p, ...patch } : p)));
  const updateWidget = (paneId: string, wid: string, patch: Partial<C360Widget>) =>
    setPanes(draft.panes.map((p) => (p.id === paneId ? { ...p, widgets: p.widgets.map((w) => (w.id === wid ? { ...w, ...patch } : w)) } : p)));

  function sourcesFor(dataset: string | null): { value: string; label: string }[] {
    if (!dataset) return [];
    const ds = cat.datasets.find((d) => d.key === dataset);
    const out = [{ value: "", label: ds?.internal ? "Automatic (BRT 360)" : "Automatic (first connected system)" }];
    if (ds?.internal) out.push({ value: "brt", label: "BRT 360" });
    for (const c of studio.connectors) if (c.endpoints[dataset]) out.push({ value: c.key, label: `${c.name}${c.is_active ? "" : " (off)"}` });
    for (const d of pending) if (d.endpoints[dataset]) out.push({ value: d.key, label: `${d.name} (on save)` });
    return out;
  }

  function addPane(domain: string) {
    const d = cat.domains.find((x) => x.key === domain)!;
    const pane: C360Pane = { id: newId("p"), title: d.label, domain, icon: d.icon, color: d.color, width: "half", collapsed: false, widgets: [] };
    setPanes([...draft.panes, pane]);
    setOpen(pane.id);
    setPicker(pane.id);
  }

  function addWidget(paneId: string, recipeId: string) {
    const base = studio.recipe_widgets[recipeId];
    if (!base) return;
    setPanes(draft.panes.map((p) => (p.id === paneId ? { ...p, widgets: [...p.widgets, { ...structuredClone(base), id: newId("w") }] } : p)));
  }

  const kpiSet = new Set(draft.header.kpis);

  return (
    <div className="space-y-5">
      <section>
        <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-slate-400">Headline numbers</h3>
        <div className="flex flex-wrap gap-1.5">
          {cat.header_kpis.map((id) => (
            <button
              key={id}
              onClick={() =>
                setDraft({
                  ...draft,
                  header: { ...draft.header, kpis: kpiSet.has(id) ? draft.header.kpis.filter((k) => k !== id) : [...draft.header.kpis, id].slice(0, 6) },
                })
              }
              className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold transition ${
                kpiSet.has(id) ? "border-sky-500 bg-sky-500/15 text-sky-200" : "border-slate-700 text-slate-400 hover:border-slate-500"
              }`}
            >
              {kpiSet.has(id) ? "✓ " : "+ "}
              {recipeTitle(id)}
            </button>
          ))}
        </div>
        <label className="mt-2 flex items-center gap-2 text-xs text-slate-400">
          <input type="checkbox" checked={draft.header.show_health} onChange={(e) => setDraft({ ...draft, header: { ...draft.header, show_health: e.target.checked } })} />
          Show the customer health score
        </label>
      </section>

      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">Panes ({draft.panes.length})</h3>
          <select
            value=""
            onChange={(e) => e.target.value && addPane(e.target.value)}
            className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs font-semibold text-sky-300"
          >
            <option value="">+ Add a pane…</option>
            {cat.domains.map((d) => (
              <option key={d.key} value={d.key}>
                {d.icon} {d.label}
              </option>
            ))}
          </select>
        </div>

        {draft.panes.map((pane, pi) => {
          const expanded = open === pane.id;
          return (
            <div
              key={pane.id}
              className="rounded-xl border border-slate-700/80 bg-slate-900/70"
              onMouseEnter={() => onFocusPane(pane.id)}
              onMouseLeave={() => onFocusPane(null)}
            >
              <div className="flex items-center gap-2 px-2.5 py-2">
                <span className="h-6 w-1.5 rounded-full" style={{ background: pane.color }} />
                <button onClick={() => setOpen(expanded ? null : pane.id)} className="min-w-0 flex-1 truncate text-left text-sm font-semibold text-slate-100">
                  {pane.icon} {pane.title}
                  <span className="ml-2 text-[11px] font-normal text-slate-500">
                    {pane.widgets.length} widget{pane.widgets.length === 1 ? "" : "s"} · {WIDTH_LABELS[pane.width]}
                  </span>
                </button>
                <button title="Move up" onClick={() => setPanes(move(draft.panes, pi, -1))} className="px-1 text-slate-500 hover:text-slate-200">
                  ↑
                </button>
                <button title="Move down" onClick={() => setPanes(move(draft.panes, pi, 1))} className="px-1 text-slate-500 hover:text-slate-200">
                  ↓
                </button>
                <button title="Remove pane" onClick={() => setPanes(draft.panes.filter((p) => p.id !== pane.id))} className="px-1 text-slate-500 hover:text-rose-400">
                  ✕
                </button>
              </div>

              {expanded && (
                <div className="space-y-3 border-t border-slate-800 p-3">
                  <div className="flex gap-2">
                    <input
                      value={pane.icon}
                      onChange={(e) => updatePane(pane.id, { icon: e.target.value.slice(0, 4) })}
                      className="w-11 rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-center text-sm"
                      title="Icon"
                    />
                    <input
                      value={pane.title}
                      onChange={(e) => updatePane(pane.id, { title: e.target.value.slice(0, 60) })}
                      className="min-w-0 flex-1 rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-100"
                    />
                    <input
                      type="color"
                      value={pane.color}
                      onChange={(e) => updatePane(pane.id, { color: e.target.value })}
                      className="h-8 w-9 cursor-pointer rounded-md border border-slate-700 bg-slate-950"
                      title="Pane colour"
                    />
                  </div>
                  <div className="flex overflow-hidden rounded-lg border border-slate-700 text-xs">
                    {(["third", "half", "two_thirds", "full"] as C360Width[]).map((w) => (
                      <button
                        key={w}
                        onClick={() => updatePane(pane.id, { width: w })}
                        className={`flex-1 py-1.5 font-semibold ${pane.width === w ? "bg-sky-600 text-white" : "text-slate-400 hover:bg-slate-800"}`}
                      >
                        {WIDTH_LABELS[w]}
                      </button>
                    ))}
                  </div>

                  <div className="space-y-2">
                    {pane.widgets.map((w, wi) => {
                      const types = compatibleTypes(w);
                      const sources = sourcesFor(w.dataset);
                      return (
                        <div key={w.id} className="rounded-lg border border-slate-800 bg-slate-950/70 p-2">
                          <div className="flex items-center gap-1.5">
                            <span title={TYPE_LABELS[w.type]}>{TYPE_ICONS[w.type]}</span>
                            <input
                              value={w.title}
                              onChange={(e) => updateWidget(pane.id, w.id, { title: e.target.value.slice(0, 80) })}
                              className="min-w-0 flex-1 rounded border border-transparent bg-transparent px-1 py-0.5 text-xs font-semibold text-slate-200 hover:border-slate-700 focus:border-sky-500 focus:outline-none"
                            />
                            <button onClick={() => setPanes(draft.panes.map((p) => (p.id === pane.id ? { ...p, widgets: move(p.widgets, wi, -1) } : p)))} className="px-0.5 text-xs text-slate-500 hover:text-slate-200">
                              ↑
                            </button>
                            <button onClick={() => setPanes(draft.panes.map((p) => (p.id === pane.id ? { ...p, widgets: move(p.widgets, wi, 1) } : p)))} className="px-0.5 text-xs text-slate-500 hover:text-slate-200">
                              ↓
                            </button>
                            <button
                              onClick={() => setPanes(draft.panes.map((p) => (p.id === pane.id ? { ...p, widgets: p.widgets.filter((x) => x.id !== w.id) } : p)))}
                              className="px-0.5 text-xs text-slate-500 hover:text-rose-400"
                            >
                              ✕
                            </button>
                          </div>
                          {types.length > 1 && (
                            <div className="mt-1.5 flex flex-wrap gap-1">
                              {types.map((t) => (
                                <button
                                  key={t}
                                  onClick={() => updateWidget(pane.id, w.id, { type: t })}
                                  className={`rounded-md px-2 py-0.5 text-[11px] ${w.type === t ? "bg-sky-600 text-white" : "bg-slate-800 text-slate-400 hover:text-slate-200"}`}
                                >
                                  {TYPE_ICONS[t]} {TYPE_LABELS[t]}
                                </button>
                              ))}
                            </div>
                          )}
                          {w.type === "records" && (
                            <div className="mt-1.5 flex flex-wrap gap-1">
                              {cat.record_sections.map((s) => {
                                const sections = (w.metric.sections as string[]) ?? [];
                                const on = sections.includes(s);
                                return (
                                  <button
                                    key={s}
                                    onClick={() =>
                                      updateWidget(pane.id, w.id, { metric: { ...w.metric, sections: on ? sections.filter((x) => x !== s) : [...sections, s] } })
                                    }
                                    className={`rounded-md px-2 py-0.5 text-[11px] capitalize ${on ? "bg-violet-600/80 text-white" : "bg-slate-800 text-slate-400"}`}
                                  >
                                    {s.replace("_", " ")}
                                  </button>
                                );
                              })}
                            </div>
                          )}
                          {sources.length > 0 && (
                            <label className="mt-1.5 flex items-center gap-2 text-[11px] text-slate-500">
                              <span className="shrink-0">{datasetLabel(w.dataset)} from</span>
                              <select
                                value={w.source ?? ""}
                                onChange={(e) => updateWidget(pane.id, w.id, { source: e.target.value || null })}
                                className="min-w-0 flex-1 rounded border border-slate-700 bg-slate-900 px-1 py-0.5 text-[11px] text-slate-300"
                              >
                                {sources.map((s) => (
                                  <option key={s.value} value={s.value}>
                                    {s.label}
                                  </option>
                                ))}
                              </select>
                            </label>
                          )}
                        </div>
                      );
                    })}
                  </div>

                  {picker === pane.id ? (
                    <div className="space-y-2 rounded-lg border border-sky-500/30 bg-sky-500/5 p-2">
                      <input
                        autoFocus
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Search charts - revenue, tickets, latency…"
                        className="w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-100"
                      />
                      <div className="max-h-64 space-y-1 overflow-y-auto">
                        {cat.recipes
                          .filter((r) => {
                            const q = query.trim().toLowerCase();
                            return q ? `${r.title} ${r.description} ${r.domain}`.toLowerCase().includes(q) : r.domain === pane.domain || pane.domain === "custom";
                          })
                          .map((r) => (
                            <button
                              key={r.id}
                              onClick={() => addWidget(pane.id, r.id)}
                              className="flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left hover:bg-slate-800"
                            >
                              <span>{TYPE_ICONS[r.type]}</span>
                              <span className="min-w-0">
                                <span className="block text-xs font-semibold text-slate-200">{r.title}</span>
                                <span className="block text-[11px] text-slate-500">{r.description}</span>
                              </span>
                            </button>
                          ))}
                      </div>
                      <button onClick={() => (setPicker(null), setQuery(""))} className="text-[11px] text-slate-400 hover:text-slate-200">
                        Done
                      </button>
                    </div>
                  ) : (
                    <button onClick={() => setPicker(pane.id)} className="w-full rounded-lg border border-dashed border-slate-700 py-1.5 text-xs font-semibold text-sky-400 hover:border-sky-500">
                      + Add a chart or table
                    </button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </section>
    </div>
  );
}
