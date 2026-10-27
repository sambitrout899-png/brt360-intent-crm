import type { C360Audience } from "../../api/c360Types";

const ALL: C360Audience = { enabled: true, customer_types: ["account", "prospect"], segments: ["B2B", "B2C"] };

export default function AudienceEditor({ value, onChange, inherited = false }: {
  value?: C360Audience; onChange: (value: C360Audience) => void; inherited?: boolean;
}) {
  const r = value ?? ALL;
  return <fieldset className="space-y-2 rounded-lg border border-slate-700 bg-slate-950/40 p-3">
    <legend className="px-1 text-xs font-semibold text-sky-300">Customer audience</legend>
    <label className="flex items-center gap-2 text-xs text-slate-300"><input type="checkbox" checked={r.enabled} onChange={e => onChange({ ...r, enabled: e.target.checked })} />Show this component</label>
    <label className="block text-xs text-slate-400">Customer type
      <select aria-label="Audience customer type" className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-1.5 text-slate-100"
        value={r.customer_types.length === 2 ? "all" : r.customer_types[0] ?? "none"}
        onChange={e => onChange({ ...r, customer_types: e.target.value === "all" ? ["account", "prospect"] : e.target.value === "none" ? [] : [e.target.value as "account" | "prospect"] })}>
        <option value="all">Accounts and Prospects</option><option value="account">Accounts only</option><option value="prospect">Prospects only</option><option value="none">None</option>
      </select>
    </label>
    <label className="block text-xs text-slate-400">Customer segment
      <select aria-label="Audience customer segment" className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-1.5 text-slate-100"
        value={r.segments.length === 2 ? "all" : r.segments[0] ?? "none"}
        onChange={e => onChange({ ...r, segments: e.target.value === "all" ? ["B2B", "B2C"] : e.target.value === "none" ? [] : [e.target.value as "B2B" | "B2C"] })}>
        <option value="all">All segments</option><option value="B2B">B2B only</option><option value="B2C">B2C only</option><option value="none">None</option>
      </select>
    </label>
    <p className="text-[11px] leading-relaxed text-slate-500">Both conditions must match the selected customer.{inherited ? " The pane’s audience must also match." : " All segments includes customers with no segment recorded."}</p>
  </fieldset>;
}
