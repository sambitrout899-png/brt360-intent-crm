import { useState } from "react";
import { ruleFromLaunch, type ModelComponent, type ProductPicture } from "../../api/client";
import { useWorkspace } from "../../workspace";
import { MODEL_BADGE, STATUS_BADGE } from "./productBadges";
const fmt = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 2 });

function Node({ c }: { c: ModelComponent }) {
  return (
    <div className="border-l border-slate-700 pl-3">
      <p className="text-xs text-slate-200">
        ▸ {c.name} <span className="text-slate-500">{c.mandatory ? "mandatory" : "optional"}</span>
        {c.charge_type && <span className="text-emerald-400"> · {fmt(c.price)} {c.charge_type === "recurring" ? "/ month" : "one-time"}</span>}
        {c.slot && <span className="text-violet-300"> · filled by {c.slot.type === "class" ? `class ${c.slot.class}` : c.slot.products.map((p) => p.name).join(" / ")}</span>}
      </p>
      {c.attributes.map((a) => (
        <p key={a.id} className="pl-3 text-[11px] text-slate-400">
          • {a.name} {a.affects_price && "💰"} {a.affects_provisioning && "🔧"}
        </p>
      ))}
      {c.children.map((child) => (
        <Node key={child.id} c={child} />
      ))}
    </div>
  );
}

/** Everything about one product in one place, with its intents as actions. */
export default function ProductPictureView({ p, inModeller = false, onChanged }: { p: ProductPicture; inModeller?: boolean; onChanged?: () => void }) {
  const ws = useWorkspace();
  const admin = ws.role === "admin";
  const [note, setNote] = useState("");
  const act = (code: string, entities: Record<string, unknown> = {}) => ws.runIntent(code, { offering_name: p.name, ...entities });
  const btn = "rounded-lg border border-slate-700 px-2.5 py-1 text-xs text-slate-200 hover:border-sky-500 hover:text-sky-300";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded px-2 py-0.5 text-xs font-semibold ${STATUS_BADGE[p.status] ?? ""}`}>{p.status}</span>
        <span className="rounded bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{p.segment}</span>
        <span className="text-sm text-slate-200">{fmt(p.price)} / month list price</span>
        <span className={`rounded px-2 py-0.5 text-xs ${MODEL_BADGE[p.model.status]}`}>{p.model.label}</span>
        <span className="ml-auto font-mono text-[10px] text-slate-500">{p.brt_id}</span>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {ws.canRun("INT-02") ? <button type="button" className={btn} onClick={() => act("INT-02")}>✏️ Change price / segment</button> : null}
        {ws.canRun("INT-43") ? <button type="button" className={btn} onClick={() => act("INT-43")}>💰 Put on a pricelist</button> : null}
        {ws.canRun("INT-42") ? <button type="button" className={btn} onClick={() => act("INT-42")}>🧩 Design attributes (AI)</button> : null}
        {(p.model.status === "workspace" || p.model.status === "released_changed") && admin && (
          (ws.canRun("INT-48") ? <button type="button" className={btn} onClick={() => act("INT-48")}>🏷️ Release the model</button> : null)
        )}
        {p.status !== "retired" && (ws.canRun("INT-03") ? <button type="button" className={btn} onClick={() => act("INT-03")}>📦 Retire</button> : null)}
        {!inModeller && ws.openProductModeller && (
          <button type="button" className="rounded-lg bg-sky-600 px-2.5 py-1 text-xs font-semibold text-white" onClick={() => ws.openProductModeller?.(p.name)}>
            Open in Product Modeller
          </button>
        )}
      </div>
      {note && <p className="text-xs text-emerald-400">{note}</p>}

      <div className="grid gap-3 md:grid-cols-2">
        <section className="rounded-xl border border-slate-800 p-3 text-xs">
          <p className="mb-1.5 font-semibold text-slate-200">Prices</p>
          {p.prices.map((x) => (
            <p key={x.price_list ?? "list"} className="flex justify-between text-slate-300">
              <span>
                {x.price_list ?? "List price"}
                {x.default ? " (default)" : ""}
              </span>
              <span>
                {fmt(x.price)} <span className="text-slate-500">· {x.source}</span>
              </span>
            </p>
          ))}
        </section>
        <section className="rounded-xl border border-slate-800 p-3 text-xs">
          <p className="mb-1.5 font-semibold text-slate-200">Who may buy it</p>
          {p.launch_eligibility.map((e, i) => (
            <p key={i} className="text-slate-300">
              Launched for {e.segment} · channel {e.channel} · region {e.region}
            </p>
          ))}
          {(p.rules ?? []).filter((r) => r.rule_type !== "constraint").map((r) => (
            <p key={r.id} className={r.is_active ? "text-slate-300" : "text-slate-600 line-through"}>
              {r.rule_type === "eligibility" ? "✓" : "⇄"} {r.explanation}
            </p>
          ))}
          {!p.model.rules.eligibility && p.launch_eligibility.length > 0 && (
            <p className="mt-1 text-slate-500">
              The launch segment is not enforced yet.{" "}
              {admin && (
                <button type="button" className="text-sky-400" onClick={() => ruleFromLaunch(p.name).then(() => (setNote("Launch eligibility is now an enforced rule"), onChanged?.()))}>
                  Enforce it
                </button>
              )}
            </p>
          )}
          {!p.launch_eligibility.length && !p.model.rules.eligibility && !p.model.rules.compatibility && <p className="text-slate-500">Anyone - no eligibility or compatibility rules.</p>}
        </section>
      </div>

      <section className="rounded-xl border border-slate-800 p-3 text-xs">
        <p className="mb-1.5 font-semibold text-slate-200">
          Structure · {p.model.attributes} attributes ({p.model.pricing_attributes} 💰 price, {p.model.technical_attributes} 🔧 provisioning) · {p.model.children} child products
          {p.model.slots ? ` (${p.model.slots} slots)` : ""} · {p.model.rules.constraint + p.model.attribute_rules} configuration rules · {p.model.tests} model tests
          {p.model.classes.length ? ` · classes ${p.model.classes.join(", ")}` : ""}
        </p>
        {p.tree ? (
          <div className="space-y-0.5">
            <p className="text-slate-200">◆ {p.tree.name}</p>
            <div className="pl-3">
              {p.tree.attributes.map((a) => (
                <p key={a.id} className="text-[11px] text-slate-400">
                  • {a.name} {a.required ? "*" : ""} {a.affects_price && "💰"} {a.affects_provisioning && "🔧"}
                  <span className="text-slate-600"> {a.min !== null ? `${a.min}-${a.max} ${a.unit ?? ""}` : a.reference_type ?? a.values.slice(0, 4).join(", ")}</span>
                </p>
              ))}
              {p.tree.children.map((c) => (
                <Node key={c.id} c={c} />
              ))}
            </div>
            {(p.rules ?? []).filter((r) => r.rule_type === "constraint").map((r) => (
              <p key={r.id} className="text-slate-400">
                ⚙ {r.explanation}
              </p>
            ))}
          </div>
        ) : null}
        {p.model.status === "not_modelled" && <p className="text-slate-500">Not modelled - it sells at its price with no options. Design its attributes or open it in the Product Modeller.</p>}
      </section>

      <section className="rounded-xl border border-slate-800 p-3 text-xs">
        <p className="mb-1.5 font-semibold text-slate-200">Catalog versions</p>
        {p.versions.map((v) => (
          <p key={v.id} className="flex flex-wrap gap-3 text-slate-300">
            <span className="font-mono text-slate-500">v{v.version}</span>
            <span className={`rounded px-1.5 ${STATUS_BADGE[v.status] ?? ""}`}>{v.status}</span>
            <span>{fmt(v.price)}</span>
            <span className="text-slate-500">{v.segment}</span>
            <span className="truncate text-slate-500">{v.composition}</span>
            {v.replacement_offering_id && <span className="text-sky-400">→ customers move to #{v.replacement_offering_id}</span>}
          </p>
        ))}
        {p.model.released_version && (
          <p className="mt-1 text-slate-500">
            Catalog versions (price, status) and model versions (attributes, rules - now v{p.model.released_version}) are separate: changing the price never changes the
            configuration rules, and the other way round.
          </p>
        )}
      </section>
    </div>
  );
}
