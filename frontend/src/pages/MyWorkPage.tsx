import { useEffect, useState } from "react";
import { approvalRuleOptions, approvalsWaiting, myApprovals, myWork, routeApproval, submitStructuredIntent } from "../api/client";
import { ENTITY_LABELS } from "../api/types";
import type { ApprovalRequest, EntityType, MyWorkItem } from "../api/types";
import { humanizeCodes } from "../intentLabels";
import { notifyIntentExecuted, useIntentRefresh, useWorkspace } from "../workspace";

type Outcome = { ok: boolean; text: string };

export default function MyWorkPage() {
  const { openRecord } = useWorkspace();
  const [items, setItems] = useState<MyWorkItem[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  // administrators only (the calls are refused for everyone else): approvals nobody can decide, and where to send them
  const [stuck, setStuck] = useState<ApprovalRequest[]>([]);
  const [positions, setPositions] = useState<{ id: number; name: string; holders: number }[]>([]);
  const [route, setRoute] = useState<Record<number, number>>({});
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  // the outcome of a decision, shown on its own row (it stays when the row leaves the queue)
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({});

  function load() {
    myApprovals().then(setApprovals).catch(() => setApprovals([]));
    approvalsWaiting().then((rows) => setStuck(rows.filter((r) => r.stuck))).catch(() => setStuck([]));
    approvalRuleOptions().then((o) => setPositions(o.positions)).catch(() => setPositions([]));
    myWork()
      .then(setItems)
      .finally(() => setLoaded(true));
  }

  useEffect(load, []);
  useIntentRefresh(load);

  const keyOf = (a: ApprovalRequest) => `${a.entity_type}-${a.entity_id}`;

  async function decide(a: ApprovalRequest, decision: "approve" | "reject") {
    const key = keyOf(a);
    setBusy(key);
    try {
      // The label as text keeps form clicks out of the AI's training history.
      const res = await submitStructuredIntent("Approve or reject", "INT-36", { entity_type: a.entity_type, entity_id: a.entity_id, decision });
      const ok = res.status === "executed";
      // an empty summary must never hide what happened - fall back to the reason
      const text = humanizeCodes((res.summary || "").trim() || (res.rationale || "").trim() || (ok ? "Done." : "That did not go through."));
      setOutcomes((o) => ({ ...o, [key]: { ok, text: ok ? `${decision === "approve" ? "Approved" : "Rejected"} - ${text}` : text } }));
      if (ok) notifyIntentExecuted();
      load();
    } catch (e) {
      setOutcomes((o) => ({ ...o, [key]: { ok: false, text: e instanceof Error ? e.message : "That did not work." } }));
    } finally {
      setBusy(null);
    }
  }

  async function sendOn(a: ApprovalRequest) {
    const positionId = route[a.id ?? 0];
    if (!a.id || !positionId) return;
    const key = keyOf(a);
    setBusy(key);
    try {
      const res = await routeApproval(a.id, positionId);
      setOutcomes((o) => ({ ...o, [key]: { ok: true, text: res.change } }));
      load();
    } catch (e) {
      setOutcomes((o) => ({ ...o, [key]: { ok: false, text: e instanceof Error ? e.message : "That did not work." } }));
    } finally {
      setBusy(null);
    }
  }

  const label = (a: ApprovalRequest) => (
    <>
      <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs font-bold text-slate-300">{ENTITY_LABELS[a.entity_type as EntityType] ?? a.entity_type}</span>
      {a.entity_type in ENTITY_LABELS ? (
        <button onClick={() => openRecord(a.entity_type as EntityType, a.entity_id)} className="text-left text-slate-200 hover:text-sky-400">
          {a.record_label ?? `#${a.entity_id}`}
        </button>
      ) : (
        <span className="text-slate-200">{a.record_label ?? `#${a.entity_id}`}</span>
      )}
      <span className="text-xs text-slate-400">{a.summary || a.rule}</span>
      {a.requested_by && <span className="text-xs text-slate-500">asked by {a.requested_by}</span>}
      {a.waiting_for && <span className="text-xs text-amber-400">waiting for {a.waiting_for}</span>}
    </>
  );
  const outcome = (key: string) =>
    outcomes[key] && (
      <p className={`basis-full text-xs ${outcomes[key].ok ? "text-emerald-300" : "text-red-300"}`}>
        {outcomes[key].ok ? "✓ " : "✗ "}
        {outcomes[key].text}
      </p>
    );
  const decidedHere = Object.keys(outcomes).filter((k) => !approvals.some((a) => keyOf(a) === k) && !stuck.some((a) => keyOf(a) === k));

  return (
    <div className="page space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">My Work</h1>
          <p className="mt-1 text-sm text-slate-400">Approvals waiting on you, and open records assigned to you or a position you hold.</p>
        </div>
        <button onClick={load} className="rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2 text-xs font-bold text-slate-300 hover:border-sky-500 hover:text-sky-400">
          Refresh
        </button>
      </div>

      {approvals.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-xs font-bold uppercase tracking-wide text-amber-400">Waiting for your approval</h2>
          {approvals.map((a) => {
            const key = keyOf(a);
            return (
              <div key={`${key}-${a.gate}`} className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-sm">
                {label(a)}
                <span className="ml-auto flex gap-2">
                  <button disabled={busy === key} onClick={() => decide(a, "approve")}
                    className="rounded-md bg-emerald-600 px-3 py-1 text-xs font-bold text-white hover:bg-emerald-500 disabled:opacity-50">{busy === key ? "…" : "Approve"}</button>
                  <button disabled={busy === key} onClick={() => decide(a, "reject")}
                    className="rounded-md border border-red-500/50 px-3 py-1 text-xs font-bold text-red-300 hover:bg-red-500/10 disabled:opacity-50">Reject</button>
                </span>
                {outcome(key)}
              </div>
            );
          })}
        </section>
      )}

      {stuck.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-xs font-bold uppercase tracking-wide text-red-400">Nobody can decide these - send each to the position that should</h2>
          {stuck.map((a) => {
            const key = keyOf(a);
            return (
              <div key={`stuck-${a.id}`} className="flex flex-wrap items-center gap-3 rounded-lg border border-red-500/30 bg-red-500/5 px-4 py-3 text-sm">
                {label(a)}
                <span className="ml-auto flex items-center gap-2">
                  <select value={route[a.id ?? 0] ?? 0} onChange={(e) => setRoute({ ...route, [a.id ?? 0]: Number(e.target.value) })}
                    className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200">
                    <option value={0}>Send to…</option>
                    {positions.map((p) => (
                      <option key={p.id} value={p.id}>{p.name}{p.holders === 0 ? " - nobody yet" : ` - ${p.holders}`}</option>
                    ))}
                  </select>
                  <button disabled={busy === key || !route[a.id ?? 0]} onClick={() => sendOn(a)}
                    className="rounded-md bg-sky-600 px-3 py-1 text-xs font-bold text-white hover:bg-sky-500 disabled:opacity-40">Send</button>
                </span>
                <p className="basis-full text-xs text-red-300">⚠ {a.stuck}</p>
                {outcome(key)}
              </div>
            );
          })}
        </section>
      )}

      {decidedHere.length > 0 && (
        <section className="space-y-1">
          {decidedHere.map((k) => (
            <p key={k} className={`rounded-lg border px-4 py-2 text-sm ${outcomes[k].ok ? "border-emerald-500/30 text-emerald-300" : "border-red-500/30 text-red-300"}`}>
              {k.replace("-", " #")}: {outcomes[k].text}
            </p>
          ))}
        </section>
      )}

      <div className="space-y-2">
        {items.map((i) => (
          <button
            key={`${i.entity_type}-${i.entity_id}`}
            onClick={() =>
              i.open_entity_type && i.open_entity_id
                ? openRecord(i.open_entity_type, i.open_entity_id)
                : i.entity_type !== "work_order" && openRecord(i.entity_type, i.entity_id)
            }
            className="flex w-full flex-wrap items-center gap-3 rounded-lg border border-slate-700 bg-slate-900/60 px-4 py-3 text-left text-sm hover:border-sky-500"
          >
            <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs font-bold text-slate-300">
              {i.entity_type === "work_order" ? "Work order" : ENTITY_LABELS[i.entity_type]}
            </span>
            <span className="text-slate-200">{i.title}</span>
            <span className="rounded-md border border-slate-700 px-2 py-0.5 text-xs text-slate-400">{i.state}</span>
            {i.via_position && <span className="text-xs text-violet-400">via {i.via_position}</span>}
            <span className="ml-auto text-xs text-slate-500">{new Date(i.updated_at).toLocaleString()}</span>
          </button>
        ))}
        {loaded && items.length === 0 && approvals.length === 0 && stuck.length === 0 && <p className="text-sm text-slate-500">Nothing assigned to you right now.</p>}
      </div>
    </div>
  );
}
