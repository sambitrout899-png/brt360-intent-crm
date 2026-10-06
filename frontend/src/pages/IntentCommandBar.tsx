import { useEffect, useRef, useState } from "react";
import { assistIntent, listCatalog, submitIntent } from "../api/client";
import type { IntentAssist, IntentResponse, Offering } from "../api/types";

const EXAMPLES = [
  {
    label: "✨ Natural language",
    text: "Sign up Acme Telecom as a new lead, they came in through a referral, they're in the North region",
  },
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
  {
    label: "INT-04 · Qualify lead",
    text: "Qualify this lead. company_name: Acme Telecom, source: referral, region: North",
  },
  {
    label: "INT-05 · Configure & quote",
    text: "Configure and quote this bundle. opportunity_id: 1, offering_name: Fiber 500 Bundle, quantity: 1, discount: 5",
  },
  {
    label: "INT-06 · Approve",
    text: "Approve this quote. quote_id: 1, reasoning: strategic account",
  },
  {
    label: "INT-07 · Activate order",
    text: "Activate this order end-to-end. quote_id: 1",
  },
  {
    label: "INT-08 · Change service",
    text: "Upgrade this service. order_id: 1, new_price: 1200",
  },
  {
    label: "INT-09 · Cancel",
    text: "Cancel this service. order_id: 1",
  },
  {
    label: "INT-10 · Log complaint",
    text: "Log this complaint. description: Fiber internet is completely down since this morning, company_name: Acme Telecom, sla_class: premium",
  },
  {
    label: "INT-11 · Diagnose",
    text: "Diagnose the root cause. ticket_id: 1",
  },
  {
    label: "INT-12 · Dispatch tech",
    text: "Dispatch a technician. ticket_id: 1, required_skill: fiber_install, region: North",
  },
];

const STATUS_STYLES: Record<string, string> = {
  executed: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  needs_review: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  failed: "bg-red-500/15 text-red-400 border-red-500/30",
  verification_failed: "bg-red-500/15 text-red-400 border-red-500/30",
};

export default function IntentCommandBar({ prefillText }: { prefillText?: string }) {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<IntentResponse | null>(null);
  const [error, setError] = useState("");
  const [assist, setAssist] = useState<IntentAssist | null>(null);
  const [assisting, setAssisting] = useState(false);
  const [offerings, setOfferings] = useState<Offering[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (prefillText) setText(prefillText);
  }, [prefillText]);

  useEffect(() => {
    listCatalog().then(setOfferings).catch(() => {});
  }, []);

  // The Expression Builder's live intellisense - debounced so typing
  // doesn't fire a call per keystroke; never executes anything.
  useEffect(() => {
    if (text.trim().length < 6) {
      setAssist(null);
      return;
    }
    const handle = setTimeout(() => {
      setAssisting(true);
      assistIntent(text)
        .then(setAssist)
        .catch(() => setAssist(null))
        .finally(() => setAssisting(false));
    }, 500);
    return () => clearTimeout(handle);
  }, [text]);

  const lastToken = text.split(/[,.\n]/).pop()?.trim().toLowerCase() ?? "";
  const productMatches =
    lastToken.length >= 2 ? offerings.filter((o) => o.name.toLowerCase().includes(lastToken)).slice(0, 5) : [];

  function appendField(fieldName: string) {
    setText((t) => {
      const trimmed = t.trimEnd();
      if (!trimmed) return `${fieldName}: `;
      const sep = trimmed.endsWith(",") ? " " : ", ";
      return `${trimmed}${sep}${fieldName}: `;
    });
    textareaRef.current?.focus();
  }

  function insertProductName(name: string) {
    setText((t) => {
      const cutAt = Math.max(t.lastIndexOf(","), t.lastIndexOf("\n"));
      const prefix = cutAt >= 0 ? `${t.slice(0, cutAt + 1)} ` : "";
      return `${prefix}${name}`;
    });
    textareaRef.current?.focus();
  }

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
    <div className="mx-auto max-w-6xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">Expression Builder</h1>
        <p className="mt-1 text-sm text-slate-400">
          Type what you want in plain English — no fixed structure required. The system corrects typos, figures
          out the intent, and tells you (to the right) which fields it's already understood and which it still
          needs, with product names it recognizes as you type.
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

      <div className="grid gap-4 md:grid-cols-[1fr_320px]">
        <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-4">
          <textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={4}
            placeholder='Type naturally — e.g. "sign up Acme Telecom as a new lead from a referral in the North region"'
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

        <div className="space-y-3 rounded-xl border border-slate-700 bg-slate-900/60 p-4">
          <h3 className="text-xs font-bold uppercase tracking-wide text-slate-500">Live Assist</h3>

          {assisting && <p className="text-xs text-slate-500">Thinking…</p>}

          {!assisting && assist?.intent_type && (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-md bg-slate-800 px-2 py-0.5 font-mono text-xs text-slate-300">{assist.intent_type}</span>
                <span className="text-xs text-slate-300">{assist.intent_label}</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                <div
                  className="h-full rounded-full bg-sky-500"
                  style={{ width: `${Math.round(assist.confidence * 100)}%` }}
                />
              </div>
              {assist.required_fields.length > 0 && (
                <div className="space-y-1.5">
                  {assist.required_fields.map((f) => {
                    const missing = assist.missing_fields.includes(f);
                    return missing ? (
                      <button
                        key={f}
                        onClick={() => appendField(f)}
                        className="block w-full rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-left text-xs font-semibold text-amber-400 hover:bg-amber-500/20"
                      >
                        + {f}
                      </button>
                    ) : (
                      <div key={f} className="flex items-center gap-2 rounded-md bg-emerald-500/10 px-2.5 py-1.5 text-xs text-emerald-400">
                        <span>✓</span>
                        <span>{f}</span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {!assisting && text.trim().length >= 6 && !assist?.intent_type && (
            <p className="text-xs text-slate-500">Not recognized yet — keep typing, or try one of the examples above.</p>
          )}

          {!assisting && text.trim().length < 6 && (
            <p className="text-xs text-slate-500">Start typing to see the detected intent and required fields here.</p>
          )}

          {productMatches.length > 0 && (
            <div className="space-y-1.5 border-t border-slate-800 pt-3">
              <p className="text-xs font-bold uppercase tracking-wide text-slate-500">Matching Products</p>
              {productMatches.map((o) => (
                <button
                  key={o.id}
                  onClick={() => insertProductName(o.name)}
                  className="block w-full rounded-md border border-sky-500/30 bg-sky-500/10 px-2.5 py-1.5 text-left text-xs font-semibold text-sky-400 hover:bg-sky-500/20"
                >
                  {o.name}
                </button>
              ))}
            </div>
          )}
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
