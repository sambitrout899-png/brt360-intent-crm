import { useState } from "react";
import { submitIntent } from "../api/client";
import type { IntentResponse } from "../api/types";

const EXAMPLES = [
  {
    label: "INT-01 · Launch",
    text: "Launch a new bundle. name: Fiber 500 Bundle, composition: Fiber500+VOIP, segment: B2C, channel: B2C, price: 999, discount: 10",
  },
  {
    label: "INT-02 · Modify",
    text: "Modify this bundle. offering_name: Fiber 500 Bundle, new_price: 849",
  },
  {
    label: "INT-03 · Retire",
    text: "Retire this bundle. offering_name: Fiber 500 Bundle, replacement_offering_name: Fiber 1000 Elite",
  },
];

const STATUS_STYLES: Record<string, string> = {
  executed: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  needs_review: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  failed: "bg-red-500/15 text-red-400 border-red-500/30",
  verification_failed: "bg-red-500/15 text-red-400 border-red-500/30",
};

export default function IntentCommandBar() {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<IntentResponse | null>(null);
  const [error, setError] = useState("");

  async function handleSubmit() {
    if (!text.trim()) return;
    setLoading(true);
    setError("");
    try {
      const result = await submitIntent(text);
      setResponse(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">Intent Command Bar</h1>
        <p className="mt-1 text-sm text-slate-400">
          State the outcome in free text. The Intent Engine classifies it, assembles context, reasons over a
          plan, and the Orchestrator executes it — or routes it to you for review.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        {EXAMPLES.map((ex) => (
          <button
            key={ex.label}
            onClick={() => setText(ex.text)}
            className="rounded-full border border-slate-700 bg-slate-800/60 px-3 py-1.5 text-xs font-semibold text-slate-300 hover:border-sky-500 hover:text-sky-400"
          >
            {ex.label}
          </button>
        ))}
      </div>

      <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-4">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          placeholder="Launch a new bundle. name: ..., composition: ..., segment: ..., price: ..."
          className="w-full resize-none rounded-lg border border-slate-700 bg-slate-950 px-3 py-3 text-sm text-slate-100 outline-none focus:border-sky-500"
        />
        <div className="mt-3 flex justify-end">
          <button
            onClick={handleSubmit}
            disabled={loading || !text.trim()}
            className="rounded-lg bg-sky-600 px-5 py-2.5 text-sm font-bold text-white hover:bg-sky-500 disabled:opacity-50"
          >
            {loading ? "Executing…" : "Submit Intent"}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      {response && (
        <div className="space-y-4 rounded-xl border border-slate-700 bg-slate-900/60 p-5">
          <div className="flex flex-wrap items-center gap-3">
            <span className="rounded-md bg-slate-800 px-2.5 py-1 font-mono text-xs text-slate-300">
              {response.intent_type ?? "UNKNOWN"}
            </span>
            <span
              className={`rounded-md border px-2.5 py-1 text-xs font-bold ${STATUS_STYLES[response.status] ?? "bg-slate-800 text-slate-300 border-slate-700"}`}
            >
              {response.status}
            </span>
            <span className="text-xs text-slate-400">
              confidence <span className="font-mono text-slate-200">{response.confidence.toFixed(2)}</span>
            </span>
            <span className="text-xs text-slate-500">#{response.intent_execution_id}</span>
          </div>

          <p className="text-sm text-slate-300">{response.rationale}</p>

          <div>
            <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">Extracted Entities</h3>
            <pre className="overflow-x-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-300">
              {JSON.stringify(response.entities, null, 2)}
            </pre>
          </div>

          {response.plan.length > 0 && (
            <div>
              <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">Execution Plan</h3>
              <div className="space-y-2">
                {response.plan.map((step) => (
                  <div
                    key={step.step}
                    className="flex items-center gap-3 rounded-lg bg-slate-950 px-3 py-2 text-sm"
                  >
                    <span
                      className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-bold ${
                        step.status === "ok"
                          ? "bg-emerald-500/20 text-emerald-400"
                          : "bg-red-500/20 text-red-400"
                      }`}
                    >
                      {step.step}
                    </span>
                    <span className="font-mono text-slate-200">{step.action}</span>
                    <span className="ml-auto text-xs text-slate-500">{step.status}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {response.result && (
            <div>
              <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">Result</h3>
              <pre className="overflow-x-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-300">
                {JSON.stringify(response.result, null, 2)}
              </pre>
            </div>
          )}

          {response.events.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {response.events.map((ev, i) => (
                <span key={i} className="rounded-md bg-sky-500/10 px-2 py-1 font-mono text-xs text-sky-400">
                  {ev}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
