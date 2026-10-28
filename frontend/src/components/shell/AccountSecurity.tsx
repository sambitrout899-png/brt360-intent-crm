import { useEffect, useState } from "react";
import { changePassword, mfaDisable, mfaNewRecoveryCodes, mfaStatus, type MfaStatus } from "../../api/client";
import { problem } from "../../api/customRecords";
import { RecoveryCodes, TwoStepEnroll } from "./TwoStep";

const INPUT = "mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 outline-none focus:border-sky-500";
const BUTTON = "rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500 disabled:opacity-50";
const OUTLINE = "rounded-lg border border-slate-600 px-3 py-2 text-xs font-bold text-slate-200 hover:border-sky-500 disabled:opacity-50";

type Mode = "view" | "enroll" | "codes" | "disable" | "new-codes";

/** The signed-in user's own sign-in: password and two-step sign-in. */
export default function AccountSecurity({ onClose }: { onClose: () => void }) {
  const [status, setStatus] = useState<MfaStatus | null>(null);
  const [mode, setMode] = useState<Mode>("view");
  const [codes, setCodes] = useState<string[]>([]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function reload() {
    mfaStatus().then(setStatus).catch(() => setStatus(null));
    setMode("view");
  }

  useEffect(reload, []);

  function showCodes(next: string[]) {
    setCodes(next);
    setMode("codes");
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-950/70 p-3 backdrop-blur-sm sm:p-6" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label="Account security"
        className="relative my-auto w-full max-w-md space-y-6 rounded-2xl border border-slate-700 bg-slate-900 p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-slate-100">Account security</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded-md px-2 py-1 text-slate-500 hover:bg-slate-800 hover:text-slate-200">✕</button>
        </div>

        <section className="space-y-3">
          <h3 className="text-sm font-semibold text-slate-200">Two-step sign-in</h3>
          {!status && <p className="text-sm text-slate-500">Loading…</p>}
          {status && mode === "view" && (
            <>
              <p className="text-sm text-slate-400">
                {status.enabled
                  ? `On: signing in asks for a code from your authenticator app. ${status.recovery_codes_left} recovery code${status.recovery_codes_left === 1 ? "" : "s"} left.`
                  : "Off: your password alone signs you in. Turn it on so a stolen password isn't enough."}
                {status.required && " Your company requires it."}
              </p>
              <div className="flex flex-wrap gap-2">
                {!status.enabled && <button type="button" onClick={() => setMode("enroll")} className={BUTTON}>Turn on</button>}
                {status.enabled && <button type="button" onClick={() => setMode("new-codes")} className={OUTLINE}>New recovery codes</button>}
                {status.enabled && !status.required && <button type="button" onClick={() => setMode("disable")} className={OUTLINE}>Turn off</button>}
              </div>
            </>
          )}
          {mode === "enroll" && <TwoStepEnroll onDone={showCodes} />}
          {mode === "codes" && <RecoveryCodes codes={codes} onContinue={reload} />}
          {mode === "disable" && <ConfirmWithCode askPassword label="Turn off two-step sign-in"
            run={(code, password) => mfaDisable(password, code).then(reload)} onCancel={() => setMode("view")} />}
          {mode === "new-codes" && <ConfirmWithCode label="Make new recovery codes (the old ones stop working)"
            run={(code) => mfaNewRecoveryCodes(code).then(showCodes)} onCancel={() => setMode("view")} />}
        </section>

        <PasswordSection />
      </div>
    </div>
  );
}

function ConfirmWithCode({ label, askPassword, run, onCancel }: {
  label: string; askPassword?: boolean; run: (code: string, password: string) => Promise<unknown>; onCancel: () => void;
}) {
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await run(code.trim(), password);
    } catch (err) {
      setError(problem(err, "That didn't work."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      {askPassword && (
        <label className="block text-xs text-slate-400">
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} className={INPUT} required />
        </label>
      )}
      <label className="block text-xs text-slate-400">
        Code from your authenticator app
        <input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code"
          className={`${INPUT} text-center font-mono tracking-[0.3em]`} maxLength={20} autoFocus required />
      </label>
      {error && <p className="text-xs text-red-400">{error}</p>}
      <div className="flex gap-2">
        <button type="submit" disabled={busy || code.trim().length < 6} className={BUTTON}>{busy ? "Checking…" : label}</button>
        <button type="button" onClick={onCancel} className={OUTLINE}>Cancel</button>
      </div>
    </form>
  );
}

function PasswordSection() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setMessage(null);
    if (next.length < 8) {
      setMessage({ ok: false, text: "The new password needs at least 8 characters." });
      return;
    }
    setBusy(true);
    try {
      await changePassword(current, next);
      setCurrent("");
      setNext("");
      setMessage({ ok: true, text: "Password changed." });
    } catch (err) {
      setMessage({ ok: false, text: problem(err, "Couldn't change the password.") });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-3 border-t border-slate-800 pt-5">
      <h3 className="text-sm font-semibold text-slate-200">Password</h3>
      <form onSubmit={submit} className="space-y-3">
        <label className="block text-xs text-slate-400">
          Current password
          <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} className={INPUT} autoComplete="current-password" required />
        </label>
        <label className="block text-xs text-slate-400">
          New password
          <input type="password" value={next} onChange={(e) => setNext(e.target.value)} className={INPUT} autoComplete="new-password" placeholder="8+ characters" required />
        </label>
        {message && <p className={`text-xs ${message.ok ? "text-emerald-400" : "text-red-400"}`}>{message.text}</p>}
        <button type="submit" disabled={busy} className={BUTTON}>{busy ? "Saving…" : "Change password"}</button>
      </form>
    </section>
  );
}
