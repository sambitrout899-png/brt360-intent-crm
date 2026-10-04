import { useEffect, useState } from "react";
import { listTechnicians, listTickets, listWorkOrders } from "../api/client";
import type { Technician, TroubleTicket, WorkOrder } from "../api/types";

const SEVERITY_STYLES: Record<string, string> = {
  critical: "bg-red-500/15 text-red-400 border-red-500/30",
  high: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  medium: "bg-sky-500/15 text-sky-400 border-sky-500/30",
  low: "bg-slate-500/15 text-slate-400 border-slate-500/30",
};

export default function TicketsPage({ onRequestIntent }: { onRequestIntent: (text: string) => void }) {
  const [tickets, setTickets] = useState<TroubleTicket[]>([]);
  const [workOrders, setWorkOrders] = useState<WorkOrder[]>([]);
  const [technicians, setTechnicians] = useState<Technician[]>([]);
  const [tab, setTab] = useState<"tickets" | "field">("tickets");

  function load() {
    listTickets().then(setTickets);
    listWorkOrders().then(setWorkOrders);
    listTechnicians().then(setTechnicians);
  }
  useEffect(load, []);

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Trouble Tickets & Field Service</h1>
          <p className="mt-1 text-sm text-slate-400">Trouble-to-Resolve: complaints, diagnosis, and the technicians dispatched to fix them.</p>
        </div>
        <button onClick={load} className="rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2 text-xs font-bold text-slate-300 hover:border-sky-500 hover:text-sky-400">
          Refresh
        </button>
      </div>

      <div className="flex gap-2">
        <button onClick={() => setTab("tickets")} className={`rounded-lg px-3 py-1.5 text-sm font-semibold ${tab === "tickets" ? "bg-sky-600 text-white" : "bg-slate-900/60 text-slate-400"}`}>
          Tickets
        </button>
        <button onClick={() => setTab("field")} className={`rounded-lg px-3 py-1.5 text-sm font-semibold ${tab === "field" ? "bg-sky-600 text-white" : "bg-slate-900/60 text-slate-400"}`}>
          Field Service
        </button>
      </div>

      {tab === "tickets" && (
        <div className="space-y-3">
          {tickets.map((t) => (
            <div key={t.id} className="rounded-xl border border-slate-700 bg-slate-900/60 p-4">
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-mono text-xs text-slate-500">#{t.id}</span>
                <span className={`rounded-md border px-2 py-0.5 text-xs font-bold ${SEVERITY_STYLES[t.severity]}`}>{t.severity}</span>
                <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{t.priority}</span>
                <span className="text-xs text-slate-500">{t.sla_class} SLA</span>
                <span className="text-xs text-slate-500">{t.status}</span>
                <div className="ml-auto flex gap-2">
                  {!t.root_cause && (
                    <button
                      onClick={() => onRequestIntent(`Diagnose the root cause. ticket_id: ${t.id}`)}
                      className="rounded-md border border-sky-500/40 bg-sky-500/10 px-2.5 py-1 text-xs font-bold text-sky-400 hover:bg-sky-500/20"
                    >
                      Diagnose
                    </button>
                  )}
                  {t.root_cause?.field_dispatch_required && (
                    <button
                      onClick={() => onRequestIntent(`Dispatch a technician. ticket_id: ${t.id}, required_skill: fiber_install, region: `)}
                      className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2.5 py-1 text-xs font-bold text-emerald-400 hover:bg-emerald-500/20"
                    >
                      Dispatch
                    </button>
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
          ))}
          {tickets.length === 0 && <p className="text-sm text-slate-500">No tickets yet.</p>}
        </div>
      )}

      {tab === "field" && (
        <div className="space-y-6">
          <div>
            <h2 className="mb-3 text-lg font-bold text-slate-100">Work Orders</h2>
            <div className="space-y-2">
              {workOrders.map((w) => (
                <div key={w.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-700 bg-slate-900/60 px-4 py-3 text-sm">
                  <span className="font-mono text-xs text-slate-500">#{w.id}</span>
                  <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{w.status}</span>
                  <span className="text-xs text-slate-500">{w.scheduled_window}</span>
                  {w.technician_name && <span className="text-slate-300">Tech: {w.technician_name}</span>}
                  {w.status !== "complete" && (
                    <button
                      onClick={() => onRequestIntent(`Complete the work order. work_order_id: ${w.id}`)}
                      className="ml-auto rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2.5 py-1 text-xs font-bold text-emerald-400 hover:bg-emerald-500/20"
                    >
                      Mark Complete
                    </button>
                  )}
                </div>
              ))}
              {workOrders.length === 0 && <p className="text-sm text-slate-500">No work orders yet.</p>}
            </div>
          </div>

          <div>
            <h2 className="mb-3 text-lg font-bold text-slate-100">Technician Roster</h2>
            <div className="grid gap-2 md:grid-cols-3">
              {technicians.map((t) => (
                <div key={t.id} className="rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-sm">
                  <div className="font-semibold text-slate-200">{t.name}</div>
                  <div className="text-xs text-slate-500">{t.skills} · {t.region}</div>
                  <span className={`mt-1 inline-block rounded-md px-2 py-0.5 text-xs ${t.status === "available" ? "bg-emerald-500/15 text-emerald-400" : "bg-slate-700 text-slate-400"}`}>
                    {t.status}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
