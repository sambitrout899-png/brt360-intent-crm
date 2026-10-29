import { useEffect, useState } from "react";
import { request } from "../api/client";

interface Delivery {
  id: number;
  at: string;
  channel: string;
  audience: string;
  recipients: number;
  status: string; // sent | in_app | skipped | no_recipients | no_provider | failed
  detail: string;
}

const TONE: Record<string, string> = {
  sent: "text-emerald-400", in_app: "text-sky-400", skipped: "text-slate-400",
  no_recipients: "text-amber-400", no_provider: "text-amber-400", failed: "text-red-400",
};
const STATUSES = ["", "sent", "failed", "skipped", "no_recipients", "no_provider", "in_app"];

/** Admin: what became of each notification flows and actions recorded - delivered
 * by the notification service (in-app; email once NOTIFY_EMAIL=smtp is set on the server). */
export default function NotificationLog() {
  const [status, setStatus] = useState("");
  const [rows, setRows] = useState<Delivery[] | null>(null);

  useEffect(() => {
    request<Delivery[]>(`/api/admin/notification-deliveries${status ? `?status=${status}` : ""}`).then(setRows).catch(() => setRows([]));
  }, [status]);

  return (
    <section className="mt-8 space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="text-sm font-semibold text-slate-200">Notification delivery log</h3>
        <select value={status} onChange={(e) => setStatus(e.target.value)} className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200">
          {STATUSES.map((s) => <option key={s} value={s}>{s ? s.replace("_", " ") : "all"}</option>)}
        </select>
      </div>
      <p className="text-xs text-slate-500">
        What each Notify step and action sent: shown in the app, emailed, or why not. Email goes out once it is switched on for the server.
      </p>
      {rows === null && <p className="text-xs text-slate-500">Loading…</p>}
      {rows?.length === 0 && <p className="text-xs text-slate-500">Nothing yet.</p>}
      <div className="space-y-1">
        {rows?.map((r) => (
          <div key={r.id} className="flex flex-wrap gap-x-3 gap-y-1 rounded-md border border-slate-800 px-3 py-2 text-xs">
            <span className="text-slate-500">{new Date(r.at.endsWith("Z") ? r.at : `${r.at}Z`).toLocaleString()}</span>
            <span className="font-semibold text-slate-300">{r.channel}</span>
            <span className={TONE[r.status] ?? "text-slate-300"}>{r.status.replace("_", " ")}</span>
            <span className="text-slate-400">to {r.audience || "-"}{r.recipients ? ` (${r.recipients})` : ""}</span>
            {r.detail && <span className="w-full text-slate-500">{r.detail}</span>}
          </div>
        ))}
      </div>
    </section>
  );
}
