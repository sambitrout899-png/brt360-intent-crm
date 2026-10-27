import { useEffect, useRef, useState } from "react";
import { prepareIntent, rehearseStructuredIntent, submitStructuredIntent } from "../../api/client";
import DocumentPreview from "../DocumentPreview";
import type { IntentContext, IntentResponse, PreparedIntent } from "../../api/types";
import { useWorkspace } from "../../workspace";
import ConflictsPanel from "./ConflictsPanel";
import FieldInput from "./FieldInput";
import ImpactPreview from "./ImpactPreview";
import { isEmpty } from "./intentUi";

/** One intent as a guided form. The copilot (POST /api/intents/prepare)
 * fills what it can - from what the user wrote, the record on screen,
 * defaults, their habits, the only sensible candidate - and re-checks as
 * the user edits, so blockers show before Run, each with the intent that
 * clears it. */
export default function IntentForm({
  code,
  initialEntities,
  text = "",
  context,
  compact = false,
  onDone,
  onSwitch,
  onCancel,
  onUse,
  supplied = [],
}: {
  code: string;
  initialEntities?: Record<string, unknown>;
  text?: string;
  context?: IntentContext;
  compact?: boolean;
  onDone: (response: IntentResponse) => void;
  onSwitch?: (code: string, entities: Record<string, unknown>) => void;
  onCancel?: () => void;
  /** a step of a longer run asked for input: hand the values back instead of running the action -
   * allowed with gaps too (an override - the run checks again and asks once more if it must) */
  onUse?: (entities: Record<string, unknown>, complete: boolean) => void;
  /** fields an earlier step of the run supplies - not asked for here */
  supplied?: string[];
}) {
  const [values, setValues] = useState<Record<string, unknown>>(() => ({ ...(initialEntities ?? {}) }));
  const [prepared, setPrepared] = useState<PreparedIntent | null>(null);
  const [checking, setChecking] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [showOptional, setShowOptional] = useState(false);
  // the rehearsal of exactly these values - dropped as soon as a value changes
  const [preview, setPreview] = useState<{ key: string; response: IntentResponse } | null>(null);
  const [rehearsing, setRehearsing] = useState(false);
  const { openConfigure360 } = useWorkspace();

  // Typing waits for a pause before the form is re-checked; a pick is checked straight away.
  const typing = useRef(false);
  const valuesKey = JSON.stringify(values);
  const contextKey = JSON.stringify(context ?? null);
  useEffect(() => {
    let cancelled = false;
    const handle = setTimeout(
      () => {
        setChecking(true);
        prepareIntent(code, JSON.parse(valuesKey), text, JSON.parse(contextKey) ?? undefined)
          .then((p) => {
            if (cancelled) return;
            setPrepared(p);
            setError("");
            if (code === "INT-13") {
              // Switching customers can invalidate an explicitly selected business ID.
              // Use the server's segment-aware correction for display and submission.
              setValues(current => {
                const next = { ...current };
                let changed = false;
                for (const key of ["id_type", "provider"]) {
                  if (key in current && current[key] !== p.values[key]) {
                    if (p.values[key] == null) delete next[key];
                    else next[key] = p.values[key];
                    changed = true;
                  }
                }
                return changed ? next : current;
              });
            }
          })
          .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "Could not prepare this action"))
          .finally(() => !cancelled && setChecking(false));
      },
      !prepared ? 0 : typing.current ? 800 : 200
    );
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
    // prepared only decides the debounce, it must not retrigger
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code, valuesKey, text, contextKey]);

  const TYPED = new Set(["text", "textarea", "number", "money", "percent", "list", "design", "updates", "page"]);
  const set = (key: string, value: unknown) => {
    typing.current = TYPED.has(prepared?.fields.find((f) => f.key === key)?.type ?? "text");
    setValues((v) => ({ ...v, [key]: value }));
  };

  // What the user typed shows at once; the server's check catches up in the background.
  const silentOrder = code === "INT-106" && (values.order_mode ?? prepared?.values.order_mode) === "silent";
  const shownNow = (f: { show_when?: { field?: string; in?: string[] } }) => {
    const rule = f.show_when;
    if (!rule?.field || !rule.in?.length) return true;
    const current = values[rule.field] ?? prepared?.values[rule.field];
    return rule.in.includes(String(current ?? ""));
  };
  // Fields that do not apply to what has been chosen (e.g. B2C-only fields on a B2B prospect).
  const hiddenKeys = new Set((prepared?.fields ?? []).filter((f) => !shownNow(f)).map((f) => f.key));
  const fields = (prepared?.fields ?? []).filter((f) => !hiddenKeys.has(f.key) && (code !== "INT-106" || (
    f.key !== "submit_for_provisioning" && !(silentOrder && ["pricing_attributes", "technical_attributes", "requested_discount_pct"].includes(f.key))
  ))).map((f) => {
    const field = f.key in values ? { ...f, value: values[f.key], origin: "you" as const } : f;
    return code === "INT-46" && f.key === "products" ? { ...field, label: "Product of Interest" } : field;
  });
  const liveValues = { ...(prepared?.values ?? {}), ...values };
  // Lead product selection belongs in the main form even though it is optional.
  // ...and so do the product carried from the lead, what is shown read-only and the attributes being configured.
  const isPrimary = (f: (typeof fields)[number]) =>
    f.required || code === "INT-106" || (code === "INT-46" && f.key === "products") || !!f.locked || !!f.readonly ||
    (["INT-37", "INT-38", "INT-18"].includes(code) && ["pricing_attributes", "technical_attributes"].includes(f.key));
  const primary = fields.filter(isPrimary);
  const optional = fields.filter((f) => !isPrimary(f));
  const optionalFilled = optional.filter((f) => !isEmpty(f.value)).length;
  const aiFilled = fields.filter((f) => f.origin && f.origin !== "you" && !isEmpty(f.value));
  const missingLabels = (prepared?.missing ?? []).map((m) =>
    m
      .split(" or ")
      .map((k) => fields.find((f) => f.key === k)?.label ?? k.replace(/_/g, " "))
      .join(" or ")
  );

  function entitiesToSend(p: PreparedIntent): Record<string, unknown> {
    const entities: Record<string, unknown> = {};
    // read-only fields are shown, never sent (e.g. the pricing attributes Presales sees)
    const shownOnly = new Set(p.fields.filter((f) => f.readonly).map((f) => f.key));
    for (const [k, v] of Object.entries({ ...p.values, ...values })) {
      if (!isEmpty(v) && !hiddenKeys.has(k) && !shownOnly.has(k)) entities[k] = v;
    }
    if (code === "INT-106") entities.submit_for_provisioning = (entities.order_mode || "interactive") === "interactive";
    return entities;
  }

  // what is still missing, field by field ("a or b" - the first of them)
  const missingKeys = new Set((prepared?.missing ?? []).map((m) => m.split(" or ")[0]).filter((k) => !supplied.includes(k)));
  // in a run: complete when all that's left is supplied by an earlier step
  const readyForRun = !!prepared && (prepared.ready || (missingKeys.size === 0 && prepared.blockers.length === 0));
  /** Each gap gets its default, else the only sensible option, else the suggested record. */
  const fillable = fields.filter((f) => missingKeys.has(f.key)).map((f) => {
    const option = f.options.find((o) => !o.via)?.value;
    const record = f.suggestions.find((s) => s.suggested)?.id ?? f.suggestions[0]?.id;
    const pick = !isEmpty(f.default) ? f.default : option ?? record;
    return { key: f.key, value: pick };
  }).filter((x) => !isEmpty(x.value));
  function fillGaps() {
    typing.current = false;
    setValues((v) => ({ ...v, ...Object.fromEntries(fillable.map((x) => [x.key, x.value])) }));
  }

  async function rehearse() {
    if (!prepared) return;
    setRehearsing(true);
    setError("");
    try {
      const response = await rehearseStructuredIntent(prepared.summary || prepared.label, code, entitiesToSend(prepared));
      setPreview({ key: valuesKey, response });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not rehearse this action");
    } finally {
      setRehearsing(false);
    }
  }

  async function run() {
    if (!prepared) return;
    const entities = entitiesToSend(prepared);
    setRunning(true);
    setError("");
    try {
      onDone(await submitStructuredIntent(prepared.summary || prepared.label, code, entities));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setRunning(false);
    }
  }

  if (!prepared) {
    return (
      <div className="rounded-2xl border border-slate-700 bg-slate-900/70 p-5">
        {error ? <p className="text-sm text-red-400">{error}</p> : <p className="animate-pulse text-sm text-slate-400">✨ Preparing the form and filling in what I can…</p>}
      </div>
    );
  }

  const grid = compact ? "space-y-4" : "grid gap-x-5 gap-y-4 md:grid-cols-2";
  const wide = (type: string) => (!compact && (type === "textarea" || type === "updates" || type === "transition" || type === "list" || type === "attributes" || type === "design" || type === "page" || type === "products") ? "md:col-span-2" : "");

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (onUse) onUse(entitiesToSend(prepared), readyForRun);
        else if (prepared.ready && !running) run();
      }}
      className="space-y-5 rounded-2xl border border-slate-700 bg-gradient-to-b from-slate-900 to-slate-950 p-5 shadow-xl"
    >
      <div className="flex items-start gap-3">
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-slate-800 text-2xl">{prepared.icon}</span>
        <div className="min-w-0 flex-1">
          <h3 className="text-lg font-bold text-slate-100">{prepared.label}</h3>
          <p className="text-sm text-slate-400">{prepared.description}</p>
        </div>
        {checking && <span className="text-xs text-slate-500">checking…</span>}
      </div>

      {aiFilled.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-violet-500/30 bg-violet-500/10 px-3 py-2 text-xs text-violet-200">
          <span className="font-semibold">✨ I filled {aiFilled.length} field{aiFilled.length > 1 ? "s" : ""} for you</span>
          <span className="text-violet-300/70">- {aiFilled.map((f) => f.label).join(", ")}. Change anything that's not right.</span>
        </div>
      )}

      {missingKeys.size > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-red-500/30 bg-red-500/[0.07] px-3 py-2 text-xs text-red-200">
          <span className="font-semibold">
            {missingKeys.size} field{missingKeys.size > 1 ? "s" : ""} still needed - marked in red.
          </span>
          {fillable.length > 0 && (
            <button type="button" onClick={fillGaps} className="ml-auto rounded-md bg-red-500/20 px-2.5 py-1 font-bold text-red-100 hover:bg-red-500/30">
              ✨ Fill {fillable.length === missingKeys.size ? "them" : `${fillable.length}`} with defaults &amp; best guesses
            </button>
          )}
        </div>
      )}

      {code === "INT-106" && <p className="rounded-lg bg-sky-500/10 p-3 text-sm text-sky-200">{silentOrder
        ? "Uses product defaults, default pricing and zero discount. Submit starts provisioning; missing required defaults stop the order."
        : "Configure this order line below. Pricing follows the selected product attributes. The line discount follows existing approval rules. Nothing is ordered until you submit."}</p>}
      <div className={grid}>
        {primary.map((f) => (
          <div key={f.key} className={`${wide(f.type)} ${missingKeys.has(f.key) ? "rounded-xl ring-1 ring-red-500/60 ring-offset-4 ring-offset-slate-900" : ""}`}>
            <FieldInput field={f} intentCode={code} values={liveValues} onChange={(v) => set(f.key, v)} />
          </div>
        ))}
      </div>

      {(code === "INT-109" || code === "INT-22") && Number(liveValues.quote_id) > 0 && (
        // the proposal as it will be written for this quote - generated and attached when the action runs
        <DocumentPreview quoteId={Number(liveValues.quote_id)} kind="proposal" />
      )}

      {optional.length > 0 && (
        <div className="rounded-xl border border-slate-800">
          <button
            type="button"
            onClick={() => setShowOptional((s) => !s)}
            className="flex w-full items-center justify-between px-3 py-2 text-xs font-semibold text-slate-400 hover:text-slate-200"
          >
            <span>
              Optional details ({optional.length}
              {optionalFilled ? `, ${optionalFilled} filled` : ""})
            </span>
            <span>{showOptional ? "▴" : "▾"}</span>
          </button>
          {showOptional && (
            <div className={`border-t border-slate-800 p-3 ${grid}`}>
              {optional.map((f) => (
                <div key={f.key} className={wide(f.type)}>
                  <FieldInput field={f} intentCode={code} values={liveValues} onChange={(v) => set(f.key, v)} />
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {code === "INT-33" && openConfigure360 && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-violet-500/30 bg-gradient-to-r from-violet-600/15 to-sky-600/10 px-3 py-2.5 text-xs text-slate-300">
          <span className="text-lg">🧩</span>
          <span className="min-w-0 flex-1">Rather see it while you change it? The studio shows a real customer's page live, with point-and-click editing and system connections.</span>
          <button
            type="button"
            onClick={() => openConfigure360({ instruction: typeof values.instruction === "string" ? values.instruction : undefined })}
            className="rounded-lg bg-violet-600 px-3 py-1.5 font-bold text-white hover:bg-violet-500"
          >
            Open the studio →
          </button>
        </div>
      )}

      {prepared.tips.map((tip) => (
        <p key={tip} className="rounded-lg border border-sky-500/20 bg-sky-500/5 px-3 py-2 text-xs text-sky-200">
          💡 {tip}
        </p>
      ))}

      {prepared.blockers.map((b) => (
        <div key={b.message} className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
          <span>⚠ {b.message}</span>
          {b.fix && onSwitch && (
            <button
              type="button"
              onClick={() => onSwitch(b.fix!.code, b.fix!.entities)}
              className="ml-auto rounded-md bg-amber-500/20 px-2.5 py-1 text-xs font-bold text-amber-100 hover:bg-amber-500/30"
            >
              Do “{b.fix.label ?? b.fix.code}” first →
            </button>
          )}
        </div>
      ))}

      <div className="flex flex-wrap items-center gap-3 border-t border-slate-800 pt-4">
        <div className="min-w-0 flex-1 text-sm">
          {prepared.ready ? (
            <p className="text-slate-300">
              <span className="font-semibold text-emerald-400">Ready:</span> {prepared.summary}
            </p>
          ) : missingLabels.length > 0 ? (
            <p className="text-slate-400">
              Still needed: <span className="font-semibold text-slate-200">{missingLabels.join(", ")}</span>
            </p>
          ) : (
            <p className="text-slate-400">Resolve the note above to continue.</p>
          )}
        </div>
        <button
          type="button"
          onClick={rehearse}
          disabled={!prepared.ready || running || rehearsing}
          title="Runs this on your live data, shows everything it would do, then takes it all back"
          className="rounded-lg border border-violet-500/50 bg-violet-500/10 px-4 py-2 text-sm font-semibold text-violet-200 hover:bg-violet-500/20 disabled:opacity-40"
        >
          {rehearsing ? "Rehearsing…" : "🔮 Preview impact"}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel} className="rounded-lg border border-slate-700 px-4 py-2 text-sm font-semibold text-slate-300 hover:border-slate-500">
            Cancel
          </button>
        )}
        {onUse ? (
          <button
            type="submit"
            title={readyForRun ? "Carry on with these values" : "Carry on anyway - the run checks again and asks once more if it must"}
            className={`rounded-lg px-5 py-2 text-sm font-bold text-white shadow-lg ${readyForRun ? "bg-emerald-600 shadow-emerald-900/40 hover:bg-emerald-500" : "bg-amber-600 shadow-amber-900/40 hover:bg-amber-500"}`}
          >
            {readyForRun ? "Use these & continue ▶" : "Continue anyway ▶"}
          </button>
        ) : (
          <button
            type="submit"
            disabled={!prepared.ready || running}
            className="rounded-lg bg-sky-600 px-5 py-2 text-sm font-bold text-white shadow-lg shadow-sky-900/40 hover:bg-sky-500 disabled:opacity-40"
          >
            {running ? "Working…" : code === "INT-106" ? "Submit for provisioning" : `Run ${prepared.icon}`}
          </button>
        )}
      </div>
      {preview && preview.key === valuesKey && (
        <ImpactPreview response={preview.response} running={running} onRun={() => prepared.ready && !running && run()} onClose={() => setPreview(null)} />
      )}
      {preview && preview.key === valuesKey && (preview.response.conflicts ?? []).length > 0 && (
        <ConflictsPanel conflicts={preview.response.conflicts ?? []} />
      )}
      {error && <p className="text-sm text-red-400">{error}</p>}
    </form>
  );
}
