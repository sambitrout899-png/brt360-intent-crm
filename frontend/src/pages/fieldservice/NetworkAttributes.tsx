import { useCallback, useEffect, useState } from "react";
import { fsApi } from "../../api/client";
import { BTN, BTN_PRIMARY, Chip, INPUT, Notice, Panel } from "./ui";

type NetAttr = {
  name: string;
  value: string;
  default: string;
  source: string;
  resource_type: string | null;
  options: string[];
  required: boolean;
  modelled: boolean;
  saved: boolean;
  reason: string;
};
type NetProduct = { order_item_id: number; product: string; access_medium: string | null; attributes: NetAttr[] };
type Proposal = { location: string | null; address: string; products: NetProduct[]; summary: string; notes: string[]; engine: string | null; model?: string };

const SOURCE: Record<string, { label: string; tone: "sky" | "emerald" | "amber" | "slate" | "violet" }> = {
  inventory: { label: "from inventory", tone: "emerald" },
  default: { label: "AI default", tone: "violet" },
  engineer: { label: "read on site", tone: "amber" },
  model: { label: "product model", tone: "sky" },
  saved: { label: "saved", tone: "slate" },
};

/** Network-level attributes of an order's installation: the AI engine's defaults from the inventory set-up for the
 *  product and access medium chosen on the quote - the engineer keeps or overrides each, then saves. */
export default function NetworkAttributes({ workOrderId, editable, onSaved }: { workOrderId: number; editable: boolean; onSaved?: () => void }) {
  const [p, setP] = useState<Proposal | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [extra, setExtra] = useState<Record<number, { name: string; value: string }>>({});
  const [busy, setBusy] = useState<"load" | "save" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const k = (item: number, name: string) => `${item}::${name}`;

  const load = useCallback(async () => {
    setBusy("load");
    setError(null);
    try {
      const out = await fsApi<Proposal>(`/work-orders/${workOrderId}/network-attributes`);
      setP(out);
      const v: Record<string, string> = {};
      out.products.forEach((prod) => prod.attributes.forEach((a) => (v[k(prod.order_item_id, a.name)] = a.value)));
      setValues(v);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }, [workOrderId]);
  useEffect(() => {
    load();
  }, [load]);

  const save = async () => {
    if (!p) return;
    setBusy("save");
    setError(null);
    setDone(null);
    try {
      const products = p.products.map((prod) => {
        const vals: Record<string, string> = {};
        const defaults: Record<string, string> = {};
        prod.attributes.forEach((a) => {
          vals[a.name] = values[k(prod.order_item_id, a.name)] ?? "";
          defaults[a.name] = a.default;
        });
        const added = extra[prod.order_item_id];
        if (added?.name.trim() && added.value.trim()) vals[added.name.trim()] = added.value.trim();
        return { order_item_id: prod.order_item_id, values: vals, defaults };
      });
      const out = await fsApi<{ overridden: string[]; missing: string[] }>(`/work-orders/${workOrderId}/network-attributes`, "POST", { products });
      setDone(
        out.missing.length
          ? `Saved - still needed before the job can close: ${out.missing.join("; ")}`
          : `Saved${out.overridden.length ? ` - ${out.overridden.length} default${out.overridden.length === 1 ? "" : "s"} overridden` : ""}`
      );
      setExtra({});
      await load();
      onSaved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Panel
      title="Network attributes"
      action={
        <button type="button" className={BTN} disabled={busy !== null} onClick={load} title="Ask the AI engine again from the current inventory">
          {busy === "load" ? "Generating…" : "↻ Regenerate defaults"}
        </button>
      }
    >
      <Notice text={error} onClose={() => setError(null)} />
      <Notice text={done} tone="emerald" onClose={() => setDone(null)} />
      {!p ? (
        <p className="text-sm text-slate-500">{busy ? "The AI engine is reading the inventory set-up for this order…" : "—"}</p>
      ) : (
        <div className="space-y-3">
          <p className="text-xs text-slate-400">
            {p.summary || "Network-level attributes for this installation."} {p.location ? `Serving location: ${p.location}.` : p.address ? "No inventory location serves this address yet." : ""}
          </p>
          {p.notes.length > 0 && (
            <ul className="space-y-0.5 text-[11px] text-amber-300/80">
              {p.notes.map((n) => <li key={n}>• {n}</li>)}
            </ul>
          )}
          {p.products.map((prod) => (
            <div key={prod.order_item_id} className="space-y-2 rounded-xl border border-slate-800 p-3">
              <p className="text-sm font-semibold text-slate-100">
                {prod.product}
                {prod.access_medium && <span className="ml-2 text-xs font-normal text-slate-400">over {prod.access_medium}</span>}
              </p>
              {prod.attributes.length === 0 && <p className="text-xs text-slate-500">No network attributes proposed for this product.</p>}
              {prod.attributes.map((a) => {
                const key = k(prod.order_item_id, a.name);
                const value = values[key] ?? "";
                const overridden = a.default !== "" && value !== a.default;
                const src = SOURCE[a.source] ?? SOURCE.default;
                return (
                  <div key={a.name} className="grid gap-1 sm:grid-cols-[12rem_minmax(0,1fr)] sm:items-start">
                    <div className="text-xs">
                      <span className="font-semibold text-slate-200">{a.name}</span>
                      {a.required && <span className="text-sky-400"> *</span>}
                      <div className="mt-0.5 flex flex-wrap gap-1">
                        <Chip tone={src.tone}>{src.label}</Chip>
                        {overridden && <Chip tone="amber">overridden</Chip>}
                      </div>
                    </div>
                    <div className="min-w-0 space-y-0.5">
                      {a.options.length > 0 ? (
                        <select disabled={!editable} value={value} onChange={(e) => setValues({ ...values, [key]: e.target.value })} className={INPUT}>
                          <option value="">—</option>
                          {a.options.map((o) => <option key={o}>{o}</option>)}
                        </select>
                      ) : (
                        <input disabled={!editable} value={value} placeholder={a.source === "engineer" ? "Read it on site" : ""}
                               onChange={(e) => setValues({ ...values, [key]: e.target.value })} className={INPUT} />
                      )}
                      <p className="text-[11px] text-slate-500">
                        {a.reason}
                        {overridden && (
                          <>
                            {" · AI default "}
                            <button type="button" disabled={!editable} className="text-sky-400 hover:text-sky-300" onClick={() => setValues({ ...values, [key]: a.default })}>
                              {a.default}
                            </button>
                          </>
                        )}
                      </p>
                    </div>
                  </div>
                );
              })}
              {editable && (
                <div className="grid gap-2 sm:grid-cols-[12rem_minmax(0,1fr)]">
                  <input value={extra[prod.order_item_id]?.name ?? ""} placeholder="+ another attribute" className={INPUT}
                         onChange={(e) => setExtra({ ...extra, [prod.order_item_id]: { name: e.target.value, value: extra[prod.order_item_id]?.value ?? "" } })} />
                  <input value={extra[prod.order_item_id]?.value ?? ""} placeholder="value" className={INPUT}
                         onChange={(e) => setExtra({ ...extra, [prod.order_item_id]: { name: extra[prod.order_item_id]?.name ?? "", value: e.target.value } })} />
                </div>
              )}
            </div>
          ))}
          {editable && (
            <button type="button" className={BTN_PRIMARY} disabled={busy !== null} onClick={save}>
              {busy === "save" ? "Saving…" : "Save network attributes"}
            </button>
          )}
          {p.model && <p className="text-[10px] text-slate-600">Defaults by {p.model}</p>}
        </div>
      )}
    </Panel>
  );
}
