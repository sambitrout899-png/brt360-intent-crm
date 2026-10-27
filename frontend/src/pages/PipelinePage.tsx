import { useCallback, useEffect, useState } from "react";
import { listLeads, listOpportunities, listQuotes } from "../api/client";
import type { Lead, Opportunity, Quote } from "../api/types";
import { useIntentRefresh, useWorkspace } from "../workspace";
import { QuoteWorkbench } from "../components/orders/OrderWorkbench";
import PresalesNetwork from "../components/PresalesNetwork";
import { RecordButtonRow } from "../components/RecordButtons";
import { QuoteDocumentChips } from "../components/QuoteDocuments";
import { useRecordButtons } from "../components/useRecordButtons";
import { Drawer } from "./fieldservice/ui";

const STAGES: Opportunity["stage"][] = ["draft", "qualified", "configured", "in_feasibility", "feasibility_failed", "feasible", "quoted", "won", "lost"];
// Always shown; the Sales-to-Order stages appear once an opportunity is in them.
const CORE_STAGES = new Set(["draft", "qualified", "quoted", "won", "lost"]);

const STAGE_META: Record<string, { label: string; color: string }> = {
  draft: { label: "Draft", color: "#94a3b8" },
  qualified: { label: "Qualified", color: "#38bdf8" },
  configured: { label: "Configured", color: "#818cf8" },
  in_feasibility: { label: "With presales", color: "#a78bfa" },
  feasibility_failed: { label: "Not feasible", color: "#fb923c" },
  feasible: { label: "Feasible", color: "#2dd4bf" },
  quoted: { label: "Quoted", color: "#fbbf24" },
  won: { label: "Won", color: "#34d399" },
  lost: { label: "Lost", color: "#f87171" },
};

const QUOTE_STATUS: Record<string, { label: string; cls: string }> = {
  draft: { label: "Draft", cls: "bg-slate-500/15 text-slate-300" },
  pending_approval: { label: "Awaiting approval", cls: "bg-amber-500/15 text-amber-300" },
  approved: { label: "Approved", cls: "bg-emerald-500/15 text-emerald-300" },
  rejected: { label: "Rejected", cls: "bg-red-500/15 text-red-300" },
  cancelled: { label: "Cancelled", cls: "bg-slate-500/15 text-slate-500" },
  superseded: { label: "Superseded", cls: "bg-slate-500/15 text-slate-500" },
};

const money = (n: number) => (Math.abs(n) >= 1e7 ? `₹${(n / 1e7).toFixed(2)} Cr` : Math.abs(n) >= 1e5 ? `₹${(n / 1e5).toFixed(2)} L` : `₹${Math.round(n).toLocaleString()}`);
const BTN = "rounded-md border border-slate-700 px-2.5 py-1 text-xs font-semibold text-slate-300 transition hover:border-sky-500 hover:text-sky-300";
const BTN_PRIMARY = "rounded-md bg-sky-600/20 px-2.5 py-1 text-xs font-semibold text-sky-300 transition hover:bg-sky-600/30";

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="min-w-0 rounded-2xl border border-slate-800 bg-slate-900/60 px-4 py-3.5">
      <p className="truncate text-[11px] font-semibold uppercase tracking-wider text-slate-500">{label}</p>
      <p className={`mt-1 truncate text-xl font-bold sm:text-2xl ${tone ?? "text-slate-100"}`}>{value}</p>
      {sub && <p className="truncate text-[11px] text-slate-500">{sub}</p>}
    </div>
  );
}

function Section({ title, count, action, children }: { title: string; count?: number; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 px-1">
        <h2 className="text-sm font-semibold text-slate-100">{title}</h2>
        {count !== undefined && <span className="rounded-full bg-slate-800 px-2 text-xs text-slate-300">{count}</span>}
        <span className="flex-1" />
        {action}
      </div>
      {children}
    </section>
  );
}

function ApprovalProgress({ q }: { q: Quote }) {
  const chain = q.approval_chain;
  if (!chain || !chain.steps.length) return null;
  const done = chain.steps.filter((s) => s.status !== "pending").length;
  return (
    <div className="space-y-1">
      <div className="flex gap-1">
        {chain.steps.map((s, i) => (
          <span key={i} className={`h-1.5 flex-1 rounded-full ${s.status === "approved" ? "bg-emerald-400" : s.status === "rejected" ? "bg-red-400" : i === done ? "bg-amber-400" : "bg-slate-800"}`} />
        ))}
      </div>
      <p className="truncate text-[11px] text-slate-500">
        {chain.template_name}
        {chain.current_tier ? ` · waiting for ${chain.current_tier} (step ${done + 1} of ${chain.steps.length})` : ` · ${chain.status}`}
      </p>
    </div>
  );
}

export default function PipelinePage({ onRequestIntent }: { onRequestIntent: (text: string) => void }) {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [quoteFilter, setQuoteFilter] = useState<"open" | "approved" | "closed" | "all">("open");
  const { openRecord } = useWorkspace();
  const [workbench, setWorkbench] = useState<number | null>(null);
  const [network, setNetwork] = useState<Opportunity | null>(null);  // Presales' network check for a deal

  const load = useCallback(() => {
    listLeads().then(setLeads);
    listOpportunities().then(setOpportunities);
    listQuotes().then(setQuotes);
  }, []);
  useEffect(load, [load]);
  useIntentRefresh(load);

  const pendingLeads = leads.filter((l) => l.status === "pending_qualification");
  const latestQuote = (oppId: number) => quotes.filter((q) => q.opportunity_id === oppId && q.status !== "superseded" && q.status !== "cancelled").sort((a, b) => b.id - a.id)[0];
  const stages = STAGES.filter((s) => CORE_STAGES.has(s) || opportunities.some((o) => o.stage === s));
  const open = opportunities.filter((o) => o.stage !== "won" && o.stage !== "lost");
  const pipelineValue = open.reduce((n, o) => n + (latestQuote(o.id)?.total_price ?? 0), 0);
  const won = opportunities.filter((o) => o.stage === "won").length;
  const lost = opportunities.filter((o) => o.stage === "lost").length;
  const awaiting = quotes.filter((q) => q.status === "pending_approval").length;
  const shownQuotes = quotes
    .filter((q) =>
      quoteFilter === "all" ? true : quoteFilter === "open" ? q.status === "draft" || q.status === "pending_approval" : quoteFilter === "approved" ? q.status === "approved" : ["rejected", "cancelled", "superseded"].includes(q.status)
    )
    .sort((a, b) => b.id - a.id);
  const buttons = useRecordButtons([
    ...pendingLeads.map((l) => `lead:${l.id}`),
    ...opportunities.filter((o) => o.stage !== "won" && o.stage !== "lost").map((o) => `opportunity:${o.id}`),
    ...shownQuotes.map((q) => `quote:${q.id}`),
  ]);

  return (
    <div className="page space-y-6 sm:space-y-8">
      {/* ---------------- header */}
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold text-slate-100">Sales Pipeline</h1>
          <p className="mt-1 hidden text-sm text-slate-400 sm:block">Every deal from lead to order - where it is, what it's worth, and the next step.</p>
        </div>
        <button type="button" onClick={() => onRequestIntent("Qualify this lead. company_name: ")} className="rounded-lg border border-sky-500/40 bg-sky-500/10 px-3 py-2 text-xs font-semibold text-sky-300 hover:bg-sky-500/20">
          + New lead
        </button>
        <button type="button" onClick={load} className="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-300 hover:border-slate-500">
          ↻ Refresh
        </button>
      </div>

      {/* ---------------- the numbers */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Open deals" value={String(open.length)} sub={`${pendingLeads.length} lead${pendingLeads.length === 1 ? "" : "s"} to qualify`} />
        <Stat label="Pipeline value" value={money(pipelineValue)} sub="quoted, still open" tone="text-sky-300" />
        <Stat label="Awaiting approval" value={String(awaiting)} sub={`quote${awaiting === 1 ? "" : "s"}`} tone={awaiting ? "text-amber-300" : undefined} />
        <Stat label="Win rate" value={won + lost ? `${Math.round((won / (won + lost)) * 100)}%` : "—"} sub={`${won} won · ${lost} lost`} tone="text-emerald-300" />
      </div>

      {/* ---------------- leads to qualify */}
      {pendingLeads.length > 0 && (
        <Section title="Leads to qualify" count={pendingLeads.length}>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {pendingLeads.map((l) => (
              <div key={l.id} className="flex flex-col rounded-2xl border border-slate-800 bg-slate-900/50 p-4">
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <button type="button" onClick={() => openRecord("lead", l.id)} className="truncate text-left text-sm font-semibold text-slate-100 hover:text-sky-300">
                      Lead #{l.id}
                    </button>
                    <p className="truncate text-xs text-slate-400">{l.interested_offering_name || "No product yet"}</p>
                  </div>
                  <span className="shrink-0 rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-semibold text-amber-300">to qualify</span>
                </div>
                <p className="mt-2 truncate text-[11px] text-slate-500">
                  {[l.source, l.region, l.owner_name].filter(Boolean).join(" · ")}
                  {l.approval_chain?.current_tier ? ` · waiting for ${l.approval_chain.current_tier}` : ""}
                </p>
                <RecordButtonRow list={buttons[`lead:${l.id}`]} className="mt-auto flex flex-wrap gap-2 pt-3" buttonClass={`${BTN_PRIMARY} flex-1`} />
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* ---------------- the board: equal columns, swipe on a phone */}
      <Section title="Opportunities by stage" count={opportunities.length}>
        <div className="thin-scroll -mx-1 snap-x overflow-x-auto px-1 pb-2">
          <div className="grid auto-cols-[minmax(15rem,1fr)] grid-flow-col gap-3">
            {stages.map((stage) => {
              const meta = STAGE_META[stage];
              const items = opportunities.filter((o) => o.stage === stage);
              const value = items.reduce((n, o) => n + (latestQuote(o.id)?.total_price ?? 0), 0);
              return (
                <div key={stage} className="flex min-h-[14rem] snap-start flex-col rounded-2xl border border-slate-800 bg-slate-900/40">
                  <div className="flex items-center gap-2 border-b border-slate-800 px-3 py-2.5">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ background: meta.color }} />
                    <span className="flex-1 truncate text-xs font-semibold uppercase tracking-wider text-slate-300">{meta.label}</span>
                    <span className="rounded-full bg-slate-800 px-2 text-xs text-slate-300">{items.length}</span>
                  </div>
                  <p className="px-3 pt-2 text-[11px] text-slate-500">{value ? money(value) : "—"}</p>
                  <div className="flex-1 space-y-2 p-2">
                    {items.map((o) => (
                      <div key={o.id} className="rounded-xl border border-slate-800 bg-slate-950/70 p-3" style={{ boxShadow: `inset 3px 0 0 ${meta.color}` }}>
                        <button type="button" onClick={() => openRecord("opportunity", o.id)} className="block w-full truncate text-left text-sm font-medium text-slate-100 hover:text-sky-300">
                          {o.name}
                        </button>
                        {o.offering_name ? (
                          <p className="truncate text-xs text-sky-300/90">
                            {o.offering_name}
                            {(o.quantity ?? 1) > 1 ? ` × ${o.quantity}` : ""}
                          </p>
                        ) : (
                          <button type="button" onClick={() => openRecord("opportunity", o.id)} className="text-xs text-slate-500 hover:text-sky-400">
                            + add product
                          </button>
                        )}
                        <div className="mt-1 flex items-center justify-between text-[11px] text-slate-500">
                          <span>#{o.id}</span>
                          {latestQuote(o.id) && <span className="font-semibold text-slate-300">{money(latestQuote(o.id)!.total_price)}</span>}
                          {!latestQuote(o.id) && o.feasibility_assessments.length > 0 && <span>{o.feasibility_assessments[0].outcome}</span>}
                        </div>
                        <RecordButtonRow list={buttons[`opportunity:${o.id}`]} className="mt-2 flex flex-wrap gap-1.5" buttonClass={`${BTN_PRIMARY} flex-1`} />
                        {["in_feasibility", "feasible", "feasibility_failed"].includes(o.stage) && (
                          <button type="button" onClick={() => setNetwork(o)} className={`${BTN} mt-1.5 w-full`}>
                            {o.stage === "in_feasibility" ? "Check the network & reserve" : "Network & reservation"}
                          </button>
                        )}
                      </div>
                    ))}
                    {items.length === 0 && <p className="rounded-xl border border-dashed border-slate-800 py-6 text-center text-[11px] text-slate-600">Nothing here</p>}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </Section>

      {/* ---------------- quotes: equal cards, actions on one line at the bottom */}
      <Section
        title="Quotes"
        count={shownQuotes.length}
        action={
          <div className="flex rounded-lg border border-slate-700 bg-slate-900/60 p-0.5">
            {([["open", "Open"], ["approved", "Approved"], ["closed", "Closed"], ["all", "All"]] as const).map(([k, l]) => (
              <button key={k} type="button" onClick={() => setQuoteFilter(k)} className={`rounded-md px-2.5 py-1 text-xs font-semibold ${quoteFilter === k ? "bg-sky-600 text-white" : "text-slate-400 hover:text-slate-200"}`}>
                {l}
              </button>
            ))}
          </div>
        }
      >
        {shownQuotes.length === 0 ? (
          <p className="rounded-2xl border border-dashed border-slate-800 py-8 text-center text-sm text-slate-500">No quotes here.</p>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {shownQuotes.map((q) => {
              const st = QUOTE_STATUS[q.status] ?? QUOTE_STATUS.draft;
              const attrs = Object.entries(q.configuration?.pricing_attributes ?? {});
              return (
                <div key={q.id} className="flex flex-col rounded-2xl border border-slate-800 bg-slate-900/50 p-4">
                  <div className="flex items-start gap-2">
                    <div className="min-w-0 flex-1">
                      <button type="button" onClick={() => openRecord("quote", q.id)} className="text-sm font-semibold text-slate-100 hover:text-sky-300">
                        Quote #{q.id}
                        {q.revision_number > 1 && <span className="ml-1 text-[11px] font-normal text-slate-500">rev {q.revision_number}</span>}
                      </button>
                      <p className="text-[11px] text-slate-500">opportunity #{q.opportunity_id}</p>
                    </div>
                    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold ${st.cls}`}>{st.label}</span>
                  </div>
                  <div className="mt-3 flex items-baseline gap-2">
                    <span className="text-2xl font-bold text-slate-100">{money(q.total_price)}</span>
                    <span className="text-xs text-slate-500">/ month</span>
                    {q.price_override?.active ? (
                      <span
                        className="ml-auto rounded-full bg-amber-500/15 px-2 text-[11px] text-amber-300"
                        title={`Negotiated from ${money(q.price_override.active.computed_price)} - ${q.price_override.active.reason}`}
                      >
                        negotiated −{q.price_override.active.effective_discount_pct}%
                      </span>
                    ) : (
                      q.requested_discount_pct > 0 && <span className="ml-auto rounded-full bg-violet-500/15 px-2 text-[11px] text-violet-300">−{q.requested_discount_pct}%</span>
                    )}
                  </div>
                  {attrs.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1" title={(q.configuration?.price_breakdown.lines ?? []).map((l) => `${l.label}: ${l.amount}`).join("\n")}>
                      {attrs.slice(0, 4).map(([k, v]) => (
                        <span key={k} className="max-w-full truncate rounded-md bg-slate-800/80 px-1.5 py-0.5 text-[10px] text-slate-300">
                          {k} {v}
                        </span>
                      ))}
                      {attrs.length > 4 && <span className="text-[10px] text-slate-500">+{attrs.length - 4}</span>}
                    </div>
                  )}
                  <div className="mt-3 space-y-1">
                    <ApprovalProgress q={q} />
                    {q.configuration?.quote_price_list && <p className="text-[11px] text-slate-500">Pricelist {q.configuration.quote_price_list}</p>}
                    {q.status === "approved" && (q.proposals.length > 0 || q.purchase_order) && (
                      <p className="truncate text-[11px] text-slate-500">
                        {q.proposals.length > 0 && `Proposal ${q.proposals[0].status.replace(/_/g, " ")}`}
                        {q.proposals.length > 0 && q.purchase_order && " · "}
                        {q.purchase_order && `PO ${q.purchase_order.po_number}${q.purchase_order.matched_at ? " · matched" : " · awaiting Sales Ops"}`}
                      </p>
                    )}
                    {(q.documents?.length ?? 0) > 0 && <QuoteDocumentChips quoteId={q.id} documents={q.documents} tone="slate" />}
                  </div>

                  <div className="mt-auto flex flex-wrap gap-1.5 border-t border-slate-800 pt-3">
                    <RecordButtonRow list={buttons[`quote:${q.id}`]} className="contents" buttonClass={BTN_PRIMARY} />
                    {!["draft", "pending_approval", "approved"].includes(q.status) && (
                      <button type="button" onClick={() => openRecord("quote", q.id)} className={`${BTN} ml-auto`}>
                        Open
                      </button>
                    )}
                    {!["cancelled", "superseded"].includes(q.status) && (
                      <button type="button" onClick={() => setWorkbench(q.id)} className={BTN}>
                        Lines &amp; pricing
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Section>
      <Drawer open={network !== null} onClose={() => setNetwork(null)} title={network ? `Network feasibility · ${network.name}` : ""}
        subtitle="BRT 360 Network Inventory at the deal's site - check, reserve, decide">
        {network && <PresalesNetwork key={network.id} opportunityId={network.id} onChanged={load} />}
      </Drawer>
      <Drawer open={workbench !== null} onClose={() => setWorkbench(null)} title={workbench ? `Quote ${workbench}` : ""} subtitle="Lines, discounts, promotions, verification, forecast and agreement" wide>
        {workbench && <QuoteWorkbench quoteId={workbench} />}
      </Drawer>
    </div>
  );
}
