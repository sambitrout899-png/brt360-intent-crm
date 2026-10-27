import type { CSSProperties } from "react";
import type { C360Insight, C360RenderedWidget, C360View } from "../../api/c360Types";
import type { Party360 } from "../../api/types";
import { BarList, DataTable, Donut, Empty, Gauge, KpiValue, TrendChart } from "./charts";
import RecordsWidget from "./RecordsWidget";
import { workspaceTheme } from "./workspacePresentation";
import { WorkspaceHealth, WorkspaceMetrics } from "./WorkspaceHeader";

/** Renders one customer's 360 exactly as the tenant configured it - theme,
 * hero header with health and headline numbers, then the panes on a
 * six-column grid. Used by the live page and the Configure 360 preview. */

const WIDTH_CLASS: Record<string, string> = {
  third: "lg:col-span-2",
  half: "lg:col-span-3",
  two_thirds: "lg:col-span-4",
  full: "lg:col-span-6",
};
const INNER_GRID: Record<string, string> = {
  third: "grid-cols-1",
  half: "grid-cols-1 sm:grid-cols-2",
  two_thirds: "grid-cols-1 sm:grid-cols-2",
  full: "grid-cols-1 sm:grid-cols-2 xl:grid-cols-3",
};
const WIDE_TYPES = new Set(["table", "records", "insights"]);
const SPAN: Record<string, number> = { third: 2, half: 3, two_thirds: 4, full: 6 };
const SPAN_CLASS: Record<number, string> = { 2: "lg:col-span-2", 3: "lg:col-span-3", 4: "lg:col-span-4", 5: "lg:col-span-5", 6: "lg:col-span-6" };

/** The published pane widths on the six-column layout, with every row filled: a pane that ends a row
 * short (the next one doesn't fit beside it, or it is the last) widens to the row's end - no half-empty
 * rows, no table squeezed beside a gap. */
function fill<T extends { width: string }>(panes: T[]): { pane: T; span: number }[] {
  const out = panes.map(pane => ({ pane, span: SPAN[pane.width] ?? 3 }));
  let used = 0;
  out.forEach((cell, i) => {
    if (used + cell.span > 6) {
      out[i - 1].span += 6 - used;
      used = 0;
    }
    used += cell.span;
    if (i === out.length - 1 && used < 6) cell.span += 6 - used;
  });
  return out;
}

/** Columns inside a pane: as many as its small widgets need, at most what its width allows. */
function innerGrid(span: number, widgets: { type: string; span?: number | null }[]): string {
  const small = widgets.filter((w) => !WIDE_TYPES.has(w.type) && w.span !== 2).length;
  const max = span >= 6 ? 3 : span >= 3 ? 2 : 1;
  const n = Math.max(1, Math.min(max, small));
  return n === 3 ? "grid-cols-1 sm:grid-cols-2 xl:grid-cols-3" : n === 2 ? "grid-cols-1 sm:grid-cols-2" : "grid-cols-1";
}

const TONE: Record<C360Insight["tone"], { icon: string; color: string }> = {
  risk: { icon: "!", color: "#f87171" },
  opportunity: { icon: "+", color: "#34d399" },
  action: { icon: "→", color: "#38bdf8" },
  info: { icon: "i", color: "#a78bfa" },
  good: { icon: "✓", color: "#34d399" },
};

function themeStyle(view: C360View): CSSProperties {
  return { ...workspaceTheme(view.theme), background: view.theme.bg, color: view.theme.text } as CSSProperties;
}

function cardStyle(view: C360View, tint?: string): CSSProperties {
  const style = view.theme.card_style;
  const base: CSSProperties = { borderColor: "var(--c-border)" };
  if (style === "outline") return { ...base, background: "transparent" };
  if (style === "solid") return { ...base, background: view.theme.surface.replace(/[\d.]+\)$/, "1)") };
  return {
    ...base,
    background: tint ? `linear-gradient(160deg, color-mix(in srgb, ${tint} 9%, var(--c-surface)), var(--c-surface))` : "var(--c-surface)",
    backdropFilter: "blur(10px)",
  };
}

const TONE_ORDER: Record<C360Insight["tone"], number> = { risk: 0, action: 1, opportunity: 2, info: 3, good: 4 };

/** What needs attention - one row each, risks first, the action on the right. */
function Insights({ items, onRequestIntent }: { items: C360Insight[]; onRequestIntent: (text: string) => void }) {
  if (!items.length) return <Empty text="Nothing needs attention." />;
  return (
    <ul className="c360-insights">
      {[...items].sort((a, b) => TONE_ORDER[a.tone] - TONE_ORDER[b.tone]).map((i) => (
        <li key={i.title} className="c360-insight" style={{ "--tone": TONE[i.tone].color } as CSSProperties}>
          <span className="c360-insight-icon" aria-hidden="true">{TONE[i.tone].icon}</span>
          <div>
            <p className="c360-insight-title">{i.title}</p>
            <p className="c360-insight-detail">{i.detail}</p>
          </div>
          {i.action && <button type="button" onClick={() => onRequestIntent(i.action!.text)}>{i.action.label}</button>}
        </li>
      ))}
    </ul>
  );
}

function WidgetBody({
  w,
  view,
  accent,
  detail,
  onRequestIntent,
  onConnect,
}: {
  w: C360RenderedWidget;
  view: C360View;
  accent: string;
  detail: Party360 | null;
  onRequestIntent: (text: string) => void;
  onConnect?: (dataset: string) => void;
}) {
  if (w.type === "insights") return <Insights items={view.insights} onRequestIntent={onRequestIntent} />;
  if (w.type === "records") return <RecordsWidget detail={detail} sections={(w.metric.sections as string[]) ?? []} onRequestIntent={onRequestIntent} />;
  if (w.status === "not_connected") {
    return (
      <div className="c360-widget-state">
        <p>{w.message}</p>
        {onConnect && w.dataset && (
          <button type="button" onClick={() => onConnect(w.dataset!)}>
            Connect a system
          </button>
        )}
      </div>
    );
  }
  if (w.status === "error") {
    return (
      <div className="c360-widget-state c360-widget-error">
        <p><strong>Couldn't load from {w.source_label}</strong></p>
        <p>{w.message}</p>
      </div>
    );
  }
  const d = w.data;
  if (!d || d.kind === "none") return <Empty />;
  if (d.kind === "value") {
    return w.type === "gauge" ? (
      <Gauge value={d.value} max={d.max ?? 100} fmt={w.format} accent={accent} />
    ) : (
      <KpiValue value={d.value} fmt={w.format} unit={w.metric.unit as string | undefined} count={d.count} accent={accent} />
    );
  }
  if (d.kind === "series") return <TrendChart points={d.points} type={w.type === "bar" ? "bar" : w.type === "line" ? "line" : "area"} fmt={w.format} accent={accent} />;
  if (d.kind === "categories") return w.type === "donut" ? <Donut items={d.items} fmt={w.format} accent={accent} /> : <BarList items={d.items} fmt={w.format} accent={accent} />;
  return <DataTable columns={d.columns} rows={d.rows} total={d.total} />;
}

function SourcePill({ w }: { w: C360RenderedWidget }) {
  if (!w.source_label) return null;
  const color = w.status === "error" ? "#f87171" : w.status === "not_connected" ? "#94a3b8" : w.source_label === "AI" ? "#a78bfa" : "#34d399";
  return (
    <span className="c360-source" title={`Data from ${w.source_label}`}>
      <span style={{ background: color }} />
      {w.source_label}
    </span>
  );
}

export default function Customer360View({
  view,
  detail,
  onRequestIntent,
  onConnect,
  highlightPane,
  showHero = true,
  paneFilter,
  widgetFilter,
}: {
  view: C360View;
  detail: Party360 | null;
  onRequestIntent: (text: string) => void;
  onConnect?: (dataset: string) => void;
  highlightPane?: string | null;
  /** false: just the panes (the Customer 360 page shows its own header) */
  showHero?: boolean;
  /** only the panes this returns true for (e.g. one tab's domains) */
  paneFilter?: (pane: C360View["panes"][number]) => boolean;
  /** only the widgets this returns true for (a pane left empty is not shown) */
  widgetFilter?: (widget: C360RenderedWidget) => boolean;
}) {
  const panes = view.panes
    .filter((pane) => !paneFilter || paneFilter(pane))
    .map((pane) => ({ ...pane, widgets: widgetFilter ? pane.widgets.filter(widgetFilter) : pane.widgets }))
    .filter((pane) => pane.widgets.length > 0 || !widgetFilter);
  const t = view.theme;
  const compact = t.density === "compact";
  const gap = compact ? "gap-3" : "gap-4";
  const initials = view.party.name
    .split(/\s+/)
    .map((s) => s[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <div className={showHero ? `space-y-5 rounded-2xl ${compact ? "p-3" : "p-5"}` : "space-y-4"} style={{ ...themeStyle(view), background: showHero ? view.theme.bg : "transparent" }}>
      {/* hero */}
      {showHero && <div
        className={`c360-rise relative overflow-hidden rounded-2xl border ${t.header_style === "compact" ? "p-4" : "p-6"}`}
        style={{ borderColor: "var(--c-border)", background: t.hero }}
      >
        <div className="pointer-events-none absolute -right-16 -top-20 h-56 w-56 rounded-full opacity-30 blur-3xl" style={{ background: t.accent }} />
        <div className="relative flex flex-wrap items-center gap-5">
          <div
            className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl text-lg font-extrabold text-white shadow-lg"
            style={{ background: `linear-gradient(135deg, ${t.accent}, color-mix(in srgb, ${t.accent} 40%, #000))` }}
          >
            {initials}
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-2xl font-extrabold tracking-tight" style={{ color: "var(--c-text)" }}>
              {view.party.name}
            </h2>
            <p className="mt-0.5 flex flex-wrap gap-x-3 text-xs" style={{ color: "var(--c-muted)" }}>
              <span className="capitalize">{view.party.party_type}</span>
              <span>#{view.party.id}</span>
              {view.party.email && <span>✉ {view.party.email}</span>}
              {view.party.phone && <span>☎ {view.party.phone}</span>}
            </p>
          </div>
          <WorkspaceHealth view={view} />
        </div>
        <WorkspaceMetrics view={view} />
      </div>}

      {/* panes */}
      <div className={`grid grid-cols-1 items-start ${gap} lg:grid-cols-6`}>
        {fill(panes).map(({ pane, span }) => (
          <details
            key={`${pane.id}-${pane.collapsed}`}
            open={!pane.collapsed}
            className={`c360-pane ${SPAN_CLASS[span] ?? WIDTH_CLASS[pane.width]}`}
            data-highlight={highlightPane === pane.id || undefined}
            style={{ ...(showHero ? cardStyle(view) : {}), "--pane-color": pane.color } as CSSProperties}
          >
            <summary className="c360-pane-head">
              <span className="c360-pane-icon" aria-hidden="true">{pane.icon}</span>
              <h3>{pane.title}</h3>
              <span className="c360-pane-toggle" aria-hidden="true">⌄</span>
            </summary>
            <div className={`c360-pane-body grid gap-3 ${innerGrid(span, pane.widgets) || INNER_GRID[pane.width]}`}>
              {pane.widgets.map((w) => (
                <div key={w.id} className={`c360-widget ${WIDE_TYPES.has(w.type) || w.span === 2 ? "col-span-full" : ""}`} data-list={WIDE_TYPES.has(w.type) || undefined}>
                  {/* a pane's only widget doesn't repeat the pane's title - its source goes in a tooltip */}
                  {pane.widgets.length > 1 ? (
                    <div className="c360-widget-head">
                      <h4>{w.title}</h4>
                      <SourcePill w={w} />
                    </div>
                  ) : w.status !== "ok" && w.status !== "empty" ? (
                    <div className="c360-widget-head"><SourcePill w={w} /></div>
                  ) : null}
                  <WidgetBody w={w} view={view} accent={pane.color || t.accent} detail={detail} onRequestIntent={onRequestIntent} onConnect={onConnect} />
                </div>
              ))}
              {pane.widgets.length === 0 && <Empty text="This pane has no widgets yet." />}
            </div>
          </details>
        ))}
      </div>
    </div>
  );
}
