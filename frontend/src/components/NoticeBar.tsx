import { useCallback, useEffect, useState } from "react";
import { myNotices } from "../api/client";
import type { EntityType, Notices, TickerItem } from "../api/types";
import { useIntentRefresh, useWorkspace } from "../workspace";

const LEVEL: Record<string, { dot: string; text: string }> = {
  info: { dot: "bg-sky-400", text: "text-slate-200" },
  success: { dot: "bg-emerald-400", text: "text-emerald-200" },
  warning: { dot: "bg-amber-400", text: "text-amber-200" },
  urgent: { dot: "bg-red-400", text: "text-red-200" },
};

const RECORD_TYPES = new Set(["party", "lead", "opportunity", "quote", "order"]);

/** The running bar of key information: administrators' broadcasts, approvals
 * waiting for you, what happened to your requests and - on Customer 360 -
 * that customer's own alerts. Pauses on hover; items with a target open it. */
export function NoticeTicker({ place, partyId, onOpenTab }: { place: "dashboard" | "customer" | "mywork"; partyId?: number; onOpenTab?: (tab: string) => void }) {
  const { openRecord } = useWorkspace();
  const [items, setItems] = useState<TickerItem[]>([]);
  const load = useCallback(() => {
    myNotices(place, partyId)
      .then((n) => setItems(n.ticker))
      .catch(() => setItems([]));
  }, [place, partyId]);
  useEffect(() => {
    load();
    const timer = window.setInterval(load, 60_000);
    return () => window.clearInterval(timer);
  }, [load]);
  useIntentRefresh(load);

  if (items.length === 0) return null;
  const open = (i: TickerItem) => {
    if (i.link && onOpenTab) onOpenTab(i.link);
    else if (i.entity_type && i.entity_id && RECORD_TYPES.has(i.entity_type)) openRecord(i.entity_type as EntityType, i.entity_id);
  };
  const row = (copy: number) =>
    items.map((i, n) => {
      const tone = LEVEL[i.level] ?? LEVEL.info;
      const clickable = Boolean((i.link && onOpenTab) || (i.entity_type && RECORD_TYPES.has(i.entity_type)));
      return (
        <button
          key={`${copy}-${n}`}
          type="button"
          tabIndex={copy ? -1 : 0}
          aria-hidden={copy ? true : undefined}
          onClick={() => open(i)}
          disabled={!clickable}
          className={`mx-5 inline-flex shrink-0 items-center gap-2 text-xs ${tone.text} ${clickable ? "hover:underline" : "cursor-default"}`}
        >
          <span className={`h-1.5 w-1.5 rounded-full ${tone.dot}`} />
          {i.text}
        </button>
      );
    });
  return (
    <div className="notice-bar page mb-6 flex items-center overflow-hidden rounded-lg border border-slate-800 bg-slate-900/70" role="region" aria-label="Key information">
      <span className="z-10 shrink-0 border-r border-slate-800 bg-slate-900 px-3 py-2 text-[11px] font-bold uppercase tracking-wide text-sky-400">📣 Live</span>
      <div className="min-w-0 flex-1 overflow-hidden py-2">
        <div className="notice-track" style={{ ["--notice-speed" as string]: `${Math.max(20, items.length * 9)}s` }}>
          {row(0)}
          {row(1)}
        </div>
      </div>
    </div>
  );
}

/** Shown once per sign-in when approvals are waiting for the user. */
export function LoginApprovals({ userId, onOpenMyWork }: { userId: number; onOpenMyWork: () => void }) {
  const [notices, setNotices] = useState<Notices | null>(null);
  const key = `brt360.approvals-shown.${userId}`;
  useEffect(() => {
    let seen = false;
    try {
      seen = sessionStorage.getItem(key) === "1";
    } catch {
      /* storage unavailable - show it */
    }
    if (seen) return;
    myNotices("dashboard")
      .then((n) => {
        if (n.approvals_waiting > 0) setNotices(n);
        try {
          sessionStorage.setItem(key, "1");
        } catch {
          /* fine */
        }
      })
      .catch(() => undefined);
  }, [key]);

  if (!notices) return null;
  return (
    <div className="fixed bottom-6 right-6 z-50 w-[min(26rem,calc(100vw-2rem))] rounded-xl border border-amber-500/40 bg-slate-900 p-4 shadow-2xl" role="alert">
      <div className="flex items-start justify-between gap-3">
        <h3 className="text-sm font-bold text-amber-300">🔔 {notices.approvals_waiting} approval(s) waiting for you</h3>
        <button onClick={() => setNotices(null)} className="text-slate-500 hover:text-slate-300" aria-label="Dismiss">
          ✕
        </button>
      </div>
      <ul className="mt-2 space-y-1.5 text-xs text-slate-300">
        {notices.approvals.slice(0, 5).map((a) => (
          <li key={`${a.entity_type}-${a.entity_id}`}>
            <span className="text-slate-200">{a.summary}</span>
            {a.requested_by && <span className="text-slate-500"> · asked by {a.requested_by}</span>}
            {a.waiting_for && <span className="text-amber-400"> · as {a.waiting_for}</span>}
          </li>
        ))}
      </ul>
      <button
        onClick={() => {
          setNotices(null);
          onOpenMyWork();
        }}
        className="mt-3 rounded-md bg-amber-500 px-3 py-1.5 text-xs font-bold text-slate-950 hover:bg-amber-400"
      >
        Open My Work
      </button>
    </div>
  );
}

/** Header bell: how many approvals are waiting. */
export function ApprovalBell({ onOpenMyWork }: { onOpenMyWork: () => void }) {
  const [count, setCount] = useState(0);
  const load = useCallback(() => {
    myNotices("mywork")
      .then((n) => setCount(n.approvals_waiting))
      .catch(() => setCount(0));
  }, []);
  useEffect(() => {
    load();
    const timer = window.setInterval(load, 60_000);
    return () => window.clearInterval(timer);
  }, [load]);
  useIntentRefresh(load);
  return (
    <button
      onClick={onOpenMyWork}
      title={count ? `${count} approval(s) waiting for you` : "No approvals waiting"}
      className="relative rounded-lg border border-slate-700 px-2 py-1.5 text-xs text-slate-300 hover:border-amber-500"
    >
      🔔
      {count > 0 && (
        <span className="absolute -right-1.5 -top-1.5 min-w-[1.1rem] rounded-full bg-amber-500 px-1 text-center text-[10px] font-bold text-slate-950">{count}</span>
      )}
    </button>
  );
}
