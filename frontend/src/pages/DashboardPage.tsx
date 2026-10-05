import { useEffect, useState } from "react";
import { dashboardSummary } from "../api/client";
import type { DashboardSummary } from "../api/types";

function Tile({ label, value, accent }: { label: string; value: string | number; accent?: string }) {
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-4">
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-2 text-3xl font-bold ${accent ?? "text-slate-100"}`}>{value}</div>
    </div>
  );
}

export default function DashboardPage() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    dashboardSummary()
      .then(setSummary)
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p className="text-sm text-slate-500">Loading…</p>;
  if (!summary) return <p className="text-sm text-red-400">Could not load dashboard.</p>;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">Dashboard</h1>
        <p className="mt-1 text-sm text-slate-400">Live KPIs across the whole Intent CRM — no manual refresh needed to trust these numbers, they're read straight from the system of record.</p>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Tile label="Customers" value={summary.total_parties} />
        <Tile label="Open Opportunities" value={summary.open_opportunities} />
        <Tile label="Pipeline Value" value={`₹${summary.pipeline_value.toLocaleString()}`} accent="text-sky-400" />
        <Tile label="Pending Approvals" value={summary.pending_approvals} accent={summary.pending_approvals > 0 ? "text-amber-400" : undefined} />
        <Tile
          label="Leads Awaiting Qualification"
          value={summary.pending_lead_qualifications}
          accent={summary.pending_lead_qualifications > 0 ? "text-amber-400" : undefined}
        />
        <Tile label="Active Orders" value={summary.active_orders} />
        <Tile label="Monthly Recurring Revenue" value={`₹${summary.monthly_recurring_revenue.toLocaleString()}`} accent="text-emerald-400" />
        <Tile label="Open Work Orders" value={summary.open_work_orders} />
      </div>

      <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-4">
        <h2 className="mb-3 font-bold text-slate-100">Open Tickets by Severity</h2>
        <div className="grid grid-cols-4 gap-3">
          {Object.entries(summary.open_tickets_by_severity).map(([severity, count]) => (
            <div key={severity} className="rounded-lg bg-slate-950 p-3 text-center">
              <div
                className={`text-2xl font-bold ${
                  severity === "critical" ? "text-red-400" : severity === "high" ? "text-amber-400" : "text-slate-200"
                }`}
              >
                {count}
              </div>
              <div className="text-xs capitalize text-slate-500">{severity}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
