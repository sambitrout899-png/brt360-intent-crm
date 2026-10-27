import { useCallback, useEffect, useState } from "react";
import { commsApi, listParties } from "../api/client";
import type { Party } from "../api/types";
import { BTN, BTN_DANGER, BTN_PRIMARY, CARD, Chip, Empty, Field, INPUT, Notice, Tabs } from "./fieldservice/ui";
import { useAction } from "./fieldservice/style";

type Kind = "providers" | "solutions" | "allowances";
type Row = Record<string, unknown> & { id: number };
type Col = { key: string; label: string; type?: "number" | "bool" | "date" | "pick" | "text" | "party"; values?: string[]; wide?: boolean };

const KINDS: { id: Kind; label: string; blurb: string; cols: Col[]; title: (r: Row) => string; sub: (r: Row) => string }[] = [
  {
    id: "providers", label: "Providers", blurb: "Who activates which part of an order - an order line goes to the first provider whose keyword is in its product name.",
    cols: [{ key: "name", label: "Name" }, { key: "keywords", label: "Product keywords (comma separated)", wide: true }, { key: "lead_days", label: "Lead time (days)", type: "number" },
      { key: "contact", label: "Contact" }, { key: "active", label: "Active", type: "bool" }],
    title: (r) => String(r.name), sub: (r) => `${r.keywords || "no keywords"} · ${r.lead_days} days`,
  },
  {
    id: "solutions", label: "Solutions", blurb: "The knowledge base agents search when resolving a ticket (Find a solution).",
    cols: [{ key: "title", label: "Title", wide: true }, { key: "problem", label: "Problem", type: "text", wide: true }, { key: "resolution", label: "Resolution", type: "text", wide: true },
      { key: "product_line", label: "Product line" }, { key: "keywords", label: "Keywords" }, { key: "published", label: "Published", type: "bool" }],
    title: (r) => String(r.title), sub: (r) => `${r.product_line || "any product"} · used ${r.uses ?? 0} times`,
  },
  {
    id: "allowances", label: "Allowances", blurb: "Included minutes, messages and data per customer - usage within them is free on the bill.",
    cols: [{ key: "party_id", label: "Customer", type: "party" }, { key: "name", label: "Name" }, { key: "event_type", label: "Usage", type: "pick", values: ["voice", "sms", "data"] },
      { key: "granted", label: "Included a period", type: "number" }, { key: "unit", label: "Unit" }, { key: "valid_from", label: "From", type: "date" }, { key: "valid_to", label: "To", type: "date" }],
    title: (r) => `${r.customer} · ${r.name}`, sub: (r) => `${r.granted} ${r.unit} ${r.event_type} a period`,
  },
];

/** The numeric / list policy settings an admin can tune. */
const SETTINGS: { key: string; label: string; hint: string }[] = [
  { key: "payment_terms_days", label: "Bill due after (days)", hint: "For new billing profiles" },
  { key: "grace_days", label: "Grace days", hint: "Before an overdue bill enters collections" },
  { key: "late_fee_days", label: "Late fee after (days overdue)", hint: "Once per bill" },
  { key: "late_fee_pct", label: "Late fee %", hint: "Of the overdue amount" },
  { key: "late_fee_min", label: "Minimum late fee", hint: "" },
  { key: "adjustment_auto_amount", label: "Agents may credit up to", hint: "Larger credits wait for a supervisor" },
  { key: "adjustment_auto_pct", label: "...and up to % of the item", hint: "" },
  { key: "arrangement_max_installments", label: "Most instalments", hint: "More is countered with this many" },
  { key: "arrangement_auto_amount", label: "Plans approved up to", hint: "Larger plans wait for a supervisor" },
  { key: "arrangement_interest_pct", label: "Plan interest % a month", hint: "" },
  { key: "arrangement_max_deferral_days", label: "Longest deferral (days)", hint: "" },
  { key: "fraud_contact_attempts", label: "Fraud: tries before disconnect", hint: "Failed attempts to reach the customer" },
  { key: "target_market_score", label: "Target market score", hint: "Opportunity qualification" },
];

export default function AdminCommsSection() {
  const [tab, setTab] = useState<Kind | "policy">("policy");
  const act = useAction();
  const [nonce, setNonce] = useState(0);
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-xl font-bold text-slate-100">Communications</h2>
        <p className="text-sm text-slate-400">Billing, collections and fraud policy, providers, the solutions knowledge base and customers' allowances.</p>
      </div>
      <div className={`${CARD} flex flex-wrap items-center gap-3 p-4`}>
        <p className="min-w-0 flex-1 text-sm text-slate-300">
          Starter setup: three providers and five solutions; for up to three customers with orders a billing profile, contact, allowances, two months of usage, an overdue
          bill (collections) and a suspicious day (fraud). Only what is missing is added.
        </p>
        <button
          type="button"
          className={BTN_PRIMARY}
          disabled={act.busy}
          onClick={async () => {
            const out = await act.run(() => commsApi<{ created: string[]; fraud_alerts: string[] }>("/setup/sample", "POST", {}, true));
            if (out) {
              act.setDone(`${out.created.length ? `Added: ${out.created.join(", ")}` : "Everything was already set up."}${out.fraud_alerts.length ? ` · fraud alerts ${out.fraud_alerts.join(", ")}` : ""}`);
              setNonce((n) => n + 1);
            }
          }}
        >
          Load starter setup
        </button>
      </div>
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <Notice text={act.done} tone="emerald" onClose={() => act.setDone(null)} />
      <Tabs value={tab} onChange={setTab} items={[{ id: "policy" as Kind | "policy", label: "Policy" }, ...KINDS.map((k) => ({ id: k.id as Kind | "policy", label: k.label }))]} />
      {tab === "policy" ? <Policy key={nonce} /> : <SetupList key={`${tab}-${nonce}`} spec={KINDS.find((k) => k.id === tab)!} />}
    </div>
  );
}

function Policy() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [ladder, setLadder] = useState("");
  const load = useCallback(() => {
    commsApi<Record<string, unknown>>("/settings", "GET", undefined, true)
      .then((d) => {
        setData(d);
        setForm(Object.fromEntries(SETTINGS.map((s) => [s.key, String(d[s.key] ?? "")])));
        setLadder(((d.collections_ladder as [number, string][]) ?? []).map(([days, a]) => `${days}: ${a}`).join("\n"));
      })
      .catch(() => setData(null));
  }, []);
  useEffect(load, [load]);
  const act = useAction(load);
  if (!data) return <p className="text-sm text-slate-500">Loading…</p>;
  const fraud = data.fraud_defaults as Record<string, number>;
  return (
    <div className="space-y-4">
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <Notice text={act.done} tone="emerald" onClose={() => act.setDone(null)} />
      <div className={`${CARD} grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3`}>
        {SETTINGS.map((s) => (
          <Field key={s.key} label={s.label} hint={s.hint}>
            <input className={INPUT} type="number" value={form[s.key] ?? ""} onChange={(e) => setForm({ ...form, [s.key]: e.target.value })} />
          </Field>
        ))}
        <Field label="Collections ladder" hint="One step a line - days overdue: action" className="sm:col-span-2 lg:col-span-3">
          <textarea className={INPUT} rows={7} value={ladder} onChange={(e) => setLadder(e.target.value)} />
        </Field>
        <div className="sm:col-span-2 lg:col-span-3">
          <button
            type="button"
            className={BTN_PRIMARY}
            disabled={act.busy}
            onClick={async () => {
              const body: Record<string, unknown> = Object.fromEntries(Object.entries(form).filter(([, v]) => v !== "").map(([k, v]) => [k, Number(v)]));
              body.collections_ladder = ladder
                .split("\n")
                .map((l) => l.match(/^\s*(\d+)\s*[:=-]\s*([\w_]+)\s*$/))
                .filter(Boolean)
                .map((m) => [Number(m![1]), m![2]]);
              if (await act.run(() => commsApi("/settings", "PUT", body, true))) act.setDone("Saved.");
            }}
          >
            Save policy
          </button>
        </div>
      </div>
      <div className={`${CARD} p-4`}>
        <p className="text-xs font-bold uppercase tracking-wider text-slate-400">Default fraud thresholds</p>
        <p className="mt-2 text-sm text-slate-300">
          {Object.entries(fraud).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" · ")}
        </p>
        <p className="mt-1 text-xs text-slate-500">Each account can have its own (Change fraud thresholds).</p>
      </div>
    </div>
  );
}

function SetupList({ spec }: { spec: (typeof KINDS)[number] }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [editing, setEditing] = useState<Row | "new" | null>(null);
  const [form, setForm] = useState<Record<string, string | boolean>>({});
  const [parties, setParties] = useState<Party[]>([]);
  const [q, setQ] = useState("");
  const load = useCallback(() => {
    commsApi<Row[]>(`/${spec.id}`, "GET", undefined, true).then(setRows).catch(() => setRows([]));
  }, [spec.id]);
  useEffect(load, [load]);
  useEffect(() => {
    if (!spec.cols.some((c) => c.type === "party")) return;
    const t = setTimeout(() => listParties(q || undefined).then((p) => setParties(p.slice(0, 50))).catch(() => setParties([])), 200);
    return () => clearTimeout(t);
  }, [q, spec.cols]);
  const act = useAction(load);
  const open = (r: Row | "new") => {
    setEditing(r);
    setForm(Object.fromEntries(spec.cols.map((c) => [c.key, c.type === "bool" ? (r === "new" ? true : Boolean(r[c.key])) : r === "new" ? "" : String(r[c.key] ?? "")])));
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
                        {v}
                      </option>
                    ))}
                  </select>
                ) : c.type === "party" ? (
                  <>
                    <input className={INPUT} placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
                    <select className={`${INPUT} mt-2`} value={String(form[c.key] ?? "")} onChange={(e) => setForm({ ...form, [c.key]: e.target.value })}>
                      <option value="">— choose —</option>
                      {parties.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                  </>
                ) : c.type === "text" ? (
                  <textarea className={INPUT} rows={3} value={String(form[c.key] ?? "")} onChange={(e) => setForm({ ...form, [c.key]: e.target.value })} />
                ) : (
                  <input className={INPUT} type={c.type === "number" ? "number" : c.type === "date" ? "date" : "text"} value={String(form[c.key] ?? "")}
                    onChange={(e) => setForm({ ...form, [c.key]: e.target.value })} />
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
                const body: Record<string, unknown> = {};
                for (const c of spec.cols) {
                  const v = form[c.key];
                  body[c.key] = c.type === "bool" ? Boolean(v) : c.type === "number" || c.type === "party" ? (v === "" ? null : Number(v)) : c.type === "pick" ? v || c.values?.[0] : v === "" ? null : v;
                }
                const ok = await act.run(() => (editing === "new" ? commsApi(`/${spec.id}`, "POST", body, true) : commsApi(`/${spec.id}/${editing.id}`, "PUT", body, true)));
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
                  if (await act.run(() => commsApi(`/${spec.id}/${editing.id}`, "DELETE", undefined, true))) setEditing(null);
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
            <button key={r.id} type="button" onClick={() => open(r)}
              className={`${CARD} p-4 text-left transition hover:border-slate-600 ${r.active === false || r.published === false ? "opacity-50" : ""}`}>
              <div className="flex items-start gap-2">
                <p className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-100">{spec.title(r)}</p>
                {(r.active === false || r.published === false) && <Chip>off</Chip>}
              </div>
              <p className="mt-1 line-clamp-2 text-xs text-slate-400">{spec.sub(r)}</p>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
