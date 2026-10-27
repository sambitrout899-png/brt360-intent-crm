import { useEffect, useMemo, useState } from "react";
import { pageForm, type FormField, type PageForm } from "../../api/client";
import { useWorkspace } from "../../workspace";
import { INPUT_CLS } from "../intent/intentUi";
import { checkForm } from "./pageRules";

/** One field of a designed page. */
export function PageFieldControl({ field, value, onChange, problems }: { field: FormField; value: unknown; onChange: (v: unknown) => void; problems?: string[] }) {
  const text = value === null || value === undefined ? "" : String(value);
  const bad = problems && problems.length > 0;
  const cls = `${INPUT_CLS} ${bad ? "!border-red-500/70" : ""}`;
  let control;
  if (field.type === "picklist") {
    control =
      field.values.length <= 4 ? (
        <div className="flex flex-wrap gap-1.5">
          {field.values.map((v) => (
            <button
              key={v}
              type="button"
              disabled={field.readonly}
              onClick={() => onChange(text === v && !field.mandatory ? "" : v)}
              className={`rounded-lg border px-2.5 py-1 text-xs font-semibold ${text === v ? "border-sky-500 bg-sky-500/20 text-sky-200" : "border-slate-700 text-slate-300 hover:border-slate-500"}`}
            >
              {v}
            </button>
          ))}
        </div>
      ) : (
        <select disabled={field.readonly} value={text} onChange={(e) => onChange(e.target.value)} className={cls}>
          <option value="">Choose…</option>
          {field.values.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
      );
  } else if (field.type === "boolean") {
    control = (
      <div className="flex gap-1.5">
        {["true", "false"].map((v) => (
          <button
            key={v}
            type="button"
            disabled={field.readonly}
            onClick={() => onChange(text === v ? "" : v)}
            className={`rounded-lg border px-3 py-1 text-xs font-semibold ${text === v ? "border-sky-500 bg-sky-500/20 text-sky-200" : "border-slate-700 text-slate-300"}`}
          >
            {v === "true" ? "Yes" : "No"}
          </button>
        ))}
      </div>
    );
  } else if (field.type === "textarea") {
    control = <textarea rows={2} readOnly={field.readonly} value={text} onChange={(e) => onChange(e.target.value)} className={`${cls} resize-y`} />;
  } else {
    const type = field.type === "date" ? "date" : field.type === "number" ? "number" : field.type === "email" ? "email" : field.type === "phone" ? "tel" : "text";
    control = <input type={type} readOnly={field.readonly} value={text} onChange={(e) => onChange(e.target.value)} className={cls} />;
  }
  return (
    <label className="block space-y-1">
      <span className="text-xs font-semibold text-slate-300">
        {field.label}
        {field.mandatory && <span className="text-sky-400"> *</span>}
        {field.readonly && <span className="ml-1 text-[10px] text-slate-500">read-only</span>}
      </span>
      {control}
      {bad ? <span className="block text-[11px] text-red-400">{problems!.join(" · ")}</span> : field.help && <span className="block text-[11px] text-slate-500">{field.help}</span>}
    </label>
  );
}

const BUTTON_STYLE: Record<string, string> = {
  primary: "bg-sky-600 text-white hover:bg-sky-500",
  danger: "border border-red-500/50 text-red-300 hover:bg-red-500/10",
  default: "border border-slate-600 text-slate-200 hover:border-sky-500 hover:text-sky-300",
};

/** A button the Page Designer put on the page: opens its intent (or Flow
 * Studio flow) prefilled with the record - hidden when the user may not run it. */
export function PageButton({ field, record }: { field: FormField; record?: { entityType: string; id: number } | null }) {
  const { runIntent, canRun } = useWorkspace();
  if (!field.intent || !canRun(field.intent)) return null;
  const entities = record ? { entity_type: record.entityType, entity_id: record.id, [`${record.entityType}_id`]: record.id } : {};
  return (
    <button type="button" onClick={() => runIntent(field.intent!, entities)}
      className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${BUTTON_STYLE[field.style ?? "default"] ?? BUTTON_STYLE.default}`}>
      {field.label}
    </button>
  );
}

/** A designed page rendered as a form: its sections, mandatory marks and
 * live checks, plus its buttons and text. `skip` = fields the surrounding
 * form already asks for; `record` = the record the page shows (buttons act on it). */
export function PageFormView({
  form,
  values,
  onChange,
  skip = [],
  record,
}: {
  form: PageForm;
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
  skip?: string[];
  record?: { entityType: string; id: number } | null;
}) {
  const problems = useMemo(() => checkForm(form, values), [form, values]);
  const general = problems[""] ?? [];
  return (
    <div className="space-y-3">
      {form.sections.map((s) => {
        const fields = s.fields.filter((f) => !skip.includes(f.key));
        if (!fields.length) return null;
        const buttons = fields.filter((f) => f.kind === "button");
        return (
          <fieldset key={s.title} className="rounded-xl border border-slate-800 bg-slate-950/40 p-3">
            <legend className="px-1 text-[11px] font-bold uppercase tracking-wide text-slate-400">{s.title}</legend>
            <div className={`grid gap-3 ${s.columns === 1 ? "" : "sm:grid-cols-2"}`}>
              {fields.filter((f) => f.kind !== "button").map((f) =>
                f.kind === "text" ? (
                  <p key={f.key} className="whitespace-pre-line rounded-lg bg-slate-800/40 px-3 py-2 text-xs text-slate-300 sm:col-span-2">{f.text}</p>
                ) : (
                  <div key={f.key} className={f.span === "full" ? "sm:col-span-2" : ""}>
                    <PageFieldControl field={f} value={values[f.key]} problems={problems[f.key]} onChange={(v) => onChange(f.key, v)} />
                  </div>
                )
              )}
              {buttons.length > 0 && record !== undefined && (
                <div className="flex flex-wrap gap-2 sm:col-span-2">
                  {buttons.map((b) => <PageButton key={b.key} field={b} record={record} />)}
                </div>
              )}
            </div>
          </fieldset>
        );
      })}
      {general.map((p) => (
        <p key={p} className="text-xs text-red-400">
          {p}
        </p>
      ))}
    </div>
  );
}

/** The "details" field of an intent form: the page the Page Designer set up
 * for this record type and segment. Its value is {field: value}. */
export default function PageDetailsInput({
  entity,
  segment,
  partyId,
  value,
  outer,
  onChange,
}: {
  entity: string;
  segment: string | null;
  partyId?: number | null;
  value: unknown;
  outer: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}) {
  const [form, setForm] = useState<PageForm | null>(null);
  const [error, setError] = useState("");
  const details = (value && typeof value === "object" && !Array.isArray(value) ? value : {}) as Record<string, unknown>;

  useEffect(() => {
    let live = true;
    pageForm(entity, { segment, partyId })
      .then((f) => live && (setForm(f), setError("")))
      .catch(() => live && setError("Could not load the page."));
    return () => {
      live = false;
    };
  }, [entity, segment, partyId]);

  // A page given to the user's role: the server checks the values against that page, and runs its after-save flow.
  const pageKey = form && !form.is_default ? form.page_key : null;
  useEffect(() => {
    if (pageKey && details._page_key !== pageKey) onChange({ ...details, _page_key: pageKey });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pageKey]);

  if (error) return <p className="text-xs text-red-400">{error}</p>;
  if (!form) return <p className="text-xs text-slate-500">Loading the {segment ?? ""} page…</p>;
  // name / email / phone / segment / source / region are asked by the form itself
  const asked = Object.keys(outer).filter((k) => k !== "details");
  const merged = { ...details, ...Object.fromEntries(asked.map((k) => [k, outer[k]])) };
  return (
    <div className="space-y-2">
      <p className="text-[11px] text-slate-500">
        {form.name}
        {form.segment || entity !== "party" ? "" : " - pick the type above to see its own fields"}
      </p>
      <PageFormView
        form={form}
        values={merged}
        skip={asked}
        onChange={(key, v) => {
          const next = { ...details };
          if (v === "" || v === null) delete next[key];
          else next[key] = v;
          onChange(next);
        }}
      />
    </div>
  );
}
