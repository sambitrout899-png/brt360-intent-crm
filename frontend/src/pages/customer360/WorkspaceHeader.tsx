import type { C360View } from "../../api/c360Types";
import { formatValue } from "./format";
import "./workspaceHeader.css";

export function WorkspaceHealth({ view }: { view: C360View | null }) {
  const health = view?.header.health;
  if (!health || view?.layout.header.show_health === false) return null;
  // the colour follows the verdict, so "Watch" never shows green
  const color = health.label === "Healthy" ? "#34d399" : health.label === "Watch" ? "#fbbf24" : "#fb7185";
  return <div className="workspace-health" title={health.reasons.join(", ") || "No account risks identified"}>
    <svg viewBox="0 0 44 44" aria-hidden="true"><circle cx="22" cy="22" r="18" fill="none" stroke="color-mix(in srgb,var(--c-muted) 20%,transparent)" strokeWidth="4" /><circle cx="22" cy="22" r="18" fill="none" stroke={color} strokeWidth="4" strokeLinecap="round" strokeDasharray={`${health.score * 1.13} 113`} transform="rotate(-90 22 22)" /></svg>
    <div><strong style={{ color }}>{health.score}<small>/100</small></strong><span>Account health · {health.label}</span></div>
  </div>;
}

export function WorkspaceMetrics({ view }: { view: C360View }) {
  if (!view.header.kpis.length) return null;
  return <section className="workspace-metrics" aria-label="Published account metrics">
    {view.header.kpis.map((k, i) => <div key={k.id ?? i} className="workspace-metric" data-metric={k.recipe ?? k.id} data-domain={k.domain}
      title={k.source_label ? `${k.title} - from ${k.source_label}` : k.title}>
      <span className="workspace-metric-label">{k.title}</span>
      <strong>{k.status === "ok" || k.status === "empty" ? formatValue(k.data?.kind === "value" ? k.data.value : 0, k.format, true, k.metric.unit as string | undefined) : "—"}</strong>
      {k.status !== "ok" && k.status !== "empty" && k.message && <small>{k.message}</small>}
    </div>)}
  </section>;
}
