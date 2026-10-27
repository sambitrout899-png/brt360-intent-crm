import { useCallback, useEffect, useState } from "react";
import { fsApi } from "../api/client";
import { useIntentRefresh } from "../workspace";
import AssetsPm from "./fieldservice/AssetsPm";
import Board from "./fieldservice/Board";
import Inventory from "./fieldservice/Inventory";
import MyJobs from "./fieldservice/MyJobs";
import Repairs from "./fieldservice/Repairs";
import Requests from "./fieldservice/Requests";
import Setup from "./fieldservice/Setup";
import type { FsOverview } from "./fieldservice/types";
import { BTN, CARD, Empty, Section, SlaBadge, Stat, Tabs } from "./fieldservice/ui";
import { STATUS, money } from "./fieldservice/style";

type View = "overview" | "requests" | "dispatch" | "jobs" | "inventory" | "assets" | "repairs" | "setup";

/**
 * Field Service - from the customer's call to the engineer's signature:
 * service requests verified against entitlements and warranties, activities
 * from job templates, a dispatch board, the engineer's job card, service
 * inventory with replenishment, the installed base with preventive
 * maintenance, the repair loop, and charges invoiced to billing.
 */
export default function FieldServicePage({ onRequestIntent }: { onRequestIntent: (text: string) => void }) {
  const [view, setView] = useState<View>("overview");
  const [o, setO] = useState<FsOverview | null>(null);
  const [openRequest, setOpenRequest] = useState<number | null>(null);
  const load = useCallback(() => {
    fsApi<FsOverview>("/overview").then(setO).catch(() => setO(null));
  }, []);
  useEffect(load, [load, view]);
  useIntentRefresh(load);

  const goRequest = (id: number) => {
    setOpenRequest(id);
    setView("requests");
  };
  const switchTo = (v: View) => {
    setOpenRequest(null);
    setView(v);
  };

  return (
    <div className="page space-y-6">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold text-slate-100">Field Service</h1>
          <p className="mt-1 hidden text-sm text-slate-400 sm:block">From the customer's call to the engineer's signature - verified, scheduled, worked, charged.</p>
        </div>
        <button type="button" onClick={() => onRequestIntent("Log & classify this complaint. description: ")} className="rounded-lg border border-sky-500/40 bg-sky-500/10 px-3 py-2 text-xs font-semibold text-sky-300 hover:bg-sky-500/20">
          + Log a call
        </button>
        <button type="button" onClick={load} className={BTN}>
          ↻ Refresh
        </button>
      </div>

      <Tabs
        value={view}
        onChange={switchTo}
        items={[
          { id: "overview", label: "Overview" },
          { id: "requests", label: "Service requests", count: o?.open_requests },
          { id: "dispatch", label: "Dispatch board", count: o?.unscheduled },
          { id: "jobs", label: "My jobs" },
          { id: "inventory", label: "Inventory", count: o?.parts_below_min },
          { id: "assets", label: "Assets & maintenance", count: o?.pm_due },
          { id: "repairs", label: "Repairs", count: o?.repairs_open },
          { id: "setup", label: "Setup" },
        ]}
      />

      {view === "overview" && <Overview o={o} go={switchTo} goRequest={goRequest} />}
      {view === "requests" && <Requests key={openRequest ?? "list"} onRequestIntent={onRequestIntent} openId={openRequest} />}
      {view === "dispatch" && <Board onRequestIntent={onRequestIntent} />}
      {view === "jobs" && <MyJobs onRequestIntent={onRequestIntent} />}
      {view === "inventory" && <Inventory />}
      {view === "assets" && <AssetsPm onOpenRequest={goRequest} />}
      {view === "repairs" && <Repairs />}
      {view === "setup" && <Setup onChanged={load} />}
    </div>
  );
}

function Overview({ o, go, goRequest }: { o: FsOverview | null; go: (v: View) => void; goRequest: (id: number) => void }) {
  if (!o) return <Empty>Loading…</Empty>;
  const flow = ["unscheduled", "scheduled", "en_route", "on_site"];
  const totalFlow = flow.reduce((n, s) => n + (o.by_status[s] ?? 0), 0);
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Open requests" value={o.open_requests} sub={`${o.unverified} not verified`} onClick={() => go("requests")} />
        <Stat label="SLA at risk" value={o.breached + o.at_risk} sub={`${o.breached} breached · ${o.at_risk} within 2h`} tone={o.breached ? "text-red-300" : o.at_risk ? "text-amber-300" : "text-emerald-300"} onClick={() => go("requests")} />
        <Stat label="Jobs today" value={o.today} sub={`${o.in_progress} in progress · ${o.done_today} done`} tone="text-sky-300" onClick={() => go("dispatch")} />
        <Stat label="First-time fix" value={o.first_time_fix === null ? "—" : `${o.first_time_fix}%`} sub="resolved in one visit" tone="text-emerald-300" />
      </div>

      <div className="grid items-stretch gap-4 lg:grid-cols-2">
        <div className={`${CARD} flex h-full flex-col p-4`}>
          <div className="mb-3 flex items-center gap-2">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">Open work</h3>
            <span className="flex-1" />
            <button type="button" className={BTN} onClick={() => go("dispatch")}>
              Dispatch board →
            </button>
          </div>
          <div className="flex h-3 overflow-hidden rounded-full bg-slate-800">
            {flow.map((s) => (o.by_status[s] ? <span key={s} style={{ width: `${(100 * o.by_status[s]) / Math.max(1, totalFlow)}%`, background: STATUS[s].dot }} /> : null))}
          </div>
          <div className="mt-4 grid flex-1 grid-cols-2 gap-3 sm:grid-cols-4">
            {flow.map((s) => (
              <div key={s} className="rounded-xl border border-slate-800 bg-slate-900/40 px-3 py-2">
                <p className="flex items-center gap-1.5 text-[11px] text-slate-400">
                  <span className="h-2 w-2 rounded-full" style={{ background: STATUS[s].dot }} />
                  {STATUS[s].label}
                </p>
                <p className="mt-1 text-xl font-bold text-slate-100">{o.by_status[s] ?? 0}</p>
              </div>
            ))}
          </div>
        </div>

        <div className={`${CARD} flex h-full flex-col p-4`}>
          <h3 className="mb-3 text-xs font-bold uppercase tracking-wider text-slate-400">Around the operation</h3>
          <div className="grid flex-1 grid-cols-2 gap-3">
            <Tile label="Maintenance due (7 days)" value={o.pm_due} tone={o.pm_due ? "text-violet-300" : undefined} onClick={() => go("assets")} />
            <Tile label="Parts below minimum" value={o.parts_below_min} tone={o.parts_below_min ? "text-amber-300" : undefined} onClick={() => go("inventory")} />
            <Tile label="Repairs in the loop" value={o.repairs_open} onClick={() => go("repairs")} />
            <Tile label="Charges to invoice" value={money(o.pending_charges)} tone={o.pending_charges ? "text-sky-300" : undefined} onClick={() => go("requests")} />
          </div>
        </div>
      </div>

      <Section title="Needs attention" count={o.attention.length}>
        {o.attention.length === 0 ? (
          <Empty>Nothing is late or unverified. 👌</Empty>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {o.attention.map((r) => (
              <button key={r.id} type="button" onClick={() => goRequest(r.id)} className={`${CARD} flex h-full flex-col p-4 text-left transition hover:border-slate-600`}>
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs text-slate-500">SR {r.id}</span>
                  <span className="flex-1" />
                  <SlaBadge sla={r.verified ? r.sla : "none"} committed={r.committed_at} />
                </div>
                <p className="mt-2 line-clamp-2 text-sm font-semibold text-slate-100">{r.description}</p>
                <p className="mt-auto truncate pt-2 text-xs text-slate-400">{r.party_name ?? "No customer"}</p>
              </button>
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}

function Tile({ label, value, tone, onClick }: { label: string; value: string | number; tone?: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className="rounded-xl border border-slate-800 bg-slate-900/40 px-3 py-2 text-left transition hover:border-slate-600">
      <p className="truncate text-[11px] text-slate-400">{label}</p>
      <p className={`mt-1 text-xl font-bold ${tone ?? "text-slate-100"}`}>{value}</p>
    </button>
  );
}
