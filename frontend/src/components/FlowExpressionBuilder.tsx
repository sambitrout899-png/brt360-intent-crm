import { useId, useState } from "react";
import type { FlowFieldCatalog } from "../api/types";

const INPUT = "w-full rounded border border-slate-700 bg-slate-950 px-2 py-2 text-xs text-slate-200";
const reference = /^\{\{\s*([\w.]+)\s*\}\}$/;
type Props = { value: unknown; onChange: (v: Record<string, unknown>) => void; catalog?: FlowFieldCatalog | null; targets?: string[] };

function MappingRow({ name, value, catalog, change, remove }: {
  name: string; value: unknown; catalog: Props["catalog"]; change: (v: unknown) => void; remove: () => void;
}) {
  const ref = typeof value === "string" ? reference.exec(value) : null;
  const inferred = ref || value === "" ? "field" : value === null ? "null" : typeof value === "object" ? "json" : typeof value;
  const [mode, setMode] = useState(inferred);
  const [search, setSearch] = useState("");
  const fields = (catalog?.fields ?? []).filter((f) => `${f.label} ${f.path}`.toLowerCase().includes(search.toLowerCase()));
  const groups = [
    ["Flow inputs · from triggering intent", (p: string) => p.startsWith("flow.inputs.")],
    ["Trigger outputs", (p: string) => p.startsWith("trigger.output") || p.startsWith("result.")],
    ["Trigger inputs", (p: string) => p.startsWith("trigger.input") || p.startsWith("entities.")],
    ["Previous steps", (p: string) => p.startsWith("steps.")],
    ["Current record", (p: string) => p.startsWith("record.")],
  ] as const;
  return <div className="space-y-2 rounded-lg border border-slate-700 p-3">
    <div className="flex justify-between gap-2"><strong className="break-all text-sky-300">{name}</strong><button type="button" onClick={remove} aria-label={`Remove ${name}`} className="text-red-400">Remove</button></div>
    <select aria-label={`Value source for ${name}`} className={INPUT} value={mode} onChange={(e) => {
      const next = e.target.value; setMode(next);
      change(next === "number" ? 0 : next === "boolean" ? false : next === "null" ? null : next === "json" ? {} : "");
    }}>
      <option value="field">Use a field / previous output</option><option value="string">Enter text</option>
      <option value="number">Enter number</option><option value="boolean">True / false</option><option value="null">Empty (null)</option><option value="json">Object / list (JSON)</option>
    </select>
    {mode === "field" && <>
      <input className={INPUT} aria-label={`Search source for ${name}`} placeholder="Search customer, order, product…" value={search} onChange={(e) => setSearch(e.target.value)} />
      <select className={INPUT} aria-label={`Source field for ${name}`} value={ref?.[1] ?? ""} onChange={(e) => change(e.target.value ? `{{${e.target.value}}}` : "")}>
        <option value="">Choose a source field…</option>
        {ref && !fields.some((f) => f.path === ref[1]) && <option value={ref[1]}>{ref[1]}</option>}
        {groups.map(([label, matches]) => <optgroup key={label} label={label}>{fields.filter((f) => matches(f.path)).map((f) => <option key={f.path} value={f.path}>{f.label}</option>)}</optgroup>)}
      </select>
      {ref && <p className="break-all text-[11px] text-slate-400">Reading: {catalog?.fields.find((f) => f.path === ref[1])?.label ?? ref[1]}</p>}
      <details className="text-slate-400"><summary>Nested field or list item</summary>
        <input aria-label={`Advanced source path for ${name}`} className={INPUT} placeholder="trigger.output.eligible.0" value={ref?.[1] ?? ""} onChange={(e) => change(e.target.value ? `{{${e.target.value}}}` : "")} />
        <p>Choose a whole output above, then append a field name or a list index here.</p>
      </details>
    </>}
    {mode === "string" && <input className={INPUT} aria-label={`Text for ${name}`} value={String(value ?? "")} onChange={(e) => change(e.target.value)} />}
    {mode === "number" && <input className={INPUT} aria-label={`Number for ${name}`} type="number" value={typeof value === "number" ? value : ""} onChange={(e) => change(e.target.value === "" ? null : Number(e.target.value))} />}
    {mode === "boolean" && <select className={INPUT} aria-label={`Boolean for ${name}`} value={String(value)} onChange={(e) => change(e.target.value === "true")}><option value="false">False</option><option value="true">True</option></select>}
    {mode === "json" && <><pre className="overflow-auto text-slate-300">{JSON.stringify(value, null, 2)}</pre><p className="text-slate-400">Use Advanced JSON to edit this object or list, or select a field containing the whole object.</p></>}
  </div>;
}

export default function FlowExpressionBuilder({ value, onChange, catalog, targets = [] }: Props) {
  const [advanced, setAdvanced] = useState(typeof value === "string");
  const [target, setTarget] = useState("");
  const [draft, setDraft] = useState(typeof value === "string" ? value : JSON.stringify(value ?? {}, null, 2));
  const [error, setError] = useState("");
  const id = useId();
  const obj = value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
  return <div className="space-y-3">
    <div className="flex gap-2"><button type="button" className={!advanced ? "font-bold text-sky-300" : "text-slate-400"} disabled={!!error} onClick={() => {
      if (typeof value === "string") {
        try { const parsed = JSON.parse(value); if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error(); onChange(parsed); }
        catch { setError("Enter a valid JSON object before using the builder."); return; }
      }
      setAdvanced(false);
    }}>Expression builder</button>
      <button type="button" className={advanced ? "font-bold text-sky-300" : "text-slate-400"} onClick={() => { setDraft(typeof value === "string" ? value : JSON.stringify(value ?? {}, null, 2)); setAdvanced(true); }}>Advanced JSON</button></div>
    {advanced ? <><textarea aria-label="Advanced mappings JSON" className={`${INPUT} min-h-32 font-mono`} value={draft} onChange={(e) => {
      setDraft(e.target.value);
      try { const parsed = JSON.parse(e.target.value); if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error(); setError(""); onChange(parsed); }
      catch { setError("Enter a valid JSON object before running the flow."); onChange(e.target.value as unknown as Record<string, unknown>); }
    }} />{error && <p role="alert" className="text-amber-400">{error}</p>}</> : <>
      {Object.entries(obj).map(([key, v]) => <MappingRow key={key} name={key} value={v} catalog={catalog} change={(next) => onChange({ ...obj, [key]: next })} remove={() => { const next = { ...obj }; delete next[key]; onChange(next); }} />)}
      {!Object.keys(obj).length && <p className="text-slate-400">Add a destination field, then choose where its value comes from.</p>}
      <div className="flex gap-2"><input list={id} className={INPUT} aria-label="Destination field" placeholder="Choose or name a destination field" value={target} onChange={(e) => setTarget(e.target.value)} />
        <datalist id={id}>{targets.map((t) => <option key={t} value={t} />)}</datalist>
        <button type="button" className="shrink-0 rounded border border-sky-600 px-2 text-sky-300" disabled={!target.trim() || Object.hasOwn(obj, target.trim())} onClick={() => { onChange({ ...obj, [target.trim()]: "" }); setTarget(""); }}>+ Field</button></div>
    </>}
  </div>;
}
