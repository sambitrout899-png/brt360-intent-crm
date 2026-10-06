import { useEffect, useState } from "react";
import { listLeads, listOpportunities, listQuotes } from "../api/client";
import type { Lead, Opportunity, Quote } from "../api/types";

const STAGES: Opportunity["stage"][] = ["draft", "qualified", "quoted", "won", "lost"];

const STAGE_COLORS: Record<string, string> = {
  draft: "border-slate-600",
  qualified: "border-sky-600",
  quoted: "border-amber-600",
  won: "border-emerald-600",
  lost: "border-red-600",
};

const QUOTE_STATUS_STYLES: Record<string, string> = {
  draft: "bg-slate-500/15 text-slate-400 border-slate-500/30",
  pending_approval: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  approved: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  rejected: "bg-red-500/15 text-red-400 border-red-500/30",
  cancelled: "bg-slate-500/15 text-slate-500 border-slate-500/30",
};

const LEAD_STATUS_STYLES: Record<string, string> = {
  pending_qualification: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  qualified: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  rejected: "bg-red-500/15 text-red-400 border-red-500/30",
};

export default function PipelinePage({ onRequestIntent }: { onRequestIntent: (text: string) => void }) {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [quotes, setQuotes] = useState<Quote[]>([]);

  function load() {
    listLeads().then(setLeads);
    listOpportunities().then(setOpportunities);
    listQuotes().then(setQuotes);
  }

  useEffect(load, []);

  const pendingLeads = leads.filter((l) => l.status === "pending_qualification");

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Sales Pipeline</h1>
          <p className="mt-1 text-sm text-slate-400">Lead-to-Quote: opportunities by stage, and every quote awaiting or past approval.</p>
        </div>
        <button onClick={load} className="rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2 text-xs font-bold text-slate-300 hover:border-sky-500 hover:text-sky-400">
          Refresh
        </button>
      </div>

      {pendingLeads.length > 0 && (
        <div>
          <h2 className="mb-3 text-lg font-bold text-slate-100">Leads Awaiting Qualification</h2>
          <div className="space-y-2">
            {pendingLeads.map((l) => (
              <div key={l.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-700 bg-slate-900/60 px-4 py-3 text-sm">
                <span className="font-mono text-xs text-slate-500">#{l.id}</span>
                <span className="text-slate-200">{l.interested_offering_name || "no product yet"}</span>
                <span className={`rounded-md border px-2 py-0.5 text-xs font-bold ${LEAD_STATUS_STYLES[l.status]}`}>{l.status}</span>
                {l.approval_chain?.current_tier && (
                  <span className="text-xs text-slate-500">awaiting {l.approval_chain.current_tier}</span>
                )}
                <button
                  onClick={() => onRequestIntent(`Add this product to the lead. lead_id: ${l.id}, offering_name: `)}
                  className="ml-auto rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-sky-500 hover:text-sky-400"
                >
                  Add Product
                </button>
                <button
                  onClick={() => onRequestIntent(`Approve this lead. lead_id: ${l.id}, reasoning: `)}
                  className="rounded-md border border-sky-500/40 bg-sky-500/10 px-2.5 py-1 text-xs font-bold text-sky-400 hover:bg-sky-500/20"
                >
                  Approve / Reject
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-5">
        {STAGES.map((stage) => {
          const items = opportunities.filter((o) => o.stage === stage);
          return (
            <div key={stage} className={`rounded-xl border-t-4 ${STAGE_COLORS[stage]} border-x border-b border-slate-700 bg-slate-900/60 p-3`}>
              <div className="mb-2 flex items-center justify-between">
                <span className="text-xs font-bold uppercase tracking-wide text-slate-400">{stage}</span>
                <span className="rounded-full bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{items.length}</span>
              </div>
              <div className="space-y-2">
                {items.map((o) => (
                  <div key={o.id} className="rounded-lg bg-slate-950 px-2.5 py-2 text-xs text-slate-300">
                    <div className="truncate font-semibold text-slate-200">{o.name}</div>
                    <div className="flex items-center justify-between text-slate-500">
                      <span>#{o.id}</span>
                      {o.feasibility_assessments.length > 0 && <span>{o.feasibility_assessments[0].outcome}</span>}
                    </div>
                    <button
                      onClick={() => onRequestIntent(`Run feasibility assessment. opportunity_id: ${o.id}, address: `)}
                      className="mt-1.5 w-full rounded-md border border-slate-700 px-2 py-1 text-xs font-bold text-slate-300 hover:border-sky-500 hover:text-sky-400"
                    >
                      Check Feasibility
                    </button>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>

      <div>
        <h2 className="mb-3 text-lg font-bold text-slate-100">Quotes</h2>
        <div className="space-y-2">
          {quotes.map((q) => (
            <div key={q.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-700 bg-slate-900/60 px-4 py-3 text-sm">
              <span className="font-mono text-xs text-slate-500">#{q.id}</span>
              <span className="text-slate-200">₹{q.total_price.toLocaleString()}</span>
              <span className="text-xs text-slate-500">{q.requested_discount_pct}% discount</span>
              <span className={`rounded-md border px-2 py-0.5 text-xs font-bold ${QUOTE_STATUS_STYLES[q.status]}`}>{q.status}</span>
              {q.approval_chain && (
                <span className="text-xs text-slate-500">
                  {q.approval_chain.template_name}
                  {q.approval_chain.current_tier
                    ? ` — awaiting ${q.approval_chain.current_tier} (step ${q.approval_chain.steps.filter((s) => s.status !== "pending").length + 1}/${q.approval_chain.steps.length})`
                    : ` — ${q.approval_chain.status}`}
                </span>
              )}
              {(q.status === "draft" || q.status === "pending_approval") && (
                <div className="ml-auto flex gap-2">
                  <button
                    onClick={() => onRequestIntent(`Revise this quote. quote_id: ${q.id}, discount: `)}
                    className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-sky-500 hover:text-sky-400"
                  >
                    Revise
                  </button>
                  <button
                    onClick={() => onRequestIntent(`Cancel this quote. quote_id: ${q.id}, reasoning: `)}
                    className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-red-500 hover:text-red-400"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => onRequestIntent(`Approve this quote. quote_id: ${q.id}, reasoning: `)}
                    className="rounded-md border border-sky-500/40 bg-sky-500/10 px-2.5 py-1 text-xs font-bold text-sky-400 hover:bg-sky-500/20"
                  >
                    Approve / Reject {q.approval_chain?.current_tier ? `(${q.approval_chain.current_tier})` : ""}
                  </button>
                </div>
              )}
              {q.status === "approved" && (
                <div className="ml-auto flex flex-wrap items-center gap-2">
                  {q.proposals.length > 0 && (
                    <span className="text-xs text-slate-500">proposal: {q.proposals[0].status}</span>
                  )}
                  <button
                    onClick={() => onRequestIntent(`Activate this order end-to-end. quote_id: ${q.id}`)}
                    className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2.5 py-1 text-xs font-bold text-emerald-400 hover:bg-emerald-500/20"
                  >
                    Activate Order (direct)
                  </button>
                  {q.proposals.length === 0 && (
                    <button
                      onClick={() => onRequestIntent(`Generate proposal for this quote. quote_id: ${q.id}`)}
                      className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-sky-500 hover:text-sky-400"
                    >
                      Generate Proposal
                    </button>
                  )}
                  {q.proposals[0]?.status === "sent" && (
                    <button
                      onClick={() =>
                        onRequestIntent(
                          `Record customer approval of the proposal. proposal_id: ${q.proposals[0].id}, decision: approve`
                        )
                      }
                      className="rounded-md border border-sky-500/40 bg-sky-500/10 px-2.5 py-1 text-xs font-bold text-sky-400 hover:bg-sky-500/20"
                    >
                      Record Customer Decision
                    </button>
                  )}
                  {q.proposals[0]?.status === "customer_approved" && (
                    <button
                      onClick={() => onRequestIntent(`Generate order from quote. quote_id: ${q.id}`)}
                      className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2.5 py-1 text-xs font-bold text-emerald-400 hover:bg-emerald-500/20"
                    >
                      Generate Order (B2B)
                    </button>
                  )}
                </div>
              )}
            </div>
          ))}
          {quotes.length === 0 && <p className="text-sm text-slate-500">No quotes yet.</p>}
        </div>
      </div>
    </div>
  );
}
