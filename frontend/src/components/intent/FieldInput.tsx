import { useEffect, useRef, useState } from "react";
import { lookupRecords, notOfferedProducts, priceSelection, recordLifecycle, type NotOfferedProduct } from "../../api/client";
import type { EntityType, PreparedField, PriceQuote, RecordLifecycle, RecordOption } from "../../api/types";
import Configurator from "../configurator/Configurator";
import PageDetailsInput from "../pages/PageFields";
import { useWorkspace } from "../../workspace";
import DesignEditor from "./DesignEditor";
import { INPUT_CLS, ORIGIN_BADGES, formatValue, isEmpty } from "./intentUi";

function OriginBadge({ field }: { field: PreparedField }) {
  if (!field.origin || field.origin === "you" || isEmpty(field.value)) return null;
  const badge = ORIGIN_BADGES[field.origin];
  return (
    <span title={field.origin_note} className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${badge.cls}`}>
      {badge.icon} {badge.label}
    </span>
  );
}

/** Search-as-you-type over the records the user may see; the copilot's
 * best candidates are listed first and starred. */
function RecordPicker({
  field,
  intentCode,
  recordType,
  onChange,
}: {
  field: PreparedField;
  intentCode: string;
  recordType: string | null;
  onChange: (value: unknown) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<RecordOption[] | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open || !recordType || !query.trim()) return;
    const handle = setTimeout(() => {
      lookupRecords(recordType, query, intentCode, field.key).then(setResults).catch(() => setResults([]));
    }, 250);
    return () => clearTimeout(handle);
  }, [open, query, recordType, intentCode, field.key]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const list = query.trim() ? results ?? [] : field.suggestions;
  const hasValue = !isEmpty(field.value);

  if (!recordType) {
    return <p className="rounded-lg border border-dashed border-slate-700 px-3 py-2 text-xs text-slate-500">Choose the record type first.</p>;
  }

  return (
    <div ref={boxRef} className="relative">
      {hasValue && !open ? (
        <button
          type="button"
          onClick={() => {
            setQuery("");
            setOpen(true);
          }}
          className="flex w-full items-center gap-2 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-left text-sm hover:border-sky-500"
        >
          <span className="text-slate-100">{field.display || `#${formatValue(field.value)}`}</span>
          <span className="text-xs text-slate-500">#{formatValue(field.value)}</span>
          <span className="ml-auto text-xs text-sky-400">change</span>
        </button>
      ) : (
        <input
          autoFocus={open}
          value={query}
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          placeholder={`Search by name or #number…`}
          className={INPUT_CLS}
        />
      )}
      {open && (
        <div className="absolute z-30 mt-1 max-h-72 w-full overflow-y-auto rounded-lg border border-slate-700 bg-slate-900 shadow-2xl">
          {!query.trim() && list.length > 0 && <p className="px-3 pt-2 text-[10px] font-bold uppercase tracking-wide text-slate-500">Suggested</p>}
          {list.map((r) => (
            <button
              key={r.id}
              type="button"
              onClick={() => {
                onChange(r.id);
                setOpen(false);
              }}
              className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-slate-800"
            >
              <span className={`mt-0.5 text-xs ${r.suggested ? "text-amber-400" : "text-transparent"}`}>★</span>
              <span className="min-w-0">
                <span className="block truncate text-sm text-slate-100">{r.label}</span>
                <span className="block truncate text-xs text-slate-500">{r.hint}</span>
              </span>
            </button>
          ))}
          {list.length === 0 && (
            <p className="px-3 py-3 text-xs text-slate-500">{query.trim() ? "Nothing matches - try a number like #12." : "Type to search."}</p>
          )}
        </div>
      )}
    </div>
  );
}

/** Free-form list (products in a bundle, sites): type and press Enter or comma. */
function ChipsInput({ value, placeholder, onChange }: { value: unknown; placeholder: string; onChange: (value: string[]) => void }) {
  const items = Array.isArray(value) ? value.map(String) : typeof value === "string" && value ? value.split(/[,+]/).map((s) => s.trim()).filter(Boolean) : [];
  const [draft, setDraft] = useState("");
  const add = () => {
    const parts = draft.split(/[,+]/).map((s) => s.trim()).filter(Boolean);
    if (parts.length) onChange([...items, ...parts]);
    setDraft("");
  };
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 focus-within:border-sky-500">
      {items.map((item, i) => (
        <span key={`${item}-${i}`} className="flex items-center gap-1 rounded-md bg-slate-800 px-2 py-0.5 text-xs text-slate-200">
          {item}
          <button type="button" onClick={() => onChange(items.filter((_, j) => j !== i))} className="text-slate-500 hover:text-red-400">
            ×
          </button>
        </span>
      ))}
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            add();
          } else if (e.key === "Backspace" && !draft && items.length) {
            onChange(items.slice(0, -1));
          }
        }}
        onBlur={add}
        placeholder={items.length ? "" : placeholder || "type and press Enter"}
        className="min-w-[8rem] flex-1 bg-transparent px-1 py-1 text-sm text-slate-100 outline-none"
      />
    </div>
  );
}

/** "Update a record": the record's editable fields, with current values. */
function UpdatesEditor({ entityType, entityId, value, onChange }: { entityType: unknown; entityId: unknown; value: unknown; onChange: (v: Record<string, unknown>) => void }) {
  const [record, setRecord] = useState<RecordLifecycle | null>(null);
  const [error, setError] = useState("");
  const changes = (value && typeof value === "object" && !Array.isArray(value) ? value : {}) as Record<string, unknown>;

  useEffect(() => {
    if (!entityType || !entityId) return;
    recordLifecycle(entityType as EntityType, Number(entityId))
      .then((r) => {
        setRecord(r);
        setError("");
      })
      .catch(() => setError("Could not load that record."));
  }, [entityType, entityId]);

  if (!entityType || !entityId) return <p className="text-xs text-slate-500">Pick the record first - its fields appear here.</p>;
  if (error) return <p className="text-xs text-red-400">{error}</p>;
  if (!record) return <p className="text-xs text-slate-500">Loading fields…</p>;
  if (!record.editable) {
    return <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">This record is locked in “{record.state_label}” and can't be edited.</p>;
  }

  const set = (key: string, v: unknown) => {
    const next = { ...changes };
    if (v === "" || v === null) delete next[key];
    else next[key] = v;
    onChange(next);
  };

  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {record.updatable_core_fields.map((key) => (
        <label key={key} className="text-xs text-slate-400">
          {key.replace(/_/g, " ")}
          <input
            value={String(changes[key] ?? "")}
            placeholder={formatValue(record.core_fields[key])}
            onChange={(e) => set(key, e.target.value)}
            className={`mt-1 ${INPUT_CLS}`}
          />
        </label>
      ))}
      {record.custom_fields.map((f) => (
        <label key={f.key} className="text-xs text-slate-400">
          {f.label}
          {f.data_type === "picklist" || f.data_type === "boolean" ? (
            <select value={String(changes[f.key] ?? "")} onChange={(e) => set(f.key, e.target.value)} className={`mt-1 ${INPUT_CLS}`}>
              <option value="">{f.value === null ? "—" : `keep: ${formatValue(f.value)}`}</option>
              {(f.data_type === "boolean" ? ["true", "false"] : f.allowed_values).map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          ) : (
            <input
              value={String(changes[f.key] ?? "")}
              type={f.data_type === "number" ? "number" : f.data_type === "date" ? "date" : "text"}
              placeholder={formatValue(f.value)}
              onChange={(e) => set(f.key, e.target.value)}
              className={`mt-1 ${INPUT_CLS}`}
            />
          )}
        </label>
      ))}
      {record.updatable_core_fields.length + record.custom_fields.length === 0 && (
        <p className="text-xs text-slate-500">This record type has no editable fields.</p>
      )}
    </div>
  );
}

/** Pricing attributes: one dropdown per attribute the tenant defined
 * (Admin -> Product Attributes), offering that attribute's allowed values. */
function AttributesEditor({ field, values, onChange }: { field: PreparedField; values: Record<string, unknown>; onChange: (v: Record<string, string>) => void }) {
  const current = (field.value && typeof field.value === "object" && !Array.isArray(field.value) ? field.value : {}) as Record<string, string>;
  const [price, setPrice] = useState<PriceQuote | null>(null);
  const offering = typeof values.offering_name === "string" ? values.offering_name : "";
  const live = field.key === "pricing_attributes" && !!offering;
  const selection = JSON.stringify(current);

  // What this configuration costs - the same pricelist precedence the quote will use.
  useEffect(() => {
    if (!live) return;
    const handle = setTimeout(() => {
      priceSelection(offering, {
        pricing_attributes: JSON.parse(selection),
        opportunity_id: typeof values.opportunity_id === "number" ? values.opportunity_id : undefined,
        quote_id: typeof values.quote_id === "number" ? values.quote_id : undefined,
        price_list: typeof values.price_list === "string" && values.price_list ? values.price_list : undefined,
        quantity: typeof values.quantity === "number" && values.quantity > 0 ? values.quantity : 1,
      }).then(setPrice).catch(() => setPrice(null));
    }, 250);
    return () => clearTimeout(handle);
  }, [live, offering, selection, values.opportunity_id, values.quote_id, values.price_list, values.quantity]);

  if (field.options.length === 0) {
    return <p className="rounded-lg border border-dashed border-slate-700 px-3 py-2 text-xs text-slate-500">No pricing attributes are set up yet - add them under Admin → Product Attributes.</p>;
  }
  const set = (name: string, v: string) => {
    const next = { ...current };
    if (v) next[name] = v;
    else delete next[name];
    onChange(next);
  };
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {field.options.map((o) => (
        <label key={o.value} className="text-xs text-slate-400">
          {o.label}
          {o.hint && <span className="ml-1 text-[10px] text-slate-500">· {o.hint}</span>}
          {o.values && o.values.length > 0 ? (
            <select value={current[o.value] ?? ""} onChange={(e) => set(o.value, e.target.value)} className={`mt-1 ${INPUT_CLS}`}>
              <option value="">—</option>
              {o.values.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          ) : (
            <input value={current[o.value] ?? ""} placeholder={o.hint} onChange={(e) => set(o.value, e.target.value)} className={`mt-1 ${INPUT_CLS}`} />
          )}
        </label>
      ))}
      {live && price && (
        <div className="rounded-lg border border-slate-700 bg-slate-950/60 px-3 py-2 text-xs sm:col-span-2">
          {price.ok ? (
            <span className="text-slate-300">
              Monthly price <b className="text-slate-100">{(price.configured_price ?? 0).toLocaleString()}</b>
              {" "}= {(price.base_price ?? 0).toLocaleString()} ({price.price_source})
              {(price.lines ?? []).map((l) => ` ${l.amount >= 0 ? "+" : "−"} ${Math.abs(l.amount).toLocaleString()} ${l.label.split(":")[0]}`).join("")}
              {(price.total ?? 0) !== (price.configured_price ?? 0) && <> · total {(price.total ?? 0).toLocaleString()}</>}
            </span>
          ) : (
            price.errors.map((e) => <p key={e} className="text-red-400">{e}</p>)
          )}
        </div>
      )}
    </div>
  );
}

/** Interested products: picked from the products sold to the prospect's
 * segment (the options), each with a quantity. */
/** Products this customer can't pick, and why (draft, other segment, not eligible). */
function NotOffered({ partyId, segment, showReasons = false }: { partyId?: number; segment?: string; showReasons?: boolean }) {
  const [list, setList] = useState<NotOfferedProduct[]>([]);
  const [open, setOpen] = useState(false);
  const admin = useWorkspace().role === "admin"; // business users never see products they can't sell
  useEffect(() => {
    if ((!partyId && !segment) || !admin) return;
    notOfferedProducts(partyId, segment).then(setList).catch(() => setList([]));
  }, [partyId, segment, admin]);
  if (!list.length || !admin) return null;
  return (
    <div className="text-[11px]">
      <button type="button" onClick={() => setOpen(!open)} className="text-slate-500 hover:text-slate-300">
        {open ? "▾" : "▸"} {list.length} product{list.length === 1 ? "" : "s"} not offered here - why?
      </button>
      {(open || showReasons) && (
        <ul className="mt-1 space-y-0.5 pl-3">
          {list.map((n) => (
            <li key={n.name} className="text-slate-500">
              <span className="text-slate-400 line-through decoration-slate-600">{n.name}</span> - {n.reason}
              {n.fix === "publish" && " (Admin › Product Modeller › Publish)"}
              {n.fix === "segment" && " (change its segment in the Product Modeller if it should be sold here)"}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Lead interest (INT-46): the products the prospect's segment and product
 * rules allow. The rest stay one click away, each with the reason it isn't
 * offered - interest may still be recorded, the rules apply again at quote
 * and order. */
function InterestPicker({ field, items, chosen, onChange, partyId }: {
  field: PreparedField; items: { offering_name: string; quantity: number }[]; chosen: Set<string>;
  onChange: (v: { offering_name: string; quantity: number }[]) => void; partyId?: number;
}) {
  const [blocked, setBlocked] = useState<{ party?: number; reasons: Map<string, string> }>({ reasons: new Map() });
  const [showOthers, setShowOthers] = useState(false);
  // business users get only what the prospect may buy (the server lists nothing else);
  // administrators also see what isn't offered, and why
  const admin = useWorkspace().role === "admin";
  useEffect(() => {
    if (!partyId || !admin) return;
    let live = true;
    notOfferedProducts(partyId)
      .then((list) => { if (live) setBlocked({ party: partyId, reasons: new Map(list.map((n) => [n.name.toLowerCase(), n.reason])) }); })
      .catch(() => { if (live) setBlocked({ party: partyId, reasons: new Map() }); });
    return () => { live = false; };
  }, [partyId, admin]);
  // Until a prospect is chosen (or its rules are loaded) nothing can be judged, so everything shows.
  const reasons = partyId && blocked.party === partyId ? blocked.reasons : new Map<string, string>();
  const all = [...field.options, ...items.filter((item) => !field.options.some((o) => o.value.toLowerCase() === item.offering_name.toLowerCase()))
    .map((item) => ({ value: item.offering_name, label: item.offering_name, hint: undefined as string | undefined }))];
  const eligible = all.filter((o) => !reasons.has(o.value.toLowerCase()));
  const others = all.filter((o) => reasons.has(o.value.toLowerCase()));
  const visible = [...eligible, ...others.filter((o) => showOthers || chosen.has(o.value.toLowerCase()))];
  const toggle = (value: string, selected: boolean) =>
    onChange(selected ? items.filter((item) => item.offering_name.toLowerCase() !== value.toLowerCase()) : [...items, { offering_name: value, quantity: 1 }]);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {visible.map((option) => {
          const selected = chosen.has(option.value.toLowerCase());
          const reason = reasons.get(option.value.toLowerCase());
          return (
            <label key={option.value} title={reason ? `Not offered to this prospect: ${reason}` : option.hint}
              className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm ${selected ? "border-sky-500 bg-sky-500/15 text-sky-100" : reason ? "border-dashed border-amber-500/40 text-slate-400" : "border-slate-700 text-slate-300"}`}>
              <input type="checkbox" checked={selected} className="accent-sky-500" onChange={() => toggle(option.value, selected)} />
              {option.label}
              {reason && <span className="text-[11px] text-amber-300/80">not offered</span>}
            </label>
          );
        })}
      </div>
      <p className="text-xs text-slate-400">
        {all.length === 0 ? "No products in the catalog yet." : eligible.length === 0 && !showOthers ? "No products are offered to this prospect under the product rules." : `${items.length} selected`}
        {others.length > 0 && (
          <button type="button" onClick={() => setShowOthers(!showOthers)} className="ml-3 text-sky-400 hover:text-sky-300">
            {showOthers ? "Hide products not offered" : `Show ${others.length} product${others.length === 1 ? "" : "s"} not offered to this prospect`}
          </button>
        )}
      </p>
      {showOthers && others.length > 0 && (
        <ul className="space-y-0.5 text-[11px] text-slate-500">
          {others.map((o) => <li key={o.value}><span className="text-slate-400">{o.label}</span> - {reasons.get(o.value.toLowerCase())}</li>)}
        </ul>
      )}
      {items.some((i) => reasons.has(i.offering_name.toLowerCase())) && (
        <p className="text-[11px] text-amber-300/80">Interest in a product not offered to this prospect is recorded as-is; the product rules are checked again when it is quoted or ordered.</p>
      )}
    </div>
  );
}

function ProductsInput({ field, onChange, partyId, segment, interestOnly = false }: {
  field: PreparedField; onChange: (v: { offering_name: string; quantity: number }[]) => void; partyId?: number; segment?: string; interestOnly?: boolean;
}) {
  const raw = Array.isArray(field.value) ? field.value : typeof field.value === "string" && field.value ? field.value.split(",") : [];
  const items = raw
    .map((item) =>
      item && typeof item === "object"
        ? { offering_name: String((item as Record<string, unknown>).offering_name ?? ""), quantity: Number((item as Record<string, unknown>).quantity ?? 1) || 1 }
        : { offering_name: String(item).trim(), quantity: 1 }
    )
    .filter((i) => i.offering_name);
  const chosen = new Set(items.map((i) => i.offering_name.toLowerCase()));
  const left = field.options.filter((o) => !chosen.has(o.value.toLowerCase()));
  if (interestOnly) return <InterestPicker field={field} items={items} chosen={chosen} onChange={onChange} partyId={partyId} />;
  return (
    <div className="space-y-2">
      {items.map((item, i) => (
        <div key={item.offering_name} className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5">
          <span className="flex-1 text-sm text-slate-100">{item.offering_name}</span>
          <span className="text-[11px] text-slate-500">qty</span>
          <input
            type="number"
            min={1}
            value={item.quantity}
            onChange={(e) => onChange(items.map((x, j) => (j === i ? { ...x, quantity: Math.max(1, Number(e.target.value) || 1) } : x)))}
            className="w-16 rounded border border-slate-700 bg-slate-900 px-2 py-0.5 text-sm text-slate-100"
          />
          <button type="button" onClick={() => onChange(items.filter((_, j) => j !== i))} className="text-slate-500 hover:text-red-400" title="Remove">
            ×
          </button>
        </div>
      ))}
      {left.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {left.map((o) => (
            <button
              key={o.value}
              type="button"
              onClick={() => onChange([...items, { offering_name: o.value, quantity: 1 }])}
              className="rounded-lg border border-dashed border-slate-600 px-2.5 py-1 text-xs text-slate-300 hover:border-sky-500 hover:text-sky-200"
              title={o.hint}
            >
              + {o.label}
              {o.hint && <span className="ml-1 text-slate-500">· {o.hint}</span>}
            </button>
          ))}
        </div>
      ) : (
        items.length === 0 && <p className="text-xs text-slate-400">{partyId
          ? "No eligible active products are available for this customer. Check the exclusion reasons below."
          : "Select a customer to see products available for their segment."}</p>
      )}
      <NotOffered partyId={partyId} segment={segment} showReasons={!!partyId && field.options.length === 0} />
    </div>
  );
}

/** A value set for the user - the product carried from the lead (locked, with
 * "Change" where allowed), or what they may see but not change (read-only). */
function FixedValue({ field, onChange }: { field: PreparedField; onChange?: () => void }) {
  const value = field.value;
  const pairs = value && typeof value === "object" && !Array.isArray(value) ? Object.entries(value as Record<string, unknown>) : null;
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-xs text-slate-500" title={field.readonly ? "Read-only" : "Set for you"}>🔒</span>
        {pairs ? (
          pairs.length ? (
            pairs.map(([k, v]) => (
              <span key={k} className="rounded-md bg-slate-800 px-2 py-0.5 text-xs text-slate-200">
                <span className="text-slate-400">{k}</span> {formatValue(v)}
              </span>
            ))
          ) : (
            <span className="text-xs text-slate-500">None yet</span>
          )
        ) : (
          <span className="text-sm font-semibold text-slate-100">{isEmpty(value) ? "—" : formatValue(value)}</span>
        )}
        {onChange && (
          <button type="button" onClick={onChange} className="ml-auto text-[11px] font-semibold text-sky-400 hover:text-sky-300">
            Change
          </button>
        )}
      </div>
      {field.locked_note && <p className="mt-1 text-[11px] text-slate-500">{field.locked_note}</p>}
    </div>
  );
}

/** One field of an intent form, shaped by its type. */
export default function FieldInput({
  field,
  intentCode,
  values,
  onChange,
}: {
  field: PreparedField;
  intentCode: string;
  values: Record<string, unknown>;
  onChange: (value: unknown) => void;
}) {
  const value = field.value;
  const [unlocked, setUnlocked] = useState(false);
  let control;
  if (field.readonly || (field.locked && !unlocked)) {
    control = <FixedValue field={field} onChange={field.locked_change && !field.readonly ? () => setUnlocked(true) : undefined} />;
  } else switch (field.type) {
    case "record":
      control = <RecordPicker field={field} intentCode={intentCode} recordType={field.record_type && !field.record_type.startsWith("@") ? field.record_type : null} onChange={onChange} />;
      break;
    case "picklist":
    case "transition":
      if (field.options.length === 0) {
        control = (
          <input value={formatValue(value)} placeholder={field.placeholder || (field.type === "transition" ? "Pick the record first" : "")} onChange={(e) => onChange(e.target.value)} className={INPUT_CLS} />
        );
      } else if (field.options.length <= 6) {
        control = (
          <div className="flex flex-wrap gap-1.5">
            {field.options.map((o) => {
              const selected = String(value ?? "") === o.value;
              return (
                <button
                  key={o.value}
                  type="button"
                  disabled={!!o.via}
                  title={o.via ? `Done through “${o.via}”` : o.hint}
                  onClick={() => onChange(selected && !field.required ? "" : o.value)}
                  className={`rounded-lg border px-3 py-1.5 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-40 ${
                    selected ? "border-sky-500 bg-sky-500/20 text-sky-200" : "border-slate-700 bg-slate-900 text-slate-300 hover:border-slate-500"
                  }`}
                >
                  {o.label}
                  {o.hint && !o.via && <span className="ml-1 font-normal text-slate-500">· {o.hint}</span>}
                  {o.via && <span className="ml-1 font-normal">· via {o.via}</span>}
                </button>
              );
            })}
          </div>
        );
      } else {
        control = (
          <select value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} className={INPUT_CLS}>
            <option value="">Choose…</option>
            {field.options.map((o) => (
              <option key={o.value} value={o.value} disabled={!!o.via}>
                {o.label}
                {o.hint ? ` - ${o.hint}` : ""}
              </option>
            ))}
          </select>
        );
      }
      break;
    case "money":
    case "percent":
    case "number":
      control = (
        <div className="relative">
          <input
            type="number"
            inputMode="decimal"
            value={value === null || value === undefined ? "" : String(value)}
            placeholder={field.placeholder}
            onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
            className={`${INPUT_CLS} ${field.type === "percent" ? "pr-8" : ""}`}
          />
          {field.type === "percent" && <span className="pointer-events-none absolute right-3 top-2 text-sm text-slate-500">%</span>}
        </div>
      );
      break;
    case "list":
      control = <ChipsInput value={value} placeholder={field.placeholder} onChange={onChange} />;
      break;
    case "textarea":
      control = <textarea rows={2} value={formatValue(value)} placeholder={field.placeholder} onChange={(e) => onChange(e.target.value)} className={`${INPUT_CLS} resize-y`} />;
      break;
    case "boolean":
      control = (
        <div className="flex gap-1.5">
          {["true", "false"].map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => onChange(v)}
              className={`rounded-lg border px-3 py-1.5 text-xs font-semibold ${String(value) === v ? "border-sky-500 bg-sky-500/20 text-sky-200" : "border-slate-700 text-slate-300"}`}
            >
              {v === "true" ? "Yes" : "No"}
            </button>
          ))}
        </div>
      );
      break;
    case "updates":
      control = <UpdatesEditor entityType={values.entity_type} entityId={values.entity_id} value={value} onChange={onChange} />;
      break;
    case "attributes":
      // A designed product gets the interactive configurator (live rules, conflicts, price);
      // anything else keeps the simple attribute list.
      if (field.key === "pricing_attributes" && typeof values.offering_name === "string" && values.offering_name && field.options.length > 0) {
        const num = (k: string) => (typeof values[k] === "number" ? (values[k] as number) : undefined);
        control = (
          <Configurator
            product={values.offering_name}
            picks={value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {}}
            onChange={onChange}
            context={{ opportunity_id: num("opportunity_id"), quote_id: num("quote_id"), party_id: num("party_id"),
                       price_list: typeof values.price_list === "string" && values.price_list ? values.price_list : undefined }}
            compact
          />
        );
      } else {
        control = <AttributesEditor field={field} values={values} onChange={onChange} />;
      }
      break;
    case "design":
      control = <DesignEditor field={field} values={values} onChange={onChange} />;
      break;
    case "products":
      control = (
        <ProductsInput field={field} onChange={onChange} interestOnly={intentCode === "INT-46"} partyId={typeof values.party_id === "number" ? values.party_id : undefined}
          segment={typeof values.segment === "string" ? values.segment : undefined} />
      );
      break;
    case "page": {
      const entity = field.record_type || "party";
      const segment = typeof values.segment === "string" && /^B2[BC]/i.test(values.segment) ? values.segment.slice(0, 3).toUpperCase() : null;
      const partyId = entity !== "party" && typeof values.party_id === "number" ? values.party_id : null;
      if (entity !== "party" && !partyId) {
        control = <p className="text-xs text-slate-500">Pick the prospect first - their segment decides the fields.</p>;
        break;
      }
      const { details: _ignored, ...outer } = values;
      void _ignored;
      control = <PageDetailsInput entity={entity} segment={segment} partyId={partyId} value={value} outer={outer} onChange={onChange} />;
      break;
    }
    default:
      control = <input value={formatValue(value)} placeholder={field.placeholder} onChange={(e) => onChange(e.target.value)} className={INPUT_CLS} />;
  }

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold text-slate-300">
          {field.label}
          {field.required && <span className="text-sky-400"> *</span>}
        </span>
        <OriginBadge field={field} />
      </div>
      {control}
      {field.help && <p className="text-[11px] text-slate-500">{field.help}</p>}
    </div>
  );
}
