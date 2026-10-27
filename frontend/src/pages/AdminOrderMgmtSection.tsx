import { useCallback, useEffect, useState } from "react";
import { listParties, omApi } from "../api/client";
import type { Party } from "../api/types";
import { BTN, BTN_DANGER, BTN_PRIMARY, CARD, Chip, Empty, Field, INPUT, Notice, Tabs } from "./fieldservice/ui";
import { money, useAction } from "./fieldservice/style";

type Kind = "tax-rules" | "shipping-methods" | "favorites" | "recommendations" | "promotions";
type Row = Record<string, unknown> & { id: number };

/** One editable field of a setup row. */
type Col = { key: string; label: string; type?: "number" | "bool" | "list" | "items" | "date" | "pick"; values?: string[]; wide?: boolean };

const KINDS: { id: Kind; label: string; blurb: string; cols: Col[]; title: (r: Row) => string; sub: (r: Row) => string }[] = [
  {
    id: "tax-rules", label: "Tax", blurb: "Rates by jurisdiction - a jurisdiction's own rules replace the 'ALL' ones.",
    cols: [{ key: "name", label: "Name" }, { key: "jurisdiction", label: "Jurisdiction (region, or ALL)" }, { key: "rate_pct", label: "Rate %", type: "number" },
      { key: "applies_to", label: "Applies to", type: "pick", values: ["all", "service", "equipment", "one_time", "recurring"] }, { key: "active", label: "Active", type: "bool" }],
    title: (r) => `${r.name} ${r.rate_pct}%`, sub: (r) => `${r.jurisdiction} · ${String(r.applies_to).replace("_", " ")}`,
  },
  {
    id: "shipping-methods", label: "Shipping", blurb: "Freight = base + per unit of equipment, free above an order value.",
    cols: [{ key: "name", label: "Name" }, { key: "carrier", label: "Carrier" }, { key: "base_charge", label: "Base charge", type: "number" },
      { key: "per_unit", label: "Per unit", type: "number" }, { key: "free_over", label: "Free over", type: "number" }, { key: "days", label: "Days", type: "number" },
      { key: "active", label: "Active", type: "bool" }],
    title: (r) => String(r.name), sub: (r) => `${r.carrier || "any carrier"} · ${money(Number(r.base_charge))} + ${money(Number(r.per_unit))}/unit · ${r.days} days${r.free_over ? ` · free over ${money(Number(r.free_over))}` : ""}`,
  },
  {
    id: "favorites", label: "Favorites", blurb: "Sets of products sellers add to a quote or order in one go.",
    cols: [{ key: "name", label: "Name" }, { key: "items", label: "Products (one per line: 2 x Router CPE)", type: "items", wide: true }, { key: "active", label: "Active", type: "bool" }],
    title: (r) => String(r.name), sub: (r) => ((r.items as { product: string; quantity: number }[]) ?? []).map((i) => `${i.quantity} x ${i.product}`).join(", "),
  },
  {
    id: "recommendations", label: "Recommendations", blurb: "When a quote or order has the product, the recommended one is suggested.",
    cols: [{ key: "product", label: "When it has" }, { key: "recommended_product", label: "Suggest" }, { key: "kind", label: "Kind", type: "pick", values: ["cross_sell", "up_sell"] },
      { key: "score", label: "Score", type: "number" }, { key: "message", label: "Message", wide: true }, { key: "active", label: "Active", type: "bool" }],
    title: (r) => `${r.product} → ${r.recommended_product}`, sub: (r) => `${String(r.kind).replace("_", "-")} · score ${r.score}${r.message ? ` · ${r.message}` : ""}`,
  },
  {
    id: "promotions", label: "Promotions", blurb: "A discount on qualifying lines, with a commitment and an early-termination fee.",
    cols: [{ key: "name", label: "Name" }, { key: "code", label: "Code" }, { key: "products", label: "Qualifying products (comma separated)", type: "list", wide: true },
      { key: "discount_pct", label: "Discount %", type: "number" }, { key: "discount_amount", label: "Or amount off", type: "number" },
      { key: "commitment_months", label: "Commitment (months)", type: "number" }, { key: "early_termination_fee", label: "Early termination fee", type: "number" },
      { key: "valid_from", label: "Valid from", type: "date" }, { key: "valid_to", label: "Valid to", type: "date" }, { key: "active", label: "Active", type: "bool" }],
    title: (r) => `${r.name}${r.code ? ` (${r.code})` : ""}`, sub: (r) => `${r.discount_pct ? `${r.discount_pct}% off` : money(Number(r.discount_amount)) + " off"} · ${((r.products as string[]) ?? []).join(", ") || "any line"}${r.commitment_months ? ` · ${r.commitment_months}-month commitment` : ""}`,
  },
];

export default function AdminOrderMgmtSection() {
  const [tab, setTab] = useState<Kind | "credit" | "b2c">("b2c");
  const act = useAction();
  const [nonce, setNonce] = useState(0);
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-xl font-bold text-slate-100">Order Management</h2>
        <p className="text-sm text-slate-400">Tax, shipping, favorites, recommendations, promotions and customers' credit limits - what quotes and orders are priced and checked with.</p>
      </div>
      <div className={`${CARD} flex flex-wrap items-center gap-3 p-4`}>
        <p className="min-w-0 flex-1 text-sm text-slate-300">Starter setup: VAT 15%, Standard and Express shipping, a favorite, two recommendations and a promotion - only what is missing is added.</p>
        <button
          type="button"
          className={BTN_PRIMARY}
          disabled={act.busy}
          onClick={async () => {
            const out = await act.run(() => omApi<{ created: string[] }>("/setup/sample", "POST", {}, true));
            if (out) {
              act.setDone(out.created.length ? `Added: ${out.created.join(", ")}` : "Everything was already set up.");
              setNonce((n) => n + 1);
            }
          }}
        >
          Load starter setup
        </button>
      </div>
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <Notice text={act.done} tone="emerald" onClose={() => act.setDone(null)} />
      <Tabs
        value={tab}
        onChange={setTab}
        items={[{ id: "b2c", label: "B2C order journey" }, ...KINDS.map((k) => ({ id: k.id as Kind | "credit" | "b2c", label: k.label })), { id: "credit", label: "Credit limits" }]}
      />
      {tab === "b2c" ? <B2cJourney /> : tab === "credit" ? <CreditLimits key={nonce} /> : <SetupList key={`${tab}-${nonce}`} spec={KINDS.find((k) => k.id === tab)!} />}
    </div>
  );
}

function toForm(cols: Col[], row?: Row): Record<string, string | boolean> {
  const out: Record<string, string | boolean> = {};
  for (const c of cols) {
    const v = row?.[c.key];
    if (c.type === "bool") out[c.key] = row ? Boolean(v) : true;
    else if (c.type === "items") out[c.key] = ((v as { product: string; quantity: number }[]) ?? []).map((i) => `${i.quantity} x ${i.product}`).join("\n");
    else if (c.type === "list") out[c.key] = ((v as string[]) ?? []).join(", ");
    else out[c.key] = v === null || v === undefined ? "" : String(v);
  }
  return out;
}

function fromForm(cols: Col[], form: Record<string, string | boolean>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const c of cols) {
    const v = form[c.key];
    if (c.type === "bool") out[c.key] = Boolean(v);
    else if (c.type === "number") out[c.key] = v === "" ? null : Number(v);
    else if (c.type === "list") out[c.key] = String(v).split(",").map((s) => s.trim()).filter(Boolean);
    else if (c.type === "items")
      out[c.key] = String(v)
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean)
        .map((l) => {
          const m = l.match(/^(\d+(?:\.\d+)?)\s*[x×]\s*(.+)$/i);
          return { quantity: m ? Number(m[1]) : 1, product: (m ? m[2] : l).trim() };
        });
    else out[c.key] = v === "" ? null : v;
  }
  return out;
}

function SetupList({ spec }: { spec: (typeof KINDS)[number] }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [editing, setEditing] = useState<Row | "new" | null>(null);
  const [form, setForm] = useState<Record<string, string | boolean>>({});
  const load = useCallback(() => {
    omApi<Row[]>(`/${spec.id}`, "GET", undefined, true).then(setRows).catch(() => setRows([]));
  }, [spec.id]);
  useEffect(load, [load]);
  const act = useAction(load);
  const open = (r: Row | "new") => {
    setEditing(r);
    setForm(toForm(spec.cols, r === "new" ? undefined : r));
  };
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <p className="min-w-0 flex-1 text-sm text-slate-400">{spec.blurb}</p>
        <button type="button" className={BTN} onClick={() => open("new")}>
          + Add
        </button>
      </div>
      <Notice text={act.error} onClose={() => act.setError(null)} />
      {editing && (
        <div className={`${CARD} space-y-3 p-4`}>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {spec.cols.map((c) => (
              <Field key={c.key} label={c.label} className={c.wide ? "sm:col-span-2 lg:col-span-3" : ""}>
                {c.type === "bool" ? (
                  <input type="checkbox" className="h-4 w-4" checked={Boolean(form[c.key])} onChange={(e) => setForm({ ...form, [c.key]: e.target.checked })} />
                ) : c.type === "pick" ? (
                  <select className={INPUT} value={String(form[c.key] || c.values?.[0])} onChange={(e) => setForm({ ...form, [c.key]: e.target.value })}>
                    {c.values?.map((v) => (
                      <option key={v} value={v}>
                        {v.replace("_", " ")}
                      </option>
                    ))}
                  </select>
                ) : c.type === "items" ? (
                  <textarea className={INPUT} rows={3} value={String(form[c.key] ?? "")} onChange={(e) => setForm({ ...form, [c.key]: e.target.value })} />
                ) : (
                  <input
                    className={INPUT}
                    type={c.type === "number" ? "number" : c.type === "date" ? "date" : "text"}
                    value={String(form[c.key] ?? "")}
                    onChange={(e) => setForm({ ...form, [c.key]: e.target.value })}
                  />
                )}
              </Field>
            ))}
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              className={BTN_PRIMARY}
              disabled={act.busy}
              onClick={async () => {
                const body = fromForm(spec.cols, form);
                const pick = spec.cols.find((c) => c.type === "pick");
                if (pick && !body[pick.key]) body[pick.key] = pick.values?.[0];
                const ok = await act.run(() => (editing === "new" ? omApi(`/${spec.id}`, "POST", body, true) : omApi(`/${spec.id}/${editing.id}`, "PUT", body, true)));
                if (ok) setEditing(null);
              }}
            >
              Save
            </button>
            <button type="button" className={BTN} onClick={() => setEditing(null)}>
              Close
            </button>
            {editing !== "new" && (
              <button
                type="button"
                className={`${BTN_DANGER} ml-auto`}
                onClick={async () => {
                  const ok = await act.run(() => omApi(`/${spec.id}/${editing.id}`, "DELETE", undefined, true));
                  if (ok) setEditing(null);
                }}
              >
                Delete
              </button>
            )}
          </div>
        </div>
      )}
      {rows.length === 0 ? (
        <Empty>Nothing set up yet.</Empty>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {rows.map((r) => (
            <button key={r.id} type="button" onClick={() => open(r)} className={`${CARD} p-4 text-left transition hover:border-slate-600 ${r.active === false ? "opacity-50" : ""}`}>
              <div className="flex items-start gap-2">
                <p className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-100">{spec.title(r)}</p>
                {r.active === false && <Chip>off</Chip>}
              </div>
              <p className="mt-1 line-clamp-2 text-xs text-slate-400">{spec.sub(r)}</p>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

interface Journey {
  settings: { direct_order: boolean; auto_submit: boolean; require_installation_address: boolean; channels: string[] };
  channels: { key: string; label: string }[];
}

const STEPS_DIRECT = ["B2C prospect / consumer", "Place a B2C order (INT-104)", "Order Module checks: service at the address, eligibility, stock, credit", "Submitted & booked", "Fulfilment: install → test → accept → billing"];
const STEPS_LEAD = ["B2C prospect", "Lead (INT-46)", "Opportunity (INT-17)", "Quote (INT-05)", "Order"];

/** Vanilla B2C: prospect -> order, no lead / opportunity / quote / Presales; orders from any channel. */
function B2cJourney() {
  const [data, setData] = useState<Journey | null>(null);
  const load = useCallback(() => {
    omApi<Journey>("/b2c-journey", "GET", undefined, true).then(setData).catch(() => setData(null));
  }, []);
  useEffect(load, [load]);
  const act = useAction(load);
  if (!data) return <Empty>Loading…</Empty>;
  const s = data.settings;
  const save = (patch: Partial<Journey["settings"]>) =>
    act.run(() => omApi<Journey>("/b2c-journey", "PUT", patch, true).then((out) => {
      setData(out);
      act.setDone("Saved.");
      return out;
    }));
  const steps = s.direct_order ? STEPS_DIRECT : STEPS_LEAD;
  return (
    <div className="space-y-4">
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <Notice text={act.done} tone="emerald" onClose={() => act.setDone(null)} />
      <div className={`${CARD} space-y-3 p-4`}>
        <div className="flex flex-wrap items-center gap-3">
          <p className="min-w-0 flex-1 text-sm font-semibold text-slate-100">{s.direct_order ? "B2C goes straight to an order (vanilla)" : "B2C follows the lead path"}</p>
          <button type="button" className={s.direct_order ? BTN : BTN_PRIMARY} disabled={act.busy} onClick={() => save({ direct_order: !s.direct_order })}>
            {s.direct_order ? "Use the lead path for B2C instead" : "Back to direct orders (vanilla)"}
          </button>
        </div>
        <ol className="flex flex-wrap items-center gap-2 text-xs">
          {steps.map((step, i) => (
            <li key={step} className="flex items-center gap-2">
              <span className={`rounded-full px-2.5 py-1 ${s.direct_order ? "bg-emerald-500/15 text-emerald-200" : "bg-slate-800 text-slate-300"}`}>{step}</span>
              {i < steps.length - 1 && <span className="text-slate-600">→</span>}
            </li>
          ))}
        </ol>
        <p className="text-xs text-slate-400">
          {s.direct_order
            ? "No lead, opportunity, quote or Presales check for B2C - those steps are refused for B2C customers. Adding a B2C prospect with products places the order at once; without products, the next step offered is the order."
            : "B2C prospects can take leads, opportunities and quotes like B2B. Orders can still be placed directly with INT-104."}
        </p>
      </div>
      <div className={`${CARD} grid gap-3 p-4 sm:grid-cols-2`}>
        <label className="flex items-center gap-2 text-sm text-slate-200">
          <input type="checkbox" className="h-4 w-4" checked={s.auto_submit} onChange={(e) => save({ auto_submit: e.target.checked })} />
          Submit and book a feasible order straight away
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-200">
          <input type="checkbox" className="h-4 w-4" checked={s.require_installation_address} onChange={(e) => save({ require_installation_address: e.target.checked })} />
          Require the installation address (service feasibility runs on it)
        </label>
      </div>
      <div className={`${CARD} space-y-2 p-4`}>
        <p className="text-sm font-semibold text-slate-100">Order channels</p>
        <p className="text-xs text-slate-400">Where B2C orders can come in - one order can start on one channel and continue on another; every touch is on the order's timeline.</p>
        <div className="flex flex-wrap gap-2">
          {data.channels.map((c) => {
            const on = s.channels.includes(c.key);
            return (
              <button
                key={c.key}
                type="button"
                aria-pressed={on}
                disabled={act.busy || (on && s.channels.length === 1)}
                onClick={() => save({ channels: on ? s.channels.filter((x) => x !== c.key) : [...s.channels, c.key] })}
                className={`rounded-full border px-3 py-1.5 text-xs font-semibold ${on ? "border-sky-500 bg-sky-500/15 text-sky-200" : "border-slate-700 text-slate-400"}`}
              >
                {on ? "✓ " : ""}
                {c.label}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

interface Credit {
  party_id: number;
  party_name: string | null;
  credit_limit: number;
  payment_terms: string;
  on_hold: boolean;
  exposure: number;
}

function CreditLimits() {
  const [rows, setRows] = useState<Credit[]>([]);
  const [parties, setParties] = useState<Party[]>([]);
  const [q, setQ] = useState("");
  const [f, setF] = useState({ party_id: "", credit_limit: "", payment_terms: "Net 30", on_hold: false });
  const load = useCallback(() => {
    omApi<Credit[]>("/credit/profiles", "GET", undefined, true).then(setRows).catch(() => setRows([]));
  }, []);
  useEffect(load, [load]);
  useEffect(() => {
    const t = setTimeout(() => listParties(q || undefined).then((p) => setParties(p.slice(0, 50))).catch(() => setParties([])), 200);
    return () => clearTimeout(t);
  }, [q]);
  const act = useAction(load);
  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-400">Purchase-order and on-account payments are checked against the limit: what the customer owes + their open orders + this order.</p>
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <div className={`${CARD} grid gap-2 p-4 sm:grid-cols-2 lg:grid-cols-5`}>
        <Field label="Customer" className="lg:col-span-2">
          <input className={INPUT} placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
          <select className={`${INPUT} mt-2`} value={f.party_id} onChange={(e) => setF({ ...f, party_id: e.target.value })}>
            <option value="">— choose —</option>
            {parties.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Credit limit">
          <input className={INPUT} type="number" min={0} value={f.credit_limit} onChange={(e) => setF({ ...f, credit_limit: e.target.value })} />
        </Field>
        <Field label="Payment terms">
          <input className={INPUT} value={f.payment_terms} onChange={(e) => setF({ ...f, payment_terms: e.target.value })} />
        </Field>
        <Field label="Credit on hold">
          <input type="checkbox" className="h-4 w-4" checked={f.on_hold} onChange={(e) => setF({ ...f, on_hold: e.target.checked })} />
        </Field>
        <div className="sm:col-span-2 lg:col-span-5">
          <button
            type="button"
            className={BTN_PRIMARY}
            disabled={!f.party_id || act.busy}
            onClick={() =>
              act.run(() => omApi(`/credit/profiles/${f.party_id}`, "PUT", { credit_limit: Number(f.credit_limit || 0), payment_terms: f.payment_terms, on_hold: f.on_hold }, true))
            }
          >
            Save credit line
          </button>
        </div>
      </div>
      {rows.length === 0 ? (
        <Empty>No credit lines yet - orders paid by PO or on account go to review.</Empty>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {rows.map((r) => (
            <button
              key={r.party_id}
              type="button"
              onClick={() => setF({ party_id: String(r.party_id), credit_limit: String(r.credit_limit), payment_terms: r.payment_terms, on_hold: r.on_hold })}
              className={`${CARD} p-4 text-left hover:border-slate-600`}
            >
              <div className="flex items-center gap-2">
                <p className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-100">{r.party_name}</p>
                {r.on_hold && <Chip tone="red">on hold</Chip>}
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-800">
                <div className={`h-full rounded-full ${r.exposure > r.credit_limit ? "bg-red-400" : "bg-emerald-400"}`} style={{ width: `${Math.min(100, (100 * r.exposure) / Math.max(1, r.credit_limit))}%` }} />
              </div>
              <p className="mt-1 text-xs text-slate-400">
                {money(r.exposure)} used of {money(r.credit_limit)} · {r.payment_terms}
              </p>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
