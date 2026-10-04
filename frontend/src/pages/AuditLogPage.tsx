import { useEffect, useState } from "react";
import { auditDetail, listAudit } from "../api/client";
import type { AuditLogDetail, AuditLogEntry } from "../api/types";

export default function AuditLogPage() {
  const [entries, setEntries] = useState<AuditLogEntry[]>([]);
  const [selected, setSelected] = useState<AuditLogDetail | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    try {
      setEntries(await listAudit());
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function openDetail(id: number) {
    setSelected(await auditDetail(id));
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Intent Execution Log</h1>
          <p className="mt-1 text-sm text-slate-400">
            Every intent, context, plan and outcome — the Learning & Feedback Loop's source of truth.
          </p>
        </div>
        <button
          onClick={load}
          className="rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2 text-xs font-bold text-slate-300 hover:border-sky-500 hover:text-sky-400"
        >
          Refresh
        </button>
      </div>

      {loading && <p className="text-sm text-slate-500">Loading…</p>}

      <div className="grid gap-6 lg:grid-cols-[1fr_1.1fr]">
        <div className="space-y-2">
          {entries.map((e) => (
            <button
              key={e.id}
              onClick={() => openDetail(e.id)}
              className={`block w-full rounded-lg border px-3 py-2.5 text-left text-sm transition ${
                selected?.id === e.id
                  ? "border-sky-500 bg-sky-500/10"
                  : "border-slate-700 bg-slate-900/60 hover:border-slate-600"
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs text-slate-500">#{e.id}</span>
                <span className="font-mono text-xs text-slate-300">{e.intent_type}</span>
                <span className="ml-auto text-xs text-slate-500">{e.status}</span>
              </div>
              <p className="mt-1 truncate text-slate-400">{e.raw_text}</p>
            </button>
          ))}
          {!loading && entries.length === 0 && <p className="text-sm text-slate-500">No executions yet.</p>}
        </div>

        <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-4">
          {!selected && <p className="text-sm text-slate-500">Select an execution to see its full trace.</p>}
          {selected && (
            <div className="space-y-4">
              <div>
                <h3 className="mb-1 text-xs font-bold uppercase tracking-wide text-slate-500">Rationale</h3>
                <p className="text-sm text-slate-300">{selected.rationale}</p>
              </div>
              <div>
                <h3 className="mb-1 text-xs font-bold uppercase tracking-wide text-slate-500">Entities</h3>
                <pre className="overflow-x-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-300">
                  {JSON.stringify(JSON.parse(selected.entities_json || "{}"), null, 2)}
                </pre>
              </div>
              <div>
                <h3 className="mb-1 text-xs font-bold uppercase tracking-wide text-slate-500">Context</h3>
                <pre className="overflow-x-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-300">
                  {JSON.stringify(JSON.parse(selected.context_json || "{}"), null, 2)}
                </pre>
              </div>
              <div>
                <h3 className="mb-1 text-xs font-bold uppercase tracking-wide text-slate-500">Plan</h3>
                <pre className="overflow-x-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-300">
                  {JSON.stringify(JSON.parse(selected.plan_json || "[]"), null, 2)}
                </pre>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
