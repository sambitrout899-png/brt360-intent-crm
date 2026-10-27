import { useCallback, useEffect, useState } from "react";
import { myPopups, popupsSeen } from "../../api/client";
import type { PopupMessage } from "../../api/client";
import type { EntityType } from "../../api/types";
import { ENTITY_LABELS } from "../../api/types";
import { useIntentRefresh, useWorkspace } from "../../workspace";

const TONE: Record<PopupMessage["level"], { ring: string; badge: string; icon: string; button: string }> = {
  info: { ring: "border-sky-500/40", badge: "bg-sky-500/15 text-sky-300", icon: "ℹ️", button: "bg-sky-600 hover:bg-sky-500" },
  success: { ring: "border-emerald-500/40", badge: "bg-emerald-500/15 text-emerald-300", icon: "✅", button: "bg-emerald-600 hover:bg-emerald-500" },
  warning: { ring: "border-amber-500/40", badge: "bg-amber-500/15 text-amber-300", icon: "⚠️", button: "bg-amber-500 text-slate-950 hover:bg-amber-400" },
  urgent: { ring: "border-red-500/50", badge: "bg-red-500/15 text-red-300", icon: "🚨", button: "bg-red-600 hover:bg-red-500" },
};
const POLL_MS = 60_000;

/** Message boxes the tenant's flows send to this user (Flow Studio: Notify,
 * channel "Message box on screen"). Checked right after any action the user
 * runs - so a box their own action triggered appears at once - and every
 * minute for boxes others' actions sent them. One at a time, until dismissed. */
export default function PopupMessages() {
  const { openRecord } = useWorkspace();
  const [queue, setQueue] = useState<PopupMessage[]>([]);

  const load = useCallback(() => {
    myPopups()
      .then((boxes) => setQueue((current) => [...current, ...boxes.filter((b) => !current.some((c) => c.id === b.id))]))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);
  useIntentRefresh(load);

  const box = queue[0];
  if (!box) return null;
  const tone = TONE[box.level] ?? TONE.info;
  const record = box.entity_type && box.entity_id && box.entity_type in ENTITY_LABELS ? (box.entity_type as EntityType) : null;

  function dismiss(all = false) {
    const upTo = all ? Math.max(...queue.map((b) => b.id)) : box!.id;
    popupsSeen(upTo).catch(() => undefined);
    setQueue((q) => (all ? [] : q.slice(1)));
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 px-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="popup-title">
      <div className={`w-full max-w-md rounded-2xl border ${tone.ring} bg-slate-900 p-6 shadow-2xl shadow-black/50`}>
        <div className="flex items-start gap-3">
          <span className="text-2xl leading-none">{tone.icon}</span>
          <div className="min-w-0 flex-1">
            <h2 id="popup-title" className="text-base font-semibold text-slate-50">
              {box.title}
            </h2>
            <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-slate-300">{box.message}</p>
          </div>
        </div>
        <div className="mt-6 flex flex-wrap items-center gap-2">
          {queue.length > 1 && (
            <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${tone.badge}`}>
              1 of {queue.length}
            </span>
          )}
          {queue.length > 1 && (
            <button onClick={() => dismiss(true)} className="text-xs font-semibold text-slate-400 hover:text-slate-200">
              Dismiss all
            </button>
          )}
          <span className="ml-auto flex gap-2">
            {record && (
              <button
                onClick={() => {
                  dismiss();
                  openRecord(record, box.entity_id!);
                }}
                className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm font-semibold text-slate-200 hover:border-slate-500"
              >
                Open {ENTITY_LABELS[record].toLowerCase()}
              </button>
            )}
            <button autoFocus onClick={() => dismiss()} className={`rounded-lg px-4 py-1.5 text-sm font-semibold text-white ${tone.button}`}>
              OK
            </button>
          </span>
        </div>
      </div>
    </div>
  );
}
