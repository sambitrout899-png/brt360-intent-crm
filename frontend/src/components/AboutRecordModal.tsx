import { useEffect, useMemo, useState } from "react";
import { aboutRecord } from "../api/about";
import type { AboutField, AboutRecord } from "../api/about";
import type { EntityType } from "../api/types";
import { useWorkspace } from "../workspace";

/* About this record - one pop-up with everything about the record the user
   is on, whatever its type: who/what it is, its numbers, every field, the
   records around it (click one to read about that one), what belongs to it
   and its whole history. Read-only; actions open their intent forms. */

type Ref = { entityType: EntityType; entityId: number };
type Tab = "overview" | "fields" | "related" | "details" | "timeline";

const ICON: Record<string, string> = { party: "👤", lead: "🌱", opportunity: "💼", quote: "🧾", order: "📦" };

function show(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}T/.test(value)) return new Date(value.endsWith("Z") || value.includes("+") ? value : `${value}Z`).toLocaleString();
  return String(value);
}

function when(value: string | null | undefined): string {
  return value ? new Date(value.endsWith("Z") || value.includes("+") ? value : `${value}Z`).toLocaleString() : "";
}

function tone(state: string): string {
  const s = state.toLowerCase();
  if (/(active|live|approved|won|account|passed|qualified|feasible|booked|fulfilled|complete|captured|authorized|verified)/.test(s))
    return "bg-emerald-500/15 text-emerald-300 border-emerald-500/30";
  if (/(reject|lost|cancel|fail|declined|terminated|expired)/.test(s)) return "bg-red-500/15 text-red-300 border-red-500/30";
  if (/(pending|waiting|review|draft|hold|feasibility|awaiting|scheduled)/.test(s)) return "bg-amber-500/15 text-amber-300 border-amber-500/30";
  return "bg-sky-500/15 text-sky-300 border-sky-500/30";
}

function Pill({ state }: { state: string }) {
  if (!state) return null;
  return <span className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-semibold capitalize ${tone(state)}`}>{state.replace(/_/g, " ")}</span>;
}

function FieldGrid({ fields, onOpen }: { fields: AboutField[]; onOpen: (r: Ref) => void }) {
  if (!fields.length) return <p className="text-sm text-slate-500">Nothing here.</p>;
  return (
    <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
      {fields.map((f) => (
        <div key={f.key} className="min-w-0 border-b border-slate-800/70 pb-1.5">
          <dt className={`text-[11px] font-medium ${f.custom ? "text-violet-300" : "text-slate-500"}`}>{f.label}</dt>
          <dd className="truncate text-sm text-slate-100" title={show(f.value)}>
            {f.record && typeof f.value === "number" ? (
              <button type="button" className="font-semibold text-sky-300 hover:underline" onClick={() => onOpen({ entityType: f.record as EntityType, entityId: f.value as number })}>
                {ICON[f.record] ?? ""} #{f.value} →
              </button>
            ) : (
              show(f.value)
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export default function AboutRecordModal({ start, onClose }: { start: Ref; onClose: () => void }) {
  const { runIntent, openRecord, canRun } = useWorkspace();
  const [stack, setStack] = useState<Ref[]>([start]);
  const current = stack[stack.length - 1];
  const [data, setData] = useState<AboutRecord | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("overview");
  const [query, setQuery] = useState("");

  useEffect(() => {
    let alive = true;
    aboutRecord(current.entityType, current.entityId)
      .then((d) => {
        if (!alive) return;
        setData(d);
        setError("");
      })
      .catch((e) => alive && setError(e instanceof Error ? e.message.replace(/^\d+ [^:]+:\s*/, "") : "Could not load this record"));
    return () => {
      alive = false;
    };
  }, [current.entityType, current.entityId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const go = (r: Ref) => {
    setData(null);
    setTab("overview");
    setQuery("");
    setStack((s) => [...s, r]);
  };
  const back = () => {
    setData(null);
    setStack((s) => s.slice(0, -1));
  };

  const fields = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (data?.fields ?? []).filter((f) => !q || f.label.toLowerCase().includes(q) || show(f.value).toLowerCase().includes(q));
  }, [data, query]);
  const groups = useMemo(() => {
    const out: Record<string, NonNullable<AboutRecord["related"]>> = {};
    for (const r of data?.related ?? []) (out[r.relation] ??= []).push(r);
    return out;
  }, [data]);

  const h = data?.header;
  const keyFields = (data?.fields ?? []).filter((f) => f.value !== null && f.value !== "" && !/^(brt_id|sys_)/.test(f.key)).slice(0, 10);
  const TABS: { id: Tab; label: string; n?: number }[] = [
    { id: "overview", label: "Overview" },
    { id: "fields", label: "All fields", n: data?.fields.length },
    { id: "related", label: "Related", n: data?.related.length },
    { id: "details", label: "Details", n: data?.sections.length },
    { id: "timeline", label: "Timeline", n: data?.timeline.length },
  ];

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-950/70 p-3 backdrop-blur-sm sm:p-6" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label="About this record"
        className="relative my-auto w-full max-w-5xl overflow-hidden rounded-2xl border border-slate-700 bg-slate-900 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        {/* header */}
        <div className="relative border-b border-slate-800 p-5"
          style={{ background: "radial-gradient(700px 160px at 0% 0%, rgba(56,189,248,0.16), transparent), radial-gradient(500px 160px at 100% 0%, rgba(167,139,250,0.14), transparent)" }}>
          <div className="flex items-start gap-3">
            {stack.length > 1 && (
              <button type="button" onClick={back} className="rounded-lg border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:border-sky-500" title="Back to the previous record">
                ← Back
              </button>
            )}
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-sky-500/15 text-2xl" aria-hidden>
              {ICON[current.entityType] ?? "📄"}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-sky-300">About this {h?.type_label ?? "record"}</p>
              <h2 className="truncate text-xl font-bold text-white">{h?.title ?? `#${current.entityId}`}</h2>
              {h && (
                <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-400">
                  <Pill state={h.state} />
                  <span className="font-mono text-slate-300">Record number: {h.entity_id}</span>
                  {h.brt_id && <span className="font-mono text-slate-300">BRT_ID: {h.brt_id}</span>}
                  {h.customer && current.entityType !== "party" && (
                    <button type="button" className="text-sky-300 hover:underline" onClick={() => go({ entityType: "party", entityId: h.customer!.id })}>
                      👤 {h.customer.name}
                    </button>
                  )}
                  {(h.owner.user || h.owner.position || h.owner.department) && (
                    <span>owner {[h.owner.user, h.owner.position && `[${h.owner.position}]`, h.owner.department].filter(Boolean).join(" ")}</span>
                  )}
                  {h.created_at && <span>created {when(h.created_at)}{h.created_by ? ` by ${h.created_by}` : ""}</span>}
                  {h.updated_at && <span>updated {when(h.updated_at)}{h.updated_by ? ` by ${h.updated_by}` : ""}</span>}
                </div>
              )}
            </div>
            <button type="button" onClick={onClose} aria-label="Close" className="rounded-lg px-2 py-1 text-lg text-slate-400 hover:bg-slate-800 hover:text-white">
              ✕
            </button>
          </div>
          {data && data.summary.length > 0 && (
            <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
              {data.summary.map((s) => (
                <div key={s.label} className="rounded-xl border border-slate-800 bg-slate-950/60 px-3 py-2">
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{s.label}</p>
                  <p className="truncate text-sm font-bold text-slate-100">{s.money && typeof s.value === "number" ? `₹${s.value.toLocaleString()}` : show(s.value)}</p>
                </div>
              ))}
            </div>
          )}
          {data && (
            <div className="mt-3 flex flex-wrap gap-2">
              {/* an approval decision is the approver's by position, whatever their roles (the server already checked) */}
              {data.actions.filter((a) => canRun(a.intent) || a.entities.decision !== undefined).slice(0, 6).map((a) => (
                <button key={`${a.intent}-${a.label}`} type="button" disabled={a.disabled} title={a.reason}
                  onClick={() => {
                    onClose();
                    runIntent(a.intent, a.entities);
                  }}
                  className="rounded-lg border border-sky-500/40 bg-sky-500/10 px-2.5 py-1 text-xs font-semibold text-sky-300 hover:bg-sky-500/20 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-sky-500/10">
                  {a.label}
                </button>
              ))}
              <button type="button"
                onClick={() => {
                  onClose();
                  openRecord(current.entityType, current.entityId);
                }}
                className="rounded-lg border border-slate-700 px-2.5 py-1 text-xs font-semibold text-slate-300 hover:border-sky-500">
                Open record panel →
              </button>
            </div>
          )}
        </div>

        {/* tabs */}
        <nav className="flex gap-1 overflow-x-auto border-b border-slate-800 px-4 pt-2" role="tablist">
          {TABS.map((t) => (
            <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}
              className={`whitespace-nowrap rounded-t-lg px-3 py-2 text-sm font-medium ${tab === t.id ? "border-b-2 border-sky-400 text-sky-300" : "text-slate-400 hover:text-slate-200"}`}>
              {t.label}
              {t.n !== undefined && <span className="ml-1.5 rounded-full bg-slate-800 px-1.5 text-[10px] text-slate-400">{t.n}</span>}
            </button>
          ))}
        </nav>

        <div className="max-h-[60vh] overflow-y-auto p-5">
          {error && <p className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200">{error}</p>}
          {!data && !error && <p className="animate-pulse text-sm text-slate-500">Gathering everything about this record…</p>}
          {data && tab === "overview" && (
            <div className="grid gap-5 lg:grid-cols-[1.3fr_1fr]">
              <div className="space-y-4">
                <section>
                  <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">Key facts</h3>
                  <FieldGrid fields={keyFields} onOpen={go} />
                  {data.fields.length > keyFields.length && (
                    <button type="button" className="mt-2 text-xs font-semibold text-sky-300" onClick={() => setTab("fields")}>
                      All {data.fields.length} fields →
                    </button>
                  )}
                </section>
                {data.sections.slice(0, 2).map((s) => (
                  <Section key={s.key} s={s} onOpen={go} />
                ))}
              </div>
              <div className="space-y-4">
                <section>
                  <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">Around it</h3>
                  {data.related.length ? (
                    <ul className="space-y-1.5">
                      {data.related.slice(0, 8).map((r) => (
                        <li key={`${r.entity_type}-${r.entity_id}`}>
                          <button type="button" onClick={() => go({ entityType: r.entity_type, entityId: r.entity_id })}
                            className="flex w-full items-center gap-2 rounded-lg border border-slate-800 bg-slate-950/50 px-3 py-2 text-left hover:border-sky-500/60">
                            <span aria-hidden>{ICON[r.entity_type]}</span>
                            <span className="min-w-0 flex-1 truncate text-sm text-slate-100">{r.title}</span>
                            <Pill state={r.state} />
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-sm text-slate-500">No related records.</p>
                  )}
                </section>
                <section>
                  <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">Latest</h3>
                  <Timeline items={data.timeline.slice(0, 6)} />
                </section>
              </div>
            </div>
          )}
          {data && tab === "fields" && (
            <div className="space-y-3">
              <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a field or value…" aria-label="Find a field"
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-sky-500 focus:outline-none" />
              <FieldGrid fields={fields} onOpen={go} />
              <p className="text-[11px] text-slate-500"><span className="text-violet-300">Violet</span> = custom and page fields.</p>
            </div>
          )}
          {data && tab === "related" && (
            <div className="space-y-4">
              {Object.keys(groups).length === 0 && <p className="text-sm text-slate-500">No related records.</p>}
              {Object.entries(groups).map(([relation, items]) => (
                <section key={relation}>
                  <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">{relation}s · {items.length}</h3>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {items.map((r) => (
                      <button key={`${r.entity_type}-${r.entity_id}`} type="button" onClick={() => go({ entityType: r.entity_type, entityId: r.entity_id })}
                        className="rounded-xl border border-slate-800 bg-slate-950/50 p-3 text-left hover:border-sky-500/60">
                        <div className="flex items-center gap-2">
                          <span aria-hidden>{ICON[r.entity_type]}</span>
                          <span className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-100">{r.title}</span>
                          <Pill state={r.state} />
                        </div>
                        <p className="mt-1 truncate text-xs text-slate-400">
                          {r.detail}
                          {r.amount !== null && r.amount !== undefined ? ` · ₹${r.amount.toLocaleString()}` : ""}
                          {r.at ? ` · ${new Date(r.at).toLocaleDateString()}` : ""}
                        </p>
                      </button>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          )}
          {data && tab === "details" && (
            <div className="space-y-5">
              {data.sections.length ? data.sections.map((s) => <Section key={s.key} s={s} onOpen={go} />) : <p className="text-sm text-slate-500">Nothing more attached to this record.</p>}
            </div>
          )}
          {data && tab === "timeline" && <Timeline items={data.timeline} />}
        </div>
      </div>
    </div>
  );
}

function Section({ s, onOpen }: { s: AboutRecord["sections"][number]; onOpen: (r: Ref) => void }) {
  return (
    <section>
      <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">
        <span aria-hidden className="mr-1">{s.icon}</span>
        {s.title}
      </h3>
      {s.rows && <FieldGrid fields={s.rows} onOpen={onOpen} />}
      {s.items && (
        <ul className="space-y-1.5">
          {s.items.map((i, n) => (
            <li key={n} className="flex flex-wrap items-center gap-2 rounded-lg bg-slate-950/60 px-3 py-2 text-sm">
              <span className="min-w-0 flex-1 text-slate-100">{i.title}</span>
              {i.amount !== undefined && <span className="tabular-nums text-slate-300">₹{i.amount.toLocaleString()}</span>}
              {i.state && <Pill state={i.state} />}
              {(i.detail || i.at) && (
                <span className="w-full text-xs text-slate-500">
                  {i.detail}
                  {i.detail && i.at ? " · " : ""}
                  {i.at ? when(i.at) : ""}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Timeline({ items }: { items: AboutRecord["timeline"] }) {
  if (!items.length) return <p className="text-sm text-slate-500">Nothing has happened yet.</p>;
  return (
    <ol className="relative space-y-3 border-l border-slate-800 pl-4">
      {items.map((e, i) => (
        <li key={i} className="relative">
          <span className="absolute -left-[25px] flex h-5 w-5 items-center justify-center rounded-full bg-slate-800 text-[11px]" aria-hidden>
            {e.icon}
          </span>
          <p className="text-sm text-slate-100">{e.title}</p>
          <p className="text-[11px] text-slate-500">
            {when(e.at)}
            {e.detail ? ` · ${e.detail}` : ""}
          </p>
        </li>
      ))}
    </ol>
  );
}
