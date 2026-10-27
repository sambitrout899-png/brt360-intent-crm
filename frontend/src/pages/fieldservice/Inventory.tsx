import { useCallback, useEffect, useState } from "react";
import { fsApi } from "../../api/client";
import type { Engineer, InvLevel, InvLocation, InvTxn, PartAvailability, Replenishment } from "./types";
import { BTN, BTN_GO, BTN_PRIMARY, CARD, Chip, Drawer, Empty, Field, INPUT, Notice, Section, Stat, Tabs } from "./ui";
import { fmtWhen, money, useAction } from "./style";

const KIND_LABEL: Record<string, string> = { warehouse: "Warehouse", field_office: "Field office", trunk: "Trunk", repair_center: "Repair center", vendor: "Vendor" };
const MOVE_KINDS: { id: string; label: string; from: boolean; to: boolean }[] = [
  { id: "receive", label: "Receive from supplier", from: false, to: true },
  { id: "transfer", label: "Transfer between locations", from: true, to: true },
  { id: "consume", label: "Issue to a customer", from: true, to: false },
  { id: "scrap", label: "Scrap", from: true, to: false },
];

export default function Inventory() {
  const [tab, setTab] = useState<"stock" | "find" | "moves" | "replenish">("stock");
  const [locs, setLocs] = useState<InvLocation[]>([]);
  const [txns, setTxns] = useState<InvTxn[]>([]);
  const [editing, setEditing] = useState<InvLevel | null>(null);
  const [moving, setMoving] = useState(false);
  const [adding, setAdding] = useState(false);
  const load = useCallback(() => {
    fsApi<InvLocation[]>("/inventory/locations").then(setLocs).catch(() => setLocs([]));
    fsApi<InvTxn[]>("/inventory/transactions?limit=150").then(setTxns).catch(() => setTxns([]));
  }, []);
  useEffect(load, [load]);

  const value = locs.reduce((n, l) => n + l.value, 0);
  const below = locs.reduce((n, l) => n + l.below_min, 0);
  const defective = locs.reduce((n, l) => n + l.levels.filter((x) => x.status === "defective").reduce((m, x) => m + x.on_hand, 0), 0);
  const products = Array.from(new Set(locs.flatMap((l) => l.levels.map((x) => x.product_name)))).sort();

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Locations" value={locs.filter((l) => l.active).length} sub={`${locs.filter((l) => l.kind === "trunk").length} trunks`} />
        <Stat label="Stock value" value={money(value)} sub="good stock at cost" tone="text-sky-300" />
        <Stat label="Below minimum" value={below} sub="levels to replenish" tone={below ? "text-amber-300" : undefined} onClick={() => setTab("replenish")} />
        <Stat label="Defective" value={defective} sub="parts awaiting repair" tone={defective ? "text-red-300" : undefined} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Tabs
          value={tab}
          onChange={setTab}
          items={[
            { id: "stock", label: "Stock by location" },
            { id: "find", label: "Find a part" },
            { id: "moves", label: "Movements" },
            { id: "replenish", label: "Replenish", count: below },
          ]}
        />
        <span className="flex-1" />
        <button type="button" className={BTN} onClick={() => setAdding(true)}>
          + Location
        </button>
        <button type="button" className={BTN_PRIMARY} onClick={() => setMoving(true)}>
          Move stock
        </button>
      </div>

      {tab === "stock" &&
        (locs.length === 0 ? (
          <Empty>No inventory locations yet - add one, or load the starter setup under Setup.</Empty>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {locs.map((l) => (
              <div key={l.id} className={`${CARD} flex h-full flex-col p-4 ${l.active ? "" : "opacity-50"}`}>
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold text-slate-100">{l.name}</p>
                    <p className="truncate text-xs text-slate-500">
                      {KIND_LABEL[l.kind] ?? l.kind}
                      {l.technician_name && ` · ${l.technician_name}`}
                      {l.parent_id && ` · under ${locs.find((p) => p.id === l.parent_id)?.name ?? "?"}`}
                    </p>
                  </div>
                  {l.below_min > 0 && <Chip tone="amber">{l.below_min} low</Chip>}
                </div>
                <div className="mt-3 flex-1 divide-y divide-slate-800/70">
                  {l.levels.length === 0 && <p className="py-2 text-xs text-slate-500">Empty</p>}
                  {l.levels.map((x) => (
                    <button key={x.id} type="button" onClick={() => setEditing(x)} className="flex w-full items-center gap-2 py-1.5 text-left text-xs hover:bg-slate-900/60">
                      <span className="min-w-0 flex-1 truncate text-slate-300">
                        {x.product_name} {x.status !== "good" && <Chip tone="red">{x.status}</Chip>}
                      </span>
                      <span className={`w-10 text-right font-semibold ${x.below_min ? "text-amber-300" : "text-slate-100"}`}>{x.on_hand}</span>
                      <span className="w-16 text-right text-slate-500">{x.min_qty || x.max_qty ? `${x.min_qty}–${x.max_qty}` : ""}</span>
                    </button>
                  ))}
                </div>
                <p className="mt-3 border-t border-slate-800 pt-2 text-right text-[11px] text-slate-500">value {money(l.value)}</p>
              </div>
            ))}
          </div>
        ))}

      {tab === "find" && <PartFinder />}

      {tab === "moves" && (
        <Section title="Latest movements" count={txns.length}>
          {txns.length === 0 ? (
            <Empty>No movements yet.</Empty>
          ) : (
            <div className={`${CARD} thin-scroll overflow-x-auto`}>
              <table className="w-full min-w-[40rem] text-left text-xs">
                <thead className="text-[11px] uppercase tracking-wider text-slate-500">
                  <tr>
                    {["When", "Movement", "Part", "Qty", "From", "To", "Ref"].map((h) => (
                      <th key={h} className="whitespace-nowrap px-3 py-2 font-semibold">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {txns.map((t) => (
                    <tr key={t.id} className="text-slate-300">
                      <td className="whitespace-nowrap px-3 py-2 text-slate-500">{fmtWhen(t.created_at)}</td>
                      <td className="whitespace-nowrap px-3 py-2">{t.label}</td>
                      <td className="px-3 py-2">{t.product_name}</td>
                      <td className="px-3 py-2 font-semibold">{t.quantity}</td>
                      <td className="whitespace-nowrap px-3 py-2">
                        {t.from}
                        {t.from_status !== "good" && t.from !== "Outside" && <span className="text-red-300"> ({t.from_status})</span>}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2">
                        {t.to}
                        {t.to_status !== "good" && t.to !== "Customer" && <span className="text-red-300"> ({t.to_status})</span>}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-slate-500">{t.reference_type ? `${t.reference_type} ${t.reference_id ?? ""}` : t.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      )}

      {tab === "replenish" && <Replenish onDone={load} />}

      <Drawer open={!!editing} onClose={() => setEditing(null)} title={editing ? editing.product_name : ""} subtitle={editing ? locs.find((l) => l.id === editing.location_id)?.name : undefined}>
        {editing && <LevelEditor level={editing} onSaved={() => { setEditing(null); load(); }} />}
      </Drawer>
      <Drawer open={moving} onClose={() => setMoving(false)} title="Move stock" subtitle="Every movement is recorded as an inventory transaction.">
        {moving && <MoveForm locs={locs} products={products} onDone={() => { setMoving(false); load(); }} />}
      </Drawer>
      <Drawer open={adding} onClose={() => setAdding(false)} title="New inventory location">
        {adding && <LocationForm locs={locs} onDone={() => { setAdding(false); load(); }} />}
      </Drawer>
    </div>
  );
}

function PartFinder() {
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<PartAvailability[]>([]);
  useEffect(() => {
    const t = setTimeout(() => fsApi<PartAvailability[]>(`/inventory/parts?q=${encodeURIComponent(q)}`).then(setRows).catch(() => setRows([])), 200);
    return () => clearTimeout(t);
  }, [q]);
  return (
    <div className="space-y-3">
      <input className={INPUT} placeholder="Which part? e.g. SFP" value={q} onChange={(e) => setQ(e.target.value)} />
      {rows.length === 0 ? (
        <Empty>No good stock of that anywhere.</Empty>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {rows.map((p) => (
            <div key={p.product_name} className={`${CARD} p-4`}>
              <div className="flex items-baseline gap-2">
                <p className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-100">{p.product_name}</p>
                <span className="text-lg font-bold text-sky-300">{p.total}</span>
              </div>
              <p className="text-[11px] text-slate-500">{p.unit_price ? `${money(p.unit_price)} each` : "no price set"}</p>
              <div className="mt-2 space-y-1">
                {p.locations.map((l) => (
                  <div key={l.location_id} className="flex items-center gap-2 text-xs">
                    <span className="min-w-0 flex-1 truncate text-slate-300">
                      {l.location_name} <span className="text-slate-500">· {KIND_LABEL[l.kind] ?? l.kind}</span>
                    </span>
                    <span className="font-semibold text-slate-100">{l.available}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Replenish({ onDone }: { onDone: () => void }) {
  const [rows, setRows] = useState<Replenishment[] | null>(null);
  const act = useAction(onDone);
  const check = useCallback(() => {
    fsApi<Replenishment[]>("/inventory/replenish", "POST", { apply: false }).then(setRows).catch(() => setRows([]));
  }, []);
  useEffect(check, [check]);
  const transfers = (rows ?? []).filter((r) => r.source_id && r.quantity > 0);
  return (
    <div className="space-y-3">
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <Notice text={act.done} tone="emerald" onClose={() => act.setDone(null)} />
      <div className="flex flex-wrap items-center gap-2">
        <p className="flex-1 text-sm text-slate-400">Every level below its minimum, topped up to its maximum from the nearest stock (its parent location first).</p>
        <button
          type="button"
          className={BTN_GO}
          disabled={act.busy || transfers.length === 0}
          onClick={async () => {
            const out = await act.run(() => fsApi<Replenishment[]>("/inventory/replenish", "POST", { apply: true }), "Transfers done");
            if (out) setRows(out);
          }}
        >
          Make {transfers.length} transfer{transfers.length === 1 ? "" : "s"}
        </button>
      </div>
      {rows && rows.length === 0 ? (
        <Empty>Nothing is below its minimum.</Empty>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {(rows ?? []).map((r, i) => (
            <div key={i} className={`${CARD} p-4`}>
              <p className="truncate text-sm font-semibold text-slate-100">{r.product_name}</p>
              <p className="truncate text-xs text-slate-400">{r.location_name}</p>
              <p className="mt-2 text-xs text-slate-300">
                {r.on_hand} on hand · min {r.min_qty} · max {r.max_qty}
              </p>
              <p className={`mt-1 text-xs font-semibold ${r.done ? "text-emerald-300" : r.source_id ? "text-sky-300" : "text-amber-300"}`}>
                {r.done ? `✓ ${r.quantity} moved from ${r.source_name}` : r.source_id ? `${r.quantity} from ${r.source_name}` : `order ${r.need} from a supplier`}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function LevelEditor({ level, onSaved }: { level: InvLevel; onSaved: () => void }) {
  const [f, setF] = useState({ min_qty: level.min_qty, max_qty: level.max_qty, unit_cost: level.unit_cost, unit_price: level.unit_price, locator: level.locator });
  const [counted, setCounted] = useState<string>("");
  const act = useAction();
  return (
    <div className="space-y-4">
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <div className="grid grid-cols-2 gap-3">
        <Stat label="On hand" value={level.on_hand} />
        <Stat label="Available" value={level.available} sub={level.reserved ? `${level.reserved} reserved` : undefined} />
      </div>
      <div className="grid grid-cols-2 gap-3">
        {(["min_qty", "max_qty", "unit_cost", "unit_price"] as const).map((k) => (
          <Field key={k} label={{ min_qty: "Minimum", max_qty: "Maximum", unit_cost: "Cost", unit_price: "Price to customer" }[k]}>
            <input className={INPUT} type="number" min={0} value={f[k]} onChange={(e) => setF({ ...f, [k]: Number(e.target.value) })} />
          </Field>
        ))}
        <Field label="Locator (aisle/shelf/bin)" className="col-span-2">
          <input className={INPUT} value={f.locator} onChange={(e) => setF({ ...f, locator: e.target.value })} />
        </Field>
      </div>
      <button type="button" className={BTN_PRIMARY} disabled={act.busy} onClick={async () => (await act.run(() => fsApi("/inventory/levels", "POST", { id: level.id, ...f }))) && onSaved()}>
        Save
      </button>
      <div className="border-t border-slate-800 pt-4">
        <Field label="Cycle count - what is really on the shelf?">
          <div className="flex gap-2">
            <input className={INPUT} type="number" min={0} value={counted} onChange={(e) => setCounted(e.target.value)} />
            <button
              type="button"
              className={BTN}
              disabled={counted === "" || act.busy}
              onClick={async () =>
                (await act.run(() => fsApi("/inventory/count", "POST", { location_id: level.location_id, product_name: level.product_name, counted: Number(counted), status: level.status }))) && onSaved()
              }
            >
              Record count
            </button>
          </div>
        </Field>
      </div>
    </div>
  );
}

function MoveForm({ locs, products, onDone }: { locs: InvLocation[]; products: string[]; onDone: () => void }) {
  const [f, setF] = useState({ kind: "transfer", product_name: "", quantity: 1, source_id: "", target_id: "", source_status: "good", note: "" });
  const kind = MOVE_KINDS.find((k) => k.id === f.kind)!;
  const act = useAction();
  return (
    <div className="space-y-3">
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <Field label="Movement">
        <select className={INPUT} value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
          {MOVE_KINDS.map((k) => (
            <option key={k.id} value={k.id}>
              {k.label}
            </option>
          ))}
        </select>
      </Field>
      <div className="grid grid-cols-[1fr_6rem] gap-2">
        <Field label="Part">
          <input className={INPUT} list="fs-products" value={f.product_name} onChange={(e) => setF({ ...f, product_name: e.target.value })} />
          <datalist id="fs-products">
            {products.map((p) => (
              <option key={p} value={p} />
            ))}
          </datalist>
        </Field>
        <Field label="Qty">
          <input className={INPUT} type="number" min={1} value={f.quantity} onChange={(e) => setF({ ...f, quantity: Number(e.target.value) })} />
        </Field>
      </div>
      {kind.from && (
        <Field label="From">
          <select className={INPUT} value={f.source_id} onChange={(e) => setF({ ...f, source_id: e.target.value })}>
            <option value="">—</option>
            {locs.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
        </Field>
      )}
      {f.kind === "scrap" && (
        <Field label="Which stock">
          <select className={INPUT} value={f.source_status} onChange={(e) => setF({ ...f, source_status: e.target.value })}>
            <option value="defective">Defective</option>
            <option value="good">Good</option>
          </select>
        </Field>
      )}
      {kind.to && (
        <Field label="To">
          <select className={INPUT} value={f.target_id} onChange={(e) => setF({ ...f, target_id: e.target.value })}>
            <option value="">—</option>
            {locs.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
        </Field>
      )}
      <Field label="Note">
        <input className={INPUT} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} />
      </Field>
      <button
        type="button"
        className={BTN_PRIMARY}
        disabled={act.busy || !f.product_name || (kind.from && !f.source_id) || (kind.to && !f.target_id)}
        onClick={async () =>
          (await act.run(() =>
            fsApi("/inventory/transactions", "POST", {
              kind: f.kind,
              product_name: f.product_name,
              quantity: f.quantity,
              source_id: kind.from ? Number(f.source_id) : null,
              target_id: kind.to ? Number(f.target_id) : null,
              source_status: f.kind === "scrap" ? f.source_status : "good",
              note: f.note,
            })
          )) && onDone()
        }
      >
        Record movement
      </button>
    </div>
  );
}

function LocationForm({ locs, onDone }: { locs: InvLocation[]; onDone: () => void }) {
  const [engineers, setEngineers] = useState<Engineer[]>([]);
  const [f, setF] = useState({ name: "", kind: "warehouse", parent_id: "", technician_id: "", address: "" });
  const act = useAction();
  useEffect(() => {
    fsApi<Engineer[]>("/engineers").then(setEngineers).catch(() => setEngineers([]));
  }, []);
  return (
    <div className="space-y-3">
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <Field label="Name">
        <input className={INPUT} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      </Field>
      <Field label="Type">
        <select className={INPUT} value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
          {Object.entries(KIND_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Inside / replenished from">
        <select className={INPUT} value={f.parent_id} onChange={(e) => setF({ ...f, parent_id: e.target.value })}>
          <option value="">—</option>
          {locs.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </select>
      </Field>
      {f.kind === "trunk" && (
        <Field label="Engineer">
          <select className={INPUT} value={f.technician_id} onChange={(e) => setF({ ...f, technician_id: e.target.value })}>
            <option value="">—</option>
            {engineers.map((e) => (
              <option key={e.technician_id} value={e.technician_id}>
                {e.name}
              </option>
            ))}
          </select>
        </Field>
      )}
      <Field label="Address">
        <input className={INPUT} value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} />
      </Field>
      <button
        type="button"
        className={BTN_PRIMARY}
        disabled={!f.name.trim() || act.busy}
        onClick={async () =>
          (await act.run(() =>
            fsApi("/inventory/locations", "POST", { ...f, parent_id: f.parent_id ? Number(f.parent_id) : null, technician_id: f.technician_id ? Number(f.technician_id) : null })
          )) && onDone()
        }
      >
        Create
      </button>
    </div>
  );
}
