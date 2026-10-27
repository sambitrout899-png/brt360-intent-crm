import { useCallback, useEffect, useState } from "react";
import { conflictsList } from "../api/client";
import type { Conflict } from "../api/conflictTypes";
import ConflictsPanel from "../components/intent/ConflictsPanel";

/** Admin → Conflicts: every automation and action in the set-up that pulls
 * against another - and the priority and conditions that settle it. The same
 * scan runs on every rehearsal and before every run of a plan. */
export default function AdminConflictsSection({ onOpenFlowStudio }: { onOpenFlowStudio?: () => void }) {
  const [conflicts, setConflicts] = useState<Conflict[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    conflictsList()
      .then((out) => {
        setError("");
        setConflicts(out.conflicts);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not scan"));
  }, []);
  useEffect(load, [load]);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-lg font-bold text-slate-100">⚖️ Conflicts</h2>
          <p className="text-sm text-slate-400">
            Automations on the same trigger that set a field differently, move a record to different stages, hand it to different people or run
            the same action twice - and custom actions that answer to the same phrase. Set the priority, whether all of them run or only the
            first that applies, and when each should run. Rehearsals and plans show the conflicts they touch.
          </p>
        </div>
        <button type="button" onClick={load} className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:border-violet-500">
          ↻ Scan again
        </button>
        {onOpenFlowStudio && (
          <button type="button" onClick={onOpenFlowStudio} className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:border-violet-500">
            Open Flow Studio →
          </button>
        )}
      </div>
      {error && <p className="text-sm text-red-400">{error}</p>}
      {conflicts === null && !error && <p className="animate-pulse text-sm text-slate-400">Scanning your automations and actions…</p>}
      {conflicts !== null && conflicts.length === 0 && (
        <p className="rounded-xl border border-emerald-500/30 bg-emerald-500/[0.06] px-4 py-3 text-sm text-emerald-200">
          ✓ Nothing pulls against anything else - every trigger has automations that agree, and every phrase reaches one action.
        </p>
      )}
      {conflicts !== null && conflicts.length > 0 && (
        <ConflictsPanel
          conflicts={conflicts}
          canSettle
          title={`${conflicts.length} found`}
          onChanged={(fresh) => (fresh ? setConflicts(fresh) : load())}
        />
      )}
    </div>
  );
}
