import { useCallback, useEffect, useState } from "react";
import { fsApi } from "../../api/client";
import ActivityPanel from "./ActivityPanel";
import type { Engineer, FsActivity, InvLocation } from "./types";
import { BTN, CARD, Chip, Drawer, Empty, INPUT, PriorityTag, Section, StatusPill } from "./ui";
import { fmtTime, fmtWhen, todayIso } from "./style";

const KEY = "brt360.fs.engineer";

function remembered(): number | "" {
  try {
    const v = window.localStorage.getItem(KEY);
    return v ? Number(v) : "";
  } catch {
    return "";
  }
}

/** The engineer's day on a phone: today's jobs in order, then what is coming, and the trunk. */
export default function MyJobs({ onRequestIntent }: { onRequestIntent: (t: string) => void }) {
  const [engineers, setEngineers] = useState<Engineer[]>([]);
  const [tech, setTech] = useState<number | "">(remembered());
  const [jobs, setJobs] = useState<FsActivity[]>([]);
  const [trunk, setTrunk] = useState<InvLocation | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  useEffect(() => {
    fsApi<Engineer[]>("/engineers").then(setEngineers).catch(() => setEngineers([]));
  }, []);
  const load = useCallback(() => {
    if (!tech) return;
    fsApi<FsActivity[]>(`/activities?status=all&technician_id=${tech}`).then(setJobs).catch(() => setJobs([]));
    const trunkId = engineers.find((e) => e.technician_id === tech)?.trunk_location_id;
    fsApi<InvLocation[]>("/inventory/locations")
      .then((l) => setTrunk(l.find((x) => x.id === trunkId) ?? null))
      .catch(() => setTrunk(null));
  }, [tech, engineers]);
  useEffect(load, [load]);

  const pick = (v: number | "") => {
    setTech(v);
    try {
      if (v) window.localStorage.setItem(KEY, String(v));
    } catch {
      /* per-device convenience only */
    }
  };

  const today = todayIso();
  const localDay = (iso: string | null) => (iso ? new Date(`${iso}Z`).toLocaleDateString("en-CA") : "");
  const todays = jobs.filter((j) => localDay(j.planned_start) === today && j.status !== "cancelled");
  const upcoming = jobs.filter((j) => j.planned_start && localDay(j.planned_start) > today && j.status !== "complete" && j.status !== "cancelled");
  const active = todays.find((j) => j.status === "en_route" || j.status === "on_site");

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <select className={`${INPUT} sm:w-72`} value={tech} onChange={(e) => pick(e.target.value ? Number(e.target.value) : "")}>
          <option value="">Whose day? Pick an engineer…</option>
          {engineers.map((e) => (
            <option key={e.technician_id} value={e.technician_id}>
              {e.name}
            </option>
          ))}
        </select>
        <button type="button" className={BTN} onClick={load}>
          ↻ Refresh
        </button>
      </div>

      {!tech ? (
        <Empty>Pick an engineer to see their jobs.</Empty>
      ) : (
        <>
          {active && (
            <button type="button" onClick={() => setOpen(active.id)} className="w-full rounded-2xl border border-amber-500/40 bg-amber-500/10 p-4 text-left">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-amber-300">Now · {active.status === "en_route" ? "on the way" : "on site"}</p>
              <p className="mt-1 text-lg font-bold text-slate-100">{active.subject}</p>
              <p className="text-sm text-slate-300">
                {active.party_name ?? ""} {active.address && `· ${active.address}`}
              </p>
            </button>
          )}

          <Section title="Today" count={todays.length}>
            {todays.length === 0 ? (
              <Empty>No jobs today.</Empty>
            ) : (
              <ol className="space-y-2">
                {todays.map((j, i) => (
                  <li key={j.id}>
                    <button type="button" onClick={() => setOpen(j.id)} className={`${CARD} flex w-full items-center gap-3 p-3.5 text-left transition hover:border-slate-600`}>
                      <div className="flex w-14 shrink-0 flex-col items-center">
                        <span className="text-base font-bold text-slate-100">{fmtTime(j.planned_start)}</span>
                        <span className="text-[10px] text-slate-500">#{i + 1}</span>
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold text-slate-100">{j.subject}</p>
                        <p className="truncate text-xs text-slate-400">
                          {j.party_name ?? "—"} {j.asset_label && `· ${j.asset_label}`}
                        </p>
                        <div className="mt-1 flex flex-wrap gap-1.5">
                          <PriorityTag priority={j.priority} label={j.priority_label} />
                          {j.steps_total > 0 && <Chip>{`${j.steps_done}/${j.steps_total} steps`}</Chip>}
                        </div>
                      </div>
                      <StatusPill status={j.status} />
                    </button>
                  </li>
                ))}
              </ol>
            )}
          </Section>

          <Section title="Coming up" count={upcoming.length}>
            {upcoming.length === 0 ? (
              <Empty>Nothing booked after today.</Empty>
            ) : (
              <div className="grid gap-2 sm:grid-cols-2">
                {upcoming.slice(0, 10).map((j) => (
                  <button key={j.id} type="button" onClick={() => setOpen(j.id)} className={`${CARD} p-3 text-left hover:border-slate-600`}>
                    <p className="text-xs font-semibold text-sky-300">{fmtWhen(j.planned_start)}</p>
                    <p className="truncate text-sm text-slate-100">{j.subject}</p>
                    <p className="truncate text-xs text-slate-500">{j.party_name ?? "—"}</p>
                  </button>
                ))}
              </div>
            )}
          </Section>

          <Section title={trunk ? trunk.name : "Trunk stock"}>
            {!trunk ? (
              <Empty>No trunk set up for this engineer (Setup → Engineers).</Empty>
            ) : (
              <div className={`${CARD} divide-y divide-slate-800`}>
                {trunk.levels.map((l) => (
                  <div key={l.id} className="flex items-center gap-3 px-4 py-2 text-sm">
                    <span className="min-w-0 flex-1 truncate text-slate-200">
                      {l.product_name} {l.status !== "good" && <Chip tone="red">{l.status}</Chip>}
                    </span>
                    <span className={`font-semibold ${l.below_min ? "text-amber-300" : "text-slate-100"}`}>{l.on_hand}</span>
                    {l.min_qty > 0 && <span className="w-16 text-right text-[11px] text-slate-500">min {l.min_qty}</span>}
                  </div>
                ))}
              </div>
            )}
          </Section>
        </>
      )}

      <Drawer open={open !== null} onClose={() => setOpen(null)} title={jobs.find((j) => j.id === open)?.subject ?? "Job"}>
        {open && <ActivityPanel activityId={open} onChanged={load} onRequestIntent={onRequestIntent} fieldMode />}
      </Drawer>
    </div>
  );
}
