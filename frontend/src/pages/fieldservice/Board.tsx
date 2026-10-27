import { useCallback, useEffect, useRef, useState } from "react";
import { fsApi } from "../../api/client";
import ActivityPanel from "./ActivityPanel";
import type { Board as BoardData, FsActivity, Region } from "./types";
import { BTN, BTN_PRIMARY, BTN_WARN, CARD, Chip, Drawer, Empty, INPUT, Notice, PriorityTag, Section } from "./ui";
import { PRIORITY_COLOR, STATUS, fmtTime, fmtWhen, todayIso, when, useAction } from "./style";

const HOUR_PX = 84;

function shift(day: string, n: number) {
  const d = new Date(`${day}T12:00:00`);
  d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export default function Board({ onRequestIntent }: { onRequestIntent: (t: string) => void }) {
  const [day, setDay] = useState(todayIso());
  const [regionId, setRegionId] = useState<number | "">("");
  const [regions, setRegions] = useState<Region[]>([]);
  const [data, setData] = useState<BoardData | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [pending, setPending] = useState<{ activity: number; technician: number; start: string } | null>(null);
  const [report, setReport] = useState<string | null>(null);
  const dragged = useRef<number | null>(null);

  const load = useCallback(() => {
    const p = new URLSearchParams({ day });
    if (regionId) p.set("region_id", String(regionId));
    fsApi<BoardData>(`/board?${p.toString()}`).then(setData).catch(() => setData(null));
  }, [day, regionId]);
  useEffect(load, [load]);
  useEffect(() => {
    fsApi<Region[]>("/regions").then(setRegions).catch(() => setRegions([]));
  }, []);
  const act = useAction(load);

  const span = data ? (when(data.end)!.getTime() - when(data.start)!.getTime()) : 1;
  const width = data ? (span / 3600000) * HOUR_PX : 0;
  const pos = (iso: string) => ((when(iso)!.getTime() - when(data!.start)!.getTime()) / span) * width;

  const drop = async (e: React.DragEvent<HTMLDivElement>, technician: number) => {
    e.preventDefault();
    const activity = dragged.current ?? Number(e.dataTransfer.getData("text/plain"));
    dragged.current = null;
    if (!activity || !data) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const t = when(data.start)!.getTime() + ratio * span;
    const snapped = new Date(Math.round(t / (15 * 60000)) * 15 * 60000);
    const start = snapped.toISOString().slice(0, 19);
    setPending(null);
    const ok = await act.run(() => fsApi(`/activities/${activity}/book`, "POST", { technician_id: technician, start }), "Booked");
    if (ok === undefined) setPending({ activity, technician, start });
  };

  const now = data && when(data.now)!.getTime() >= when(data.start)!.getTime() && when(data.now)!.getTime() <= when(data.end)!.getTime() ? pos(data.now) : null;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1">
          <button type="button" className={BTN} onClick={() => setDay(shift(day, -1))}>
            ‹
          </button>
          <input type="date" className={`${INPUT} w-auto py-1.5`} value={day} onChange={(e) => e.target.value && setDay(e.target.value)} />
          <button type="button" className={BTN} onClick={() => setDay(shift(day, 1))}>
            ›
          </button>
          <button type="button" className={BTN} onClick={() => setDay(todayIso())}>
            Today
          </button>
        </div>
        <select className={`${INPUT} w-auto py-1.5`} value={regionId} onChange={(e) => setRegionId(e.target.value ? Number(e.target.value) : "")}>
          <option value="">All regions</option>
          {regions.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </select>
        <span className="flex-1" />
        <button
          type="button"
          className={BTN_PRIMARY}
          disabled={act.busy || !data?.unscheduled.length}
          onClick={async () => {
            const out = await act.run(() => fsApi<{ booked: unknown[]; skipped: { subject: string; reason: string }[] }>("/auto-schedule", "POST", { days: 5, region_id: regionId || null }));
            if (out) setReport(`${out.booked.length} booked${out.skipped.length ? ` · ${out.skipped.length} left: ${out.skipped.map((s) => `${s.subject} (${s.reason})`).join("; ")}` : ""}`);
          }}
        >
          ⚡ Auto-schedule
        </button>
        <button type="button" className={BTN} onClick={load}>
          ↻
        </button>
      </div>
      <Notice text={act.error} onClose={() => act.setError(null)} />
      {pending && act.error && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-200">
          <span className="flex-1">Override and book it there anyway?</span>
          <button type="button" className={BTN_WARN} onClick={() => act.run(() => fsApi(`/activities/${pending.activity}/book`, "POST", { technician_id: pending.technician, start: pending.start, force: true }), "Booked").then(() => setPending(null))}>
            Book anyway
          </button>
          <button type="button" className={BTN} onClick={() => setPending(null)}>
            No
          </button>
        </div>
      )}
      <Notice text={report} tone="emerald" onClose={() => setReport(null)} />

      {/* ---------------- the Gantt */}
      {!data ? (
        <Empty>Loading the board…</Empty>
      ) : data.rows.length === 0 ? (
        <Empty>No engineers in this region.</Empty>
      ) : (
        <div className={`${CARD} overflow-hidden`}>
          <div className="thin-scroll overflow-x-auto">
            <div className="flex min-w-max">
              {/* names */}
              <div className="sticky left-0 z-10 w-36 shrink-0 border-r border-slate-800 bg-slate-950 sm:w-48">
                <div className="flex h-9 items-center border-b border-slate-800 px-3 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                  {data.region_name ?? "Engineers"}
                </div>
                {data.rows.map((r) => (
                  <div key={r.technician_id} className="flex h-16 flex-col justify-center border-b border-slate-800/70 px-3">
                    <p className="truncate text-sm font-semibold text-slate-100">{r.name}</p>
                    <div className="mt-1 flex items-center gap-2">
                      <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-800">
                        <span className="block h-full rounded-full bg-sky-500" style={{ width: `${Math.min(100, r.utilization)}%` }} />
                      </span>
                      <span className="text-[10px] text-slate-500">{r.working ? `${r.utilization}%` : "off"}</span>
                    </div>
                  </div>
                ))}
              </div>
              {/* timeline */}
              <div className="relative" style={{ width }}>
                <div className="relative h-9 border-b border-slate-800">
                  {data.hours.map((h) => (
                    <span key={h.at} className="absolute top-0 flex h-full -translate-x-1/2 items-center text-[11px] text-slate-500" style={{ left: pos(h.at) }}>
                      {fmtTime(h.at)}
                    </span>
                  ))}
                </div>
                {data.rows.map((r) => (
                  <div
                    key={r.technician_id}
                    className="relative h-16 border-b border-slate-800/70"
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={(e) => drop(e, r.technician_id)}
                  >
                    {/* off hours */}
                    {!r.working ? (
                      <div className="absolute inset-0 bg-slate-900/60" />
                    ) : (
                      <>
                        <div className="absolute inset-y-0 left-0 bg-slate-900/60" style={{ width: Math.max(0, pos(r.window_start!)) }} />
                        <div className="absolute inset-y-0 right-0 bg-slate-900/60" style={{ left: Math.min(width, pos(r.window_end!)) }} />
                      </>
                    )}
                    {data.hours.map((h) => (
                      <span key={h.at} className="absolute inset-y-0 w-px bg-slate-800/60" style={{ left: pos(h.at) }} />
                    ))}
                    {r.away.map((a, i) => (
                      <div
                        key={i}
                        title={a.reason}
                        className="absolute inset-y-1.5 rounded-md border border-dashed border-slate-600 bg-[repeating-linear-gradient(45deg,transparent,transparent_6px,rgba(148,163,184,0.12)_6px,rgba(148,163,184,0.12)_12px)] px-2 text-[10px] text-slate-400"
                        style={{ left: Math.max(0, pos(a.start)), width: Math.max(8, Math.min(width, pos(a.end)) - Math.max(0, pos(a.start))) }}
                      >
                        {a.reason}
                      </div>
                    ))}
                    {r.jobs.map((j) => {
                      const left = Math.max(0, pos(j.planned_start));
                      const w = Math.max(28, Math.min(width, pos(j.planned_end)) - left);
                      const st = STATUS[j.status] ?? STATUS.scheduled;
                      return (
                        <button
                          key={j.id}
                          type="button"
                          draggable={j.status === "scheduled" && !j.locked}
                          onDragStart={(e) => {
                            dragged.current = j.id;
                            e.dataTransfer.setData("text/plain", String(j.id));
                          }}
                          onClick={() => setOpen(j.id)}
                          title={`${j.subject} · ${fmtTime(j.planned_start)}–${fmtTime(j.planned_end)} · ${st.label}`}
                          className={`absolute inset-y-1.5 overflow-hidden rounded-md border-l-4 px-2 py-1 text-left shadow transition hover:brightness-125 ${j.late ? "ring-1 ring-red-400/70" : ""}`}
                          style={{ left, width: w, background: `${st.dot}26`, borderLeftColor: PRIORITY_COLOR[j.priority] ?? "#38bdf8" }}
                        >
                          <p className="truncate text-[11px] font-semibold text-slate-100">{j.subject}</p>
                          <p className="truncate text-[10px] text-slate-400">
                            {fmtTime(j.planned_start)} · {j.party_name ?? st.label}
                          </p>
                        </button>
                      );
                    })}
                  </div>
                ))}
                {now !== null && <span className="pointer-events-none absolute inset-y-0 w-0.5 bg-red-400/80" style={{ left: now }} />}
              </div>
            </div>
          </div>
          <div className="flex flex-wrap gap-3 border-t border-slate-800 px-3 py-2 text-[10px] text-slate-500">
            {["scheduled", "en_route", "on_site", "complete"].map((s) => (
              <span key={s} className="inline-flex items-center gap-1">
                <span className="h-2 w-2 rounded-sm" style={{ background: STATUS[s].dot }} /> {STATUS[s].label}
              </span>
            ))}
            <span className="inline-flex items-center gap-1">
              <span className="h-2 w-2 rounded-sm ring-1 ring-red-400" /> after committed time
            </span>
            <span className="hidden sm:inline">· drag a job onto an engineer's row to (re)book it</span>
          </div>
        </div>
      )}

      {/* ---------------- unscheduled */}
      <Section title="Unscheduled" count={data?.unscheduled.length ?? 0}>
        {!data?.unscheduled.length ? (
          <Empty>Everything is scheduled.</Empty>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {data.unscheduled.map((a: FsActivity) => (
              <button
                key={a.id}
                type="button"
                draggable
                onDragStart={(e) => {
                  dragged.current = a.id;
                  e.dataTransfer.setData("text/plain", String(a.id));
                }}
                onClick={() => setOpen(a.id)}
                className={`${CARD} flex h-full cursor-grab flex-col p-3.5 text-left transition hover:border-slate-600 active:cursor-grabbing`}
              >
                <div className="flex items-center gap-2">
                  <PriorityTag priority={a.priority} label={a.priority_label} />
                  <span className="flex-1" />
                  {a.due_at && <span className={`text-[11px] ${a.late ? "text-red-300" : "text-slate-400"}`}>due {fmtWhen(a.due_at)}</span>}
                </div>
                <p className="mt-1.5 line-clamp-2 text-sm font-semibold text-slate-100">{a.subject}</p>
                <p className="truncate text-xs text-slate-400">
                  {a.party_name ?? "—"} · {a.duration_minutes} min
                </p>
                <div className="mt-auto flex flex-wrap gap-1 pt-2">
                  {a.technician_name && <Chip tone="sky">{a.technician_name}</Chip>}
                  {a.skills.map((s) => (
                    <Chip key={s} tone="violet">
                      {s}
                    </Chip>
                  ))}
                  {a.close_intent && <Chip tone="amber">order step</Chip>}
                </div>
              </button>
            ))}
          </div>
        )}
      </Section>

      <Drawer open={open !== null} onClose={() => setOpen(null)} title="Activity" wide>
        {open && <ActivityPanel activityId={open} onChanged={load} onRequestIntent={onRequestIntent} />}
      </Drawer>
    </div>
  );
}
