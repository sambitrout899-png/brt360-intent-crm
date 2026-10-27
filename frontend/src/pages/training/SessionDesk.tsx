import { useEffect, useState } from "react";
import { fmt, imageUrl, tr } from "./api";
import type { SessionDetail } from "./api";
import SessionResults from "./SessionResults";
import { toIso, useAction, useLoad } from "./hooks";
import { Badge, Card, Note, Table, btnDanger, btnGhost, btnPrimary, field, label } from "./ui";

/* One session, end to end (section 12.4): overview, facilitators,
   registrations, attendance, assessments, notifications, results. */

type Tab = "overview" | "registrations" | "attendance" | "assessments" | "notifications" | "results" | "facilitators";

const NEXT: Record<string, { to: string; label: string; staffOnly?: boolean }[]> = {
  draft: [{ to: "scheduled", label: "Publish (schedule)", staffOnly: true }],
  scheduled: [{ to: "in_progress", label: "Start session" }],
  in_progress: [{ to: "completed", label: "End session" }],
  completed: [{ to: "closed", label: "Close", staffOnly: true }],
};

function Qr({ id, url }: { id: number; url?: string }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    let alive = true;
    let made = "";
    imageUrl(`/api/training/sessions/${id}/qr.png`)
      .then((u) => {
        made = u;
        if (alive) setSrc(u);
      })
      .catch(() => setSrc(""));
    return () => {
      alive = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [id, url]);
  return src ? <img src={src} alt="Session check-in QR code" className="h-44 w-44 rounded-lg bg-white p-2" /> : <p className="text-xs text-slate-500">QR unavailable - set TRAINING_PUBLIC_ORIGIN on the server.</p>;
}

function Overview({ s, onChanged, isStaff, isManager }: { s: SessionDetail; onChanged: () => void; isStaff: boolean; isManager: boolean }) {
  const action = useAction();
  const [edit, setEdit] = useState(false);
  const [reason, setReason] = useState("");
  const toLocalInput = (iso: string) => {
    const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  };
  const [f, setF] = useState({ starts_at: toLocalInput(s.starts_at), ends_at: toLocalInput(s.ends_at), capacity: s.capacity, room: s.room ?? "", notes: s.notes ?? "" });
  const publicReg = (s.settings as { public_registration?: boolean } | null)?.public_registration;
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_240px]">
      <div className="space-y-3">
        <dl className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-1.5 text-sm">
          <dt className="text-slate-500">Program</dt>
          <dd>
            {s.program} v{s.version}
          </dd>
          <dt className="text-slate-500">When</dt>
          <dd>
            {fmt(s.starts_at)} – {fmt(s.ends_at)}
            {s.timezone && <span className="text-xs text-slate-500"> · venue time {s.start_local ? new Date(s.start_local).toLocaleString(undefined, { timeZone: s.timezone }) : ""} ({s.timezone})</span>}
          </dd>
          <dt className="text-slate-500">Where</dt>
          <dd>
            {s.location || "—"} {s.room ? `· ${s.room}` : ""}{" "}
            {s.virtual_url && (
              <a className="text-sky-300 underline" href={s.virtual_url} target="_blank" rel="noreferrer">
                Join link
              </a>
            )}
          </dd>
          <dt className="text-slate-500">Capacity</dt>
          <dd>
            {s.capacity} {s.waitlist_enabled ? "· waitlist on" : ""} {s.registration_deadline ? `· register by ${fmt(s.registration_deadline)}` : ""}
          </dd>
          <dt className="text-slate-500">Feedback</dt>
          <dd>
            {s.feedback_mode ?? "identifiable"} {s.feedback_locked && <span className="text-xs text-slate-500">(locked - responses received)</span>}
          </dd>
          {s.status_reason && (
            <>
              <dt className="text-slate-500">Reason</dt>
              <dd>{s.status_reason}</dd>
            </>
          )}
          {s.counts && (
            <>
              <dt className="text-slate-500">People</dt>
              <dd className="flex flex-wrap gap-1">
                {Object.entries(s.counts)
                  .filter(([, n]) => n)
                  .map(([k, n]) => (
                    <Badge key={k} value={k}>
                      {k.replace("_", " ")}: {n}
                    </Badge>
                  ))}
              </dd>
            </>
          )}
        </dl>
        {s.modules.length > 0 && (
          <div>
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Curriculum</p>
            <ol className="list-decimal space-y-0.5 pl-5 text-sm text-slate-300">
              {s.modules.map((m, i) => (
                <li key={i}>
                  {m.title}
                  {m.duration_min ? <span className="text-slate-500"> · {m.duration_min} min</span> : null}
                </li>
              ))}
            </ol>
          </div>
        )}
        {s.notes && <p className="whitespace-pre-wrap rounded-lg border border-slate-800 p-2 text-sm text-slate-300">{s.notes}</p>}
        <Note>{action.error}</Note>
        <Note kind="ok">{action.ok}</Note>
        {isStaff && (
          <div className="flex flex-wrap gap-2">
            {(NEXT[s.status] ?? [])
              .filter((n) => !n.staffOnly || isManager)
              .map((n) => (
                <button key={n.to} type="button" className={btnPrimary} disabled={action.busy} onClick={() => action.run(() => tr.transition(s.id, n.to), "Done.").then((o) => o && onChanged())}>
                  {n.label}
                </button>
              ))}
            {!["completed", "closed", "cancelled"].includes(s.status) && (
              <button type="button" className={btnGhost} onClick={() => setEdit(!edit)}>
                {isManager ? "Edit / reschedule" : "Session notes"}
              </button>
            )}
          </div>
        )}
        {edit && (
          <div className="space-y-2 rounded-xl border border-slate-800 p-3">
            {isManager && (
              <div className="grid gap-2 sm:grid-cols-4">
                <label className={label}>
                  Starts
                  <input type="datetime-local" className={field} value={f.starts_at} onChange={(e) => setF({ ...f, starts_at: e.target.value })} />
                </label>
                <label className={label}>
                  Ends
                  <input type="datetime-local" className={field} value={f.ends_at} onChange={(e) => setF({ ...f, ends_at: e.target.value })} />
                </label>
                <label className={label}>
                  Capacity
                  <input type="number" min={1} className={field} value={f.capacity} onChange={(e) => setF({ ...f, capacity: Number(e.target.value) })} />
                </label>
                <label className={label}>
                  Room
                  <input className={field} value={f.room} onChange={(e) => setF({ ...f, room: e.target.value })} />
                </label>
              </div>
            )}
            <label className={label}>
              Notes
              <textarea className={field} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} />
            </label>
            <p className="text-xs text-slate-500">Rescheduling sends an update to everyone registered and moves every pending reminder.</p>
            <button
              type="button"
              className={btnPrimary}
              disabled={action.busy}
              onClick={() =>
                action
                  .run(
                    () =>
                      tr.editSession(
                        s.id,
                        isManager ? { starts_at: toIso(f.starts_at), ends_at: toIso(f.ends_at), capacity: f.capacity, room: f.room, notes: f.notes } : { notes: f.notes },
                      ),
                    "Saved.",
                  )
                  .then((o) => {
                    if (o) {
                      setEdit(false);
                      onChanged();
                    }
                  })
              }
            >
              Save
            </button>
          </div>
        )}
        {isManager && !["completed", "closed", "cancelled"].includes(s.status) && (
          <div className="flex flex-wrap items-end gap-2 border-t border-slate-800 pt-3">
            <label className={`${label} min-w-48 flex-1`}>
              Cancel with reason
              <input className={field} value={reason} onChange={(e) => setReason(e.target.value)} />
            </label>
            <button
              type="button"
              className={btnDanger}
              disabled={!reason.trim() || action.busy}
              onClick={() => action.run(() => tr.transition(s.id, "cancelled", reason), "Cancelled - notices are on their way.").then((o) => o && onChanged())}
            >
              Cancel session
            </button>
          </div>
        )}
      </div>
      {isStaff && s.checkin_code && (
        <div className="space-y-2">
          <Qr id={s.id} url={s.checkin_code} />
          <p className="text-xs text-slate-400">Check-in opens 30 min before the start. The same QR routes participants to their own assessment.</p>
          {s.checkin_url && <input readOnly className={`${field} text-xs`} value={s.checkin_url} aria-label="Check-in link" onFocus={(e) => e.target.select()} />}
          {publicReg && s.checkin_url && (
            <input readOnly className={`${field} text-xs`} value={s.checkin_url.replace("/p/q/", "/p/r/")} aria-label="Public registration link" onFocus={(e) => e.target.select()} />
          )}
          <button type="button" className={btnGhost} onClick={() => tr.regenerateQr(s.id).then(onChanged)}>
            Regenerate QR
          </button>
        </div>
      )}
    </div>
  );
}

function Registrations({ s, onChanged, isManager }: { s: SessionDetail; onChanged: () => void; isManager: boolean }) {
  const [person, setPerson] = useState({ email: "", name: "", phone: "", organization: "" });
  const [csv, setCsv] = useState("");
  const [report, setReport] = useState<string>("");
  const action = useAction();
  const regs = s.registrations ?? [];
  return (
    <div className="space-y-4">
      {isManager && (
        <div className="grid gap-3 lg:grid-cols-2">
          <form
            className="space-y-2 rounded-xl border border-slate-800 p-3"
            onSubmit={async (e) => {
              e.preventDefault();
              const out = await action.run(() => tr.enrol(s.id, person), "Added.");
              if (out) {
                setPerson({ email: "", name: "", phone: "", organization: "" });
                onChanged();
              }
            }}
          >
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Nominate someone</p>
            <input required type="email" className={field} placeholder="Email" value={person.email} onChange={(e) => setPerson({ ...person, email: e.target.value })} />
            <div className="grid gap-2 sm:grid-cols-3">
              <input className={field} placeholder="Name (if new)" value={person.name} onChange={(e) => setPerson({ ...person, name: e.target.value })} />
              <input className={field} placeholder="Phone" value={person.phone} onChange={(e) => setPerson({ ...person, phone: e.target.value })} />
              <input className={field} placeholder="Organization" value={person.organization} onChange={(e) => setPerson({ ...person, organization: e.target.value })} />
            </div>
            <p className="text-[11px] text-slate-500">Existing users and directory entries are matched by email; anyone else becomes an external participant without a login.</p>
            <button className={btnPrimary} disabled={action.busy}>
              Register
            </button>
          </form>
          <div className="space-y-2 rounded-xl border border-slate-800 p-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Bulk register (CSV: email,name,phone,organization)</p>
            <textarea className={`${field} h-24 font-mono text-xs`} value={csv} onChange={(e) => setCsv(e.target.value)} placeholder={"email,name\nasha@example.com,Asha Rao"} />
            <button
              type="button"
              className={btnGhost}
              disabled={!csv.trim() || action.busy}
              onClick={async () => {
                const out = await action.run(() => tr.enrolBulk(s.id, csv));
                if (out) {
                  setReport(`${out.registered} registered; ${out.rows.filter((r) => !r.ok).map((r) => `row ${r.row}: ${r.errors?.join(", ")}`).join(" · ") || "no errors"}`);
                  onChanged();
                }
              }}
            >
              Upload
            </button>
            {report && <Note kind="info">{report}</Note>}
          </div>
        </div>
      )}
      <Note>{action.error}</Note>
      <Table
        head={["Person", "Status", "Source", "Pre", "Post", "Feedback", ""]}
        rows={regs.map((r) => [
          <span key="p">
            {r.person.name}
            <span className="block text-[11px] text-slate-500">
              {r.person.email}
              {r.person.kind === "participant" ? " · external" : ""}
              {r.person.email_invalid ? " · email bounced" : ""}
            </span>
          </span>,
          <span key="s">
            <Badge value={r.status} />
            {r.waitlist_position ? <span className="ml-1 text-xs text-slate-500">#{r.waitlist_position}</span> : null}
          </span>,
          r.source ?? "—",
          ...(["pre", "post", "feedback"] as const).map((ph) => (r.assessments[ph] ? (r.assessments[ph].submitted ? "✓" : r.assessments[ph].started ? "started" : "invited") : "—")),
          isManager ? (
            <span key="a" className="flex flex-wrap gap-1">
              {r.status === "nominated" && (
                <button type="button" className="text-xs text-sky-300" onClick={() => action.run(() => tr.confirm(r.id)).then(onChanged)}>
                  Confirm
                </button>
              )}
              {["pre", "post"].map((ph) =>
                r.assessments[ph] && !r.assessments[ph].submitted ? (
                  <button key={ph} type="button" className="text-xs text-sky-300" onClick={() => action.run(() => tr.resend([r.id], ph, false), `Sent the ${ph} link again.`)}>
                    Resend {ph}
                  </button>
                ) : null,
              )}
              {!["cancelled", "attended", "no_show"].includes(r.status) && (
                <button type="button" className="text-xs text-red-300" onClick={() => action.run(() => tr.cancelRegistration(r.id)).then(onChanged)}>
                  Cancel
                </button>
              )}
            </span>
          ) : (
            ""
          ),
        ])}
        empty="No one is registered yet."
      />
      <Note kind="ok">{action.ok}</Note>
    </div>
  );
}

function Attendance({ s, onChanged }: { s: SessionDetail; onChanged: () => void }) {
  const regs = (s.registrations ?? []).filter((r) => !["cancelled", "waitlisted"].includes(r.status));
  const [marks, setMarks] = useState<Record<number, string>>(() =>
    Object.fromEntries(regs.map((r) => [r.id, r.status === "attended" ? "present" : r.status === "no_show" ? "absent" : ""])),
  );
  const [csv, setCsv] = useState("");
  const action = useAction();
  if (["draft", "cancelled"].includes(s.status)) return <Note kind="info">Attendance is taken once the session is scheduled.</Note>;
  return (
    <div className="space-y-3">
      <ul className="divide-y divide-slate-800">
        {regs.map((r) => (
          <li key={r.id} className="flex flex-wrap items-center gap-2 py-2">
            <span className="min-w-0 flex-1 text-sm">{r.person.name}</span>
            {(["present", "late", "absent"] as const).map((m) => (
              <button
                key={m}
                type="button"
                aria-pressed={marks[r.id] === m}
                onClick={() => setMarks({ ...marks, [r.id]: m })}
                className={`min-h-11 min-w-20 rounded-lg border px-3 text-sm ${marks[r.id] === m ? (m === "absent" ? "border-red-400 bg-red-500/20" : "border-emerald-400 bg-emerald-500/20") : "border-slate-700"}`}
              >
                {m}
              </button>
            ))}
          </li>
        ))}
        {!regs.length && <li className="py-2 text-sm text-slate-500">No registered participants.</li>}
      </ul>
      <Note>{action.error}</Note>
      <Note kind="ok">{action.ok}</Note>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className={btnPrimary}
          disabled={action.busy}
          onClick={() =>
            action
              .run(
                () =>
                  tr.mark(
                    s.id,
                    Object.entries(marks)
                      .filter(([, v]) => v)
                      .map(([id, status]) => ({ registration_id: Number(id), status })),
                  ),
                "Attendance saved.",
              )
              .then((o) => o && onChanged())
          }
        >
          Save roster
        </button>
      </div>
      <div className="space-y-2 rounded-xl border border-slate-800 p-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Bulk upload (CSV: email,status)</p>
        <textarea className={`${field} h-20 font-mono text-xs`} value={csv} onChange={(e) => setCsv(e.target.value)} />
        <button type="button" className={btnGhost} disabled={!csv.trim()} onClick={() => action.run(() => tr.markUpload(s.id, csv), "Uploaded.").then((o) => o && onChanged())}>
          Upload attendance
        </button>
      </div>
    </div>
  );
}

function Notifications({ id }: { id: number }) {
  const { data, error } = useLoad(() => tr.notifyLog(id), [id]);
  return (
    <>
      <Note>{error}</Note>
      <Table
        head={["Message", "To", "Planned", "Sent", "Status", "Note"]}
        rows={(data ?? []).map((n) => [n.event.replace(/_/g, " "), n.recipient, fmt(n.scheduled_at), fmt(n.sent_at), <Badge key="b" value={n.status} />, n.reason ?? ""])}
        empty="No messages planned."
      />
    </>
  );
}

function QaForm({ s }: { s: SessionDetail }) {
  const rubric = useLoad(() => tr.qaReviews().then((r) => r.rubric), []);
  const [fid, setFid] = useState(s.facilitators[0]?.id ?? 0);
  const [scores, setScores] = useState<Record<string, number>>({});
  const [comments, setComments] = useState("");
  const [actions, setActions] = useState("");
  const action = useAction();
  return (
    <div className="space-y-2 rounded-xl border border-slate-800 p-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Quality review</p>
      <select className={field} value={fid} onChange={(e) => setFid(Number(e.target.value))} aria-label="Facilitator">
        {s.facilitators.map((f) => (
          <option key={f.id} value={f.id}>
            {f.name}
          </option>
        ))}
      </select>
      {(rubric.data ?? []).map((c) => (
        <div key={c} className="flex items-center gap-2 text-sm">
          <span className="w-40 text-slate-300">{c}</span>
          {[1, 2, 3, 4, 5].map((n) => (
            <button key={n} type="button" aria-pressed={scores[c] === n} onClick={() => setScores({ ...scores, [c]: n })}
              className={`h-8 w-8 rounded border ${scores[c] === n ? "border-sky-400 bg-sky-600" : "border-slate-700"}`}>
              {n}
            </button>
          ))}
        </div>
      ))}
      <textarea className={field} placeholder="Comments" value={comments} onChange={(e) => setComments(e.target.value)} />
      <textarea className={field} placeholder="Action items" value={actions} onChange={(e) => setActions(e.target.value)} />
      <Note>{action.error}</Note>
      <Note kind="ok">{action.ok}</Note>
      <button type="button" className={btnPrimary} disabled={!Object.keys(scores).length || action.busy}
        onClick={() => action.run(() => tr.addQaReview({ session_id: s.id, facilitator_id: fid, scores, comments, actions }), "Review saved.")}>
        Save review
      </button>
    </div>
  );
}

export default function SessionDesk({ sessionId, roles, onBack }: { sessionId: number; roles: string[]; onBack: () => void }) {
  const { data: s, error, reload } = useLoad(() => tr.session(sessionId), [sessionId]);
  const [tab, setTab] = useState<Tab>("overview");
  const action = useAction();
  if (error) return <Note>{error}</Note>;
  if (!s) return <p className="animate-pulse text-sm text-slate-500">Loading session…</p>;
  const isManager = roles.includes("admin") || roles.includes("hr");
  const isStaff = s.staff;
  const tabs: Tab[] = isStaff
    ? ["overview", "registrations", "attendance", "assessments", "notifications", "results", "facilitators"]
    : roles.includes("executive")
      ? ["overview", "results"]
      : ["overview"];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={btnGhost} onClick={onBack}>
          ← Calendar
        </button>
        <h2 className="min-w-0 flex-1 truncate text-lg font-bold">{s.title}</h2>
        <Badge value={s.status} />
      </div>
      <nav className="flex flex-wrap gap-1" aria-label="Session sections">
        {tabs.map((t) => (
          <button key={t} type="button" onClick={() => setTab(t)} className={`rounded-lg px-3 py-1.5 text-sm capitalize ${tab === t ? "bg-sky-600 text-white" : "bg-slate-800 text-slate-300"}`}>
            {t}
          </button>
        ))}
      </nav>
      <Card>
        {tab === "overview" && <Overview s={s} onChanged={reload} isStaff={isStaff} isManager={isManager} />}
        {tab === "overview" && !isStaff && (
          <div className="mt-4 space-y-2 border-t border-slate-800 pt-3">
            <Note>{action.error}</Note>
            {s.mine ? (
              <p className="text-sm">
                You are <Badge value={s.mine.status} />{" "}
                {!["cancelled", "attended", "no_show"].includes(s.mine.status) && (
                  <button type="button" className="ml-2 text-xs text-red-300" onClick={() => action.run(() => tr.cancelRegistration(s.mine!.id)).then(reload)}>
                    Cancel my registration
                  </button>
                )}
              </p>
            ) : (
              s.status === "scheduled" && (
                <button type="button" className={btnPrimary} onClick={() => action.run(() => tr.enrol(s.id, {})).then(reload)}>
                  Register me
                </button>
              )
            )}
          </div>
        )}
        {tab === "registrations" && <Registrations s={s} onChanged={reload} isManager={isManager} />}
        {tab === "attendance" && <Attendance key={JSON.stringify(s.counts)} s={s} onChanged={reload} />}
        {tab === "assessments" && (
          <Table
            head={["Assessment", "Opens", "Closes", "Anonymous", "Status"]}
            rows={s.instances.map((i) => [i.type, fmt(i.opens_at) === "—" ? "at invitation" : fmt(i.opens_at), fmt(i.closes_at), i.anonymous ? "yes" : "no", <Badge key="b" value={i.status} />])}
            empty="This session's version has no assessment templates (original single-form flow)."
          />
        )}
        {tab === "notifications" && <Notifications id={s.id} />}
        {tab === "results" && <SessionResults sessionId={s.id} canRecompute={isManager} />}
        {tab === "facilitators" && (
          <div className="space-y-3">
            <ul className="space-y-1 text-sm">
              {s.facilitators.map((f) => (
                <li key={f.id}>
                  {f.name} {f.lead && <Badge value="scheduled">lead</Badge>}
                </li>
              ))}
            </ul>
            {roles.includes("admin") && <QaForm s={s} />}
          </div>
        )}
      </Card>
    </div>
  );
}
