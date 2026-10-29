import { useState } from "react";
import { download, fmt, num, tr } from "./api";
import type { NotifyTemplate } from "./api";
import { useAction, useLoad } from "./hooks";
import { Card, Note, Table, btnGhost, btnPrimary, field, label } from "./ui";

/* Analytics comparison (ANL-005, ANL-008), message templates (WFA-003),
   org settings (ORG-010) and the audit log (ORG-012). */

const GROUPS = [
  ["session", "Session"],
  ["facilitator", "Facilitator"],
  ["location", "Location"],
  ["department", "Department"],
  ["program_version", "Program version"],
  ["program", "Program"],
];

export function CompareSection() {
  const [groupBy, setGroupBy] = useState("program");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const params = { s: start ? new Date(start).toISOString() : undefined, e: end ? new Date(`${end}T23:59:59`).toISOString() : undefined };
  const { data, error } = useLoad(() => tr.compare(groupBy, params.s, params.e), [groupBy, start, end]);
  const exportAs = (fmtName: string) =>
    download(
      `/api/training/analytics/export?format=${fmtName}&group_by=${groupBy}${params.s ? `&start=${encodeURIComponent(params.s)}` : ""}${params.e ? `&end=${encodeURIComponent(params.e)}` : ""}`,
      `compare-${groupBy}.${fmtName}`,
    ).catch(() => undefined);
  return (
    <Card
      title="Compare outcomes"
      actions={["csv", "xlsx", "pdf"].map((f) => (
        <button key={f} type="button" className={btnGhost} onClick={() => exportAs(f)}>
          ⬇ {f.toUpperCase()}
        </button>
      ))}
    >
      <div className="mb-3 grid gap-2 sm:grid-cols-3">
        <label className={label}>
          Group by
          <select className={field} value={groupBy} onChange={(e) => setGroupBy(e.target.value)}>
            {GROUPS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label className={label}>
          From
          <input type="date" className={field} value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className={label}>
          To
          <input type="date" className={field} value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
      </div>
      <Note>{error}</Note>
      <Table
        head={["Group", "n", "Sessions", "Knowledge", "Change", "Gain", "Confidence", "Change"]}
        rows={(data?.groups ?? []).map((g) => [
          <span key="l">
            {g.label}
            {g.small_group && <span className="ml-1 text-[11px] text-amber-300">small group</span>}
          </span>,
          g.n,
          g.sessions,
          `${num(g.knowledge_pre, "%")} → ${num(g.knowledge_post, "%")}`,
          num(g.knowledge_change),
          num(g.gain),
          `${num(g.confidence_pre)} → ${num(g.confidence_post)}`,
          num(g.confidence_change),
        ])}
        empty="No paired results in this range yet. Results appear once participants complete both the pre and post assessments."
      />
      {data && data.hidden_small_groups > 0 && <p className="mt-2 text-xs text-slate-500">{data.hidden_small_groups} group(s) below {data.threshold} participants are hidden to protect anonymity.</p>}
    </Card>
  );
}

const EVENT_LABEL: Record<string, string> = {
  registration_confirmed: "Registration confirmed (+ pre link)",
  pre_reminder_1: "Pre reminder 1 (start − 48 h)",
  pre_reminder_2: "Pre reminder 2 (start − 4 h)",
  session_day: "Session day (start − 2 h)",
  post_invite: "Post-assessment + feedback (end + 15 min)",
  post_reminder_1: "Post reminder 1 (end + 48 h)",
  post_reminder_2: "Post reminder 2 (24 h before close)",
  window_closed: "Assessment window closed (staff)",
  cert_expiring: "Certification expiring",
  session_cancelled: "Session cancelled",
  session_updated: "Session rescheduled / updated",
  waitlist_promoted: "Waitlist - seat opened",
  otp: "One-time check-in code",
  bounce_alert: "Email bounced (to HR)",
  rating_alert: "Facilitator rating alert",
};

function TemplateRow({ t, fields, canEdit }: { t: NotifyTemplate; fields: string[]; canEdit: boolean }) {
  const [open, setOpen] = useState(false);
  const [v, setV] = useState(t);
  const [preview, setPreview] = useState<{ subject: string; body_text: string } | null>(null);
  const action = useAction();
  return (
    <li className="space-y-2 py-2">
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 text-sm">
          {EVENT_LABEL[t.event] ?? t.event} {v.custom && <span className="text-[11px] text-sky-300">customized</span>}
        </span>
        <button type="button" className="text-xs font-semibold text-sky-300" onClick={() => setOpen(!open)}>
          {open ? "Close" : canEdit ? "Edit" : "View"}
        </button>
      </div>
      {open && (
        <div className="space-y-2 rounded-xl border border-slate-800 p-3">
          <input className={field} disabled={!canEdit} value={v.subject} onChange={(e) => setV({ ...v, subject: e.target.value })} aria-label="Subject" />
          <textarea className={`${field} h-32 font-mono text-xs`} disabled={!canEdit} value={v.body_text} onChange={(e) => setV({ ...v, body_text: e.target.value })} aria-label="Plain text" />
          <textarea className={`${field} h-20 font-mono text-xs`} disabled={!canEdit} placeholder="HTML version (optional)" value={v.body_html} onChange={(e) => setV({ ...v, body_html: e.target.value })} aria-label="HTML" />
          <p className="text-[11px] text-slate-500">Merge fields: {fields.map((f) => `{{${f}}}`).join(" ")}</p>
          <Note>{action.error}</Note>
          <Note kind="ok">{action.ok}</Note>
          <div className="flex gap-2">
            <button type="button" className={btnGhost} onClick={() => action.run(() => tr.previewNotify(t.event, v)).then((p) => p && setPreview(p))}>
              Preview
            </button>
            {canEdit && (
              <button type="button" className={btnPrimary} onClick={() => action.run(() => tr.saveNotifyTemplate(t.event, v), "Saved.").then((o) => o && setV(o))}>
                Save
              </button>
            )}
          </div>
          {preview && (
            <div className="rounded-lg border border-slate-700 bg-slate-950 p-3 text-sm">
              <p className="font-semibold">{preview.subject}</p>
              <p className="mt-1 whitespace-pre-wrap text-slate-300">{preview.body_text}</p>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

export function MessagesSection({ canEdit }: { canEdit: boolean }) {
  const { data, error } = useLoad(() => tr.notifyTemplates(), []);
  const action = useAction();
  return (
    <div className="space-y-4">
      <Card
        title="Automated messages"
        actions={
          <button
            type="button"
            className={btnGhost}
            disabled={action.busy}
            onClick={() => action.run(() => tr.tick()).then((o) => o && action.setOk(`Ran the scheduler: ${o.transitions} status change(s), ${o.sent} message(s) sent.`))}
          >
            Run scheduler now
          </button>
        }
      >
        <p className="mb-2 text-xs text-slate-500">
          Messages go out automatically from session events; reminders skip anyone who has already completed the step. Email goes through the outbox adapter unless SMTP is
          configured on the server; SMS and WhatsApp plug into the same adapter interface.
        </p>
        <Note>{error || action.error}</Note>
        <Note kind="ok">{action.ok}</Note>
        <ul className="divide-y divide-slate-800">
          {(data?.events ?? []).map((t) => (
            <TemplateRow key={t.event} t={t} fields={data?.fields ?? []} canEdit={canEdit} />
          ))}
        </ul>
      </Card>
    </div>
  );
}

type SettingsShape = {
  timezone: string;
  anonymity_default: string;
  pass_mark: number;
  retention_months: number;
  min_group_size: number;
  show_own_scores: boolean;
  exclude_late_pre: boolean;
  pre_grace_min: number;
  post_window_days: number;
  checkin_before_min: number;
  checkin_after_min: number;
  expiring_days: number;
  rating_threshold: number;
  travel_buffer_min: number;
  quiet_hours: { enabled: boolean; start: number; end: number };
  reminders: Record<string, number | number[]>;
  qa_rubric: string[];
  brand: { logo_url: string; primary_color: string };
};

export function SettingsSection({ canEdit }: { canEdit: boolean }) {
  const { data, error, setData } = useLoad(() => tr.settings(), []);
  const action = useAction();
  if (error) return <Note>{error}</Note>;
  if (!data) return <p className="animate-pulse text-sm text-slate-500">Loading…</p>;
  const s = data.settings as unknown as SettingsShape;
  const set = (patch: Partial<SettingsShape>) => setData({ ...data, settings: { ...data.settings, ...patch } });
  const numberField = (key: keyof SettingsShape, text: string, step = 1) => (
    <label className={label}>
      {text}
      <input type="number" step={step} disabled={!canEdit} className={field} value={s[key] as number} onChange={(e) => set({ [key]: Number(e.target.value) } as Partial<SettingsShape>)} />
    </label>
  );
  return (
    <Card title={`Organization settings · version ${data.version}`}>
      <div className="space-y-4">
        <div className="grid gap-2 md:grid-cols-3">
          <label className={label}>
            Time zone
            <input disabled={!canEdit} className={field} value={s.timezone} onChange={(e) => set({ timezone: e.target.value })} />
          </label>
          <label className={label}>
            Feedback forms by default
            <select disabled={!canEdit} className={field} value={s.anonymity_default} onChange={(e) => set({ anonymity_default: e.target.value })}>
              <option value="identifiable">Identifiable</option>
              <option value="anonymous">Anonymous</option>
            </select>
          </label>
          {numberField("pass_mark", "Pass mark (%)")}
          {numberField("min_group_size", "Minimum group for anonymous results (≥ 3)")}
          {numberField("retention_months", "Keep personal data (months)")}
          {numberField("rating_threshold", "Facilitator rating alert below", 0.1)}
          {numberField("pre_grace_min", "Pre-assessment grace after start (min)")}
          {numberField("post_window_days", "Post-assessment open for (days)")}
          {numberField("expiring_days", "Certificate 'expiring' within (days)")}
          {numberField("checkin_before_min", "Check-in opens before start (min)")}
          {numberField("checkin_after_min", "Check-in closes after start (min)")}
          {numberField("travel_buffer_min", "Facilitator travel buffer (min)")}
        </div>
        <div className="flex flex-wrap gap-4 text-sm text-slate-300">
          <label className="flex items-center gap-2">
            <input type="checkbox" disabled={!canEdit} checked={s.show_own_scores} onChange={(e) => set({ show_own_scores: e.target.checked })} /> Participants see their own scores
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" disabled={!canEdit} checked={s.exclude_late_pre} onChange={(e) => set({ exclude_late_pre: e.target.checked })} /> Exclude late pre-assessments from comparison
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" disabled={!canEdit} checked={s.quiet_hours.enabled} onChange={(e) => set({ quiet_hours: { ...s.quiet_hours, enabled: e.target.checked } })} /> Quiet hours{" "}
            {s.quiet_hours.start}:00–{String(s.quiet_hours.end).padStart(2, "0")}:00
          </label>
        </div>
        <label className={label}>
          QA rubric criteria (one per line)
          <textarea disabled={!canEdit} className={`${field} h-24`} value={s.qa_rubric.join("\n")} onChange={(e) => set({ qa_rubric: e.target.value.split("\n").map((x) => x.trim()).filter(Boolean) })} />
        </label>
        <Note>{action.error}</Note>
        <Note kind="ok">{action.ok}</Note>
        {canEdit && (
          <button type="button" className={btnPrimary} disabled={action.busy} onClick={() => action.run(() => tr.saveSettings(data.settings), "Saved - a new settings version is in the audit log.").then((o) => o && setData(o))}>
            Save settings
          </button>
        )}
      </div>
    </Card>
  );
}

export function AuditSection() {
  const { data, error } = useLoad(() => tr.audit(), []);
  return (
    <Card
      title="Audit log"
      actions={
        <button type="button" className={btnGhost} onClick={() => download("/api/training/audit-log?format=csv", "training-audit.csv").catch(() => undefined)}>
          ⬇ CSV
        </button>
      }
    >
      <Note>{error}</Note>
      <Table
        head={["When", "Who", "Action", "Record", "Change", "IP"]}
        rows={(data ?? []).map((r) => [
          fmt(r.at),
          r.actor,
          r.action,
          `${r.entity} ${r.entity_id}`,
          <code key="c" className="block max-w-md truncate text-[11px] text-slate-400" title={JSON.stringify({ before: r.before, after: r.after })}>
            {r.after ? JSON.stringify(r.after) : ""}
          </code>,
          r.ip,
        ])}
        empty="No entries yet."
      />
    </Card>
  );
}
