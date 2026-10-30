import { useEffect, useState } from "react";
import { flowFieldCatalog } from "../../api/client";
import type { MapCondition, MapEdge, MapNode } from "../../api/functionMapTypes";
import type { FlowFieldDef } from "../../api/types";
import { OPERATORS, conditionText } from "./geometry";

const RECORD_TYPES = new Set(["party", "lead", "opportunity", "quote", "order"]);
const catalogs = new Map<string, Promise<FlowFieldDef[]>>();

/** What a condition after this step can test: its inputs, its result and
 * its record - the same fields Flow Studio offers. */
function fieldsAfter(node: MapNode): Promise<FlowFieldDef[]> {
  const entity = node.target && RECORD_TYPES.has(node.target) ? node.target : null;
  const key = `${node.code}|${entity ?? ""}`;
  if (!catalogs.has(key)) {
    const load = (entity_type: string | null) =>
      flowFieldCatalog({ entity_type, trigger_type: "intent", trigger_intent_type: node.code, required_fields: null }).then((c) =>
        c.fields.filter((f) => /^(entities|result|record|customer|parent\.(lead|opportunity|quote))\./.test(f.path)),
      );
    catalogs.set(
      key,
      load(entity).catch(() => load(null).catch(() => [])),
    );
  }
  return catalogs.get(key)!;
}

const GROUPS: [string, string][] = [
  ["entities.", "What was entered"],
  ["result.", "What it produced"],
  ["record.", "The record afterwards"],
  // the records above it (an opportunity's lead, a quote's opportunity ...) and the customer behind it
  ["parent.quote.", "Its quote"],
  ["parent.opportunity.", "Its opportunity"],
  ["parent.lead.", "Its lead"],
  ["customer.", "The customer"],
];

const input = "mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-sm text-slate-200 focus:border-sky-500 focus:outline-none";

/** When this link applies: always, only when a condition holds on what the
 * step did, or otherwise (none of the step's other conditions held). */
export default function ConditionEditor({
  edge,
  source,
  siblings,
  names,
  editable,
  onChange,
}: {
  edge: MapEdge;
  source?: MapNode;
  siblings: MapEdge[];
  names: Record<string, MapNode>;
  editable: boolean;
  onChange: (when: MapCondition | null) => void;
}) {
  const [fields, setFields] = useState<FlowFieldDef[] | null>(null);
  const when = edge.when ?? null;
  const mode = !when ? "always" : when.operator === "otherwise" ? "otherwise" : "when";

  useEffect(() => {
    let live = true;
    if (source && mode === "when") fieldsAfter(source).then((f) => live && setFields(f));
    return () => {
      live = false;
    };
  }, [source, mode]);

  const field = fields?.find((f) => f.path === when?.field);
  const op = OPERATORS.find((o) => o.key === (when?.operator ?? "eq"));
  const values = field?.values?.length ? field.values : field?.recent ?? [];
  const known = fields ?? [];
  const missingField = when?.field && fields && !field;
  const others = siblings.filter((s) => s !== edge && s.kind === "next");
  const branches = others.some((s) => s.when);

  function setMode(m: string) {
    if (m === "always") onChange(null);
    else if (m === "otherwise") onChange({ operator: "otherwise" });
    else onChange({ field: when?.field ?? "", operator: when && when.operator !== "otherwise" ? when.operator : "eq", value: when?.value ?? "" });
  }

  const patch = (p: Partial<MapCondition>) => onChange({ field: when?.field ?? "", operator: when?.operator ?? "eq", value: when?.value ?? "", ...p });

  return (
    <div className="space-y-2">
      <p className="text-xs text-slate-400">{edge.kind === "auto" ? "Runs" : "Offered"}</p>
      {editable ? (
        <div className="flex rounded-lg border border-slate-700 p-0.5 text-xs">
          {(
            [
              ["always", "Always"],
              ["when", "Only when…"],
              ...(edge.kind === "next" ? [["otherwise", "Otherwise"]] : []),
            ] as [string, string][]
          ).map(([k, label]) => (
            <button
              key={k}
              onClick={() => setMode(k)}
              className={`flex-1 rounded-md px-2 py-1 ${mode === k ? "bg-violet-600 text-white" : "text-slate-400 hover:text-slate-200"}`}
              title={k === "otherwise" ? "Offered only when none of this step's other conditions holds" : undefined}
            >
              {label}
            </button>
          ))}
        </div>
      ) : (
        <p className="text-sm text-slate-200">{when ? conditionText(when) : "Always"}</p>
      )}

      {editable && mode === "when" && (
        <div className="space-y-2 rounded-lg border border-violet-500/30 bg-violet-500/5 p-2">
          <label className="block text-xs text-slate-400">
            Field
            <select value={when?.field ?? ""} onChange={(e) => patch({ field: e.target.value, value: "" })} className={input}>
              <option value="">{fields ? "Choose what to test…" : "Loading the fields…"}</option>
              {missingField && <option value={when!.field}>{when!.field}</option>}
              {GROUPS.map(([prefix, label]) => {
                const group = known.filter((f) => f.path.startsWith(prefix));
                return group.length ? (
                  <optgroup key={prefix} label={label}>
                    {group.map((f) => (
                      <option key={f.path} value={f.path}>
                        {f.label.replace(/^(Intent (input|result)|Customer|Lead|Opportunity|Quote): /, "")}
                      </option>
                    ))}
                  </optgroup>
                ) : null;
              })}
            </select>
          </label>
          <div className="flex gap-2">
            <label className="block w-36 shrink-0 text-xs text-slate-400">
              Test
              <select value={when?.operator ?? "eq"} onChange={(e) => patch({ operator: e.target.value })} className={input}>
                {OPERATORS.map((o) => (
                  <option key={o.key} value={o.key}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            {!op?.noValue && (
              <label className="block min-w-0 flex-1 text-xs text-slate-400">
                Value
                {values.length && when?.operator !== "in" ? (
                  <select value={when?.value ?? ""} onChange={(e) => patch({ value: e.target.value })} className={input}>
                    <option value="">Choose…</option>
                    {when?.value && !values.includes(when.value) && <option value={when.value}>{when.value}</option>}
                    {values.map((v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    value={when?.value ?? ""}
                    onChange={(e) => patch({ value: e.target.value })}
                    placeholder={when?.operator === "in" ? (values.length ? values.slice(0, 3).join(", ") : "a, b, c") : "value"}
                    className={input}
                  />
                )}
              </label>
            )}
          </div>
          {!when?.field && <p className="text-[11px] text-amber-300">Pick a field - until then the link has no condition.</p>}
        </div>
      )}

      {(branches || when) && others.length > 0 && (
        <div className="rounded-lg border border-slate-800 bg-slate-950/50 p-2 text-[11px] text-slate-400">
          <p className="mb-1 font-semibold text-slate-300">After “{source?.label}”, the next step is decided by:</p>
          <ul className="space-y-0.5">
            {[edge, ...others].map((s) => (
              <li key={`${s.target}-${s.kind}`} className={s === edge ? "text-slate-200" : ""}>
                → {names[s.target]?.label ?? s.target}
                <span className="ml-1 text-violet-300">{s.when ? conditionText(s.when) : "always"}</span>
              </li>
            ))}
          </ul>
          <p className="mt-1 text-slate-500">Once any link here has a condition, the map alone decides what's offered after this step.</p>
        </div>
      )}
    </div>
  );
}
