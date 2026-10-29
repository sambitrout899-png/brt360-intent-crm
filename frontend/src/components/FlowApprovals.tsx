import { useCallback, useEffect, useState } from "react";
import { decideFlowApproval, myFlowApprovals, type FlowRunView } from "../api/client";
import { problem } from "../api/customRecords";
import { ENTITY_LABELS } from "../api/types";
import type { EntityType } from "../api/types";
import { notifyIntentExecuted, useIntentRefresh, useWorkspace } from "../workspace";

/** My Work: what flows are waiting on me to approve (Flow Studio: Wait for
 * approval). Deciding continues the flow at once, down that answer. */
export default function FlowApprovals() {
  const { openRecord } = useWorkspace();
  const [runs, setRuns] = useState<FlowRunView[]>([]);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [result, setResult] = useState<Record<number, { ok: boolean; text: string }>>({});

  const load = useCallback(() => {
    myFlowApprovals().then(setRuns).catch(() => setRuns([]));
  }, []);
  useEffect(load, [load]);
  useIntentRefresh(load);

  async function decide(run: FlowRunView, approve: boolean) {
    setBusy(run.id);
    try {
      const done = await decideFlowApproval(run.id, approve, notes[run.id] ?? "");
      const ended = done.status === "failed" ? `, but the flow then failed: ${done.error?.message ?? "see Flow Studio"}` : "";
      setResult((r) => ({ ...r, [run.id]: { ok: done.status !== "failed", text: `${approve ? "Approved" : "Rejected"}${ended}` } }));
      notifyIntentExecuted(); // whatever the flow did next shows up elsewhere too
      load();
    } catch (e) {
      setResult((r) => ({ ...r, [run.id]: { ok: false, text: problem(e, "Couldn't record the decision.") } }));
    } finally {
      setBusy(null);
    }
  }

  const decided = Object.keys(result).map(Number).filter((id) => !runs.some((r) => r.id === id));
  if (runs.length === 0 && decided.length === 0) return null;
  return (
    <section className="space-y-2">
      <h2 className="text-xs font-bold uppercase tracking-wide text-fuchsia-400">Flows waiting for your approval</h2>
      {runs.map((run) => {
        const record = run.entity_type && run.entity_id && run.entity_type in ENTITY_LABELS ? (run.entity_type as EntityType) : null;
        return (
          <div key={run.id} className="space-y-2 rounded-lg border border-fuchsia-500/30 bg-fuchsia-500/5 px-4 py-3 text-sm">
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-semibold text-slate-100">{run.subject}</span>
              <span className="text-xs text-slate-500">{run.flow_name}</span>
              {record && run.entity_id && (
                <button onClick={() => openRecord(record, run.entity_id!)} className="text-xs text-sky-400 hover:underline">
                  {ENTITY_LABELS[record]} #{run.entity_id}
                </button>
              )}
              {run.wait.kind === "approval" && run.wait.expires && (
                <span className="text-xs text-slate-500">expires {new Date(`${run.wait.expires}Z`).toLocaleString()}</span>
              )}
            </div>
            {run.message && <p className="text-xs text-slate-300">{run.message}</p>}
            <div className="flex flex-wrap items-center gap-2">
              <input value={notes[run.id] ?? ""} onChange={(e) => setNotes((n) => ({ ...n, [run.id]: e.target.value }))} placeholder="Note (optional)"
                className="min-w-[12rem] flex-1 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200" />
              <button disabled={busy === run.id} onClick={() => decide(run, true)}
                className="rounded-md bg-emerald-600 px-3 py-1 text-xs font-bold text-white hover:bg-emerald-500 disabled:opacity-50">{busy === run.id ? "…" : "Approve"}</button>
              <button disabled={busy === run.id} onClick={() => decide(run, false)}
                className="rounded-md border border-red-500/50 px-3 py-1 text-xs font-bold text-red-300 hover:bg-red-500/10 disabled:opacity-50">Reject</button>
            </div>
          </div>
        );
      })}
      {decided.map((id) => (
        <p key={id} className={`text-xs ${result[id].ok ? "text-emerald-400" : "text-red-400"}`}>{result[id].text}</p>
      ))}
    </section>
  );
}
