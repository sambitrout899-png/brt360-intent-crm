import { useCallback, useEffect, useState } from "react";
import { auditFeed, auditStory, intentCatalog, type AuditFeed, type AuditItem, type AuditStory } from "../api/client";
import type { EntityType } from "../api/types";
import { humanizeCodes } from "../intentLabels";
import { useIntentRefresh, useWorkspace } from "../workspace";

const STATUS: Record<string, { label: string; dot: string; chip: string; icon: string }> = {
  done: { label: "Done", dot: "bg-emerald-400", chip: "bg-emerald-500/15 text-emerald-300", icon: "✓" },
  review: { label: "Needs review", dot: "bg-amber-400", chip: "bg-amber-500/15 text-amber-300", icon: "!" },
  failed: { label: "Failed", dot: "bg-red-500", chip: "bg-red-500/15 text-red-300", icon: "✕" },
};
const PERIODS: [number, string][] = [[1, "24 hours"], [7, "7 days"], [30, "30 days"], [90, "90 days"], [0, "All time"]];
const OPENABLE = new Set(["party", "lead", "opportunity", "quote", "order"]);
const SELECT = "rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 outline-none focus:border-sky-500";

const time = (s: string) => new Date(s).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const dayLabel = (s: string) => {
  const d = new Date(s);
  const today = new Date();
  const y = new Date();
  y.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === y.toDateString()) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" });
};
const ms = (n: number | null) => (n === null ? "—" : n < 1000 ? `${n} ms` : `${(n / 1000).toFixed(1)} s`);
const initials = (name: string) => name.split(/[\s@.]+/).filter(Boolean).map((w) => w[0]).join("").slice(0, 2).toUpperCase();

function Stat({ label, value, tone, onClick, active }: { label: string; value: string; tone?: string; onClick?: () => void; active?: boolean }) {
  return (
    <button type="button" onClick={onClick} disabled={!onClick}
      className={`rounded-xl border px-4 py-3 text-left transition ${active ? "border-sky-500 bg-sky-500/10" : "border-slate-800 bg-slate-900/60"} ${onClick ? "hover:border-slate-600" : "cursor-default"}`}>
      <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-1 text-xl font-bold ${tone ?? "text-slate-100"}`}>{value}</p>
    </button>
  );
}

function Value({ v }: { v: unknown }) {
  if (v === null || v === undefined || v === "—") return <span className="text-slate-600">—</span>;
  if (typeof v === "boolean") return <span>{v ? "Yes" : "No"}</span>;
  return <span className="break-words">{String(v)}</span>;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h4 className="mb-2 text-[11px] font-bold uppercase tracking-wider text-slate-500">{title}</h4>
      {children}
    </section>
  );
}

function Story({ s, onRecord, onRerun, onFilterRecord }: { s: AuditStory; onRecord: (t: string, id: number) => void; onRerun: () => void; onFilterRecord: (r: string) => void }) {
  const st = STATUS[s.status] ?? STATUS.review;
  const [raw, setRaw] = useState(false);
  return (
    <div className="space-y-5">
      <header className="space-y-2">
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-slate-800 text-lg">{s.icon}</span>
          <div className="min-w-0 flex-1">
            <h3 className="text-lg font-semibold text-slate-100">{s.label}</h3>
            <p className="text-xs text-slate-500">{s.description}</p>
          </div>
          <span className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold ${st.chip}`}>{st.label}</span>
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
          {[
            ["Who", s.user?.name ?? "System"],
            ["When", `${new Date(s.at).toLocaleDateString()} ${time(s.at)}`],
            ["Took", ms(s.duration_ms)],
            ["Channel", s.channel],
          ].map(([k, v]) => (
            <div key={k} className="rounded-lg bg-slate-950/60 px-3 py-2">
              <p className="text-[10px] uppercase tracking-wide text-slate-500">{k}</p>
              <p className="truncate text-slate-200">{v}</p>
            </div>
          ))}
        </div>
        {s.rerun && (
          <button type="button" onClick={onRerun} className="rounded-md bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-500">
            ↻ Open it again to finish
          </button>
        )}
      </header>

      <Section title="What was asked">
        <blockquote className="rounded-lg border-l-2 border-sky-500 bg-slate-950/60 px-3 py-2 text-sm italic text-slate-300">{s.text || "Submitted from a form"}</blockquote>
      </Section>

      <Section title={`How it was understood · ${s.engine} · ${Math.round(s.confidence * 100)}% sure`}>
        <div className="mb-2 h-1.5 overflow-hidden rounded-full bg-slate-800">
          <div className={`h-full ${s.confidence >= 0.75 ? "bg-emerald-400" : s.confidence >= 0.5 ? "bg-amber-400" : "bg-red-400"}`} style={{ width: `${s.confidence * 100}%` }} />
        </div>
        {s.understood.length ? (
          <dl className="divide-y divide-slate-800 rounded-lg border border-slate-800">
            {s.understood.map((u) => (
              <div key={u.key} className="grid grid-cols-[40%_60%] gap-2 px-3 py-1.5 text-xs">
                <dt className="text-slate-500">{u.label}</dt>
                <dd className={`text-slate-200 ${u.complex ? "font-mono text-[11px]" : ""}`}>
                  <Value v={u.value} />
                </dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="text-xs text-slate-500">Nothing extracted.</p>
        )}
      </Section>

      {s.checks.length > 0 && (
        <Section title="What it checked first">
          <ul className="space-y-1">
            {s.checks.map((c) => (
              <li key={c.label} className="flex items-start gap-2 text-xs">
                <span className={c.ok ? "text-emerald-400" : "text-red-400"}>{c.ok ? "✓" : "✕"}</span>
                <span className="text-slate-400">{c.label}:</span>
                <span className={c.ok ? "text-slate-200" : "text-red-300"}>
                  <Value v={c.value} />
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Why it ran this way">
        <p className="rounded-lg bg-slate-950/60 px-3 py-2 text-xs leading-relaxed text-slate-300">{humanizeCodes(s.rationale)}</p>
      </Section>

      <Section title={`What it did · ${s.steps.length} step${s.steps.length === 1 ? "" : "s"}`}>
        {s.steps.length === 0 ? (
          <p className="text-xs text-slate-500">Nothing ran - it stopped before changing anything.</p>
        ) : (
          <ol className="relative space-y-3 border-l border-slate-800 pl-5">
            {s.steps.map((step) => {
              const ok = step.status === "ok";
              const undone = step.status === "compensated";
              return (
                <li key={step.n} className="relative">
                  <span className={`absolute -left-[27px] flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${
                    ok ? "bg-emerald-500 text-white" : undone ? "bg-slate-600 text-white" : "bg-red-500 text-white"
                  }`}>
                    {ok ? "✓" : undone ? "↶" : "✕"}
                  </span>
                  <p className="text-sm font-medium text-slate-200">
                    {step.action}
                    {undone && <span className="ml-2 text-[11px] font-normal text-slate-500">undone</span>}
                  </p>
                  {step.error && <p className="text-xs text-red-300">{step.error}</p>}
                  {step.detail.length > 0 && (
                    <p className="mt-0.5 text-[11px] text-slate-500">
                      {step.detail.slice(0, 5).map((d, i) => (
                        <span key={d.key}>
                          {i > 0 && " · "}
                          {d.label}: <span className="text-slate-400">{d.complex ? "…" : String(d.value)}</span>
                        </span>
                      ))}
                    </p>
                  )}
                </li>
              );
            })}
          </ol>
        )}
      </Section>

      {s.records.length > 0 && (
        <Section title="Records it touched">
          <div className="flex flex-wrap gap-1.5">
            {s.records.map((r) => (
              <span key={`${r.entity_type}${r.id}`} className="inline-flex items-center overflow-hidden rounded-md border border-slate-700 text-xs">
                <button type="button" disabled={!OPENABLE.has(r.entity_type)} onClick={() => onRecord(r.entity_type, r.id)}
                  className="px-2 py-1 text-slate-200 hover:bg-slate-800 disabled:hover:bg-transparent">
                  {r.label}
                  {r.change && <span className="ml-1 text-slate-500">· {r.change}</span>}
                </button>
                <button type="button" onClick={() => onFilterRecord(`${r.entity_type}:${r.id}`)} title="Show everything that touched it"
                  className="border-l border-slate-700 px-1.5 py-1 text-slate-500 hover:bg-slate-800 hover:text-sky-300">
                  ⌕
                </button>
              </span>
            ))}
          </div>
        </Section>
      )}

      {s.events.length > 0 && (
        <Section title="Events it raised">
          <ul className="space-y-1">
            {s.events.map((e, i) => (
              <li key={i} className="flex gap-2 text-xs">
                <span className="text-violet-300">◆</span>
                <span className="text-slate-300">{e.label}</span>
                <span className="truncate text-slate-600">
                  {Object.entries(e.payload)
                    .filter(([, v]) => typeof v !== "object")
                    .slice(0, 3)
                    .map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`)
                    .join(" · ")}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <div className="border-t border-slate-800 pt-3">
        <button type="button" onClick={() => setRaw(!raw)} className="text-xs text-slate-500 hover:text-slate-300">
          {raw ? "▾" : "▸"} Technical detail (raw data)
        </button>
        {raw && <pre className="mt-2 max-h-96 overflow-auto rounded-lg bg-slate-950 p-3 text-[11px] text-slate-400">{JSON.stringify(s.raw, null, 2)}</pre>}
        <p className="mt-2 font-mono text-[10px] text-slate-600">
          #{s.id} · {s.brt_id}
          {s.overridden ? " · changed by a person afterwards" : ""}
        </p>
      </div>
    </div>
  );
}

export default function AuditLogPage() {
  const ws = useWorkspace();
  const [q, setQ] = useState("");
  const [days, setDays] = useState(30);
  const [status, setStatus] = useState("");
  const [code, setCode] = useState("");
  const [userId, setUserId] = useState("");
  const [channel, setChannel] = useState("");
  const [record, setRecord] = useState("");
  const [feed, setFeed] = useState<AuditFeed | null>(null);
  const [items, setItems] = useState<AuditItem[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [story, setStory] = useState<AuditStory | null>(null);
  const [loading, setLoading] = useState(true);

  const filters = { q: q || undefined, days: days || undefined, status: status || undefined, code: code || undefined, user_id: userId ? Number(userId) : undefined, channel: channel || undefined, record: record || undefined };
  const key = JSON.stringify(filters);

  const load = useCallback(() => {
    setLoading(true);
    auditFeed({ ...JSON.parse(key), limit: 60 })
      .then((f) => {
        setFeed(f);
        setItems(f.items);
        setSelected((cur) => (cur && f.items.some((i) => i.id === cur) ? cur : f.items[0]?.id ?? null));
      })
      .finally(() => setLoading(false));
  }, [key]);

  useEffect(() => {
    intentCatalog().catch(() => null);
  }, []);
  useEffect(() => {
    const h = setTimeout(load, q ? 300 : 0);
    return () => clearTimeout(h);
  }, [load, q]);
  useIntentRefresh(load);
  useEffect(() => {
    if (selected !== null) auditStory(selected).then(setStory).catch(() => setStory(null));
  }, [selected]);

  const more = () => auditFeed({ ...filters, limit: 60, offset: items.length }).then((f) => setItems((cur) => [...cur, ...f.items]));
  const clear = () => (setQ(""), setStatus(""), setCode(""), setUserId(""), setChannel(""), setRecord(""));
  const anyFilter = q || status || code || userId || channel || record;
  const openRecord = (t: string, id: number) => OPENABLE.has(t) && ws.openRecord(t as EntityType, id);

  const exportCsv = () => {
    const rows = [["#", "When", "Action", "Status", "Who", "Channel", "Outcome", "Request"], ...items.map((i) => [i.id, i.at, i.label, i.status, i.user?.name ?? "", i.channel, i.outcome, i.text])];
    const csv = rows.map((r) => r.map((c) => `"${String(c ?? "").replace(/"/g, '""')}"`).join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `audit-trail-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // group by day
  const groups: [string, AuditItem[]][] = [];
  for (const i of items) {
    const label = dayLabel(i.at);
    const last = groups[groups.length - 1];
    if (last && last[0] === label) last[1].push(i);
    else groups.push([label, [i]]);
  }
  const s = feed?.stats;

  return (
    <div className="page space-y-5">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex-1">
          <h1 className="text-2xl font-bold text-slate-100">Audit trail</h1>
          <p className="mt-1 text-sm text-slate-400">Everything anyone asked the CRM to do - who, when, what it understood, every step it took and what it changed.</p>
        </div>
        <button type="button" onClick={exportCsv} disabled={!items.length} className="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-300 hover:border-slate-500 disabled:opacity-40">
          ⬇ Export CSV
        </button>
        <button type="button" onClick={load} className="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-300 hover:border-slate-500">
          ↻ Refresh
        </button>
      </div>

      {s && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Stat label="Actions" value={s.total.toLocaleString()} onClick={() => setStatus("")} active={!status} />
          <Stat label="Done" value={s.done.toLocaleString()} tone="text-emerald-300" onClick={() => setStatus("done")} active={status === "done"} />
          <Stat label="Needs review" value={s.review.toLocaleString()} tone={s.review ? "text-amber-300" : undefined} onClick={() => setStatus("review")} active={status === "review"} />
          <Stat label="Failed" value={s.failed.toLocaleString()} tone={s.failed ? "text-red-300" : undefined} onClick={() => setStatus("failed")} active={status === "failed"} />
          <Stat label="Straight through" value={s.automation === null ? "—" : `${s.automation}%`} />
          <Stat label="Average time" value={ms(s.avg_ms)} />
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-800 bg-slate-900/50 p-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search words, a person, a value or #id…"
          className="min-w-[220px] flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-100 outline-none focus:border-sky-500" />
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} className={SELECT}>
          {PERIODS.map(([n, l]) => (
            <option key={n} value={n}>
              {l}
            </option>
          ))}
        </select>
        <select value={code} onChange={(e) => setCode(e.target.value)} className={SELECT}>
          <option value="">All actions</option>
          {feed?.facets.actions.filter((a) => a.code).map((a) => (
            <option key={a.code} value={a.code ?? ""}>
              {a.icon} {a.label} ({a.count})
            </option>
          ))}
        </select>
        <select value={userId} onChange={(e) => setUserId(e.target.value)} className={SELECT}>
          <option value="">Everyone</option>
          {feed?.facets.users.map((u) => (
            <option key={u.id} value={u.id}>
              {u.name} ({u.count})
            </option>
          ))}
        </select>
        {(feed?.facets.channels.length ?? 0) > 1 && (
          <select value={channel} onChange={(e) => setChannel(e.target.value)} className={SELECT}>
            <option value="">All channels</option>
            {feed?.facets.channels.map((c) => (
              <option key={c.channel} value={c.channel}>
                {c.channel} ({c.count})
              </option>
            ))}
          </select>
        )}
        {record && (
          <span className="flex items-center gap-1 rounded-full bg-sky-500/15 px-2.5 py-1 text-xs text-sky-200">
            Touched {record.replace(":", " #")}
            <button type="button" onClick={() => setRecord("")} className="text-sky-300 hover:text-white">
              ✕
            </button>
          </span>
        )}
        {anyFilter && (
          <button type="button" onClick={clear} className="text-xs text-slate-400 hover:text-slate-200">
            Clear filters
          </button>
        )}
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
        {/* ---------------- the trail */}
        <div className="space-y-4">
          {loading && !items.length && <div className="h-64 animate-pulse rounded-xl border border-slate-800 bg-slate-900/50" />}
          {!loading && !items.length && <p className="rounded-xl border border-slate-800 p-6 text-center text-sm text-slate-500">Nothing matches these filters.</p>}
          {groups.map(([day, list]) => (
            <div key={day}>
              <p className="sticky top-0 z-10 mb-1.5 bg-slate-950/90 py-1 text-[11px] font-bold uppercase tracking-wider text-slate-500 backdrop-blur">
                {day} <span className="font-normal text-slate-600">· {list.length}</span>
              </p>
              <div className="space-y-1.5">
                {list.map((i) => {
                  const st = STATUS[i.status] ?? STATUS.review;
                  return (
                    <button key={i.id} type="button" onClick={() => setSelected(i.id)}
                      className={`flex w-full gap-3 rounded-xl border px-3 py-2.5 text-left transition ${selected === i.id ? "border-sky-500/60 bg-sky-500/10" : "border-slate-800 bg-slate-900/50 hover:border-slate-700"}`}>
                      <span className="relative mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-800 text-sm">
                        {i.icon}
                        <span className={`absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-slate-950 ${st.dot}`} />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-2">
                          <span className="truncate text-sm font-semibold text-slate-100">{i.label}</span>
                          {i.status !== "done" && <span className={`rounded px-1.5 text-[10px] font-semibold ${st.chip}`}>{st.label}</span>}
                          <span className="ml-auto shrink-0 text-[11px] text-slate-500">{time(i.at)}</span>
                        </span>
                        <span className="block truncate text-xs text-slate-400">{i.outcome}</span>
                        <span className="mt-1 flex items-center gap-2 text-[11px] text-slate-500">
                          {i.user ? (
                            <span className="flex items-center gap-1">
                              <span className="flex h-4 w-4 items-center justify-center rounded-full bg-slate-700 text-[8px] font-bold text-slate-200">{initials(i.user.name)}</span>
                              {i.user.name}
                            </span>
                          ) : (
                            <span>System</span>
                          )}
                          {i.records.slice(0, 3).map((r) => (
                            <span key={`${r.entity_type}${r.id}`} className="rounded bg-slate-800 px-1.5 text-slate-400">
                              {r.label}
                            </span>
                          ))}
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
          {feed && items.length < feed.total && (
            <button type="button" onClick={more} className="w-full rounded-lg border border-slate-800 py-2 text-xs text-slate-400 hover:border-slate-600">
              Show more ({feed.total - items.length} older)
            </button>
          )}
        </div>

        {/* ---------------- the story */}
        <div className="lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)] lg:self-start lg:overflow-y-auto">
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5">
            {story ? (
              <Story s={story} onRecord={openRecord} onFilterRecord={setRecord} onRerun={() => story.rerun && ws.runIntent(story.rerun.code, story.rerun.entities)} />
            ) : (
              <p className="text-sm text-slate-500">Pick an action to see its full story.</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
