import { useCallback, useEffect, useState } from "react";
import { fsApi } from "../../api/client";
import type { FsAsset, InvLocation, Repair } from "./types";
import { BTN, BTN_PRIMARY, CARD, Chip, Drawer, Empty, Field, INPUT, Notice, Tabs } from "./ui";
import { fmtDate, useAction } from "./style";

const STAGES = ["received", "in_repair", "at_vendor", "repaired"] as const;
const STAGE_LABEL: Record<string, string> = {
  received: "Received & tagged",
  in_repair: "In repair",
  at_vendor: "At vendor",
  repaired: "Repaired",
  returned: "Returned to customer",
  in_stock: "Back in stock",
  scrapped: "Scrapped",
};
const ACTION_LABEL: Record<string, string> = {
  start_repair: "Start repair",
  ship_to_vendor: "Ship to vendor",
  receive_from_vendor: "Received back",
  mark_repaired: "Repaired",
  return_to_customer: "Return to customer",
  return_to_stock: "Back to stock",
  scrap: "Scrap",
};

/** The repair loop (RMA): receive & tag -> repair in-house or at a vendor -> back to the customer, to stock, or scrapped. */
export default function Repairs() {
  const [filter, setFilter] = useState<"open" | "all">("open");
  const [rows, setRows] = useState<Repair[]>([]);
  const [adding, setAdding] = useState(false);
  const [vendor, setVendor] = useState<Record<number, string>>({});
  const load = useCallback(() => {
    fsApi<Repair[]>(`/repairs?status=${filter}`).then(setRows).catch(() => setRows([]));
  }, [filter]);
  useEffect(load, [load]);
  const act = useAction(load);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Tabs
          value={filter}
          onChange={setFilter}
          items={[
            { id: "open", label: "In the loop" },
            { id: "all", label: "All" },
          ]}
        />
        <span className="flex-1" />
        <button type="button" className={BTN_PRIMARY} onClick={() => setAdding(true)}>
          + Receive a defective part
        </button>
      </div>
      <Notice text={act.error} onClose={() => act.setError(null)} />

      {filter === "open" && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {STAGES.map((s) => (
            <div key={s} className={`${CARD} px-4 py-3`}>
              <p className="truncate text-[11px] font-semibold uppercase tracking-wider text-slate-500">{STAGE_LABEL[s]}</p>
              <p className="mt-1 text-2xl font-bold text-slate-100">{rows.filter((r) => r.status === s).length}</p>
            </div>
          ))}
        </div>
      )}

      {rows.length === 0 ? (
        <Empty>No repairs {filter === "open" ? "in progress" : "yet"}.</Empty>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {rows.map((r) => (
            <div key={r.id} className={`${CARD} flex h-full flex-col p-4`}>
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs text-slate-400">{r.repair_number}</span>
                {r.under_warranty && <Chip tone="emerald">warranty recovery</Chip>}
                <span className="flex-1" />
                <Chip tone={r.closed_at ? "slate" : "sky"}>{STAGE_LABEL[r.status] ?? r.status}</Chip>
              </div>
              <p className="mt-2 truncate text-sm font-semibold text-slate-100">{r.product_name}</p>
              <p className="truncate text-xs text-slate-400">
                {r.serial_number || "no serial"} · {r.party_name ?? "internal"}
              </p>
              {r.symptoms && <p className="mt-1 line-clamp-2 text-xs text-slate-400">{r.symptoms}</p>}
              <p className="mt-1 text-[11px] text-slate-500">
                {r.location_name} · received {fmtDate(r.received_at)}
                {r.vendor && ` · ${r.vendor}`}
              </p>
              <div className="mt-2 flex gap-1">
                {STAGES.map((s, i) => (
                  <span key={s} className={`h-1.5 flex-1 rounded-full ${STAGES.indexOf(r.status as (typeof STAGES)[number]) >= i || r.closed_at ? "bg-sky-400" : "bg-slate-800"}`} />
                ))}
              </div>
              {r.actions.length > 0 && (
                <div className="mt-auto space-y-2 border-t border-slate-800 pt-3" style={{ marginTop: "auto" }}>
                  {r.actions.includes("ship_to_vendor") && (
                    <input className={`${INPUT} py-1 text-xs`} placeholder="Vendor (to ship it out)" value={vendor[r.id] ?? ""} onChange={(e) => setVendor({ ...vendor, [r.id]: e.target.value })} />
                  )}
                  <div className="flex flex-wrap gap-1.5">
                    {r.actions.map((a) => (
                      <button
                        key={a}
                        type="button"
                        className={a === "scrap" ? `${BTN} text-red-300` : BTN}
                        disabled={act.busy || (a === "ship_to_vendor" && !vendor[r.id])}
                        onClick={() => act.run(() => fsApi(`/repairs/${r.id}/action`, "POST", { action: a, vendor: vendor[r.id] ?? "" }))}
                      >
                        {ACTION_LABEL[a] ?? a}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <Drawer open={adding} onClose={() => setAdding(false)} title="Receive a defective part" subtitle="It gets a repair number and goes into the repair center's defective stock.">
        {adding && <ReceiveForm onDone={() => { setAdding(false); load(); }} />}
      </Drawer>
    </div>
  );
}

function ReceiveForm({ onDone }: { onDone: () => void }) {
  const [locs, setLocs] = useState<InvLocation[]>([]);
  const [assets, setAssets] = useState<FsAsset[]>([]);
  const [q, setQ] = useState("");
  const [f, setF] = useState({ asset_id: "", product_name: "", serial_number: "", symptoms: "", location_id: "", kind: "internal" });
  const act = useAction();
  useEffect(() => {
    fsApi<InvLocation[]>("/inventory/locations").then((l) => {
      setLocs(l);
      const rc = l.find((x) => x.kind === "repair_center");
      if (rc) setF((x) => ({ ...x, location_id: String(rc.id) }));
    });
  }, []);
  useEffect(() => {
    const t = setTimeout(() => fsApi<FsAsset[]>(`/assets?q=${encodeURIComponent(q)}`).then((a) => setAssets(a.slice(0, 30))).catch(() => setAssets([])), 200);
    return () => clearTimeout(t);
  }, [q]);
  return (
    <div className="space-y-3">
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <Field label="Customer asset (optional)" hint="Picking the asset fills in the product, serial and warranty.">
        <input className={INPUT} placeholder="Search assets…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className={`${INPUT} mt-2`} value={f.asset_id} onChange={(e) => setF({ ...f, asset_id: e.target.value })}>
          <option value="">— none —</option>
          {assets.map((a) => (
            <option key={a.id} value={a.id}>
              {a.label} · {a.party_name ?? ""}
            </option>
          ))}
        </select>
      </Field>
      {!f.asset_id && (
        <div className="grid grid-cols-2 gap-2">
          <Field label="Product">
            <input className={INPUT} value={f.product_name} onChange={(e) => setF({ ...f, product_name: e.target.value })} />
          </Field>
          <Field label="Serial">
            <input className={INPUT} value={f.serial_number} onChange={(e) => setF({ ...f, serial_number: e.target.value })} />
          </Field>
        </div>
      )}
      <Field label="Symptoms">
        <textarea className={INPUT} rows={2} value={f.symptoms} onChange={(e) => setF({ ...f, symptoms: e.target.value })} />
      </Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Received at">
          <select className={INPUT} value={f.location_id} onChange={(e) => setF({ ...f, location_id: e.target.value })}>
            <option value="">—</option>
            {locs.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Repaired">
          <select className={INPUT} value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
            <option value="internal">In-house</option>
            <option value="external">By a vendor</option>
          </select>
        </Field>
      </div>
      <button
        type="button"
        className={BTN_PRIMARY}
        disabled={act.busy || !f.location_id || (!f.asset_id && !f.product_name.trim())}
        onClick={async () =>
          (await act.run(() => fsApi("/repairs", "POST", { ...f, asset_id: f.asset_id ? Number(f.asset_id) : null, location_id: Number(f.location_id) }))) && onDone()
        }
      >
        Receive & tag
      </button>
    </div>
  );
}
