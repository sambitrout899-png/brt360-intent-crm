import { useMemo, useState } from "react";
import { request } from "../../api/client";
import { tr } from "./api";
import type { CalendarItem, Catalog, Facilitator, Location } from "./api";
import { toIso, useAction, useLoad } from "./hooks";
import { Badge, Card, Note, btnGhost, btnPrimary, field, label } from "./ui";

/* Calendar (SES-010) - month / week / list, filtered by program, location,
   facilitator and status, in the viewer's time zone with the location's time
   in the tooltip - and the scheduling form (SES-001, SES-003). */

type View = "month" | "week" | "list";

const dayKey = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;

function Item({ s, onOpen }: { s: CalendarItem; onOpen: (id: number) => void }) {
  const start = new Date(s.starts_at);
  return (
    <button
      type="button"
      onClick={() => onOpen(s.id)}
      title={`${s.title}\n${s.program} v${s.version}\nAt the venue: ${s.start_local ? new Date(s.start_local).toLocaleString(undefined, { timeZone: s.timezone }) : ""} (${s.timezone})\n${s.registered}/${s.capacity} registered`}
      className={`block w-full truncate rounded px-1.5 py-0.5 text-left text-[11px] ${s.status === "cancelled" ? "bg-red-500/10 text-red-300 line-through" : "bg-sky-500/15 text-sky-200 hover:bg-sky-500/25"}`}
    >
      {start.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} {s.title}
    </button>
  );
}

function ScheduleForm({ catalog, locations, facilitators, onDone }: { catalog: Catalog; locations: Location[]; facilitators: Facilitator[]; onDone: (id: number) => void }) {
  const departments = useLoad(() => request<{ departments: { id: number; name: string }[] }>("/api/training/workspace").then((w) => w.departments), []);
  const versions = catalog.programs.flatMap((p) => p.versions.filter((v) => v.state === "published").map((v) => ({ id: v.id, label: `${p.name} v${v.version_label}` })));
  const [f, setF] = useState({
    revision_id: "",
    title: "",
    facilitator_id: "",
    co: [] as string[],
    location_id: "",
    virtual_url: "",
    room: "",
    starts_at: "",
    ends_at: "",
    capacity: 30,
    registration_deadline: "",
    waitlist_enabled: true,
    feedback_mode: "",
    department_id: "",
    status: "scheduled",
    override_reason: "",
    public_registration: false,
  });
  const action = useAction();
  return (
    <Card title="Schedule a session">
      <form
        className="space-y-3"
        onSubmit={async (e) => {
          e.preventDefault();
          const out = await action.run(() =>
            tr.schedule({
              revision_id: Number(f.revision_id),
              title: f.title,
              facilitator_id: Number(f.facilitator_id),
              co_facilitator_ids: f.co.map(Number),
              location_id: f.location_id ? Number(f.location_id) : null,
              virtual_url: f.virtual_url,
              room: f.room,
              starts_at: toIso(f.starts_at),
              ends_at: toIso(f.ends_at),
              capacity: f.capacity,
              registration_deadline: f.registration_deadline ? toIso(f.registration_deadline) : null,
              waitlist_enabled: f.waitlist_enabled,
              feedback_mode: f.feedback_mode || null,
              department_id: f.department_id ? Number(f.department_id) : null,
              status: f.status,
              override_reason: f.override_reason,
              settings: { public_registration: f.public_registration },
            }),
          );
          if (out) onDone(out.id);
        }}
      >
        <div className="grid gap-2 md:grid-cols-2">
          <label className={label}>
            Published program version
            <select required className={field} value={f.revision_id} onChange={(e) => setF({ ...f, revision_id: e.target.value, title: f.title || versions.find((v) => String(v.id) === e.target.value)?.label || "" })}>
              <option value="">Choose…</option>
              {versions.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.label}
                </option>
              ))}
            </select>
          </label>
          <label className={label}>
            Title
            <input required className={field} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} />
          </label>
        </div>
        <div className="grid gap-2 md:grid-cols-2">
          <label className={label}>
            Lead facilitator
            <select required className={field} value={f.facilitator_id} onChange={(e) => setF({ ...f, facilitator_id: e.target.value })}>
              <option value="">Choose…</option>
              {facilitators.map((x) => (
                <option key={x.id} value={x.id}>
                  {x.name}
                </option>
              ))}
            </select>
          </label>
          <label className={label}>
            Co-facilitators
            <select multiple className={`${field} h-[38px] focus:h-24`} value={f.co} onChange={(e) => setF({ ...f, co: Array.from(e.target.selectedOptions).map((o) => o.value) })}>
              {facilitators
                .filter((x) => String(x.id) !== f.facilitator_id)
                .map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.name}
                  </option>
                ))}
            </select>
          </label>
        </div>
        <div className="grid gap-2 md:grid-cols-3">
          <label className={label}>
            Location
            <select className={field} value={f.location_id} onChange={(e) => setF({ ...f, location_id: e.target.value })}>
              <option value="">Virtual / none</option>
              {locations
                .filter((l) => l.status === "active")
                .map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name} ({l.timezone})
                  </option>
                ))}
            </select>
          </label>
          <label className={label}>
            Room
            <input className={field} value={f.room} onChange={(e) => setF({ ...f, room: e.target.value })} />
          </label>
          <label className={label}>
            Virtual meeting link (https://)
            <input className={field} value={f.virtual_url} onChange={(e) => setF({ ...f, virtual_url: e.target.value })} />
          </label>
        </div>
        <div className="grid gap-2 md:grid-cols-3">
          <label className={label}>
            Starts (your time)
            <input required type="datetime-local" className={field} value={f.starts_at} onChange={(e) => setF({ ...f, starts_at: e.target.value })} />
          </label>
          <label className={label}>
            Ends
            <input required type="datetime-local" className={field} value={f.ends_at} onChange={(e) => setF({ ...f, ends_at: e.target.value })} />
          </label>
          <label className={label}>
            Registration deadline
            <input type="datetime-local" className={field} value={f.registration_deadline} onChange={(e) => setF({ ...f, registration_deadline: e.target.value })} />
          </label>
        </div>
        <div className="grid gap-2 md:grid-cols-4">
          <label className={label}>
            Capacity
            <input type="number" min={1} className={field} value={f.capacity} onChange={(e) => setF({ ...f, capacity: Number(e.target.value) })} />
          </label>
          <label className={label}>
            Feedback
            <select className={field} value={f.feedback_mode} onChange={(e) => setF({ ...f, feedback_mode: e.target.value })}>
              <option value="">Org default</option>
              <option value="identifiable">Identifiable</option>
              <option value="anonymous">Anonymous</option>
            </select>
          </label>
          <label className={label}>
            Department
            <select className={field} value={f.department_id} onChange={(e) => setF({ ...f, department_id: e.target.value })}>
              <option value="">Whole organization</option>
              {(departments.data ?? []).map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
          <label className={label}>
            Status
            <select className={field} value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>
              <option value="scheduled">Scheduled</option>
              <option value="draft">Draft</option>
            </select>
          </label>
        </div>
        <div className="flex flex-wrap gap-4 text-sm text-slate-300">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={f.waitlist_enabled} onChange={(e) => setF({ ...f, waitlist_enabled: e.target.checked })} /> Waitlist when full
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={f.public_registration} onChange={(e) => setF({ ...f, public_registration: e.target.checked })} /> Public self-registration link
          </label>
        </div>
        {action.error.includes("BR-FAC-01") && (
          <label className={label}>
            Override reason (Org Admin only - recorded in the audit log)
            <input className={field} value={f.override_reason} onChange={(e) => setF({ ...f, override_reason: e.target.value })} />
          </label>
        )}
        <Note>{action.error}</Note>
        <button className={btnPrimary} disabled={action.busy}>
          Schedule
        </button>
      </form>
    </Card>
  );
}

export default function CalendarSection({ catalog, canSchedule, onOpen }: { catalog: Catalog; canSchedule: boolean; onOpen: (id: number) => void }) {
  const [view, setView] = useState<View>("month");
  const [cursor, setCursor] = useState(() => new Date());
  const [filters, setFilters] = useState({ program_id: "", location_id: "", facilitator_id: "", status: "" });
  const [scheduling, setScheduling] = useState(false);
  const [today] = useState(() => dayKey(new Date()));
  const locations = useLoad(() => tr.locations().catch(() => [] as Location[]), []);
  const facilitators = useLoad(() => tr.facilitators().catch(() => [] as Facilitator[]), []);
  const items = useLoad(() => tr.calendar(filters), [JSON.stringify(filters)]);
  const days = useMemo(() => {
    if (view === "week") {
      const start = new Date(cursor);
      start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
      return Array.from({ length: 7 }, (_, i) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + i));
    }
    const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
    const offset = (first.getDay() + 6) % 7;
    return Array.from({ length: 42 }, (_, i) => new Date(cursor.getFullYear(), cursor.getMonth(), 1 - offset + i));
  }, [cursor, view]);
  const byDay = useMemo(() => {
    const out: Record<string, CalendarItem[]> = {};
    for (const s of items.data ?? []) (out[dayKey(new Date(s.starts_at))] ??= []).push(s);
    return out;
  }, [items.data]);
  const step = (n: number) => setCursor(view === "week" ? new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate() + 7 * n) : new Date(cursor.getFullYear(), cursor.getMonth() + n, 1));
  return (
    <div className="space-y-4">
      {scheduling && (
        <ScheduleForm
          catalog={catalog}
          locations={locations.data ?? []}
          facilitators={facilitators.data ?? []}
          onDone={(id) => {
            setScheduling(false);
            items.reload();
            onOpen(id);
          }}
        />
      )}
      <Card
        title={
          view === "list" ? "All sessions" : view === "week" ? `Week of ${days[0].toLocaleDateString()}` : cursor.toLocaleDateString(undefined, { month: "long", year: "numeric" })
        }
        actions={
          <>
            {view !== "list" && (
              <>
                <button type="button" className={btnGhost} aria-label="Previous" onClick={() => step(-1)}>
                  ‹
                </button>
                <button type="button" className={btnGhost} onClick={() => setCursor(new Date())}>
                  Today
                </button>
                <button type="button" className={btnGhost} aria-label="Next" onClick={() => step(1)}>
                  ›
                </button>
              </>
            )}
            {(["month", "week", "list"] as View[]).map((v) => (
              <button key={v} type="button" className={view === v ? btnPrimary : btnGhost} onClick={() => setView(v)}>
                {v[0].toUpperCase() + v.slice(1)}
              </button>
            ))}
            {canSchedule && (
              <button type="button" className={btnPrimary} onClick={() => setScheduling(!scheduling)}>
                {scheduling ? "Close form" : "+ Session"}
              </button>
            )}
          </>
        }
      >
        <div className="mb-3 grid gap-2 sm:grid-cols-4">
          <select className={field} aria-label="Program" value={filters.program_id} onChange={(e) => setFilters({ ...filters, program_id: e.target.value })}>
            <option value="">All programs</option>
            {catalog.programs.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <select className={field} aria-label="Location" value={filters.location_id} onChange={(e) => setFilters({ ...filters, location_id: e.target.value })}>
            <option value="">All locations</option>
            {(locations.data ?? []).map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
          <select className={field} aria-label="Facilitator" value={filters.facilitator_id} onChange={(e) => setFilters({ ...filters, facilitator_id: e.target.value })}>
            <option value="">All facilitators</option>
            {(facilitators.data ?? []).map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </select>
          <select className={field} aria-label="Status" value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
            <option value="">Any status</option>
            {["draft", "scheduled", "in_progress", "completed", "closed", "cancelled"].map((s) => (
              <option key={s} value={s}>
                {s.replace("_", " ")}
              </option>
            ))}
          </select>
        </div>
        <Note>{items.error}</Note>
        {view === "list" ? (
          <ul className="divide-y divide-slate-800">
            {(items.data ?? []).map((s) => (
              <li key={s.id} className="flex flex-wrap items-center gap-3 py-2">
                <span className="w-40 text-xs tabular-nums text-slate-400">{new Date(s.starts_at).toLocaleString()}</span>
                <button type="button" className="min-w-0 flex-1 truncate text-left text-sm font-medium text-slate-100 hover:text-sky-300" onClick={() => onOpen(s.id)}>
                  {s.title}
                </button>
                <span className="text-xs text-slate-500">
                  {s.location || (s.virtual ? "Online" : "")} · {s.registered}/{s.capacity}
                </span>
                <Badge value={s.status} />
              </li>
            ))}
            {!items.data?.length && <li className="py-2 text-sm text-slate-500">No sessions match.</li>}
          </ul>
        ) : (
          <div className="overflow-x-auto">
            <div className="grid min-w-[640px] grid-cols-7 gap-px overflow-hidden rounded-lg border border-slate-800 bg-slate-800">
              {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => (
                <div key={d} className="bg-slate-900 px-2 py-1 text-[11px] font-semibold text-slate-500">
                  {d}
                </div>
              ))}
              {days.map((d) => {
                const inMonth = view === "week" || d.getMonth() === cursor.getMonth();
                return (
                  <div key={d.toISOString()} className={`space-y-1 bg-slate-950 p-1 ${view === "week" ? "min-h-48" : "min-h-24"} ${inMonth ? "" : "opacity-40"}`}>
                    <p className={`text-[11px] ${dayKey(d) === today ? "font-bold text-sky-300" : "text-slate-500"}`}>{d.getDate()}</p>
                    {(byDay[dayKey(d)] ?? []).map((s) => (
                      <Item key={s.id} s={s} onOpen={onOpen} />
                    ))}
                  </div>
                );
              })}
            </div>
          </div>
        )}
        <p className="mt-2 text-[11px] text-slate-500">Times are in your time zone; hover a session for the venue's local time.</p>
      </Card>
    </div>
  );
}
