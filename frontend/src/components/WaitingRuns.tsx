import { useEffect, useState } from "react";
import { cancelFlowRun, listFlowRuns, type FlowRunView } from "../api/client";
import { problem } from "../api/customRecords";

const STATUSES: [string, string][] = [["waiting", "Waiting"], ["failed", "Failed after a wait"], ["cancelled", "Cancelled"]];

function when(iso: string | null): string {
  return iso ? new Date(iso.endsWith("Z") ? iso : `${iso}Z`).toLocaleString() : "";
}

/** Flow Studio: runs paused at a Wait or Wait for approval step - what they wait
 * for, and cancel; and runs whose rest failed after a wait, with the error. */
export default function WaitingRuns() {
  const [status, setStatus] = useState("waiting");
  const [runs, setRuns] = useState<FlowRunView[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listFlowRuns(status).then(setRuns).catch(() => setRuns([]));
  }, [status]);

  async function cancel(run: FlowRunView) {
    setError(null);
    try {
      await cancelFlowRun(run.id);
      setRuns((rs) => (rs ?? []).filter((r) => r.id !== run.id));
    } catch (e) {
      setError(problem(e, "Couldn't cancel the run."));
    }
  }

  if (runs === null) return null;
  return (
    <details className="rounded-xl border border-slate-700 bg-slate-900/60 p-4 text-sm" open={status !== "waiting" || runs.length > 0}>
      <summary className="cursor-pointer font-semibold text-slate-200">
        Paused flow runs {status === "waiting" && <span className="text-slate-400">({runs.length} waiting)</span>}
      </summary>
      <div className="mt-3 flex gap-2">
        {STATUSES.map(([key, label]) => (
          <button key={key} type="button" onClick={() => setStatus(key)}
            className={`rounded-md px-2.5 py-1 text-xs font-bold ${status === key ? "bg-sky-600 text-white" : "border border-slate-700 text-slate-300"}`}>
            {label}
          </button>
        ))}
      </div>
      {error && <p className="mt-2 text-xs text-red-400">{error}</p>}
      <div className="mt-3 space-y-2">
        {runs.length === 0 && <p className="text-xs text-slate-500">None.</p>}
        {runs.map((run) => (
          <div key={run.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-800 px-3 py-2 text-xs">
            <span className="font-semibold text-slate-200">{run.flow_name}</span>
            {run.entity_type && <span className="text-slate-500">{run.entity_type} #{run.entity_id}</span>}
            <span className="text-slate-400">
              {run.wait.kind === "timer"
                ? `waits until ${when(run.wait.until)}`
                : `waits for ${run.wait.approver}${run.wait.expires ? ` (expires ${when(run.wait.expires)})` : ""}`}
            </span>
            {run.error && <span className="text-red-400">{run.error.code}: {run.error.message}</span>}
            <span className="text-slate-600">since {when(run.created_at)}</span>
            {run.status === "waiting" && (
              <button type="button" onClick={() => cancel(run)} className="ml-auto rounded-md border border-slate-700 px-2 py-0.5 font-bold text-slate-300 hover:border-red-500 hover:text-red-400">
                Cancel
              </button>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}
