import { useCallback, useEffect, useState } from "react";
import { mobileApi, mobileVoucherPins } from "../api/client";
import { useIntentRefresh, useWorkspace } from "../workspace";
import { BTN, BTN_DANGER, BTN_PRIMARY, BTN_WARN, CARD, Chip, Drawer, Empty, INPUT, Notice, Panel, Section, Stat, Tabs } from "./fieldservice/ui";

/* ---------------------------------------------------------------- shapes (backend: crm_core/comm_mobile.py) */

type Line = {
  id: number; msisdn: string; party_id: number; customer: string; user_name: string; iccid: string; imsi: string; sim_type: string; plan: string;
  payment_type: "prepaid" | "postpaid"; status: string; status_reason: string; barrings: string[]; roaming: boolean; balance: number;
  valid_until: string | null; advance_owed: number; credit_limit: number; spend_alert_pct: number; cycle_start: string | null; cycle_spend: number;
  deposit: number; group_head_id: number | null; partner: string | null; activated_at: string; terminated_at: string | null;
};
type Pack = { id: number; code: string; name: string; kind: string; granted: Record<string, number>; remaining: Record<string, number>; roaming: boolean;
  price: number; paid_from: string; valid_to: string; auto_renew: boolean };
type LineDetail = Line & {
  packs: Pack[]; shared_from: { msisdn: string; packs: Pack[] } | null; members: string[]; remaining: Record<string, number>;
  wallet: { kind: string; amount: number; balance_after: number; method: string; reference: string; at: string }[];
  usage: { id: number; at: string; event_type: string; quantity: number; amount: number; international: boolean; rate_plan: string; destination: string }[];
  history: { action: string; summary: string; at: string }[];
};
type Overview = {
  lines: number; prepaid: number; postpaid: number; active: number; suspended: number; barred: number; roaming: number; terminated: number;
  prepaid_balance: number; postpaid_spend: number; vouchers_available: number; partners: number;
  recent: { line_id: number; action: string; summary: string; at: string }[];
};
type CatalogRow = { code: string; name: string; payment_type?: string; kind?: string; fee?: number; price?: number; validity_days: number;
  allowance: Record<string, number>; shared?: boolean; roaming?: boolean };
type Catalog = Record<string, unknown> & { plans: CatalogRow[]; packs: CatalogRow[] };
type Batch = { batch: string; face_value: number; validity_days: number; expires_on: string; count: number; redeemed: number; available: number;
  blocked: number; expired: number; pins_pending: boolean; created_at: string };
type Partner = { id: number; name: string; kind: string; brand: string; wholesale_rates: Record<string, number>; revenue_share_pct: number;
  number_range: string; status: string; lines: number };
type Settlement = { id: number; number: string; partner: string; period_start: string; period_end: string; wholesale_due: number;
  commission_due: number; net: number; status: string; lines: { label: string; quantity: number; rate: number; amount: number }[] };

const UNIT: Record<string, string> = { voice: "min", sms: "SMS", data: "MB" };
const money = (n: number) => n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const allowance = (a: Record<string, number>) =>
  Object.entries(a).filter(([, v]) => v > 0).map(([k, v]) => `${k === "data" && v >= 1024 ? `${(v / 1024).toFixed(v % 1024 ? 1 : 0)} GB` : `${v} ${UNIT[k] ?? k}`}`).join(" · ") || "—";

function errorText(e: unknown, fallback: string) {
  return e instanceof Error && e.message ? e.message : fallback;
}

function StatusChip({ line }: { line: Line }) {
  if (line.status === "terminated") return <Chip tone="slate">terminated</Chip>;
  if (line.status === "suspended") return <Chip tone="amber">suspended</Chip>;
  if (line.barrings.length) return <Chip tone="red">barred</Chip>;
  return <Chip tone="emerald">active</Chip>;
}

/* ---------------------------------------------------------------- page */

type View = "lines" | "catalog" | "vouchers" | "partners";

/**
 * Mobile Subscribers - an operator's, MVNO's or MVNE's prepaid and postpaid lines: who has
 * which number and SIM, wallets and validity, postpaid spend and limits, packs, barrings and
 * roaming; the plan and pack catalog; recharge vouchers; wholesale partners and settlements.
 * Every change runs an intent (INT-129..148), so it is checked, audited and can start flows.
 */
export default function MobilePage() {
  const { runIntent, canRun, role } = useWorkspace();
  const admin = role === "admin";
  const [view, setView] = useState<View>("lines");
  const [o, setO] = useState<Overview | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [payment, setPayment] = useState("");
  const [open, setOpen] = useState<LineDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    const params = new URLSearchParams({ q, status, payment_type: payment });
    Promise.all([mobileApi<Overview>("/overview"), mobileApi<Line[]>(`/lines?${params}`)])
      .then(([ov, ls]) => {
        setO(ov);
        setLines(ls);
      })
      .catch((e) => setError(errorText(e, "Could not load the mobile lines")));
  }, [q, status, payment]);
  useEffect(load, [load]);

  const openLine = useCallback((id: number) => {
    mobileApi<LineDetail>(`/lines/${id}`).then(setOpen).catch((e) => setError(errorText(e, "Could not open the line")));
  }, []);
  useIntentRefresh(() => {
    load();
    if (open) openLine(open.id);
  });

  const act = (code: string, entities: Record<string, unknown> = {}) => runIntent(code, entities);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-slate-100">Mobile Subscribers</h1>
          <p className="text-sm text-slate-400">Prepaid and postpaid lines, packs, SIMs and numbers, vouchers and wholesale partners.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {canRun("INT-136") && <button type="button" className={BTN_PRIMARY} onClick={() => act("INT-136")}>📱 Activate lines</button>}
          {canRun("INT-129") && <button type="button" className={BTN} onClick={() => act("INT-129")}>💳 Recharge</button>}
          {canRun("INT-144") && <button type="button" className={BTN} onClick={() => act("INT-144")}>📶 Charge usage</button>}
        </div>
      </div>
      <Notice text={error} onClose={() => setError(null)} />

      {o && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
          <Stat label="Lines" value={o.lines} sub={`${o.prepaid} prepaid · ${o.postpaid} postpaid`} />
          <Stat label="Active" value={o.active} sub={`${o.suspended} suspended`} tone="emerald" onClick={() => { setView("lines"); setStatus("active"); }} />
          <Stat label="Barred" value={o.barred} tone={o.barred ? "red" : undefined} />
          <Stat label="Roaming" value={o.roaming} />
          <Stat label="Prepaid wallets" value={money(o.prepaid_balance)} sub="held for subscribers" />
          <Stat label="Postpaid spend" value={money(o.postpaid_spend)} sub="this cycle" />
        </div>
      )}

      <Tabs value={view} onChange={setView} items={[
        { id: "lines", label: "Lines", count: o?.lines },
        { id: "catalog", label: "Plans & packs" },
        { id: "vouchers", label: "Vouchers", count: o?.vouchers_available },
        { id: "partners", label: "Partners", count: o?.partners },
      ]} />

      {view === "lines" && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
          <Section title="Lines" count={lines.length} action={
            <div className="flex flex-wrap gap-2">
              <input className={`${INPUT} w-48`} placeholder="Number, ICCID, customer…" value={q} onChange={(e) => setQ(e.target.value)} />
              <select className={`${INPUT} w-32`} value={payment} onChange={(e) => setPayment(e.target.value)} aria-label="Payment type">
                <option value="">All types</option><option value="prepaid">Prepaid</option><option value="postpaid">Postpaid</option>
              </select>
              <select className={`${INPUT} w-32`} value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
                <option value="">Any status</option><option value="active">Active</option><option value="suspended">Suspended</option><option value="terminated">Terminated</option>
              </select>
            </div>
          }>
            {lines.length === 0 ? <Empty>No lines yet - activate one for a customer (an SME's lines in one go).</Empty> : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[720px] text-sm">
                  <thead className="text-left text-[11px] uppercase tracking-wide text-slate-500">
                    <tr><th className="py-2">Number</th><th>Customer / user</th><th>Plan</th><th>Type</th><th className="text-right">Balance / spend</th><th>Status</th></tr>
                  </thead>
                  <tbody>
                    {lines.map((l) => (
                      <tr key={l.id} className="cursor-pointer border-t border-slate-800 hover:bg-slate-800/30" onClick={() => openLine(l.id)}>
                        <td className="py-2 font-mono text-slate-100">{l.msisdn}{l.roaming && <span title="Roaming"> ✈️</span>}</td>
                        <td className="text-slate-300">{l.customer}{l.user_name && <span className="text-slate-500"> · {l.user_name}</span>}</td>
                        <td className="text-slate-300">{l.plan}{l.partner && <span className="text-slate-500"> · {l.partner}</span>}</td>
                        <td><Chip tone={l.payment_type === "prepaid" ? "sky" : "violet"}>{l.payment_type}</Chip></td>
                        <td className="text-right tabular-nums text-slate-200">
                          {l.payment_type === "prepaid" ? money(l.balance) : `${money(l.cycle_spend)}${l.credit_limit ? ` / ${money(l.credit_limit)}` : ""}`}
                        </td>
                        <td><StatusChip line={l} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Section>
          <Panel title="Recent activity">
            {o?.recent.length ? (
              <ul className="space-y-2 text-xs">
                {o.recent.map((r, i) => (
                  <li key={i} className="cursor-pointer rounded-lg border border-slate-800 p-2 hover:border-slate-600" onClick={() => openLine(r.line_id)}>
                    <div className="text-slate-200">{r.summary}</div>
                    <div className="text-slate-500">{new Date(r.at).toLocaleString()}</div>
                  </li>
                ))}
              </ul>
            ) : <Empty>Nothing yet.</Empty>}
          </Panel>
        </div>
      )}

      {view === "catalog" && <CatalogView admin={admin} onError={setError} />}
      {view === "vouchers" && <VouchersView admin={admin} onError={setError} act={act} canRun={canRun} />}
      {view === "partners" && <PartnersView act={act} canRun={canRun} onError={setError} />}

      <Drawer open={open !== null} onClose={() => setOpen(null)} wide title={open ? <span className="font-mono">{open.msisdn}</span> : ""}
        subtitle={open ? `${open.customer}${open.user_name ? ` · ${open.user_name}` : ""} · ${open.plan} · ${open.payment_type}` : ""}>
        {open && <LineView line={open} act={act} canRun={canRun} onOpen={openLine} lines={lines} />}
      </Drawer>
    </div>
  );
}

/* ---------------------------------------------------------------- one line */

function LineView({ line, act, canRun, onOpen, lines }: { line: LineDetail; act: (c: string, e?: Record<string, unknown>) => void;
  canRun: (c: string) => boolean; onOpen: (id: number) => void; lines: Line[] }) {
  const m = { msisdn: line.msisdn };
  const live = line.status !== "terminated";
  const prepaid = line.payment_type === "prepaid";
  const button = (code: string, label: string, entities: Record<string, unknown> = m, cls = BTN) =>
    canRun(code) && <button key={code + label} type="button" className={cls} onClick={() => act(code, entities)}>{label}</button>;
  const head = line.group_head_id ? lines.find((l) => l.id === line.group_head_id) : null;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <StatusChip line={line} />
        {line.barrings.map((b) => <Chip key={b} tone="red">{b.replace("_", " ")}</Chip>)}
        {line.roaming && <Chip tone="sky">roaming</Chip>}
        <Chip>{line.sim_type === "esim" ? "eSIM" : "SIM"} {line.iccid}</Chip>
        {line.partner && <Chip tone="violet">{line.partner}</Chip>}
      </div>
      {line.status_reason && <p className="text-xs text-slate-400">{line.status} - {line.status_reason}</p>}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {prepaid ? (
          <>
            <Stat label="Balance" value={money(line.balance)} tone={line.balance <= 0 ? "red" : undefined} />
            <Stat label="Valid until" value={line.valid_until ?? "—"} />
            <Stat label="Emergency credit owed" value={money(line.advance_owed)} />
          </>
        ) : (
          <>
            <Stat label="Spend this cycle" value={money(line.cycle_spend)} sub={line.cycle_start ? `since ${line.cycle_start}` : ""} />
            <Stat label="Credit limit" value={line.credit_limit ? money(line.credit_limit) : "none"} sub={`alert at ${line.spend_alert_pct}%`} />
            <Stat label="Deposit" value={money(line.deposit)} />
          </>
        )}
        <Stat label="Left" value={allowance(line.remaining)} />
      </div>

      {live && (
        <Panel title="Actions">
          <div className="flex flex-wrap gap-2">
            {prepaid && button("INT-129", "💳 Recharge", m, BTN_PRIMARY)}
            {button("INT-130", "📦 Add a pack")}
            {button("INT-142", "🧾 Change plan")}
            {prepaid && button("INT-132", "🔀 Transfer balance")}
            {prepaid && button("INT-133", "🆘 Emergency credit")}
            {!prepaid && button("INT-135", "🛑 Spend limit")}
            {!prepaid && button("INT-148", "🏦 Deposit")}
            {button("INT-134", prepaid ? "🔄 Move to postpaid" : "🔄 Move to prepaid", { ...m, to_payment_type: prepaid ? "postpaid" : "prepaid" })}
            {button("INT-140", line.roaming ? "✈️ Roaming off" : "✈️ Roaming on", { ...m, action: line.roaming ? "off" : "on" })}
            {button("INT-141", "👨‍👩‍👧 Add to its shared plan", { ...m, action: "add" })}
            {button("INT-137", "🔁 Swap SIM", m, BTN_WARN)}
            {button("INT-138", "🔢 Change number")}
            {button("INT-139", "⛔ Bar / unbar", m, BTN_WARN)}
            {line.status === "active" ? button("INT-143", "⏸️ Suspend", { ...m, action: "suspend" }) : button("INT-143", "▶️ Resume", { ...m, action: "resume" })}
            {button("INT-143", "✖ Terminate", { ...m, action: "terminate" }, BTN_DANGER)}
          </div>
        </Panel>
      )}

      <Section title="Packs" count={line.packs.length}>
        {line.packs.length === 0 ? <Empty>No active packs.</Empty> : (
          <ul className="space-y-2">
            {line.packs.map((p) => (
              <li key={p.id} className={`${CARD} flex flex-wrap items-center justify-between gap-2 p-3 text-sm`}>
                <div>
                  <div className="font-semibold text-slate-100">{p.name} {p.kind === "plan" && <Chip tone="sky">plan</Chip>} {p.roaming && <Chip tone="violet">roaming</Chip>}</div>
                  <div className="text-xs text-slate-400">left {allowance(p.remaining)} of {allowance(p.granted)} · until {new Date(p.valid_to).toLocaleDateString()}
                    {p.auto_renew ? " · renews" : ""} · {p.paid_from}</div>
                </div>
                {p.kind !== "plan" && canRun("INT-130") && (
                  <button type="button" className={BTN} onClick={() => act("INT-130", { msisdn: line.msisdn, pack: p.code, action: "remove" })}>Remove</button>
                )}
              </li>
            ))}
          </ul>
        )}
        {line.shared_from && (
          <p className="mt-2 text-xs text-slate-400">Also draws on the shared plan of{" "}
            {head ? <button type="button" className="text-sky-300 underline" onClick={() => onOpen(head.id)}>{line.shared_from.msisdn}</button> : line.shared_from.msisdn}.</p>
        )}
        {line.members.length > 0 && <p className="mt-2 text-xs text-slate-400">Shared with: {line.members.join(", ")}</p>}
      </Section>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title={prepaid ? "Wallet" : "Charges"} count={line.wallet.length}>
          {line.wallet.length === 0 ? <Empty>No movements.</Empty> : (
            <table className="w-full text-xs">
              <tbody>
                {line.wallet.map((w, i) => (
                  <tr key={i} className="border-t border-slate-800">
                    <td className="py-1.5 text-slate-400">{new Date(w.at).toLocaleString()}</td>
                    <td className="text-slate-300">{w.kind.replace("_", " ")}{w.reference && <span className="text-slate-500"> · {w.reference}</span>}</td>
                    <td className={`text-right tabular-nums ${w.amount < 0 ? "text-red-300" : "text-emerald-300"}`}>{money(w.amount)}</td>
                    <td className="text-right tabular-nums text-slate-400">{money(w.balance_after)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
        <Section title="Usage" count={line.usage.length}>
          {line.usage.length === 0 ? <Empty>No usage yet.</Empty> : (
            <table className="w-full text-xs">
              <tbody>
                {line.usage.map((u) => (
                  <tr key={u.id} className="border-t border-slate-800">
                    <td className="py-1.5 text-slate-400">{new Date(u.at).toLocaleString()}</td>
                    <td className="text-slate-300">{u.quantity} {UNIT[u.event_type]}{u.international ? " · intl" : ""}{u.rate_plan === "roaming" ? " · roaming" : ""}</td>
                    <td className="text-right tabular-nums text-slate-400">{u.amount ? money(u.amount) : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      </div>

      <Section title="History" count={line.history.length}>
        <ul className="space-y-1 text-xs">
          {line.history.map((h, i) => (
            <li key={i} className="flex gap-2"><span className="shrink-0 text-slate-500">{new Date(h.at).toLocaleString()}</span><span className="text-slate-300">{h.summary}</span></li>
          ))}
        </ul>
      </Section>
    </div>
  );
}

/* ---------------------------------------------------------------- catalog */

function CatalogView({ admin, onError }: { admin: boolean; onError: (s: string | null) => void }) {
  const [c, setC] = useState<Catalog | null>(null);
  const [draft, setDraft] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  useEffect(() => {
    mobileApi<Catalog>("/catalog").then((data) => {
      setC(data);
      const { pack_kinds: _kinds, ...rest } = data;
      setDraft(JSON.stringify(rest, null, 2));
    }).catch((e) => onError(errorText(e, "Could not load the catalog")));
  }, [onError]);
  if (!c) return <Empty>Loading…</Empty>;
  const save = () => {
    let body: unknown;
    try {
      body = JSON.parse(draft);
    } catch {
      onError("That isn't valid JSON");
      return;
    }
    mobileApi<Catalog>("/catalog", "PUT", body).then((data) => {
      setC(data);
      setSaved("Saved - new activations, recharges and packs use it straight away.");
    }).catch((e) => onError(errorText(e, "Could not save")));
  };
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Plans" count={c.plans.length}>
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] uppercase text-slate-500"><tr><th className="py-1">Plan</th><th>Type</th><th className="text-right">Monthly</th><th>Allowance</th></tr></thead>
            <tbody>
              {c.plans.map((p) => (
                <tr key={p.code} className="border-t border-slate-800">
                  <td className="py-1.5"><div className="text-slate-100">{p.name}</div><div className="font-mono text-[11px] text-slate-500">{p.code}{p.shared ? " · shared" : ""}</div></td>
                  <td><Chip tone={p.payment_type === "prepaid" ? "sky" : "violet"}>{p.payment_type}</Chip></td>
                  <td className="text-right tabular-nums text-slate-200">{money(p.fee ?? 0)}</td>
                  <td className="text-xs text-slate-400">{allowance(p.allowance)} · {p.validity_days} d</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
        <Section title="Packs & value-added services" count={c.packs.length}>
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] uppercase text-slate-500"><tr><th className="py-1">Pack</th><th>Kind</th><th className="text-right">Price</th><th>Gives</th></tr></thead>
            <tbody>
              {c.packs.map((p) => (
                <tr key={p.code} className="border-t border-slate-800">
                  <td className="py-1.5"><div className="text-slate-100">{p.name}</div><div className="font-mono text-[11px] text-slate-500">{p.code}</div></td>
                  <td><Chip tone={p.roaming ? "violet" : "slate"}>{p.kind}</Chip></td>
                  <td className="text-right tabular-nums text-slate-200">{money(p.price ?? 0)}</td>
                  <td className="text-xs text-slate-400">{allowance(p.allowance)} · {p.validity_days} d</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      </div>
      {admin && (
        <Panel title="Edit the catalog and policy" action={<button type="button" className={BTN_PRIMARY} onClick={save}>Save</button>}>
          <p className="mb-2 text-xs text-slate-400">Plans, packs, number range, recharge limits, emergency credit, transfer limits, fees, quarantine and
            roaming policy. Allowances are in minutes (voice), messages (sms) and MB (data).</p>
          <Notice text={saved} tone="emerald" onClose={() => setSaved(null)} />
          <textarea className={`${INPUT} h-96 font-mono text-xs`} value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck={false} aria-label="Catalog JSON" />
        </Panel>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- vouchers */

function VouchersView({ admin, onError, act, canRun }: { admin: boolean; onError: (s: string | null) => void;
  act: (c: string, e?: Record<string, unknown>) => void; canRun: (c: string) => boolean }) {
  const [rows, setRows] = useState<Batch[]>([]);
  const load = useCallback(() => {
    mobileApi<Batch[]>("/vouchers/batches").then(setRows).catch((e) => onError(errorText(e, "Could not load the vouchers")));
  }, [onError]);
  useEffect(load, [load]);
  useIntentRefresh(load);
  const download = (batch: string) => {
    if (!window.confirm("The PINs can be downloaded only once - they are not kept after this. Download now?")) return;
    mobileVoucherPins(batch).then((csv) => {
      const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `${batch}-pins.csv`;
      a.click();
      URL.revokeObjectURL(url);
      load();
    }).catch((e) => onError(errorText(e, "Could not download the PINs")));
  };
  const block = (batch: string, on: boolean) =>
    mobileApi(`/vouchers/batches/${encodeURIComponent(batch)}/${on ? "block" : "unblock"}`, "POST").then(load).catch((e) => onError(errorText(e, "Could not change the batch")));
  return (
    <Section title="Voucher batches" count={rows.length} action={canRun("INT-145") && admin ? <button type="button" className={BTN_PRIMARY} onClick={() => act("INT-145")}>🎟️ Generate vouchers</button> : undefined}>
      {rows.length === 0 ? <Empty>No vouchers yet.</Empty> : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="text-left text-[11px] uppercase text-slate-500">
              <tr><th className="py-1">Batch</th><th className="text-right">Value</th><th className="text-right">Count</th><th className="text-right">Redeemed</th><th className="text-right">Left</th><th>Expires</th><th /></tr>
            </thead>
            <tbody>
              {rows.map((b) => (
                <tr key={b.batch} className="border-t border-slate-800">
                  <td className="py-1.5 font-mono text-slate-100">{b.batch}</td>
                  <td className="text-right tabular-nums">{money(b.face_value)}</td>
                  <td className="text-right tabular-nums">{b.count}</td>
                  <td className="text-right tabular-nums">{b.redeemed}</td>
                  <td className="text-right tabular-nums">{b.available}{b.blocked ? <span className="text-amber-300"> ({b.blocked} blocked)</span> : null}</td>
                  <td className="text-slate-400">{b.expires_on}</td>
                  <td className="space-x-2 text-right">
                    {admin && b.pins_pending && <button type="button" className={BTN_WARN} onClick={() => download(b.batch)}>Download PINs (once)</button>}
                    {admin && (b.available > 0 ? <button type="button" className={BTN} onClick={() => block(b.batch, true)}>Block</button>
                      : b.blocked > 0 ? <button type="button" className={BTN} onClick={() => block(b.batch, false)}>Unblock</button> : null)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

/* ---------------------------------------------------------------- partners */

function PartnersView({ act, canRun, onError }: { act: (c: string, e?: Record<string, unknown>) => void; canRun: (c: string) => boolean;
  onError: (s: string | null) => void }) {
  const [partners, setPartners] = useState<Partner[]>([]);
  const [settlements, setSettlements] = useState<Settlement[]>([]);
  const load = useCallback(() => {
    Promise.all([mobileApi<{ partners: Partner[] }>("/partners"), mobileApi<Settlement[]>("/settlements")])
      .then(([p, s]) => {
        setPartners(p.partners);
        setSettlements(s);
      }).catch((e) => onError(errorText(e, "Could not load the partners")));
  }, [onError]);
  useEffect(load, [load]);
  useIntentRefresh(load);
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Section title="Wholesale partners" count={partners.length}
        action={canRun("INT-146") ? <button type="button" className={BTN_PRIMARY} onClick={() => act("INT-146")}>🤝 Onboard a partner</button> : undefined}>
        {partners.length === 0 ? <Empty>No MVNOs, MVNEs, resellers or dealers yet.</Empty> : (
          <ul className="space-y-2">
            {partners.map((p) => (
              <li key={p.id} className={`${CARD} p-3 text-sm`}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="font-semibold text-slate-100">{p.name} <Chip tone="violet">{p.kind}</Chip> {p.status !== "active" && <Chip tone="amber">{p.status}</Chip>}</div>
                  <div className="flex gap-2">
                    {canRun("INT-147") && <button type="button" className={BTN} onClick={() => act("INT-147", { wholesale_partner: p.name })}>Settle</button>}
                    {canRun("INT-146") && <button type="button" className={BTN} onClick={() => act("INT-146", { wholesale_partner: p.name })}>Change</button>}
                  </div>
                </div>
                <div className="mt-1 text-xs text-slate-400">
                  {p.lines} line(s){p.number_range ? ` · numbers ${p.number_range}` : ""}
                  {Object.keys(p.wholesale_rates).length ? ` · rates ${Object.entries(p.wholesale_rates).map(([k, v]) => `${k} ${v}`).join(", ")}` : ""}
                  {p.revenue_share_pct ? ` · ${p.revenue_share_pct}% commission` : ""}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Section>
      <Section title="Settlements" count={settlements.length}>
        {settlements.length === 0 ? <Empty>No settlements yet.</Empty> : (
          <ul className="space-y-2">
            {settlements.map((s) => (
              <li key={s.id} className={`${CARD} p-3 text-sm`}>
                <div className="flex flex-wrap justify-between gap-2">
                  <span className="font-semibold text-slate-100">{s.number} · {s.partner}</span>
                  <span className={`tabular-nums ${s.net < 0 ? "text-amber-300" : "text-emerald-300"}`}>{s.net < 0 ? `we owe ${money(-s.net)}` : `owes us ${money(s.net)}`}</span>
                </div>
                <div className="text-xs text-slate-400">{s.period_start} – {s.period_end}</div>
                <ul className="mt-1 text-xs text-slate-400">
                  {s.lines.map((l, i) => <li key={i}>{l.label}: {l.quantity} × {l.rate} = {money(l.amount)}</li>)}
                </ul>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}
