import { useState } from "react";
import { login, loginWithCode } from "../api/client";
import { BrtCopyright, BrtLogo } from "../components/shell/BrtBrand";
import { RecoveryCodes, TwoStepEnroll } from "../components/shell/TwoStep";

const INPUT = "mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 outline-none focus:border-sky-500";

// password -> (a code from the authenticator app | setting it up, then the recovery codes) -> signed in
type Stage = { kind: "password" } | { kind: "code"; stepToken: string } | { kind: "setup"; stepToken: string } | { kind: "codes"; codes: string[] };

/** notice: why the last session ended, when it wasn't by signing out (the idle timeout). */
export default function LoginPage({ onLoggedIn, notice }: { onLoggedIn: () => void; notice?: string | null }) {
  const [stage, setStage] = useState<Stage>({ kind: "password" });
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submitPassword(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const next = await login(email, password);
      if (next.kind === "done") onLoggedIn();
      else setStage(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Incorrect email or password.");
    } finally {
      setLoading(false);
    }
  }

  async function submitCode(e: React.FormEvent) {
    e.preventDefault();
    if (stage.kind !== "code") return;
    setError(null);
    setLoading(true);
    try {
      await loginWithCode(stage.stepToken, code.trim());
      onLoggedIn();
    } catch (err) {
      setError(err instanceof Error ? err.message : "That code isn't right.");
      setCode("");
    } finally {
      setLoading(false);
    }
  }

  function startOver() {
    setStage({ kind: "password" });
    setPassword("");
    setCode("");
    setError(null);
  }

  const subtitle = {
    password: "Sign in to continue.",
    code: "Enter the code from your authenticator app.",
    setup: "Your company requires two-step sign-in. Set it up to continue.",
    codes: "Two-step sign-in is on.",
  }[stage.kind];

  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden bg-[#070b14] px-4 py-10">
      <div className="pointer-events-none absolute -top-40 left-1/2 h-[28rem] w-[28rem] -translate-x-1/2 rounded-full bg-sky-500/10 blur-3xl" />
      <div className="relative w-full max-w-sm space-y-5 rounded-2xl border border-slate-800 bg-slate-900/70 p-8 shadow-2xl shadow-black/40 backdrop-blur">
        <div className="flex flex-col items-center text-center">
          <BrtLogo className="h-9" />
          <h1 className="mt-6 text-xl font-semibold text-slate-100">
            BRT 360 <span className="text-sky-500">·</span> Intent CRM
          </h1>
          <p className="mt-1 text-sm text-slate-400">{subtitle}</p>
        </div>

        {stage.kind === "password" && notice && (
          <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">{notice}</p>
        )}
        {stage.kind === "password" && (
          <form onSubmit={submitPassword} className="space-y-5">
            <label className="block text-xs text-slate-400">
              Email
              <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} className={INPUT} autoFocus required />
            </label>
            <label className="block text-xs text-slate-400">
              Password
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} className={INPUT} required />
            </label>
            {error && <p className="text-xs text-red-400">{error}</p>}
            <button type="submit" disabled={loading} className="w-full rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500 disabled:opacity-50">
              {loading ? "Signing in…" : "Sign In"}
            </button>
          </form>
        )}

        {stage.kind === "code" && (
          <form onSubmit={submitCode} className="space-y-5">
            <label className="block text-xs text-slate-400">
              Code
              <input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code"
                className={`${INPUT} text-center font-mono tracking-[0.3em]`} maxLength={20} autoFocus required />
            </label>
            <p className="text-xs text-slate-500">Lost your phone? Enter one of your recovery codes instead.</p>
            {error && <p className="text-xs text-red-400">{error}</p>}
            <button type="submit" disabled={loading || code.trim().length < 6} className="w-full rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500 disabled:opacity-50">
              {loading ? "Checking…" : "Verify"}
            </button>
          </form>
        )}

        {stage.kind === "setup" && <TwoStepEnroll stepToken={stage.stepToken} onDone={(codes) => setStage({ kind: "codes", codes })} />}

        {stage.kind === "codes" && <RecoveryCodes codes={stage.codes} onContinue={onLoggedIn} />}

        {(stage.kind === "code" || stage.kind === "setup") && (
          <button type="button" onClick={startOver} className="w-full text-center text-xs text-slate-500 hover:text-slate-300">
            Back to sign in
          </button>
        )}
      </div>
      <p className="relative mt-8 text-xs text-slate-500">
        <BrtCopyright />
      </p>
    </div>
  );
}
