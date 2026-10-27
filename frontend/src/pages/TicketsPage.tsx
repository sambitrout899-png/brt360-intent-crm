import { useEffect, useState } from "react";
import { commsApi, listTickets } from "../api/client";
import type { TroubleTicket } from "../api/types";
import { useIntentRefresh, useWorkspace } from "../workspace";
import type { CommsOverview, TicketDetail } from "./comms/types";

const SEVERITY_STYLES: Record<string, string> = {
  critical: "bg-red-500/15 text-red-400 border-red-500/30",
  high: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  medium: "bg-sky-500/15 text-sky-400 border-sky-500/30",
  low: "bg-slate-500/15 text-slate-400 border-slate-500/30",
};

const ACTION = "rounded-md border px-2.5 py-1 text-xs font-bold";
const ACTION_SKY = `${ACTION} border-sky-500/40 bg-sky-500/10 text-sky-400 hover:bg-sky-500/20`;
const ACTION_SLATE = `${ACTION} border-slate-700 text-slate-300 hover:border-sky-500 hover:text-sky-300`;

/** Trouble tickets: complaints and their diagnosis, network tickets with
 * the customer tickets they caused, order fallout, resolution and surveys.
 * Field work - engineers, work orders, scheduling and dispatch - lives in
 * the Field Service module. */
export default function TicketsPage({ onRequestIntent, onOpenFieldService }: { onRequestIntent: (text: string) => void; onOpenFieldService?: () => void }) {
  const { runIntent, canRun } = useWorkspace();
  const [tickets, setTickets] = useState<TroubleTicket[]>([]);
  const [details, setDetails] = useState<Record<number, TicketDetail>>({});
  const [stats, setStats] = useState<CommsOverview["tickets"] | null>(null);
  const [filter, setFilter] = useState<"all" | "open" | "network" | "fallout">("all");

  function load() {
    listTickets().then(setTickets);
    commsApi<Record<number, TicketDetail>>("/tickets").then(setDetails).catch(() => setDetails({}));
    commsApi<CommsOverview["tickets"]>("/tickets/analysis").then(setStats).catch(() => setStats(null));
  }
  useEffect(load, []);
  useIntentRefresh(load);

  const shown = tickets.filter((t) => {
    const d = details[t.id];
    if (filter === "open") return t.status !== "resolved";
    if (filter === "network") return d?.origin === "network" || (d?.children ?? 0) > 0;
    if (filter === "fallout") return d?.origin === "fallout";
    return true;
  });

  return (
    <div className="page space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold text-slate-100">Trouble Tickets</h1>
          <p className="mt-1 text-sm text-slate-400">Complaints and their diagnosis, network outages, order fallout. Engineers, scheduling and dispatch are in Field Service.</p>
        </div>
        {canRun("INT-98") ? <button onClick={() => runIntent("INT-98")} className="rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2 text-xs font-bold text-slate-300 hover:border-sky-500 hover:text-sky-400">
          Log order fallout
        </button> : null}
        {onOpenFieldService && (
          <button onClick={onOpenFieldService} className="rounded-lg border border-sky-500/40 bg-sky-500/10 px-3 py-2 text-xs font-bold text-sky-300 hover:bg-sky-500/20">
            Field Service →
          </button>
        )}
        <button onClick={load} className="rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2 text-xs font-bold text-slate-300 hover:border-sky-500 hover:text-sky-400">
          Refresh
        </button>
      </div>

      {stats && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            ["Last 30 days", String(stats.total), `${stats.today} today`],
            ["Open", String(stats.open), `${stats.network_parents} network ticket(s)`],
            ["Time to resolve", stats.mean_hours_to_resolve !== null ? `${stats.mean_hours_to_resolve} h` : "—", "average"],
            ["Satisfaction", stats.satisfaction !== null ? `${stats.satisfaction}/100` : "—", `${stats.fallout_open} fallout open`],
          ].map(([label, value, sub]) => (
            <div key={label} className="rounded-2xl border border-slate-800 bg-slate-900/60 px-4 py-3">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">{label}</p>
              <p className="mt-1 text-xl font-bold text-slate-100">{value}</p>
              <p className="text-[11px] text-slate-500">{sub}</p>
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {(["all", "open", "network", "fallout"] as const).map((f) => (
          <button key={f} onClick={() => setFilter(f)}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${filter === f ? "bg-sky-600 text-white" : "bg-slate-900/60 text-slate-400 hover:text-slate-200"}`}>
            {{ all: "All", open: "Open", network: "Network & outages", fallout: "Order fallout" }[f]}
          </button>
        ))}
      </div>

      <div className="space-y-3">
        {shown.map((t) => {
          const d = details[t.id];
          const closed = t.status === "resolved";
          return (
            <div key={t.id} className={`rounded-xl border border-slate-700 bg-slate-900/60 p-4 ${closed ? "opacity-80" : ""}`}>
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-mono text-xs text-slate-500">{d?.number ?? `#${t.id}`}</span>
                <span className={`rounded-md border px-2 py-0.5 text-xs font-bold ${SEVERITY_STYLES[t.severity]}`}>{t.severity}</span>
                <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{t.priority}</span>
                <span className="text-xs text-slate-500">{t.sla_class} SLA</span>
                <span className="text-xs text-slate-500">{t.status}{d?.sub_status ? ` · ${d.sub_status.replace("_", " ")}` : ""}</span>
                {d?.origin === "network" && <span className="rounded-md bg-violet-500/15 px-2 py-0.5 text-xs text-violet-300">network{d.service_item ? ` · ${d.service_item}` : ""}</span>}
                {d?.origin === "fallout" && <span className="rounded-md bg-red-500/15 px-2 py-0.5 text-xs text-red-300">fallout · order #{d.order_id}{d.error_code ? ` · ${d.error_code}` : ""}</span>}
                {(d?.children ?? 0) > 0 && <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{d!.children} child ticket(s)</span>}
                {d?.parent_ticket_id && <span className="text-xs text-slate-500">under #{d.parent_ticket_id}</span>}
                {d?.group && <span className="text-xs text-slate-500">{d.group}</span>}
                <div className="ml-auto flex flex-wrap gap-2">
                  {!t.root_cause && (
                    <button onClick={() => onRequestIntent(`Diagnose the root cause. ticket_id: ${t.id}`)} className={ACTION_SKY}>
                      Diagnose
                    </button>
                  )}
                  {t.root_cause?.field_dispatch_required && onOpenFieldService && (
                    <button onClick={onOpenFieldService} className={`${ACTION} border-emerald-500/40 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20`}>
                      Schedule in Field Service
                    </button>
                  )}
                  {!closed && (
                    <>
                      {canRun("INT-99") ? <button onClick={() => runIntent("INT-99", { ticket_id: t.id })} className={ACTION_SLATE}>
                        Solutions
                      </button> : null}
                      {canRun("INT-95") ? <button onClick={() => runIntent("INT-95", { parent_ticket_id: t.id })} className={ACTION_SLATE} title="Make this the parent of other tickets (an outage)">
                        Link children
                      </button> : null}
                      {canRun("INT-96") ? <button onClick={() => runIntent("INT-96", { ticket_id: t.id, ticket_action: "resolve" })} className={ACTION_SKY}>
                        Resolve
                      </button> : null}
                    </>
                  )}
                  {closed && (
                    <>
                      {canRun("INT-97") ? <button onClick={() => runIntent("INT-97", { ticket_id: t.id })} className={ACTION_SLATE}>
                        Survey
                      </button> : null}
                      {canRun("INT-96") ? <button onClick={() => runIntent("INT-96", { ticket_id: t.id, ticket_action: "reopen" })} className={ACTION_SLATE}>
                        Reopen
                      </button> : null}
                    </>
                  )}
                </div>
              </div>
              <p className="mt-2 text-sm text-slate-300">{t.description}</p>
              {t.root_cause && (
                <div className="mt-2 rounded-lg bg-slate-950 px-3 py-2 text-xs text-slate-400">
                  <span className="font-semibold text-slate-300">Root cause:</span> {t.root_cause.probable_cause} (confidence {t.root_cause.confidence})
                  {t.root_cause.escalated_to_human && <span className="ml-2 text-amber-400">⚠ escalated to human</span>}
                </div>
              )}
            </div>
          );
        })}
        {shown.length === 0 && <p className="text-sm text-slate-500">No tickets here.</p>}
      </div>
    </div>
  );
}
