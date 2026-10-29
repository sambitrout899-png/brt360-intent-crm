import { useEffect, useMemo, useRef, useState } from "react";
import { configuratorAi, configuratorSession, type CfgContext, type CfgResult, type CfgVariable } from "../../api/client";
import { INPUT_CLS } from "../intent/intentUi";
import InstanceBanner from "./InstanceBanner";

const fmt = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 2 });

function Choice({ v, onPick }: { v: CfgVariable; onPick: (value: string) => void }) {
  const [draft, setDraft] = useState("");
  if (v.numeric) {
    const current = v.value ?? "";
    return (
      <div className="flex flex-wrap items-center gap-2">
        <input
          value={draft || current}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => draft && (onPick(draft), setDraft(""))}
          onKeyDown={(e) => e.key === "Enter" && draft && (onPick(draft), setDraft(""))}
          placeholder={v.range ? `${fmt(v.range.min)} – ${fmt(v.range.max)} ${v.unit ?? ""}` : v.unit ?? ""}
          className={`${INPUT_CLS} w-40`}
        />
        {v.range && (
          <span className="text-[11px] text-slate-500">
            allowed now: {fmt(v.range.min)} – {fmt(v.range.max)} {v.unit}
          </span>
        )}
        {v.values
          .filter((x) => x.value)
          .map((x) => (
            <button
              key={x.value}
              type="button"
              title={x.reason}
              disabled={x.state === "excluded"}
              onClick={() => onPick(x.value)}
              className={`rounded border px-1.5 py-0.5 text-[10px] ${x.state === "selected" || current === x.value ? "border-sky-500 text-sky-300" : x.state === "excluded" ? "border-slate-800 text-slate-600 line-through" : "border-slate-700 text-slate-400"}`}
            >
              {x.value}
            </button>
          ))}
      </div>
    );
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {v.values.map((x) => {
        const selected = (v.value ?? "") === x.value;
        const label = x.value === "" ? "None" : v.kind === "component" ? (x.value === "0" ? "Without" : x.value === "1" && v.values.length <= 2 ? "With" : `× ${x.value}`) : x.value;
        if (x.value === "" && v.required) return null;
        return (
          <button
            key={x.value || "none"}
            type="button"
            title={x.state === "excluded" ? `Not possible: ${x.reason ?? "the product's rules"}` : x.price ? `${fmt(x.price)} / month` : undefined}
            onClick={() => onPick(selected && !v.required ? "" : x.value)}
            className={`rounded-lg border px-2.5 py-1 text-xs font-semibold transition ${
              selected
                ? "border-sky-500 bg-sky-500/20 text-sky-200"
                : x.state === "excluded"
                  ? "cursor-help border-slate-800 bg-slate-950 text-slate-600 line-through"
                  : "border-slate-700 bg-slate-900 text-slate-300 hover:border-slate-500"
            }`}
          >
            {label}
            {x.price ? <span className="ml-1 font-normal text-slate-500">· {fmt(x.price)}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

/** Interactive configuration: every pick is propagated by the
 * engine - impossible options grey out with the reason, forced ones are added
 * for you, clashes come with ranked repairs, the price follows. */
export default function Configurator({
  product,
  picks,
  onChange,
  context = {},
  compact = false,
}: {
  product: string;
  picks: Record<string, unknown>;
  onChange: (picks: Record<string, string>) => void;
  context?: CfgContext;
  compact?: boolean;
}) {
  const [result, setResult] = useState<CfgResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [need, setNeed] = useState("");
  const [aiNote, setAiNote] = useState("");
  const abort = useRef<AbortController | null>(null);
  const [round, setRound] = useState(0); // bumped when the deal takes the catalog's newer definition
  const key = JSON.stringify(picks ?? {});
  const ctxKey = JSON.stringify(context);

  useEffect(() => {
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    const handle = setTimeout(() => {
      setBusy(true);
      configuratorSession(product, JSON.parse(key), JSON.parse(ctxKey), { signal: controller.signal })
        .then((r) => {
          setResult(r);
          setError("");
        })
        .catch((e) => {
          if ((e as Error).name !== "AbortError") setError(String((e as Error).message).replace(/^\d+ [^:]*: /, ""));
        })
        .finally(() => setBusy(false));
    }, 250);
    return () => {
      clearTimeout(handle);
      controller.abort();
    };
  }, [product, key, ctxKey, round]);

  const set = (name: string, value: string) => {
    const next: Record<string, string> = Object.fromEntries(Object.entries(picks ?? {}).map(([k, v]) => [k, String(v)]));
    if (value === "") delete next[name];
    else next[name] = value;
    onChange(next);
  };

  const groups = useMemo(() => {
    const out: { title: string; vars: CfgVariable[] }[] = [{ title: product, vars: [] }];
    for (const v of result?.variables ?? []) {
      const owner = v.kind === "attribute" ? v.component : null;
      const title = owner ?? product;
      let group = out.find((g) => g.title === title);
      if (!group) {
        group = { title, vars: [] };
        out.push(group);
      }
      group.vars.push(v);
    }
    return out.filter((g) => g.vars.length);
  }, [result, product]);

  const autoBy = Object.fromEntries((result?.auto ?? []).map((a) => [a.name, a]));

  const askAi = () =>
    configuratorAi(product, need, picks ?? {}, context)
      .then((r) => {
        onChange(Object.fromEntries(Object.entries(r.instance).map(([k, v]) => [k, String(v)])));
        setAiNote(
          `${r.proposal?.engine === "ai" ? "AI" : "Read from your words"}: ${Object.entries(r.proposal?.picks ?? {}).map(([k, v]) => `${k} ${v}`).join(", ") || "nothing specific"}` +
            (r.dropped?.length ? ` · not possible, left out: ${r.dropped.join(", ")}` : "") +
            (r.proposal?.max_monthly ? ` · within ${fmt(r.proposal.max_monthly)} / month` : "")
        );
        setNeed("");
      })
      .catch((e) => setError(String((e as Error).message)));

  return (
    <div className="space-y-3">
      {!context.workspace && <InstanceBanner product={product} quoteId={context.quote_id} opportunityId={context.opportunity_id} onRefreshed={() => setRound((n) => n + 1)} />}
      <div className="flex gap-2">
        <input
          value={need}
          onChange={(e) => setNeed(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && need.trim() && askAi()}
          placeholder="✨ Describe what the customer needs - e.g. fibre, 2 Mbps, with a router, budget 4,000"
          className={`${INPUT_CLS} flex-1`}
        />
        <button type="button" disabled={!need.trim()} onClick={askAi} className="shrink-0 rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40">
          Configure for me
        </button>
        <button
          type="button"
          onClick={() =>
            configuratorSession(product, picks ?? {}, context, { objective: "cost" }).then((r) => onChange(Object.fromEntries(Object.entries(r.instance).map(([k, v]) => [k, String(v)]))))
          }
          className="shrink-0 rounded-lg border border-slate-600 px-3 py-1.5 text-xs text-slate-200"
          title="Fill in the rest at the lowest monthly price"
        >
          Complete cheapest
        </button>
      </div>
      {aiNote && <p className="text-[11px] text-violet-300">{aiNote}</p>}
      {error && <p className="text-xs text-red-400">{error}</p>}

      {result?.conflict && (
        <div className="space-y-2 rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-xs">
          <p className="text-red-200">⚠ {result.conflict.message}</p>
          {result.conflict.repairs.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {result.conflict.repairs.map((r) => (
                <button
                  key={r.label}
                  type="button"
                  onClick={() => onChange({ ...Object.fromEntries(Object.entries(picks ?? {}).map(([k, v]) => [k, String(v)])), ...r.changes })}
                  className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-2 py-1 text-emerald-200 hover:bg-emerald-500/20"
                >
                  {r.label} <span className="text-emerald-400/70">→ {fmt(r.monthly)} / month</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      {(result?.customer_errors ?? []).map((e) => (
        <p key={e} className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
          {e}
        </p>
      ))}

      <div className={`grid gap-3 ${compact ? "" : "lg:grid-cols-[minmax(0,1fr)_16rem]"}`}>
        <div className="space-y-3">
          {groups.map((g) => (
            <fieldset key={g.title} className="rounded-xl border border-slate-800 bg-slate-950/40 p-3">
              <legend className="px-1 text-[11px] font-bold uppercase tracking-wide text-slate-400">{g.title}</legend>
              <div className="space-y-2.5">
                {g.vars.map((v) => (
                  <div key={v.name}>
                    <div className="mb-1 flex flex-wrap items-center gap-2 text-xs">
                      <span className="font-semibold text-slate-200">
                        {v.kind === "attribute" ? v.name : v.label}
                        {v.required && <span className="text-sky-400"> *</span>}
                      </span>
                      {v.kind !== "attribute" && <span className="rounded bg-violet-500/15 px-1.5 text-[10px] text-violet-300">{v.kind === "slot" ? "choose a product" : "child product"}</span>}
                      {autoBy[v.name] && <span className="rounded bg-amber-500/15 px-1.5 text-[10px] text-amber-300" title={autoBy[v.name].reason}>added for you</span>}
                      {v.help && <span className="text-[10px] text-slate-500">{v.help}</span>}
                    </div>
                    <Choice v={v} onPick={(value) => set(v.name, value)} />
                  </div>
                ))}
              </div>
            </fieldset>
          ))}
          {!result && <p className="text-xs text-slate-500">Loading the configurator…</p>}
        </div>
        {result && (
          <aside className="space-y-2 rounded-xl border border-slate-800 bg-slate-950/60 p-3 text-xs">
            <p className="flex items-center gap-2 text-[11px] uppercase tracking-wide text-slate-500">
              Price {busy && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-sky-400" />}
              <span className="ml-auto normal-case">{result.version ? `model v${result.version}` : "workspace model"}</span>
            </p>
            <p className="text-lg font-bold text-slate-100">
              {fmt(result.price.monthly)} <span className="text-xs font-normal text-slate-400">/ month</span>
            </p>
            {result.price.one_time > 0 && <p className="text-slate-300">+ {fmt(result.price.one_time)} one-time</p>}
            <div className="space-y-0.5 text-slate-400">
              <p>
                Base {fmt(result.price.base)} {result.price.source ? `(${result.price.source})` : ""}
              </p>
              {result.price.lines.map((l) => (
                <p key={l.label}>
                  {l.amount >= 0 ? "+" : "−"} {fmt(Math.abs(l.amount))} {l.label}
                </p>
              ))}
            </div>
            {Object.entries(result.resources).map(([name, pool]) => (
              <div key={name}>
                <p className="text-slate-400">
                  {name}: {pool.used} of {pool.provided} used
                </p>
                <div className="h-1.5 rounded bg-slate-800">
                  <div className={`h-1.5 rounded ${pool.used > pool.provided ? "bg-red-500" : "bg-emerald-500"}`} style={{ width: `${Math.min(100, pool.provided ? (pool.used / pool.provided) * 100 : 100)}%` }} />
                </div>
              </div>
            ))}
            {result.messages.map((m) => (
              <p key={m} className="rounded bg-sky-500/10 px-2 py-1 text-sky-200">
                💡 {m}
              </p>
            ))}
            {result.missing.length > 0 && <p className="text-amber-300">Still needed: {result.missing.join(", ")}</p>}
            <p className={result.valid && !result.missing.length ? "text-emerald-400" : "text-slate-500"}>
              {result.valid ? (result.missing.length ? "Valid so far" : "✓ Valid configuration") : "Not valid yet"}
            </p>
          </aside>
        )}
      </div>
    </div>
  );
}
