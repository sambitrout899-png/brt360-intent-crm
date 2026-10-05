import { useEffect, useState } from "react";
import { listParties, party360 } from "../api/client";
import type { Party, Party360 } from "../api/types";

function ActionButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="rounded-md border border-sky-500/40 bg-sky-500/10 px-2.5 py-1 text-xs font-bold text-sky-400 hover:bg-sky-500/20"
    >
      {label}
    </button>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-4">
      <h3 className="mb-3 font-bold text-slate-100">{title}</h3>
      {children}
    </div>
  );
}

export default function CustomerPage({ onRequestIntent }: { onRequestIntent: (text: string) => void }) {
  const [parties, setParties] = useState<Party[]>([]);
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<Party360 | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const handle = setTimeout(() => {
      listParties(search || undefined).then(setParties);
    }, 250);
    return () => clearTimeout(handle);
  }, [search]);

  useEffect(() => {
    if (selectedId === null) return;
    setLoading(true);
    party360(selectedId)
      .then(setDetail)
      .finally(() => setLoading(false));
  }, [selectedId]);

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">Customer 360</h1>
        <p className="mt-1 text-sm text-slate-400">Every lead, quote, order, bill, and ticket for one customer, in one view.</p>
      </div>

      <input
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search customers by name…"
        className="w-full max-w-sm rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-sky-500"
      />

      <div className="grid gap-6 lg:grid-cols-[260px_1fr]">
        <div className="space-y-1">
          {parties.map((p) => (
            <button
              key={p.id}
              onClick={() => setSelectedId(p.id)}
              className={`block w-full rounded-lg px-3 py-2 text-left text-sm ${
                selectedId === p.id ? "bg-sky-600 text-white" : "bg-slate-900/60 text-slate-300 hover:bg-slate-800"
              }`}
            >
              {p.name}
            </button>
          ))}
          {parties.length === 0 && <p className="text-sm text-slate-500">No customers yet — qualify a lead first.</p>}
        </div>

        <div className="space-y-4">
          {loading && <p className="text-sm text-slate-500">Loading…</p>}
          {!loading && !detail && <p className="text-sm text-slate-500">Select a customer.</p>}
          {!loading && detail && (
            <>
              <Section title={detail.party.name}>
                <div className="flex flex-wrap gap-4 text-sm text-slate-400">
                  <span>Type: {detail.party.party_type}</span>
                  {detail.party.email && <span>Email: {detail.party.email}</span>}
                  {detail.party.phone && <span>Phone: {detail.party.phone}</span>}
                </div>
              </Section>

              <Section title={`Identity Verification (${detail.identity_verifications.length})`}>
                {detail.identity_verifications.map((v) => (
                  <div key={v.id} className="flex items-center gap-3 rounded-lg bg-slate-950 px-3 py-2 text-sm mb-2">
                    <span className="text-slate-200">{v.provider_name}</span>
                    <span className="text-xs text-slate-500">{v.id_type} ····{v.id_last4}</span>
                    <span
                      className={`ml-auto rounded-md px-2 py-0.5 text-xs font-bold ${
                        v.status === "verified"
                          ? "bg-emerald-500/15 text-emerald-400"
                          : v.status === "rejected"
                            ? "bg-red-500/15 text-red-400"
                            : "bg-amber-500/15 text-amber-400"
                      }`}
                    >
                      {v.status}
                    </span>
                  </div>
                ))}
                {detail.identity_verifications.length === 0 && <p className="text-sm text-slate-500 mb-2">None yet.</p>}
                <ActionButton
                  label="Verify Identity"
                  onClick={() => onRequestIntent(`Verify customer identity. party_id: ${detail.party.id}, id_number: `)}
                />
              </Section>

              <Section title={`Opportunities (${detail.opportunities.length})`}>
                {detail.opportunities.map((o) => (
                  <div key={o.id} className="flex items-center gap-3 rounded-lg bg-slate-950 px-3 py-2 text-sm mb-2">
                    <span className="font-mono text-xs text-slate-500">#{o.id}</span>
                    <span className="text-slate-200">{o.name}</span>
                    <span className="ml-auto rounded-md bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{o.stage}</span>
                  </div>
                ))}
                {detail.opportunities.length === 0 && <p className="text-sm text-slate-500">None.</p>}
              </Section>

              <Section title={`Quotes (${detail.quotes.length})`}>
                {detail.quotes.map((q) => (
                  <div key={q.id} className="rounded-lg bg-slate-950 px-3 py-2 text-sm mb-2">
                    <div className="flex items-center gap-3">
                      <span className="font-mono text-xs text-slate-500">#{q.id}</span>
                      <span className="text-slate-200">₹{q.total_price.toLocaleString()}</span>
                      <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{q.status}</span>
                      {q.status === "draft" && (
                        <ActionButton label="Approve" onClick={() => onRequestIntent(`Approve this quote. quote_id: ${q.id}`)} />
                      )}
                      {q.status === "approved" && (
                        <ActionButton label="Activate" onClick={() => onRequestIntent(`Activate this order end-to-end. quote_id: ${q.id}`)} />
                      )}
                    </div>
                  </div>
                ))}
                {detail.quotes.length === 0 && <p className="text-sm text-slate-500">None.</p>}
              </Section>

              {detail.billing_account && (
                <Section title="Billing">
                  <div className="text-sm text-slate-300">
                    Status: {detail.billing_account.status} · Rate plan: {detail.billing_account.rate_plan} · Balance: ₹
                    {detail.billing_account.balance.toLocaleString()}
                  </div>
                </Section>
              )}

              <Section title={`Service Accounts (${detail.service_accounts.length})`}>
                {detail.service_accounts.map((sa) => (
                  <div key={sa.id} className="mb-3 rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-sm">
                    <div className="mb-2 flex items-center gap-3">
                      <span className="font-semibold text-slate-200">{sa.name}</span>
                      <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{sa.status}</span>
                    </div>
                    <div className="space-y-2 pl-3">
                      {sa.orders.map((o) => (
                        <div key={o.id} className="flex items-center gap-3 rounded-lg bg-slate-900/60 px-3 py-2">
                          <span className="font-mono text-xs text-slate-500">#{o.id}</span>
                          <span className="text-slate-200">₹{o.monthly_price.toLocaleString()}/mo</span>
                          <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{o.status}</span>
                          {(o.status === "active" || o.status === "changed") && (
                            <>
                              <ActionButton label="Change" onClick={() => onRequestIntent(`Upgrade this service. order_id: ${o.id}, new_price: `)} />
                              <ActionButton label="Cancel" onClick={() => onRequestIntent(`Cancel this service. order_id: ${o.id}`)} />
                            </>
                          )}
                          {o.status === "pending_cancellation" && (
                            <ActionButton label="Retain Customer" onClick={() => onRequestIntent(`Cancel this service. order_id: ${o.id}, decision: retain`)} />
                          )}
                        </div>
                      ))}
                      {sa.orders.length === 0 && <p className="text-xs text-slate-500">No orders under this service account.</p>}
                    </div>
                  </div>
                ))}
                {detail.service_accounts.length === 0 && <p className="text-sm text-slate-500">None yet - created automatically on first order activation.</p>}
              </Section>

              <Section title={`Tickets (${detail.tickets.length})`}>
                {detail.tickets.map((t) => (
                  <div key={t.id} className="rounded-lg bg-slate-950 px-3 py-2 text-sm mb-2">
                    <div className="flex items-center gap-3">
                      <span className="font-mono text-xs text-slate-500">#{t.id}</span>
                      <span className="text-slate-200">{t.category}</span>
                      <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{t.priority}</span>
                      <span className="text-xs text-slate-500">{t.status}</span>
                      {!t.root_cause && (
                        <ActionButton label="Diagnose" onClick={() => onRequestIntent(`Diagnose the root cause. ticket_id: ${t.id}`)} />
                      )}
                    </div>
                  </div>
                ))}
                {detail.tickets.length === 0 && <p className="text-sm text-slate-500">None.</p>}
              </Section>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
