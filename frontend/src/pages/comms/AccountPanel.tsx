import { useCallback, useEffect, useState } from "react";
import { commsApi } from "../../api/client";
import { useIntentRefresh, useWorkspace } from "../../workspace";
import { BTN, Chip, Empty, Panel } from "../fieldservice/ui";
import { money } from "../fieldservice/style";
import type { AccountView, TreeNode } from "./types";

const CLASS_LABEL: Record<string, string> = {
  customer: "Customer", service: "Service", billing: "Billing", service_aggregator: "Service aggregator", billing_aggregator: "Billing aggregator",
};
const CLASS_TONE: Record<string, "sky" | "emerald" | "violet" | "amber" | "slate"> = {
  customer: "sky", service: "emerald", billing: "violet", service_aggregator: "emerald", billing_aggregator: "violet",
};

function Tree({ node, current, depth = 0 }: { node: TreeNode; current: number; depth?: number }) {
  return (
    <li>
      <div className={`flex flex-wrap items-center gap-2 rounded-lg px-2 py-1.5 ${node.party_id === current ? "bg-sky-500/10" : ""}`} style={{ marginLeft: depth * 16 }}>
        <span className="text-slate-600">{depth ? "└" : "●"}</span>
        <span className={`text-sm ${node.party_id === current ? "font-semibold text-sky-200" : "text-slate-200"}`}>{node.name}</span>
        <Chip tone={CLASS_TONE[node.account_class] ?? "slate"}>{CLASS_LABEL[node.account_class] ?? node.account_class}</Chip>
        {!node.good_standing && <Chip tone="red">blocked</Chip>}
        {node.site && <span className="text-[11px] text-slate-500">{node.site}</span>}
        <span className="ml-auto text-[11px] text-slate-500">
          {node.rollup_services} service{node.rollup_services === 1 ? "" : "s"} · {money(node.rollup_balance)}
        </span>
      </div>
      {node.children.length > 0 && (
        <ul>
          {node.children.map((c) => (
            <Tree key={c.party_id} node={c} current={current} depth={depth + 1} />
          ))}
        </ul>
      )}
    </li>
  );
}

const PROFILE_TITLES: Record<string, string> = {
  exemption: "Exemption", financial: "Financial", site: "Site", statement: "Statement", customer: "Customer", fraud: "Fraud thresholds",
};

/** Customer 360 > Account: where the account sits, who to talk to, how it is billed, its profiles, premises and lists. */
export default function AccountPanel({ partyId, name }: { partyId: number; name: string }) {
  const { runIntent, canRun } = useWorkspace();
  const [view, setView] = useState<AccountView | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    commsApi<AccountView>(`/accounts/${partyId}`)
      .then((v) => (setView(v), setError("")))
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load the account"));
  }, [partyId]);
  useEffect(load, [load]);
  useIntentRefresh(load);
  if (error) return <p className="text-sm text-red-400">{error}</p>;
  if (!view) return <p className="text-sm text-slate-500">Loading…</p>;
  const party = { party_id: partyId };
  const loyalty = view.loyalty;
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel
          title="Account hierarchy"
          action={
            <span className="flex gap-2">
              {canRun("INT-72") ? <button type="button" className={BTN} onClick={() => runIntent("INT-72", { parent_id: partyId, account_class: "service" })}>
                + Child account
              </button> : null}
              {canRun("INT-72") ? <button type="button" className={BTN} onClick={() => runIntent("INT-72", party)}>
                Move / classify
              </button> : null}
            </span>
          }
        >
          <ul className="space-y-0.5">
            <Tree node={view.hierarchy} current={partyId} />
          </ul>
          {!view.account.good_standing && <p className="mt-2 text-xs text-red-300">Blocked: {view.account.block_reason}</p>}
        </Panel>
        <Panel title="Loyalty" action={(canRun("INT-75") ? <button type="button" className={BTN} onClick={() => runIntent("INT-75", { ...party, profile_kind: "loyalty" })}>Re-score</button> : null)}>
          <div className="grid grid-cols-3 gap-3 text-center">
            <div>
              <p className={`text-2xl font-bold ${loyalty.churn_band === "high" ? "text-red-400" : loyalty.churn_band === "medium" ? "text-amber-300" : "text-emerald-400"}`}>{loyalty.churn_risk}%</p>
              <p className="text-[11px] text-slate-500">churn risk</p>
            </div>
            <div>
              <p className="text-2xl font-bold text-slate-100">{money(loyalty.lifetime_value)}</p>
              <p className="text-[11px] text-slate-500">lifetime value</p>
            </div>
            <div>
              <p className={`text-2xl font-bold ${loyalty.debt_risk >= 50 ? "text-red-400" : "text-slate-100"}`}>{loyalty.debt_risk}%</p>
              <p className="text-[11px] text-slate-500">debt risk</p>
            </div>
          </div>
          <p className="mt-3 text-xs text-slate-400">{loyalty.reasons.join(" · ")}</p>
          {loyalty.upsell.length > 0 && (
            <p className="mt-1 text-xs text-sky-300">Offer: {loyalty.upsell.map((u) => u.product).join(", ")}</p>
          )}
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title={`Contacts · ${view.contacts.length}`} action={(canRun("INT-73") ? <button type="button" className={BTN} onClick={() => runIntent("INT-73", party)}>+ Contact</button> : null)}>
          {view.contacts.length === 0 ? (
            <p className="text-sm text-slate-500">No contacts yet.</p>
          ) : (
            <ul className="divide-y divide-slate-800">
              {view.contacts.map((c) => {
                const here = c.accounts.find((a) => a.party_id === partyId);
                return (
                  <li key={c.id} className="flex flex-wrap items-center gap-2 py-2">
                    <span className="text-sm text-slate-200">{c.name}</span>
                    {here?.role && <Chip>{here.role.replace("_", " ")}</Chip>}
                    {here?.primary && <Chip tone="sky">primary</Chip>}
                    <span className="ml-auto text-xs text-slate-500">{[c.email, c.phone].filter(Boolean).join(" · ")}</span>
                    {c.accounts.length > 1 && <span className="w-full text-[11px] text-slate-500">Also on: {c.accounts.filter((a) => a.party_id !== partyId).map((a) => a.name).join(", ")}</span>}
                  </li>
                );
              })}
            </ul>
          )}
        </Panel>

      </div>

      <Panel title="Profiles" action={(canRun("INT-75") ? <button type="button" className={BTN} onClick={() => runIntent("INT-75", party)}>Update a profile</button> : null)}>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(PROFILE_TITLES).map(([kind, title]) => {
            const data = view.profiles[kind] ?? {};
            const entries = Object.entries(data).filter(([k]) => !["credit_reason"].includes(k));
            return (
              <button
                key={kind}
                type="button"
                onClick={() => runIntent(kind === "fraud" ? "INT-76" : "INT-75", kind === "fraud" ? party : { ...party, profile_kind: kind })}
                disabled={!canRun(kind === "fraud" ? "INT-76" : "INT-75")}
                className="rounded-lg border border-slate-800 p-3 text-left transition hover:border-slate-600 disabled:cursor-default disabled:hover:border-slate-800"
              >
                <p className="text-xs font-semibold text-slate-300">{title}</p>
                {entries.length === 0 ? (
                  <p className="mt-1 text-[11px] text-slate-600">Not set</p>
                ) : (
                  <p className="mt-1 line-clamp-3 text-[11px] text-slate-400">
                    {entries.map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "boolean" ? (v ? "yes" : "no") : String(v)}`).join(" · ")}
                  </p>
                )}
              </button>
            );
          })}
        </div>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel
          title="Addresses & premises"
          action={
            <span className="flex gap-2">
              {canRun("INT-77") ? <button type="button" className={BTN} onClick={() => runIntent("INT-77", { ...party, make_premise: true })}>
                Validate address
              </button> : null}
              {canRun("INT-78") ? <button type="button" className={BTN} onClick={() => runIntent("INT-78", party)}>
                Hookup
              </button> : null}
            </span>
          }
        >
          {view.addresses.length === 0 && view.premises.length === 0 ? (
            <p className="text-sm text-slate-500">No validated addresses yet.</p>
          ) : (
            <ul className="space-y-1.5 text-sm">
              {view.addresses.map((a) => (
                <li key={`a${a.id}`} className="flex flex-wrap items-center gap-2">
                  <span className="text-slate-200">{a.standard_form || a.raw}</span>
                  <Chip tone={a.validated ? "emerald" : "amber"}>{a.validated ? "validated" : "not validated"}</Chip>
                  {a.premise_id && <Chip tone="sky">premises #{a.premise_id}</Chip>}
                </li>
              ))}
              {view.premises
                .filter((p) => !view.addresses.some((a) => a.premise_id === p.id))
                .map((p) => (
                  <li key={`p${p.id}`} className="flex flex-wrap items-center gap-2">
                    <span className="text-slate-200">{p.address}</span>
                    <Chip tone="sky">premises #{p.id}</Chip>
                    <span className="text-xs text-slate-500">{p.service_points} service point(s)</span>
                  </li>
                ))}
            </ul>
          )}
          {view.site_items.length > 0 && (
            <div className="mt-3 border-t border-slate-800 pt-2">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Equipment & infrastructure</p>
              <ul className="mt-1 space-y-0.5 text-xs text-slate-400">
                {view.site_items.map((i) => (
                  <li key={i.id}>
                    {i.kind.replace("_", " ")}: {i.name}
                    {i.value ? ` = ${i.value}` : ""} {i.quantity > 1 ? `x${i.quantity}` : ""} {i.owner ? `(${i.owner})` : ""} {i.planned ? "· planned" : ""}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Panel>
        <Panel title="Friends & family lists" action={(canRun("INT-79") ? <button type="button" className={BTN} onClick={() => runIntent("INT-79", party)}>Manage</button> : null)}>
          {view.rating_lists.length === 0 ? (
            <p className="text-sm text-slate-500">No special rating lists.</p>
          ) : (
            view.rating_lists.map((l) => (
              <div key={l.id} className="mb-2">
                <p className="text-sm text-slate-200">
                  {l.name} <span className="text-xs text-slate-500">· {l.product} · {l.active_items}/{l.max_items}</span>
                </p>
                <p className="mt-1 flex flex-wrap gap-1">
                  {l.items.filter((i) => !i.inactive).map((i) => (
                    <Chip key={i.id}>{i.value}</Chip>
                  ))}
                </p>
              </div>
            ))
          )}
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Agreements" action={view.agreements[0] ? (canRun("INT-100") ? <button type="button" className={BTN} onClick={() => runIntent("INT-100", { agreement_id: view.agreements[0].agreement_number })}>Revise</button> : null) : undefined}>
          {view.agreements.length === 0 ? (
            <p className="text-sm text-slate-500">No agreements - create one from a quote.</p>
          ) : (
            <ul className="divide-y divide-slate-800">
              {view.agreements.map((a) => (
                <li key={a.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                  <span className="font-mono text-xs text-slate-500">{a.agreement_number}</span>
                  <span className="text-slate-200">{a.name}</span>
                  <Chip tone={a.status === "active" ? "emerald" : "slate"}>{a.status}</Chip>
                  <span className="ml-auto flex gap-2">
                    {canRun("INT-100") ? <button type="button" className="text-xs text-sky-400" onClick={() => runIntent("INT-100", { agreement_id: a.agreement_number })}>
                      Revise
                    </button> : null}
                    {canRun("INT-101") ? <button type="button" className="text-xs text-sky-400" onClick={() => runIntent("INT-101", { agreement_id: a.agreement_number })}>
                      Document
                    </button> : null}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="Change requests" action={(canRun("INT-81") ? <button type="button" className={BTN} onClick={() => runIntent("INT-81", party)}>+ Change request</button> : null)}>
          {view.change_requests.length === 0 ? (
            <Empty>No change requests from {name}.</Empty>
          ) : (
            <ul className="divide-y divide-slate-800">
              {view.change_requests.map((c) => (
                <li key={c.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                  <span className="font-mono text-xs text-slate-500">{c.number}</span>
                  <span className="min-w-0 flex-1 truncate text-slate-200">{c.description}</span>
                  <Chip>{c.type}</Chip>
                  <Chip tone={c.status === "closed" ? "emerald" : "amber"}>{c.status}</Chip>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}
