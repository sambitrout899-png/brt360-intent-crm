import { useState } from "react";
import { fmt, num, tr } from "./api";
import type { Catalog } from "./api";
import { toIso, useAction, useLoad } from "./hooks";
import { Badge, Card, Note, Stat, Table, btnGhost, btnPrimary, field, label } from "./ui";

/* Facilitators: certification catalog and records with computed status
   (FAC-002, PRG-006), scorecards (FAC-004) and QA reviews (FAC-003). */

function Scorecard({ id }: { id: number }) {
  const { data, error } = useLoad(() => tr.scorecard(id), [id]);
  const reviews = useLoad(() => tr.qaReviews(id), [id]);
  if (error) return <Note>{error}</Note>;
  if (!data) return <p className="animate-pulse text-sm text-slate-500">Loading…</p>;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Stat label="Sessions delivered" value={data.sessions_delivered} hint={`${data.sessions_assigned} assigned`} />
        <Stat label="Participant feedback" value={num(data.average_feedback, " / 5")} hint={`${data.feedback_n} responses`} />
        <Stat label="Avg knowledge gain" value={num(data.average_knowledge_gain, " pts")} />
        <Stat label="Latest QA rating" value={num(data.latest_qa_rating, " / 5")} />
      </div>
      {data.below_threshold && <Note>Average feedback is below the organization's threshold - Org Admins have been alerted.</Note>}
      <Table
        head={["Review date", "Session", "Overall", "Scores", "Comments", "Actions"]}
        rows={(reviews.data?.reviews ?? []).map((r) => [
          fmt(r.review_date),
          `#${r.session_id}`,
          r.overall_rating,
          Object.entries(r.scores)
            .map(([k, v]) => `${k} ${v}`)
            .join(", "),
          r.comments,
          r.actions,
        ])}
        empty="No QA reviews yet."
      />
    </div>
  );
}

export default function FacilitatorsSection({ catalog, isAdmin, onChanged, selfId, seeAll }: { catalog: Catalog; isAdmin: boolean; onChanged: () => void; selfId: number | null; seeAll: boolean }) {
  const list = useLoad(() => tr.facilitators(), []);
  const [open, setOpen] = useState<number | null>(seeAll ? null : selfId);
  const [cert, setCert] = useState({ name: "", issuer: "", validity_months: "", program_ids: [] as string[] });
  const [record, setRecord] = useState({ user_id: "", certification_id: "", issuer: "", issued_on: "", expires_at: "", evidence_url: "" });
  const action = useAction();
  return (
    <div className="space-y-4">
      <Card title="Facilitators">
        <Note>{list.error}</Note>
        <ul className="divide-y divide-slate-800">
          {(list.data ?? []).map((f) => (
            <li key={f.id} className="space-y-2 py-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 flex-1 text-sm">
                  {f.name}
                  {f.profile.is_external ? <span className="ml-1 text-xs text-slate-500">external · {String(f.profile.vendor ?? "")}</span> : null}
                  {f.profile.expertise ? <span className="block text-xs text-slate-500">{String(f.profile.expertise)}</span> : null}
                </span>
                {f.certificates.map((c) => (
                  <Badge key={c.id} value={c.status}>
                    {c.name}: {c.status}
                  </Badge>
                ))}
                <button type="button" className="text-xs font-semibold text-sky-300" onClick={() => setOpen(open === f.id ? null : f.id)}>
                  {open === f.id ? "Close" : "Scorecard"}
                </button>
              </div>
              {open === f.id && (
                <div className="space-y-3 rounded-xl border border-slate-800 p-3">
                  <Scorecard id={f.id} />
                  {isAdmin && f.certificates.some((c) => c.status !== "Revoked") && (
                    <div className="flex flex-wrap gap-2">
                      {f.certificates
                        .filter((c) => c.status !== "Revoked")
                        .map((c) => (
                          <button key={c.id} type="button" className="text-xs text-red-300"
                            onClick={() => {
                              const reason = window.prompt(`Revoke ${c.name}? Reason:`);
                              if (reason) action.run(() => tr.revokeCert(c.id, reason)).then(list.reload);
                            }}>
                            Revoke {c.name}
                          </button>
                        ))}
                    </div>
                  )}
                </div>
              )}
            </li>
          ))}
          {!list.data?.length && <li className="py-2 text-sm text-slate-500">No facilitators yet - give someone the facilitator role under People.</li>}
        </ul>
      </Card>
      {isAdmin && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="Certifications">
            <ul className="mb-3 space-y-1 text-sm">
              {(catalog.certifications ?? []).map((c) => (
                <li key={c.id}>
                  {c.name} <span className="text-xs text-slate-500">{c.issuer} {c.validity_months ? `· ${c.validity_months} months` : ""}</span>
                </li>
              ))}
            </ul>
            <form
              className="space-y-2"
              onSubmit={async (e) => {
                e.preventDefault();
                const ok = await action.run(
                  () => tr.addCertification({ ...cert, validity_months: cert.validity_months ? Number(cert.validity_months) : null, program_ids: cert.program_ids.map(Number) }),
                  "Certification added.",
                );
                if (ok) {
                  setCert({ name: "", issuer: "", validity_months: "", program_ids: [] });
                  onChanged();
                }
              }}
            >
              <input required className={field} placeholder="Certification name" value={cert.name} onChange={(e) => setCert({ ...cert, name: e.target.value })} />
              <div className="grid grid-cols-2 gap-2">
                <input className={field} placeholder="Issuer" value={cert.issuer} onChange={(e) => setCert({ ...cert, issuer: e.target.value })} />
                <input type="number" min={1} className={field} placeholder="Valid for (months)" value={cert.validity_months} onChange={(e) => setCert({ ...cert, validity_months: e.target.value })} />
              </div>
              <label className={label}>
                Programs that require it
                <select multiple className={`${field} h-20`} value={cert.program_ids} onChange={(e) => setCert({ ...cert, program_ids: Array.from(e.target.selectedOptions).map((o) => o.value) })}>
                  {catalog.programs.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
              <button className={btnPrimary}>Add certification</button>
            </form>
          </Card>
          <Card title="Record a facilitator's certificate">
            <form
              className="space-y-2"
              onSubmit={async (e) => {
                e.preventDefault();
                const ok = await action.run(
                  () =>
                    tr.addFacilitatorCert({
                      user_id: Number(record.user_id),
                      certification_id: record.certification_id ? Number(record.certification_id) : null,
                      name: (catalog.certifications ?? []).find((c) => String(c.id) === record.certification_id)?.name ?? "",
                      issuer: record.issuer,
                      issued_on: record.issued_on ? toIso(record.issued_on) : null,
                      expires_at: record.expires_at ? toIso(record.expires_at) : null,
                      evidence_url: record.evidence_url,
                    }),
                  "Certificate recorded.",
                );
                if (ok) list.reload();
              }}
            >
              <select required className={field} value={record.user_id} onChange={(e) => setRecord({ ...record, user_id: e.target.value })} aria-label="Facilitator">
                <option value="">Facilitator…</option>
                {(list.data ?? []).map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name}
                  </option>
                ))}
              </select>
              <select required className={field} value={record.certification_id} onChange={(e) => setRecord({ ...record, certification_id: e.target.value })} aria-label="Certification">
                <option value="">Certification…</option>
                {(catalog.certifications ?? []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
              <div className="grid grid-cols-2 gap-2">
                <label className={label}>
                  Issued
                  <input type="datetime-local" className={field} value={record.issued_on} onChange={(e) => setRecord({ ...record, issued_on: e.target.value })} />
                </label>
                <label className={label}>
                  Expires (blank = from validity)
                  <input type="datetime-local" className={field} value={record.expires_at} onChange={(e) => setRecord({ ...record, expires_at: e.target.value })} />
                </label>
              </div>
              <input className={field} placeholder="Evidence link (https://…)" value={record.evidence_url} onChange={(e) => setRecord({ ...record, evidence_url: e.target.value })} />
              <button className={btnPrimary}>Record</button>
            </form>
          </Card>
        </div>
      )}
      <Note>{action.error}</Note>
      <Note kind="ok">{action.ok}</Note>
      {!isAdmin && !seeAll && (
        <button type="button" className={btnGhost} onClick={() => setOpen(selfId)}>
          My scorecard
        </button>
      )}
    </div>
  );
}
