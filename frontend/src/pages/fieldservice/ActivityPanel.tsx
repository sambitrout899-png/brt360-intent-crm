import { useCallback, useEffect, useState } from "react";
import { fsApi } from "../../api/client";
import type { Candidate, FsActivity, Slot } from "./types";
import { BTN, BTN_DANGER, BTN_GO, BTN_PRIMARY, BTN_WARN, Chip, Field, INPUT, Notice, Panel, PriorityTag, StatusPill } from "./ui";
import { CATEGORY_LABEL, fmtTime, fmtWhen, fromLocalInput, money, relative, toLocalInput, useAction } from "./style";

type Props = { activityId: number; onChanged?: () => void; onRequestIntent?: (text: string) => void; fieldMode?: boolean };

/** One activity: who and when (scheduling), then the job itself (the engineer's job card), then what it costs. */
export default function ActivityPanel({ activityId, onChanged, onRequestIntent, fieldMode }: Props) {
  const [a, setA] = useState<FsActivity | null>(null);
  const load = useCallback(() => {
    fsApi<FsActivity>(`/activities/${activityId}`).then(setA).catch(() => setA(null));
  }, [activityId]);
  useEffect(load, [load]);
  const after = useCallback(() => {
    load();
    onChanged?.();
  }, [load, onChanged]);
  const act = useAction(after);

  if (!a) return <p className="text-sm text-slate-500">Loading…</p>;
  const schedulable = a.status === "unscheduled" || a.status === "scheduled";
  const working = a.status === "scheduled" || a.status === "en_route" || a.status === "on_site";
  const fulfillment = !!a.close_intent;

  return (
    <div className="space-y-4">
      <Notice text={act.error} onClose={() => act.setError(null)} />
      <Notice text={act.done} tone="emerald" onClose={() => act.setDone(null)} />

      {/* ------------- the facts */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Fact label="Status" value={<StatusPill status={a.status} />} />
        <Fact label="Priority" value={<PriorityTag priority={a.priority} label={a.priority_label} />} />
        <Fact label="Engineer" value={a.technician_name ?? "—"} />
        <Fact label="Planned" value={a.planned_start ? `${fmtWhen(a.planned_start)}–${fmtTime(a.planned_end)}` : "Not yet"} />
        <Fact label="Customer" value={a.party_name ?? "—"} />
        <Fact label="Asset" value={a.asset_label ?? "—"} />
        <Fact label="Due (committed)" value={a.due_at ? <span className={a.late ? "text-red-300" : ""}>{fmtWhen(a.due_at)}</span> : "—"} />
        <Fact label="Type" value={`${CATEGORY_LABEL[a.category] ?? a.category} · ${a.duration_minutes} min`} />
      </div>
      {(a.skills.length > 0 || a.description) && (
        <div className="flex flex-wrap gap-1.5">
          {a.skills.map((s) => (
            <Chip key={s} tone="violet">
              needs {s}
            </Chip>
          ))}
          {a.billable ? <Chip tone="amber">billable</Chip> : <Chip>not billable</Chip>}
          {a.locked && <Chip tone="sky">assignment locked</Chip>}
        </div>
      )}
      {a.description && <p className="rounded-xl bg-slate-900/70 px-3 py-2 text-sm text-slate-300">{a.description}</p>}

      {fulfillment && a.status !== "complete" && (
        <Panel title="Order fulfillment">
          <p className="text-sm text-slate-400">This work order is a step of an order. Schedule it here; close it through its order step so the order moves on.</p>
          {onRequestIntent && (
            <button type="button" className={`${BTN_PRIMARY} mt-3`} onClick={() => onRequestIntent(a.close_intent!)}>
              Close the work order…
            </button>
          )}
        </Panel>
      )}

      {schedulable && !fieldMode && <Scheduling a={a} act={act} />}
      {working && !fulfillment && <JobCard a={a} act={act} />}
      {a.status === "complete" && <Completed a={a} />}
      {(a.charges?.lines.length ?? 0) > 0 && <ChargesTable a={a} act={act} />}
      <Records a={a} />
    </div>
  );
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0 rounded-xl border border-slate-800 bg-slate-900/40 px-3 py-2">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{label}</p>
      <div className="mt-0.5 truncate text-sm text-slate-200">{value}</div>
    </div>
  );
}

type Act = ReturnType<typeof useAction>;

function Scheduling({ a, act }: { a: FsActivity; act: Act }) {
  const [cands, setCands] = useState<Candidate[]>([]);
  const [slots, setSlots] = useState<Slot[]>([]);
  const [heuristic, setHeuristic] = useState("earliest");
  const [manualTech, setManualTech] = useState<number | "">("");
  const [manualStart, setManualStart] = useState("");
  const [conflict, setConflict] = useState<string | null>(null);

  useEffect(() => {
    fsApi<Candidate[]>(`/activities/${a.id}/candidates`).then(setCands).catch(() => setCands([]));
  }, [a.id, a.status, a.technician_id, a.planned_start]);
  useEffect(() => {
    fsApi<Slot[]>(`/activities/${a.id}/slots?heuristic=${heuristic}&limit=9`).then(setSlots).catch(() => setSlots([]));
  }, [a.id, heuristic, a.planned_start, a.technician_id]);

  const book = async (technician_id: number, start: string, force = false) => {
    setConflict(null);
    const out = await act.run(() => fsApi(`/activities/${a.id}/book`, "POST", { technician_id, start, force }), "Booked");
    if (out === undefined && !force) setConflict(`${technician_id}|${start}`);
  };

  const top = cands.slice(0, 4);
  return (
    <Panel
      title="Assign & schedule"
      action={
        a.status === "scheduled" ? (
          <div className="flex gap-2">
            <button type="button" className={BTN} onClick={() => act.run(() => fsApi(`/activities/${a.id}`, "PATCH", { locked: !a.locked }))}>
              {a.locked ? "Unlock" : "Lock"} assignment
            </button>
            <button type="button" className={BTN_DANGER} disabled={act.busy || a.locked} onClick={() => act.run(() => fsApi(`/activities/${a.id}/unassign`, "POST"), "Back on the unscheduled list")}>
              Unassign
            </button>
          </div>
        ) : undefined
      }
    >
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Best engineers</p>
      <div className="grid gap-2 sm:grid-cols-2">
        {top.map((c) => (
          <div key={c.technician_id} className={`rounded-xl border px-3 py-2 ${c.qualified ? "border-slate-800 bg-slate-900/50" : "border-slate-900 bg-slate-950/40 opacity-60"}`}>
            <div className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-100">{c.name}</span>
              <span className="text-sm font-bold text-sky-300">{c.qualified ? c.score : "—"}</span>
            </div>
            <div className="mt-1.5 flex h-1.5 overflow-hidden rounded-full bg-slate-800">
              {Object.entries(c.breakdown).map(([k, v]) => (
                <span key={k} title={`${k} ${v}`} style={{ width: `${(v / 95) * 100}%`, background: BAR[k] }} />
              ))}
            </div>
            <p className="mt-1 truncate text-[11px] text-slate-500">
              {!c.qualified ? `missing ${c.missing_skills.join(", ")}` : c.next_start ? `free ${fmtWhen(c.next_start)}${c.late ? " · after due" : ""} · ${c.parts_note}` : "no free time in 7 days"}
            </p>
          </div>
        ))}
      </div>
      <div className="mt-1 flex flex-wrap gap-3 text-[10px] text-slate-500">
        {Object.entries(BAR).map(([k, c]) => (
          <span key={k} className="inline-flex items-center gap-1">
            <span className="h-2 w-2 rounded-full" style={{ background: c }} /> {k}
          </span>
        ))}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <p className="flex-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Appointment slots</p>
        <select className={`${INPUT} w-auto py-1 text-xs`} value={heuristic} onChange={(e) => setHeuristic(e.target.value)}>
          <option value="earliest">Earliest first</option>
          <option value="leveling">Spread the load</option>
          <option value="loading">Fill schedules</option>
        </select>
      </div>
      {slots.length === 0 ? (
        <p className="mt-2 text-sm text-slate-500">No qualified engineer has free time in the next 7 days. Book one by hand below.</p>
      ) : (
        <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
          {slots.map((s) => (
            <button
              key={`${s.technician_id}-${s.start}`}
              type="button"
              disabled={act.busy}
              onClick={() => book(s.technician_id, s.start)}
              className={`rounded-xl border px-3 py-2 text-left transition hover:border-sky-500 ${s.within_commitment ? "border-slate-800 bg-slate-900/50" : "border-amber-500/30 bg-amber-500/5"}`}
            >
              <p className="text-sm font-semibold text-slate-100">{fmtWhen(s.start)}</p>
              <p className="truncate text-[11px] text-slate-400">
                {s.name} · until {fmtTime(s.end)}
              </p>
              {!s.within_commitment && <p className="text-[11px] text-amber-300">after the committed time</p>}
            </button>
          ))}
        </div>
      )}

      <div className="mt-4 grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
        <select className={INPUT} value={manualTech} onChange={(e) => setManualTech(e.target.value ? Number(e.target.value) : "")}>
          <option value="">Engineer…</option>
          {cands.map((c) => (
            <option key={c.technician_id} value={c.technician_id}>
              {c.name}
              {c.qualified ? "" : " (missing skills)"}
            </option>
          ))}
        </select>
        <input type="datetime-local" className={INPUT} value={manualStart || toLocalInput(a.planned_start)} onChange={(e) => setManualStart(e.target.value)} />
        <button
          type="button"
          className={BTN_PRIMARY}
          disabled={!manualTech || !(manualStart || a.planned_start) || act.busy}
          onClick={() => book(Number(manualTech), fromLocalInput(manualStart || toLocalInput(a.planned_start))!)}
        >
          Book
        </button>
      </div>
      {conflict && act.error && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-amber-200">
          <span className="flex-1">Book anyway (overtime, double-booking or a skill override)?</span>
          <button type="button" className={BTN_WARN} onClick={() => {
            const [t, s] = conflict.split("|");
            book(Number(t), s, true);
          }}>
            Book anyway
          </button>
        </div>
      )}
    </Panel>
  );
}

const BAR: Record<string, string> = { skills: "#a78bfa", region: "#38bdf8", workload: "#2dd4bf", availability: "#34d399", parts: "#fbbf24" };

function JobCard({ a, act }: { a: FsActivity; act: Act }) {
  const [part, setPart] = useState("");
  const [qty, setQty] = useState(1);
  const [minutes, setMinutes] = useState(30);
  const [timeKind, setTimeKind] = useState("work");
  const [expense, setExpense] = useState({ kind: "travel", amount: "" });
  const [serial, setSerial] = useState("");
  const [finish, setFinish] = useState({ resolution_code: "", notes: "", signature: "" });
  const [reading, setReading] = useState<Record<number, string>>({});
  const post = (path: string, body: unknown, ok?: string) => act.run(() => fsApi(`/activities/${a.id}${path}`, "POST", body), ok);
  const steps = a.steps ?? [];
  const trunkGood = (a.trunk?.levels ?? []).filter((l) => l.status === "good");

  return (
    <div className="space-y-4">
      <Panel title="On the job">
        <div className="grid grid-cols-3 gap-2">
          <button type="button" className={a.status === "scheduled" ? BTN_PRIMARY : BTN} disabled={a.status !== "scheduled" || act.busy} onClick={() => post("/status", { status: "en_route" }, "Travel started")}>
            🚐 En route
          </button>
          <button type="button" className={a.status === "en_route" ? BTN_PRIMARY : BTN} disabled={a.status === "on_site" || act.busy} onClick={() => post("/status", { status: "on_site" }, "Work started")}>
            📍 Arrived
          </button>
          <button type="button" className={BTN_DANGER} disabled={act.busy} onClick={() => post("/status", { status: "cancelled" }, "Cancelled")}>
            Cancel
          </button>
        </div>
        {a.actual_start && <p className="mt-2 text-xs text-slate-500">On site since {fmtWhen(a.actual_start)} ({relative(a.actual_start)})</p>}
      </Panel>

      {steps.length > 0 && (
        <Panel title={`Steps · ${a.steps_done}/${a.steps_total}`}>
          <ul className="space-y-1.5">
            {steps.map((s, i) => (
              <li key={i}>
                <label className="flex cursor-pointer items-start gap-2 rounded-lg px-2 py-1.5 hover:bg-slate-900">
                  <input
                    type="checkbox"
                    className="mt-0.5 h-4 w-4 accent-emerald-500"
                    checked={s.done}
                    onChange={() => act.run(() => fsApi(`/activities/${a.id}/steps`, "PUT", { steps: steps.map((x, j) => (j === i ? { ...x, done: !x.done } : x)) }))}
                  />
                  <span className={`text-sm ${s.done ? "text-slate-500 line-through" : "text-slate-200"}`}>{s.text}</span>
                </label>
              </li>
            ))}
          </ul>
          {a.instructions && <p className="mt-2 rounded-lg bg-slate-900/70 px-3 py-2 text-xs text-slate-400">{a.instructions}</p>}
        </Panel>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Parts" className="h-full">
          {(a.items ?? []).length > 0 && (
            <div className="mb-3 space-y-1">
              {(a.items ?? []).map((it, i) => {
                const have = trunkGood.find((l) => l.product_name.toLowerCase() === it.product.toLowerCase());
                return (
                  <div key={i} className="flex items-center gap-2 text-xs">
                    <span className="min-w-0 flex-1 truncate text-slate-300">
                      {it.quantity} × {it.product} {it.tool && <Chip>tool</Chip>}
                    </span>
                    {!it.tool && <span className={have && have.available >= it.quantity ? "text-emerald-300" : "text-amber-300"}>{have ? `${have.available} in trunk` : "not in trunk"}</span>}
                  </div>
                );
              })}
            </div>
          )}
          <div className="grid grid-cols-[1fr_4.5rem] gap-2">
            <input className={INPUT} list={`trunk-${a.id}`} placeholder="Part used" value={part} onChange={(e) => setPart(e.target.value)} />
            <input className={INPUT} type="number" min={1} value={qty} onChange={(e) => setQty(Number(e.target.value) || 1)} />
            <datalist id={`trunk-${a.id}`}>
              {trunkGood.map((l) => (
                <option key={l.id} value={l.product_name} />
              ))}
            </datalist>
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            <button type="button" className={BTN_GO} disabled={!part || act.busy} onClick={() => post("/parts", { product_name: part, quantity: qty }, `${part} installed`).then(() => setPart(""))}>
              Installed
            </button>
            <button type="button" className={BTN} disabled={!part || act.busy} onClick={() => post("/parts/remove", { product_name: part, quantity: qty, defective: true }, `${part} taken back (defective)`).then(() => setPart(""))}>
              Removed (defective)
            </button>
          </div>
          {a.asset_id && (
            <div className="mt-4 border-t border-slate-800 pt-3">
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Swap the asset</p>
              <div className="flex gap-2">
                <input className={INPUT} placeholder="New serial number" value={serial} onChange={(e) => setSerial(e.target.value)} />
                <button type="button" className={BTN_WARN} disabled={!serial || act.busy} onClick={() => post("/swap", { new_serial: serial }, "Asset swapped - warranty and entitlements moved over").then(() => setSerial(""))}>
                  Swap
                </button>
              </div>
            </div>
          )}
        </Panel>

        <Panel title="Time & expenses" className="h-full">
          <div className="grid grid-cols-[1fr_5rem_auto] gap-2">
            <select className={INPUT} value={timeKind} onChange={(e) => setTimeKind(e.target.value)}>
              <option value="work">Work</option>
              <option value="travel">Travel</option>
            </select>
            <input className={INPUT} type="number" min={5} step={5} value={minutes} onChange={(e) => setMinutes(Number(e.target.value) || 0)} />
            <button type="button" className={BTN} disabled={act.busy || minutes <= 0} onClick={() => post("/time", { kind: timeKind, minutes }, `${minutes} min logged`)}>
              Log min
            </button>
          </div>
          <div className="mt-2 grid grid-cols-[1fr_5rem_auto] gap-2">
            <select className={INPUT} value={expense.kind} onChange={(e) => setExpense({ ...expense, kind: e.target.value })}>
              {["travel", "parking", "toll", "meal", "material", "other"].map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
            <input className={INPUT} type="number" min={0} placeholder="₹" value={expense.amount} onChange={(e) => setExpense({ ...expense, amount: e.target.value })} />
            <button type="button" className={BTN} disabled={act.busy || !Number(expense.amount)} onClick={() => post("/expenses", { kind: expense.kind, amount: Number(expense.amount) }, "Expense added").then(() => setExpense({ ...expense, amount: "" }))}>
              Add
            </button>
          </div>
          {(a.measurements ?? []).length > 0 && (
            <div className="mt-4 border-t border-slate-800 pt-3">
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Readings</p>
              {(a.measurements ?? []).map((m) => (
                <div key={m.id} className="mb-2 flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-xs text-slate-300">
                    {m.name} <span className="text-slate-500">(last {m.last_value ?? "—"} {m.unit})</span>
                  </span>
                  <input className={`${INPUT} w-24 py-1`} type="number" value={reading[m.id] ?? ""} onChange={(e) => setReading({ ...reading, [m.id]: e.target.value })} />
                  <button
                    type="button"
                    className={BTN}
                    disabled={!reading[m.id]}
                    onClick={() => act.run(() => fsApi(`/measurements/${m.id}/readings`, "POST", { value: Number(reading[m.id]), activity_id: a.id }), "Reading saved").then(() => setReading({ ...reading, [m.id]: "" }))}
                  >
                    Save
                  </button>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      <Panel title="Finish the job">
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label="Resolution">
            <input className={INPUT} placeholder="e.g. replaced CPE" value={finish.resolution_code} onChange={(e) => setFinish({ ...finish, resolution_code: e.target.value })} />
          </Field>
          <Field label="Customer signature">
            <input className={INPUT} placeholder="Name of who signed" value={finish.signature} onChange={(e) => setFinish({ ...finish, signature: e.target.value })} />
          </Field>
          <Field label="Notes" className="sm:col-span-2">
            <textarea className={INPUT} rows={2} value={finish.notes} onChange={(e) => setFinish({ ...finish, notes: e.target.value })} />
          </Field>
        </div>
        <button type="button" className={`${BTN_PRIMARY} mt-3 w-full py-2 text-sm`} disabled={act.busy || !a.technician_id} onClick={() => post("/complete", finish, "Done - charges calculated")}>
          ✓ Complete the activity
        </button>
      </Panel>
    </div>
  );
}

function Completed({ a }: { a: FsActivity }) {
  return (
    <Panel title="Completed">
      <p className="text-sm text-slate-300">
        Done {fmtWhen(a.actual_end)} by {a.technician_name ?? "—"}
        {a.resolution_code && <> · {a.resolution_code}</>}
        {a.signature && <> · signed by {a.signature}</>}
      </p>
      {a.notes && <p className="mt-1 text-xs text-slate-400">{a.notes}</p>}
    </Panel>
  );
}

function ChargesTable({ a, act }: { a: FsActivity; act: Act }) {
  const c = a.charges!;
  return (
    <Panel
      title="Charges"
      action={
        <div className="flex gap-2">
          <button type="button" className={BTN} disabled={act.busy} onClick={() => act.run(() => fsApi(`/activities/${a.id}/charges`, "POST"), "Recalculated")}>
            Recalculate
          </button>
          {!a.ticket_id && c.pending > 0 && (
            <button type="button" className={BTN_GO} disabled={act.busy} onClick={() => act.run(() => fsApi(`/activities/${a.id}/invoice`, "POST"), "Invoiced to the billing account")}>
              Invoice
            </button>
          )}
        </div>
      }
    >
      <ChargeLines charges={c} />
    </Panel>
  );
}

export function ChargeLines({ charges }: { charges: { lines: { id: number; description: string; gross: number; discount_pct: number; discount_reason: string; net: number; status: string }[]; gross: number; covered: number; net: number } }) {
  return (
    <div className="space-y-1.5">
      {charges.lines.map((l) => (
        <div key={l.id} className="flex items-start gap-2 text-sm">
          <div className="min-w-0 flex-1">
            <p className="truncate text-slate-200">{l.description}</p>
            {l.discount_pct > 0 && <p className="truncate text-[11px] text-emerald-300">−{l.discount_pct}% {l.discount_reason}</p>}
          </div>
          <div className="text-right">
            {l.discount_pct > 0 && <p className="text-[11px] text-slate-500 line-through">{money(l.gross)}</p>}
            <p className="font-semibold text-slate-100">{money(l.net)}</p>
          </div>
          <span className="w-16 text-right text-[11px] text-slate-500">{l.status}</span>
        </div>
      ))}
      <div className="mt-2 grid grid-cols-3 gap-2 border-t border-slate-800 pt-2 text-center text-xs">
        <div>
          <p className="text-slate-500">Gross</p>
          <p className="font-semibold text-slate-200">{money(charges.gross)}</p>
        </div>
        <div>
          <p className="text-slate-500">Covered</p>
          <p className="font-semibold text-emerald-300">{money(charges.covered)}</p>
        </div>
        <div>
          <p className="text-slate-500">To bill</p>
          <p className="font-semibold text-sky-300">{money(charges.net)}</p>
        </div>
      </div>
    </div>
  );
}

function Records({ a }: { a: FsActivity }) {
  const time = a.time ?? [];
  const parts = a.parts ?? [];
  const exp = a.expenses ?? [];
  if (!time.length && !parts.length && !exp.length) return null;
  return (
    <Panel title="Recorded">
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="min-w-0">
          <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Time</p>
          {time.map((t) => (
            <p key={t.id} className="truncate text-xs text-slate-300">
              {t.kind} · {t.ended_at ? `${Math.round(t.minutes)} min` : `running since ${fmtTime(t.started_at)}`}
            </p>
          ))}
        </div>
        <div className="min-w-0">
          <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Parts</p>
          {parts.map((p) => (
            <p key={p.id} className="truncate text-xs text-slate-300">
              {p.direction === "install" ? "＋" : "－"} {p.quantity} × {p.product_name}
              {p.serial_number && <span className="text-slate-500"> · {p.serial_number}</span>}
            </p>
          ))}
        </div>
        <div className="min-w-0">
          <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Expenses</p>
          {exp.map((e) => (
            <p key={e.id} className="truncate text-xs text-slate-300">
              {e.kind} · {money(e.amount)}
            </p>
          ))}
        </div>
      </div>
    </Panel>
  );
}
