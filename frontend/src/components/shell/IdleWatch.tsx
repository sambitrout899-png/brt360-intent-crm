import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { currentUser, logout } from "../../api/client";

const PING_EVERY_MS = 60_000; // the server counts activity to the minute (app/auth.py)
const WARN_MS = 60_000;

/** The company's idle timeout, in the browser: what the user does (clicks,
 * typing, scrolling) tells the server they are here - at most once a
 * minute, so reading a long page doesn't end the session - and a minute
 * before the limit a notice offers to stay signed in. The server enforces
 * the limit itself; this only makes it visible. */
export default function IdleWatch({ minutes, onSignedOut }: { minutes: number; onSignedOut: (reason: string) => void }) {
  const lastActivity = useRef(Date.now());
  const lastPing = useRef(Date.now());
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);

  useEffect(() => {
    const limit = minutes * 60_000;

    function ping() {
      lastPing.current = Date.now();
      currentUser().catch(() => undefined); // a 401 here signs out through the usual path
    }

    function active() {
      lastActivity.current = Date.now();
      if (Date.now() - lastPing.current >= PING_EVERY_MS) ping();
    }

    const events = ["mousedown", "keydown", "wheel", "touchstart"] as const;
    events.forEach((e) => window.addEventListener(e, active, { passive: true }));
    const timer = window.setInterval(() => {
      const idle = Date.now() - lastActivity.current;
      if (idle >= limit) {
        logout();
        onSignedOut(`Signed out after ${minutes} minutes without activity.`);
      } else {
        setSecondsLeft(idle >= limit - WARN_MS ? Math.ceil((limit - idle) / 1000) : null);
      }
    }, 1000);
    return () => {
      events.forEach((e) => window.removeEventListener(e, active));
      window.clearInterval(timer);
    };
  }, [minutes, onSignedOut]);

  if (secondsLeft === null) return null;
  return createPortal(
    <div role="alertdialog" aria-live="assertive" className="fixed bottom-6 left-1/2 z-[60] w-[min(26rem,calc(100%-2rem))] -translate-x-1/2 rounded-xl border border-amber-500/40 bg-slate-900 p-4 shadow-2xl">
      <p className="text-sm text-slate-200">
        You'll be signed out in <span className="font-semibold text-amber-300">{secondsLeft} s</span> - no activity for a while.
      </p>
      <button type="button" className="mt-3 rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500"
        onClick={() => { lastActivity.current = Date.now(); lastPing.current = Date.now(); currentUser().catch(() => undefined); setSecondsLeft(null); }}>
        Stay signed in
      </button>
    </div>,
    document.body,
  );
}
