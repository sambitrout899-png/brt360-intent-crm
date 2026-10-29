import { useEffect, useState } from "react";
import { mfaEnable, mfaSetup, type MfaSetup } from "../../api/client";
import { problem } from "../../api/customRecords";

const INPUT = "mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 outline-none focus:border-sky-500";
const BUTTON = "w-full rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500 disabled:opacity-50";

/** Setting up two-step sign-in: scan the QR code, confirm one code. During
 * sign-in pass the step token; signed in, leave it out. */
export function TwoStepEnroll({ stepToken, onDone }: { stepToken?: string; onDone: (recoveryCodes: string[]) => void }) {
  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showSecret, setShowSecret] = useState(false);

  useEffect(() => {
    mfaSetup(stepToken).then(setSetup).catch((e) => setError(problem(e, "Couldn't start the setup.")));
  }, [stepToken]);

  async function confirm(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      onDone(await mfaEnable(code.trim(), stepToken));
    } catch (err) {
      setError(problem(err, "That code didn't work."));
    } finally {
      setBusy(false);
    }
  }

  if (!setup) return <p className="text-sm text-slate-400">{error ?? "Preparing your code…"}</p>;
  return (
    <form onSubmit={confirm} className="space-y-4">
      <ol className="list-decimal space-y-1 pl-5 text-sm text-slate-300">
        <li>Open an authenticator app on your phone (any app that shows 6-digit sign-in codes).</li>
        <li>Scan this code with it.</li>
        <li>Type the 6-digit code it shows.</li>
      </ol>
      <div className="flex justify-center">
        <img src={setup.qr_data_url} alt="QR code for your authenticator app" className="h-44 w-44 rounded-lg bg-white p-2" />
      </div>
      <p className="text-center text-xs text-slate-500">
        Can't scan?{" "}
        {showSecret ? (
          <span className="select-all break-all font-mono text-slate-300">{setup.secret}</span>
        ) : (
          <button type="button" onClick={() => setShowSecret(true)} className="text-sky-400 hover:underline">
            Enter a key instead
          </button>
        )}
      </p>
      <label className="block text-xs text-slate-400">
        6-digit code
        <input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code"
          className={`${INPUT} text-center font-mono tracking-[0.4em]`} maxLength={7} autoFocus required />
      </label>
      {error && <p className="text-xs text-red-400">{error}</p>}
      <button type="submit" disabled={busy || code.trim().length < 6} className={BUTTON}>
        {busy ? "Checking…" : "Turn on two-step sign-in"}
      </button>
    </form>
  );
}

/** The recovery codes, shown once: each signs in once in place of a code. */
export function RecoveryCodes({ codes, onContinue }: { codes: string[]; onContinue: () => void }) {
  const [saved, setSaved] = useState(false);
  const text = `BRT 360 recovery codes - each works once, in place of a code from your app\n\n${codes.join("\n")}\n`;

  function download() {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "brt360-recovery-codes.txt";
    a.click();
    URL.revokeObjectURL(url);
    setSaved(true);
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-300">
        Keep these recovery codes somewhere safe. If you lose your phone, each one signs you in once in place of a code.
        They won't be shown again.
      </p>
      <div className="grid grid-cols-2 gap-2 rounded-lg border border-slate-700 bg-slate-950 p-3 font-mono text-sm text-slate-200">
        {codes.map((c) => <span key={c}>{c}</span>)}
      </div>
      <div className="flex gap-2">
        <button type="button" onClick={download} className="flex-1 rounded-lg border border-slate-600 px-3 py-2 text-xs font-bold text-slate-200 hover:border-sky-500">
          Download
        </button>
        <button type="button" onClick={() => navigator.clipboard?.writeText(text).then(() => setSaved(true))}
          className="flex-1 rounded-lg border border-slate-600 px-3 py-2 text-xs font-bold text-slate-200 hover:border-sky-500">
          Copy
        </button>
      </div>
      <label className="flex items-center gap-2 text-xs text-slate-400">
        <input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} />
        I've saved my recovery codes
      </label>
      <button type="button" disabled={!saved} onClick={onContinue} className={BUTTON}>
        Continue
      </button>
    </div>
  );
}
