import { useState } from "react";

const NUMBER_FIELDS = /(_id$|^quantity$|^new_quantity$|price$|_pct$)/;
const LIST_FIELDS = new Set(["composition", "sites"]);

const HINTS: Record<string, string> = {
  composition: "products, separated by + or comma - e.g. Fiber500 + VOIP",
  sites: "e.g. A-End + Z-End",
  list_price: "monthly list price",
  requested_discount_pct: "percent, e.g. 10",
  offering_name: "an existing bundle / offering",
  replacement_offering_name: "the bundle customers move to",
  to_user: "email or name",
  to_position: "position name",
};

function humanize(field: string) {
  return field.replace(/_id$/, " #").replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

/** Converts what was typed into the shape each field expects. */
export function coerceFieldValues(values: Record<string, string>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, raw] of Object.entries(values)) {
    const value = raw.trim();
    if (!value) continue;
    if (LIST_FIELDS.has(key)) out[key] = value.split(/[,+]/).map((p) => p.trim()).filter(Boolean);
    else if (NUMBER_FIELDS.test(key) && !Number.isNaN(Number(value))) out[key] = Number(value);
    else out[key] = value;
  }
  return out;
}

/** The second turn of a conversation with the Intent Engine: it knows (or
 * has guessed) the action, and asks for just the details still missing.
 * With no fields it is a plain "yes, run it" confirmation. */
export default function IntentFollowUp({
  title,
  fields,
  busy,
  offerings = [],
  submitLabel = "Run it",
  onSubmit,
  onCancel,
}: {
  title: string;
  fields: string[];
  busy: boolean;
  offerings?: string[];
  submitLabel?: string;
  onSubmit: (values: Record<string, unknown>) => void;
  onCancel?: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(fields.map((f) => [f, ""])));
  const complete = fields.every((f) => values[f]?.trim());
  const set = (key: string, value: string) => setValues((v) => ({ ...v, [key]: value }));
  const inputCls = "mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-200 outline-none focus:border-sky-500";

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (complete && !busy) onSubmit(coerceFieldValues(values));
      }}
      className="space-y-2 rounded-lg border border-sky-500/30 bg-slate-950 p-3"
    >
      <p className="text-xs font-semibold text-sky-300">{title}</p>
      {fields.map((key, i) => (
        <label key={key} className="block text-xs text-slate-400">
          {humanize(key)}
          {key === "decision" ? (
            <select value={values[key]} onChange={(e) => set(key, e.target.value)} className={inputCls}>
              <option value="">—</option>
              <option value="approve">approve</option>
              <option value="reject">reject</option>
            </select>
          ) : key === "action" ? (
            <input value={values[key]} onChange={(e) => set(key, e.target.value)} placeholder="e.g. close_won, suspend, cancel" className={inputCls} autoFocus={i === 0} />
          ) : (
            <input
              value={values[key]}
              onChange={(e) => set(key, e.target.value)}
              list={key.includes("offering_name") && offerings.length ? "intent-offerings" : undefined}
              inputMode={NUMBER_FIELDS.test(key) ? "decimal" : undefined}
              placeholder={HINTS[key] ?? ""}
              className={inputCls}
              autoFocus={i === 0}
            />
          )}
        </label>
      ))}
      {offerings.length > 0 && (
        <datalist id="intent-offerings">
          {offerings.map((o) => (
            <option key={o} value={o} />
          ))}
        </datalist>
      )}
      <div className="flex gap-2 pt-1">
        <button type="submit" disabled={busy || !complete} className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-sky-500 disabled:opacity-50">
          {busy ? "Running…" : submitLabel}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel} className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-bold text-slate-300">
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
