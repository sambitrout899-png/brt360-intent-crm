import { useState } from "react";
import { fmt, num, tr } from "./api";
import type { Me, Overview } from "./api";
import CalendarSection from "./CalendarSection";
import FacilitatorsSection from "./FacilitatorsSection";
import { useLoad } from "./hooks";
import { LEARNING_PAGES } from "./learningMeta";
import type { LearningPageKey } from "./learningMeta";
import { AuditSection, CompareSection, MessagesSection, SettingsSection } from "./InsightsSections";
import PeopleSection from "./PeopleSection";
import ProgramsSection from "./ProgramsSection";
import SessionDesk from "./SessionDesk";
import SessionResults from "./SessionResults";
import TemplatesSection from "./TemplatesSection";
import { Badge, Card, Note, Stat, Table, btnGhost } from "./ui";

/* Every Learning page is its own menu entry and its own page key under
   Admin -> Roles (training_*), so roles can be given exactly the pages they
   need. Each page opens with what needs attention and the numbers that
   matter for it; what a person can change inside still follows their
   training role (section 4 of the BRD). */


function Header({ page, stats, overview }: { page: LearningPageKey; stats?: React.ReactNode; overview: Overview | null }) {
  const meta = LEARNING_PAGES.find((p) => p.id === page)!;
  const alerts = (overview?.alerts ?? []).filter((a) => a.page === page);
  return (
    <div className="space-y-3">
      <div>
        <p className="text-xs uppercase tracking-widest text-sky-400">Training & Learning</p>
        <h1 className="mt-1 text-2xl font-bold text-slate-50">{meta.label}</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-400">{meta.blurb}</p>
      </div>
      {stats && <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">{stats}</div>}
      {alerts.length > 0 && (
        <ul className="space-y-1.5">
          {alerts.map((a, i) => (
            <li key={i} className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${a.tone === "warn" ? "border-amber-500/40 bg-amber-500/10 text-amber-100" : "border-slate-700 bg-slate-900 text-slate-300"}`}>
              <span aria-hidden>{a.tone === "warn" ? "⚠" : "ℹ"}</span>
              <span className="flex-1">{a.text}</span>
              {a.link && (
                <a className="font-semibold text-sky-300" href={a.link}>
                  Open →
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Mine({ overview }: { overview: Overview | null }) {
  const { data, error } = useLoad(() => tr.me(), []);
  if (error) return <Note>{error}</Note>;
  if (!data) return null;
  const open = overview?.mine.open_assessments ?? [];
  return (
    <div className="space-y-4">
      <Card title="Open assessments">
        {open.length ? (
          <ul className="divide-y divide-slate-800">
            {open.map((a, i) => (
              <li key={i} className="flex flex-wrap items-center gap-3 py-2">
                <span className="min-w-0 flex-1 text-sm">
                  {a.session} <span className="text-xs text-slate-500">· {a.phase} · closes {fmt(a.closes_at)}</span>
                </span>
                {a.link && (
                  <a className={btnGhost} href={a.link}>
                    Start →
                  </a>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-500">Nothing to complete right now.</p>
        )}
      </Card>
      <Card title="My registrations">
        <Table
          head={["Session", "Starts", "My status", "Session"]}
          rows={data.registrations.map((r) => [r.title, fmt(r.starts_at), <Badge key="s" value={r.status} />, <Badge key="t" value={r.session_status} />])}
          empty="You're not registered for anything yet - open Sessions to find one."
        />
      </Card>
      {data.scores.length > 0 && (
        <Card title="My scores">
          <Table head={["Session", "Assessment", "Knowledge", "Confidence"]} rows={data.scores.map((s) => [s.session, s.phase, num(s.knowledge, "%"), num(s.confidence)])} />
        </Card>
      )}
      <Card title="Messages">
        <ul className="space-y-2">
          {data.messages.map((m) => (
            <li key={m.id} className="rounded-lg border border-slate-800 p-3">
              <p className="text-sm font-semibold">{m.subject}</p>
              <p className="text-[11px] text-slate-500">{fmt(m.sent_at)}</p>
              <p className="mt-1 whitespace-pre-wrap text-sm text-slate-300">{m.body}</p>
            </li>
          ))}
          {!data.messages.length && <li className="text-sm text-slate-500">No messages.</li>}
        </ul>
      </Card>
    </div>
  );
}

function OutcomesPage() {
  const sessions = useLoad(() => tr.calendar({}), []);
  const [picked, setPicked] = useState<number | "">("");
  const finished = (sessions.data ?? []).filter((s) => ["in_progress", "completed", "closed"].includes(s.status));
  return (
    <div className="space-y-4">
      <Card
        title="Session results"
        actions={
          <select className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1 text-sm" value={picked} aria-label="Session" onChange={(e) => setPicked(e.target.value ? Number(e.target.value) : "")}>
            <option value="">Choose a session…</option>
            {finished.map((s) => (
              <option key={s.id} value={s.id}>
                {new Date(s.starts_at).toLocaleDateString()} · {s.title}
              </option>
            ))}
          </select>
        }
      >
        {picked ? <SessionResults key={picked} sessionId={picked} /> : <p className="text-sm text-slate-500">Pick a session that has run to see its funnel, pre/post change, objectives and feedback.</p>}
      </Card>
      <CompareSection />
    </div>
  );
}

function denied(me: Me, resource: string) {
  return !me.can[resource]?.includes("r");
}

/** One Learning page - the menu entry `page`. */
export function LearningPage({ page }: { page: LearningPageKey }) {
  const me = useLoad(() => tr.me(), []);
  const catalog = useLoad(() => tr.catalog(), []);
  const ov = useLoad(() => tr.overview(), []);
  const [sessionId, setSessionId] = useState<number | null>(null);
  if (me.error) return <Note>{me.error}</Note>;
  if (!me.data || !catalog.data) return <p className="animate-pulse text-sm text-slate-500">Loading…</p>;
  const m = me.data;
  const o = ov.data;
  const roles = m.roles;
  const isAdmin = roles.includes("admin");
  const need = (resource: string, body: React.ReactNode) =>
    denied(m, resource) ? <Note kind="info">This page is open to you, but your training role ({roles.join(" + ")}) can't use it. Ask an Org Admin to give you the right training role in People & places.</Note> : body;

  let stats: React.ReactNode = null;
  let body: React.ReactNode = null;
  switch (page) {
    case "training_mine":
      stats = o && (
        <>
          <Stat label="Upcoming" value={o.mine.upcoming} />
          <Stat label="Completed" value={o.mine.completed} />
          <Stat label="Open assessments" value={o.mine.open_assessments.length} />
        </>
      );
      body = <Mine overview={o} />;
      break;
    case "training_sessions":
      stats = o && (
        <>
          <Stat label="Next 7 days" value={o.sessions.next_7_days} />
          <Stat label="In progress" value={o.sessions.in_progress} />
          <Stat label="Need attendance" value={o.sessions.awaiting_attendance} />
          <Stat label="Waitlisted" value={o.sessions.waitlisted} />
          <Stat label="Seats filled" value={num(o.sessions.seats_filled, "%")} hint="upcoming sessions" />
          {o.sessions.i_facilitate > 0 && <Stat label="I facilitate" value={o.sessions.i_facilitate} />}
        </>
      );
      body = sessionId ? (
        <SessionDesk sessionId={sessionId} roles={roles} onBack={() => setSessionId(null)} />
      ) : (
        <CalendarSection catalog={catalog.data} canSchedule={!!m.can.sessions?.includes("c")} onOpen={setSessionId} />
      );
      break;
    case "training_programs":
      stats = o?.programs && (
        <>
          <Stat label="Programs" value={o.programs.programs} />
          <Stat label="Published versions" value={o.programs.published} />
          <Stat label="Drafts" value={o.programs.drafts} />
          <Stat label="Retired" value={o.programs.retired} />
        </>
      );
      body = need("programs", <ProgramsSection catalog={catalog.data} canEdit={!!m.can.programs?.includes("u")} onChanged={catalog.reload} />);
      break;
    case "training_assessments":
      stats = o?.assessments && (
        <>
          <Stat label="Templates" value={o.assessments.templates} />
          <Stat label="Pre / post / feedback" value={`${o.assessments.by_type.pre} / ${o.assessments.by_type.post} / ${o.assessments.by_type.feedback}`} />
          <Stat label="Drafts" value={o.assessments.drafts} />
          <Stat label="Questions" value={o.assessments.questions} />
        </>
      );
      body = need("templates", <TemplatesSection templates={catalog.data.templates ?? []} canEdit={!!m.can.templates?.includes("c")} onChanged={catalog.reload} />);
      break;
    case "training_people":
      stats = o?.people && (
        <>
          <Stat label="Users" value={o.people.users} />
          <Stat label="HR / facilitators / executives" value={`${o.people.roles.hr} / ${o.people.roles.facilitator} / ${o.people.roles.executive}`} />
          <Stat label="Participants (no login)" value={o.people.participants} />
          <Stat label="Bounced emails" value={o.people.bounced} />
          <Stat label="No reminders" value={o.people.unsubscribed} />
          <Stat label="Erased" value={o.people.erased} />
        </>
      );
      body = need("people", <PeopleSection isAdmin={isAdmin} />);
      break;
    case "training_facilitators":
      stats = o?.facilitators && (
        <>
          <Stat label="Facilitators" value={o.facilitators.facilitators} />
          <Stat label="Valid certificates" value={o.facilitators.valid} />
          <Stat label="Expiring" value={o.facilitators.expiring} />
          <Stat label="Expired" value={o.facilitators.expired} />
        </>
      );
      body = need(
        "certifications",
        <FacilitatorsSection catalog={catalog.data} isAdmin={isAdmin} onChanged={catalog.reload} selfId={null} seeAll={roles.some((r) => ["admin", "hr", "executive"].includes(r))} />,
      );
      break;
    case "training_outcomes":
      stats = o?.outcomes && (
        <>
          <Stat label="Paired participants" value={o.outcomes.paired_participants} />
          <Stat label="Sessions with results" value={o.outcomes.sessions_with_results} />
          <Stat label="Knowledge change" value={num(o.outcomes.knowledge_change, " pts")} />
          <Stat label="Confidence change" value={num(o.outcomes.confidence_change)} />
          <Stat label="Learning gain" value={num(o.outcomes.gain)} />
        </>
      );
      body = need("analytics", roles.length === 1 && roles[0] === "participant" ? <Note kind="info">Outcome reports need a staff training role - your own scores are under My trainings.</Note> : <OutcomesPage />);
      break;
    case "training_messages":
      stats = o?.messages && (
        <>
          <Stat label="Sent (7 days)" value={o.messages.sent_7d} />
          <Stat label="Opened / clicked" value={o.messages.opened_7d} />
          <Stat label="Planned" value={o.messages.planned} />
          <Stat label="Skipped (done already)" value={o.messages.skipped_7d} />
          <Stat label="Failed" value={o.messages.failed} />
        </>
      );
      body = need(
        "notify_templates",
        <div className="space-y-4">
          {o?.messages && o.messages.recent_failures.length > 0 && (
            <Card title="Recent failures">
              <Table head={["Message", "To", "Error"]} rows={o.messages.recent_failures.map((f) => [f.event.replace(/_/g, " "), f.recipient, f.error ?? ""])} />
            </Card>
          )}
          <MessagesSection canEdit={!!m.can.notify_templates?.includes("u")} />
        </div>,
      );
      break;
    case "training_settings":
      body = need("settings", <SettingsSection canEdit={!!m.can.settings?.includes("u")} />);
      break;
    case "training_audit":
      body = need("audit", <AuditSection />);
      break;
  }
  return (
    <div className="space-y-5 p-4 text-slate-100 md:p-6">
      <Header page={page} stats={stats} overview={o} />
      {body}
    </div>
  );
}
