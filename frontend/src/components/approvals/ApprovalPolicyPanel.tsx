import { useEffect, useState } from "react";
import { approvalPolicy, approvalsWaiting, routeApproval, saveApprovalPolicy } from "../../api/client";
import type { ApprovalPolicy, ApprovalRequest } from "../../api/types";

/* Admin → Approvals: who decides an approval when its position is empty, whether requesters may decide
   their own requests, whether administrators may decide any step, the segments rules can be scoped to -
   and every approval under way, with the ones nobody can decide flagged and sent to a real position. */

type Position = { id: number; name: string; holders: number };
const errorText = (e: unknown) => String((e as Error)?.message ?? e).replace(/^\d+ [^:]*: /, "").replace(/^\{"detail":"(.*)"\}$/, "$1");
const BOX = "rounded-xl border border-slate-700 bg-slate-900/60 p-5";
const SELECT = "rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200";

export function WhoDecides({ positions, onSaved }: { positions: Position[]; onSaved: () => void }) {
  const [p, setP] = useState<ApprovalPolicy | null>(null);
  const [segments, setSegments] = useState("");
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    approvalPolicy().then((x) => (setP(x), setSegments(x.segments.join(", ")))).catch((e) => setNote({ ok: false, text: errorText(e) }));
  }, []);

  if (!p) return note ? <p className="text-xs text-red-400">{note.text}</p> : null;

  const save = async () => {
    setNote(null);
    try {
      const saved = await saveApprovalPolicy({
        unstaffed: p.unstaffed, fallback_position_id: p.unstaffed === "position" ? p.fallback_position_id : null,
        self_approval: p.self_approval, admins_decide_any: p.admins_decide_any, segments: segments.split(",").map((s) => s.trim()).filter(Boolean),
      });
      setP(saved);
      setSegments(saved.segments.join(", "));
      setNote({ ok: true, text: "Saved - it applies to every approval from now on, including those waiting now." });
      onSaved();
    } catch (e) {
      setNote({ ok: false, text: errorText(e) });
    }
  };

  const option = (value: ApprovalPolicy["unstaffed"], title: string, help: string) => (
    <label className={`flex cursor-pointer gap-2 rounded-lg border px-3 py-2 ${p.unstaffed === value ? "border-sky-500 bg-sky-500/10" : "border-slate-700"}`}>
      <input type="radio" checked={p.unstaffed === value} onChange={() => setP({ ...p, unstaffed: value })} className="mt-0.5" />
      <span>
        <span className="block text-sm font-semibold text-slate-200">{title}</span>
        <span className="text-xs text-slate-500">{help}</span>
      </span>
    </label>
  );

  return (
    <div className={BOX}>
      <h3 className="text-base font-bold text-slate-100">Who decides</h3>
      <p className="mb-4 text-xs text-slate-500">
        Each step is decided by the people in its position, and waits in their My Work. These settings say what happens around that.
      </p>
      <p className="mb-2 text-xs font-semibold text-slate-300">When nobody holds a step's position (or the step names none), it goes to</p>
      <div className="grid gap-2 md:grid-cols-3">
        {option("position", "A fallback position", "e.g. an Approvals Desk - its people get the approval")}
        {option("admins", "Administrators", "only an administrator can decide it")}
        {option("anyone", "Anyone allowed to approve", "anyone whose role may run the approval")}
      </div>
      {p.unstaffed === "position" && (
        <select value={p.fallback_position_id ?? 0} onChange={(e) => setP({ ...p, fallback_position_id: Number(e.target.value) || null })} className={`${SELECT} mt-2 w-full md:w-1/2`}>
          <option value={0}>Choose the fallback position…</option>
          {positions.map((x) => <option key={x.id} value={x.id}>{x.name}{x.holders === 0 ? " - nobody yet" : ` - ${x.holders} ${x.holders === 1 ? "person" : "people"}`}</option>)}
        </select>
      )}
      <div className="mt-4 space-y-2 text-sm text-slate-300">
        <label className="flex items-start gap-2">
          <input type="checkbox" checked={p.self_approval} onChange={(e) => setP({ ...p, self_approval: e.target.checked })} className="mt-1" />
          <span>Whoever asked for an approval may decide it themselves <span className="text-xs text-slate-500">- off: someone else in the position (or the fallback) must</span></span>
        </label>
        <label className="flex items-start gap-2">
          <input type="checkbox" checked={p.admins_decide_any} onChange={(e) => setP({ ...p, admins_decide_any: e.target.checked })} className="mt-1" />
          <span>Administrators may decide any step <span className="text-xs text-slate-500">- it is still only in their My Work when it is routed to them</span></span>
        </label>
      </div>
      <label className="mt-4 block text-xs text-slate-400">
        Segments a money rule can be limited to (comma-separated)
        <input value={segments} onChange={(e) => setSegments(e.target.value)} className={`${SELECT} mt-1 w-full`} />
      </label>
      <div className="mt-4 flex items-center gap-3">
        <button onClick={save} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500">Save</button>
        {note && <span className={`text-xs ${note.ok ? "text-emerald-400" : "text-red-400"}`}>{note.text}</span>}
        {!p.configured && !note && <span className="text-xs text-slate-500">Not saved yet - following "Strict approvals" (Admin → Roles &amp; Access).</span>}
      </div>
    </div>
  );
}

export function WaitingNow({ positions, refresh }: { positions: Position[]; refresh: number }) {
  const [rows, setRows] = useState<ApprovalRequest[] | null>(null);
  const [route, setRoute] = useState<Record<number, number>>({});
  const [note, setNote] = useState<Record<number, { ok: boolean; text: string }>>({});
  const [onlyStuck, setOnlyStuck] = useState(false);

  const load = () => approvalsWaiting().then(setRows).catch(() => setRows([]));
  useEffect(() => {
    load();
  }, [refresh]);

  const send = async (a: ApprovalRequest) => {
    if (!a.id || !route[a.id]) return;
    try {
      const res = await routeApproval(a.id, route[a.id]);
      setNote({ ...note, [a.id]: { ok: true, text: res.change } });
      load();
    } catch (e) {
      setNote({ ...note, [a.id]: { ok: false, text: errorText(e) } });
    }
  };

  const stuckCount = rows?.filter((r) => r.stuck).length ?? 0;
  const shown = (rows ?? []).filter((r) => !onlyStuck || r.stuck);
  return (
    <div className={BOX}>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h3 className="text-base font-bold text-slate-100">Waiting now</h3>
        {stuckCount > 0 && <span className="rounded-md bg-red-500/15 px-2 py-0.5 text-xs font-semibold text-red-300">{stuckCount} nobody can decide</span>}
        <label className="ml-auto flex items-center gap-1.5 text-xs text-slate-400">
          <input type="checkbox" checked={onlyStuck} onChange={(e) => setOnlyStuck(e.target.checked)} /> only those nobody can decide
        </label>
      </div>
      {rows === null ? <p className="text-xs text-slate-500">Loading…</p> : shown.length === 0 ? (
        <p className="text-sm text-slate-500">{onlyStuck ? "Every approval waiting has someone who can decide it." : "No approvals are waiting."}</p>
      ) : (
        <div className="space-y-2">
          {shown.map((a) => (
            <div key={a.id} className={`flex flex-wrap items-center gap-3 rounded-lg border px-3 py-2 text-sm ${a.stuck ? "border-red-500/30 bg-red-500/5" : "border-slate-800"}`}>
              <span className="text-slate-200">{a.record_label}</span>
              <span className="text-xs text-slate-500">{a.rule}{a.requested_by ? ` · asked by ${a.requested_by}` : ""}</span>
              <span className="text-xs text-amber-400">waiting for {a.waiting_for}</span>
              <span className="ml-auto flex items-center gap-2">
                <select value={route[a.id ?? 0] ?? 0} onChange={(e) => setRoute({ ...route, [a.id ?? 0]: Number(e.target.value) })} className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200">
                  <option value={0}>Send to…</option>
                  {positions.filter((x) => x.id !== a.position_id).map((x) => <option key={x.id} value={x.id}>{x.name}{x.holders === 0 ? " - nobody yet" : ` - ${x.holders}`}</option>)}
                </select>
                <button disabled={!route[a.id ?? 0]} onClick={() => send(a)} className="rounded-md bg-sky-600 px-3 py-1 text-xs font-bold text-white disabled:opacity-40">Send</button>
              </span>
              {a.stuck && <p className="basis-full text-xs text-red-300">⚠ {a.stuck}</p>}
              {a.id && note[a.id] && <p className={`basis-full text-xs ${note[a.id].ok ? "text-emerald-300" : "text-red-300"}`}>{note[a.id].text}</p>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
