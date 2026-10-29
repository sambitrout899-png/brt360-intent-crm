import FlowExpressionBuilder from "./FlowExpressionBuilder";
import { useId, useMemo, useRef, useState } from "react";
import type { FlowCatalog, FlowFieldCatalog, FlowFieldDef, FlowIssue, FlowNode } from "../api/types";

const INPUT = "mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200";

const SOURCE_STYLES: Record<string, string> = {
  core: "text-sky-400",
  custom: "text-violet-400",
  lifecycle: "text-emerald-400",
  intent: "text-amber-400",
  result: "text-rose-400",
  error: "text-red-400",
};
const SOURCE_LABELS: Record<string, string> = { core: "field", custom: "custom", lifecycle: "lifecycle", intent: "intent input", result: "intent result", error: "error" };

const OPERATORS_BY_TYPE: Record<string, string[]> = {
  number: ["eq", "ne", "gt", "gte", "lt", "lte", "empty", "not_empty"],
  date: ["eq", "ne", "gt", "gte", "lt", "lte", "empty", "not_empty"],
  picklist: ["eq", "ne", "in", "empty", "not_empty"],
  boolean: ["eq", "ne", "empty", "not_empty"],
  text: ["eq", "ne", "contains", "in", "empty", "not_empty"],
};
const OPERATOR_LABELS: Record<string, string> = {
  eq: "equals",
  ne: "does not equal",
  gt: "is greater than",
  gte: "is at least",
  lt: "is less than",
  lte: "is at most",
  contains: "contains",
  in: "is one of",
  empty: "is empty",
  not_empty: "is not empty",
};

export function findField(catalog: FlowFieldCatalog | null, path: string | undefined): FlowFieldDef | undefined {
  if (!catalog || !path) return undefined;
  return catalog.fields.find((f) => f.path === path) ?? catalog.fields.find((f) => f.key === path);
}

/** Searchable field picker: type a label, a key or a path. */
export function FieldCombo({
  value,
  catalog,
  settableOnly = false,
  onChange,
}: {
  value: string;
  catalog: FlowFieldCatalog | null;
  settableOnly?: boolean;
  onChange: (path: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const blurTimer = useRef<number | undefined>(undefined);
  const selected = findField(catalog, value);
  const options = useMemo(() => {
    const all = (catalog?.fields ?? []).filter((f) => !settableOnly || f.settable);
    const q = query.trim().toLowerCase();
    return q ? all.filter((f) => `${f.label} ${f.key} ${f.path}`.toLowerCase().includes(q)) : all;
  }, [catalog, query, settableOnly]);

  return (
    <div className="relative mt-1">
      <input
        value={open ? query : selected ? `${selected.label}` : value}
        placeholder={catalog ? "Search fields…" : "loading fields…"}
        onFocus={() => {
          setQuery("");
          setOpen(true);
        }}
        onChange={(e) => setQuery(e.target.value)}
        onBlur={() => {
          // let a click on an option land first; free text is kept as typed
          blurTimer.current = window.setTimeout(() => {
            setOpen(false);
            if (query.trim() && !options.some((o) => o.path === value)) onChange(query.trim());
          }, 150);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && options[0]) {
            e.preventDefault();
            onChange(options[0].path);
            setOpen(false);
          }
        }}
        className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
      />
      {selected && !open && (
        <div className="mt-1 flex items-center gap-2 text-[11px]">
          <span className="font-mono text-slate-500">{selected.path}</span>
          <span className={SOURCE_STYLES[selected.source]}>{SOURCE_LABELS[selected.source]}</span>
          <span className="text-slate-500">{selected.data_type}</span>
        </div>
      )}
      {open && (
        <div className="absolute z-20 mt-1 max-h-64 w-full overflow-y-auto rounded-md border border-slate-700 bg-slate-900 shadow-xl">
          {options.map((f) => (
            <button
              key={f.path}
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => {
                window.clearTimeout(blurTimer.current);
                onChange(f.path);
                setOpen(false);
              }}
              className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-slate-800 ${f.path === value ? "bg-slate-800" : ""}`}
            >
              <span className="text-slate-200">{f.label}</span>
              <span className="font-mono text-slate-500">{f.path}</span>
              <span className={`ml-auto ${SOURCE_STYLES[f.source]}`}>{f.data_type === "picklist" ? `${f.values.length} values` : f.data_type}</span>
            </button>
          ))}
          {options.length === 0 && <p className="px-3 py-2 text-xs text-slate-500">No matching field{settableOnly ? " that a flow can set" : ""}.</p>}
        </div>
      )}
    </div>
  );
}

/** Value input shaped by the field: picklist -> choices, boolean -> yes/no,
 * number -> numeric, text -> free with previously used values offered. */
function ValueInput({
  field,
  operator,
  value,
  onChange,
}: {
  field: FlowFieldDef | undefined;
  operator: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const listId = useId();
  if (field?.data_type === "picklist" && field.values.length && operator === "in") {
    const chosen = value.split(",").map((v) => v.trim()).filter(Boolean);
    return (
      <div className="mt-1 flex flex-wrap gap-1">
        {field.values.map((v) => {
          const on = chosen.includes(v);
          return (
            <button
              key={v}
              type="button"
              onClick={() => onChange((on ? chosen.filter((c) => c !== v) : [...chosen, v]).join(","))}
              className={`rounded border px-2 py-0.5 text-xs ${on ? "border-sky-500/50 bg-sky-500/10 text-sky-300" : "border-slate-700 text-slate-400"}`}
            >
              {v}
            </button>
          );
        })}
      </div>
    );
  }
  if ((field?.data_type === "picklist" && field.values.length) || field?.data_type === "boolean") {
    const options = field.data_type === "boolean" ? ["true", "false"] : field.values;
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)} className={INPUT}>
        <option value="">— choose —</option>
        {!options.includes(value) && value && <option value={value}>{value} (not a valid value)</option>}
        {options.map((v) => (
          <option key={v} value={v}>
            {field.data_type === "boolean" ? (v === "true" ? "yes" : "no") : v}
          </option>
        ))}
      </select>
    );
  }
  return (
    <>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        list={field?.recent.length ? listId : undefined}
        inputMode={field?.data_type === "number" ? "decimal" : undefined}
        placeholder={field?.data_type === "number" ? "a number, or {{field}}" : "value, or {{field}}"}
        className={INPUT}
      />
      {field && field.recent.length > 0 && (
        <datalist id={listId}>
          {field.recent.map((v) => (
            <option key={v} value={v} />
          ))}
        </datalist>
      )}
    </>
  );
}

/** Textarea with an "insert field" picker that drops in a {{path}}. */
function TemplateInput({
  value,
  catalog,
  placeholder,
  onChange,
}: {
  value: string;
  catalog: FlowFieldCatalog | null;
  placeholder: string;
  onChange: (value: string) => void;
}) {
  return (
    <>
      <textarea value={value} onChange={(e) => onChange(e.target.value)} rows={2} placeholder={placeholder} className={`${INPUT} resize-none`} />
      {catalog && catalog.fields.length > 0 && (
        <select
          value=""
          onChange={(e) => e.target.value && onChange(`${value}{{${e.target.value}}}`)}
          className="mt-1 w-full rounded-md border border-slate-800 bg-slate-950 px-2 py-1 text-[11px] text-slate-400"
        >
          <option value="">+ insert a field…</option>
          {catalog.fields.map((f) => (
            <option key={f.path} value={f.path}>
              {f.label} ({f.path})
            </option>
          ))}
        </select>
      )}
    </>
  );
}

function Select({ value, options, onChange, blank = "—" }: { value: string; options: { value: string; label: string }[]; onChange: (v: string) => void; blank?: string }) {
  const known = options.some((o) => o.value === value);
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className={INPUT}>
      <option value="">{blank}</option>
      {value && !known && <option value={value}>{value} (not found)</option>}
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

const KEY_LABELS: Record<string, string> = {
  field: "Field",
  operator: "Operator",
  value: "Value",
  to_state: "Move to state",
  to_user: "User",
  to_position: "Position",
  strategy: "Strategy",
  channel: "Channel",
  audience: "Audience",
  message: "Message",
  question: "Question for the AI",
  default: "If the AI is unavailable",
  title: "Box title",
  level: "Box style",
  intent: "Action to run",
  entities: "Values for it (the record's own ids are filled in)",
  on_error: "If it can't run (and its error output isn't linked)",
  codes: "Catch these errors",
  code: "Error code",
  category: "Kind of error",
  retries: "Retry if it hits an unexpected error",
  amount: "Wait for",
  unit: "Unit",
  approver: "Who approves",
  subject: "What they're asked",
  expire_days: "Expires after (days) - then it goes down 'expired'",
};

/** A Catch errors step's filter: error codes and categories, kept as "A, B, validation". */
function CodePicker({ value, options, onChange }: { value: string; options: FlowCatalog["error_codes"]; onChange: (v: string) => void }) {
  const chosen = value.split(",").map((s) => s.trim()).filter(Boolean);
  const known = new Set((options ?? []).map((o) => o.value));
  const custom = chosen.filter((c) => !known.has(c));
  const toggle = (code: string) => onChange((chosen.includes(code) ? chosen.filter((c) => c !== code) : [...chosen, code]).join(", "));
  return (
    <div className="mt-1 space-y-1 rounded-md border border-slate-700 bg-slate-950 p-2">
      <p className="text-[11px] text-slate-500">{chosen.length ? "Only these:" : "None ticked = every error (except a loop, and Reject which is a decision)"}</p>
      <div className="max-h-44 space-y-0.5 overflow-auto">
        {(options ?? []).map((o) => (
          <label key={o.value} className="flex items-start gap-2 text-[11px] text-slate-300">
            <input type="checkbox" className="mt-0.5" checked={chosen.includes(o.value)} onChange={() => toggle(o.value)} />
            <span>{o.label}</span>
          </label>
        ))}
      </div>
      <input
        value={custom.join(", ")}
        onChange={(e) => onChange([...chosen.filter((c) => known.has(c)), ...e.target.value.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean)].join(", "))}
        placeholder="your own codes from Raise error steps, e.g. CREDIT_CHECK_FAILED"
        className={INPUT}
      />
    </div>
  );
}

const CHANNEL_LABELS: Record<string, string> = { popup: "message box on screen", in_app: "in-app notification", sms: "SMS", chat: "Chat message" };

/** A Run-an-action node's values, edited as JSON - kept as an object. */
/** Every setting of a node, each with the input that fits it. */
export default function NodeConfigFields({
  node,
  configKeys,
  catalog,
  issues,
  onConfig,
  recordEntities = [],
  errorCodes = [],
  hasCatch = false,
}: {
  node: FlowNode;
  configKeys: string[];
  catalog: FlowFieldCatalog | null;
  issues: FlowIssue[];
  onConfig: (config: Record<string, string>) => void;
  recordEntities?: FlowCatalog["record_entities"];
  /** what a Catch errors step can be limited to */
  errorCodes?: FlowCatalog["error_codes"];
  /** the flow has a Catch errors step - an action's default failure goes there */
  hasCatch?: boolean;
}) {
  const config = node.config;
  const set = (key: string, value: string) => onConfig({ ...config, [key]: value });
  const field = findField(catalog, config.field);
  const operators = field ? OPERATORS_BY_TYPE[field.data_type] ?? OPERATORS_BY_TYPE.text : Object.keys(OPERATOR_LABELS);

  function input(key: string) {
    const value = config[key] ?? "";
    switch (key) {
      case "entity":
        return <Select value={value} onChange={(v) => set(key, v)} options={recordEntities.map((e) => ({ value: e.name, label: e.label }))} />;
      case "operation":
        return <Select value={value} onChange={(v) => set(key, v)} options={["read", "insert", "update", "upsert", "delete"].map((v) => ({ value: v, label: v }))} />;
      case "match":
      case "values":
        return <FlowExpressionBuilder key={`${node.id}-${key}`} value={config[key]} catalog={catalog} targets={recordEntities.find((e) => e.name === config.entity)?.fields.filter((f) => key === "match" || f.writable).map((f) => f.name)} onChange={(v) => onConfig({ ...config, [key]: v as unknown as string })} />;
      case "field":
        return <FieldCombo value={value} catalog={catalog} settableOnly={node.type === "set_field"} onChange={(v) => set("field", v)} />;
      case "operator":
        return (
          <select value={value || "eq"} onChange={(e) => set("operator", e.target.value)} className={INPUT}>
            {!operators.includes(value || "eq") && <option value={value}>{OPERATOR_LABELS[value] ?? value} (not for this field)</option>}
            {operators.map((o) => (
              <option key={o} value={o}>
                {OPERATOR_LABELS[o]}
              </option>
            ))}
          </select>
        );
      case "value":
        if (config.operator === "empty" || config.operator === "not_empty") return null;
        return <ValueInput field={field} operator={config.operator ?? "eq"} value={value} onChange={(v) => set("value", v)} />;
      case "to_state":
        return <Select value={value} onChange={(v) => set(key, v)} options={(catalog?.states ?? []).map((s) => ({ value: s.code, label: `${s.label} (${s.code})` }))} />;
      case "to_user":
        return <Select value={value} onChange={(v) => set(key, v)} options={(catalog?.users ?? []).map((u) => ({ value: u.email, label: `${u.name} - ${u.email}` }))} />;
      case "to_position":
        return <Select value={value} onChange={(v) => set(key, v)} options={(catalog?.positions ?? []).map((p) => ({ value: p, label: p }))} />;
      case "strategy":
        return (
          <Select
            value={value}
            blank="assign to the position"
            onChange={(v) => set(key, v)}
            options={[{ value: "round_robin", label: "rotate across its members (round robin)" }]}
          />
        );
      case "channel":
        return (
          <Select
            value={value}
            blank="email (default)"
            onChange={(v) => set(key, v)}
            options={(catalog?.channels ?? []).map((c) => ({ value: c, label: CHANNEL_LABELS[c] ?? c }))}
          />
        );
      case "title":
        if (config.channel !== "popup") return null;
        return <TemplateInput value={value} catalog={catalog} placeholder="e.g. Quote {{record.id}} approved (blank = the flow's name)" onChange={(v) => set(key, v)} />;
      case "level":
        if (config.channel !== "popup") return null;
        return (
          <Select
            value={value}
            blank="info (default)"
            onChange={(v) => set(key, v)}
            options={[
              { value: "success", label: "success - green" },
              { value: "warning", label: "warning - amber" },
              { value: "urgent", label: "urgent - red" },
            ]}
          />
        );
      case "audience":
        return (
          <>
            <TemplateInput
              value={value}
              catalog={catalog}
              placeholder={config.channel === "popup" ? "me (who did it) · owner · a user's email · a position" : "owner, a position, an email…"}
              onChange={(v) => set(key, v)}
            />
            {config.channel === "popup" && (
              <span className="mt-1 block text-[11px] text-slate-500">
                Blank or “me”: the person whose action started the flow sees it at once. Others see it the next time they open a page.
              </span>
            )}
          </>
        );
      case "default":
        return <Select value={value} blank="false" onChange={(v) => set(key, v)} options={[{ value: "true", label: "take the true branch" }, { value: "false", label: "take the false branch" }]} />;
      case "intent":
        return <input value={value} onChange={(e) => set(key, e.target.value.trim().toUpperCase())} placeholder="e.g. INT-38 (Send to Presales)" className={INPUT} />;
      case "entities":
        return <FlowExpressionBuilder value={config[key] as unknown} catalog={catalog} onChange={(v) => onConfig({ ...config, entities: v as unknown as string })} />;
      case "on_error":
        return (
          <Select
            value={value}
            blank={hasCatch ? "default - go to the flow's Catch errors step" : "default - skip it and carry on"}
            onChange={(v) => set(key, v)}
            options={[
              { value: "skip", label: "skip it and carry on" },
              { value: "stop", label: hasCatch ? "raise it - Catch errors, else the flow fails" : "stop - fail the flow (see “If this flow fails”)" },
            ]}
          />
        );
      case "codes":
        return <CodePicker value={value} options={errorCodes} onChange={(v) => set(key, v)} />;
      case "code":
        return (
          <input
            value={value}
            onChange={(e) => set(key, e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_"))}
            placeholder="e.g. CREDIT_CHECK_FAILED"
            className={`${INPUT} font-mono`}
          />
        );
      case "category":
        return (
          <Select
            value={value}
            blank="business - a rule of yours said no (default)"
            onChange={(v) => set(key, v)}
            options={[{ value: "validation", label: "validation - the data isn't right" }]}
          />
        );
      case "retries":
        return (
          <Select
            value={value}
            blank="no - fail at once"
            onChange={(v) => set(key, v)}
            options={["1", "2", "3"].map((n) => ({ value: n, label: `up to ${n} more time${n === "1" ? "" : "s"} (only unexpected/system errors)` }))}
          />
        );
      case "amount":
        return <input type="number" min={1} value={value} onChange={(e) => set(key, e.target.value.replace(/\D/g, ""))} placeholder="e.g. 2" className={INPUT} />;
      case "unit":
        return <Select value={value} blank="choose…" onChange={(v) => set(key, v)} options={["minutes", "hours", "days"].map((u) => ({ value: u, label: u }))} />;
      case "approver":
        return (
          <Select
            value={value}
            blank="choose a user or a position…"
            onChange={(v) => set(key, v)}
            options={[
              ...(catalog?.positions ?? []).map((p) => ({ value: p, label: `${p} (anyone in the position)` })),
              ...(catalog?.users ?? []).map((u) => ({ value: u.email, label: `${u.name} - ${u.email}` })),
            ]}
          />
        );
      case "subject":
        return <TemplateInput value={value} catalog={catalog} placeholder="e.g. Approve the discount on quote {{record.id}}" onChange={(v) => set(key, v)} />;
      case "expire_days":
        return <input type="number" min={1} max={365} value={value} onChange={(e) => set(key, e.target.value.replace(/\D/g, ""))} placeholder="blank = it waits until someone decides" className={INPUT} />;
      case "message":
      case "question":
        return <TemplateInput value={value} catalog={catalog} placeholder={key === "question" ? "e.g. Is {{record.discount_justification}} a credible reason?" : "supports {{record.id}}"} onChange={(v) => set(key, v)} />;
      default:
        return <input value={value} onChange={(e) => set(key, e.target.value)} className={INPUT} />;
    }
  }

  return (
    <>
      {node.type === "record" && <details className="text-slate-400" open>
        <summary>Entity fields (required on insert marked *)</summary>
        <p>Match selects one record. Use id, brt_id or a unique combination. System IDs are generated automatically.</p>
        <div className="max-h-48 overflow-auto">{recordEntities.find((e) => e.name === config.entity)?.fields.map((f) =>
          <div key={f.name} className="py-1"><code>{f.name}{f.required ? " *" : ""}</code> · {f.type}{!f.writable ? " · read only" : ""}{f.choices.length ? ` · ${f.choices.join(", ")}` : ""}{f.references.length ? ` → ${f.references.join(", ")}` : ""}</div>)}</div>
      </details>}
      {configKeys.map((key) => {
        const control = input(key);
        if (control === null) return null;
        const keyIssues = issues.filter((i) => i.key === key);
        return (
          <label key={key} className="block text-slate-400">
            {KEY_LABELS[key] ?? key}
            {control}
            {keyIssues.map((i, n) => (
              <span key={n} className="mt-1 flex items-center gap-2 text-[11px] text-amber-400">
                ⚠ {i.message}
                {i.suggestion && (
                  <button type="button" onClick={() => set(key, i.suggestion!)} className="rounded border border-amber-500/40 px-1.5 text-amber-300 hover:bg-amber-500/10">
                    use {findField(catalog, i.suggestion)?.label ?? i.suggestion}
                  </button>
                )}
              </span>
            ))}
          </label>
        );
      })}
    </>
  );
}
