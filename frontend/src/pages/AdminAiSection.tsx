import { intentLabel } from "../intentLabels";
import { useEffect, useState } from "react";
import { clearAiSettings, downloadTrainingData, getAiSettings, localModelStatus, testAi, updateAiSettings } from "../api/client";
import type { AiProvider, AiSettings, AiTestResult, LocalModelStatus } from "../api/types";

/** Admin -> AI Settings: which brain powers this tenant's AI - an external
 * API, the platform's own telecom model on this server, or rules only -
 * with a live test, the local model's health, and training-data export. */

const OPTIONS: { id: AiProvider; icon: string; title: string; text: string }[] = [
  { id: "local", icon: "🏠", title: "Our own model (this server)", text: "BRT 360's telecom model runs on your server. Private - no data leaves it, no per-call fees." },
  { id: "external", icon: "☁️", title: "External AI API", text: "A hosted AI service with your key (or the platform's). Highest raw quality; requests go to the provider." },
  { id: "rules", icon: "⚙️", title: "Built-in rules only", text: "No AI model. Instant and private; understands the standard phrasings and field: value text." },
];

export default function AdminAiSection() {
  const [settings, setSettings] = useState<AiSettings | null>(null);
  const [choice, setChoice] = useState<AiProvider>("rules");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(""); // blank = the platform's default model
  const [localModel, setLocalModel] = useState("brt360-telecom");
  const [liveAssist, setLiveAssist] = useState(false);
  const [status, setStatus] = useState<LocalModelStatus | null>(null);
  const [testText, setTestText] = useState("Acme Telecom's internet has been down since this morning at the head office");
  const [test, setTest] = useState<AiTestResult | null>(null);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  function apply(s: AiSettings) {
    setSettings(s);
    setChoice(s.provider);
    setModel(s.model);
    setLocalModel(s.local_model);
    setLiveAssist(s.local_live_assist);
  }

  useEffect(() => {
    getAiSettings().then(apply).catch((e) => setError(e instanceof Error ? e.message : "Could not load AI settings"));
    localModelStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  const flash = (text: string) => {
    setMessage(text);
    setTimeout(() => setMessage(""), 4000);
  };

  async function save() {
    setBusy("save");
    setError("");
    try {
      apply(await updateAiSettings({ api_key: apiKey.trim() || null, model: model.trim(), is_active: true, provider: choice, local_model: localModel, local_live_assist: liveAssist }));
      setApiKey("");
      flash("Saved - new requests use this now.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save");
    } finally {
      setBusy("");
    }
  }

  async function runTest() {
    setBusy("test");
    setTest(null);
    setError("");
    try {
      setTest(await testAi({ provider: choice, local_model: localModel, text: testText }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Test failed");
    } finally {
      setBusy("");
    }
  }

  async function refreshStatus() {
    setBusy("status");
    try {
      setStatus(await localModelStatus());
    } finally {
      setBusy("");
    }
  }

  async function exportData() {
    setBusy("export");
    setError("");
    try {
      const n = await downloadTrainingData();
      flash(n ? `Downloaded ${n} training examples.` : "Download started.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Export failed");
    } finally {
      setBusy("");
    }
  }

  async function clearKey() {
    await clearAiSettings();
    apply(await getAiSettings());
    flash("Your key was removed.");
  }

  const dirty =
    !!settings &&
    (choice !== settings.provider || apiKey.trim() !== "" || model !== settings.model || localModel !== settings.local_model || liveAssist !== settings.local_live_assist);
  const installed = status?.models.some((m) => m.name === localModel || m.name === `${localModel}:latest`);

  return (
    <div className="space-y-6">
      {settings && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-slate-700 bg-slate-900/60 px-4 py-3 text-sm">
          <span className="text-slate-400">Answering now:</span>
          <span
            className={`rounded-md px-2.5 py-1 text-xs font-bold ${
              settings.effective === "local" ? "bg-emerald-500/15 text-emerald-300" : settings.effective === "external" ? "bg-sky-500/15 text-sky-300" : "bg-slate-500/20 text-slate-300"
            }`}
          >
            {settings.effective_label}
          </span>
          {settings.provider === "external" && settings.effective === "rules" && (
            <span className="text-xs text-amber-300">External was chosen but there is no API key - the rules are answering.</span>
          )}
          {message && <span className="ml-auto text-xs font-semibold text-emerald-400">✓ {message}</span>}
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-3">
        {OPTIONS.map((o) => (
          <button
            key={o.id}
            onClick={() => (setChoice(o.id), setTest(null))}
            className={`rounded-2xl border p-4 text-left transition ${
              choice === o.id ? "border-sky-400 bg-sky-500/10 ring-2 ring-sky-500/30" : "border-slate-700 bg-slate-900/60 hover:border-slate-500"
            }`}
          >
            <div className="flex items-center gap-2">
              <span className="text-xl">{o.icon}</span>
              <span className="font-bold text-slate-100">{o.title}</span>
              {choice === o.id && <span className="ml-auto text-sky-300">●</span>}
            </div>
            <p className="mt-2 text-xs leading-relaxed text-slate-400">{o.text}</p>
          </button>
        ))}
      </div>

      {choice === "local" && (
        <div className="space-y-4 rounded-2xl border border-emerald-500/25 bg-slate-900/60 p-5">
          <div className="flex flex-wrap items-center gap-3">
            <h3 className="text-base font-bold text-slate-100">Model on this server</h3>
            <span
              className={`rounded-full px-2.5 py-0.5 text-xs font-bold ${status?.reachable ? "bg-emerald-500/15 text-emerald-300" : "bg-rose-500/15 text-rose-300"}`}
            >
              {status === null ? "checking…" : status.reachable ? "● running" : "● not running"}
            </span>
            <button onClick={refreshStatus} className="text-xs text-slate-400 hover:text-slate-200">
              {busy === "status" ? "checking…" : "↻ re-check"}
            </button>
          </div>

          {status && !status.reachable && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-100">
              <p className="font-semibold">The model server isn't installed or isn't running yet.</p>
              <p className="mt-1 text-amber-200/80">On the server, run once (about 10 minutes, downloads ~2 GB):</p>
              <code className="mt-1.5 block rounded bg-slate-950 px-2 py-1.5 font-mono text-[11px] text-emerald-300">bash /opt/brt360/deploy/local_llm.sh install</code>
              <p className="mt-1.5 text-amber-200/70">Until then, requests fall back to the built-in rules - nothing breaks.</p>
            </div>
          )}

          <div className="grid gap-3 md:grid-cols-2">
            <label className="text-xs text-slate-400">
              Model
              {status?.reachable && status.models.length > 0 ? (
                <select value={localModel} onChange={(e) => setLocalModel(e.target.value)} className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200">
                  {!installed && <option value={localModel}>{localModel} (not installed)</option>}
                  {status.models.map((m) => (
                    <option key={m.name} value={m.name.replace(/:latest$/, "")}>
                      {m.name.replace(/:latest$/, "")} {m.parameters ? `· ${m.parameters}` : ""} {m.size_gb ? `· ${m.size_gb} GB` : ""}
                    </option>
                  ))}
                </select>
              ) : (
                <input value={localModel} onChange={(e) => setLocalModel(e.target.value)} className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" />
              )}
            </label>
            <label className="flex items-start gap-2 pt-5 text-xs text-slate-400">
              <input type="checkbox" checked={liveAssist} onChange={(e) => setLiveAssist(e.target.checked)} className="mt-0.5" />
              <span>
                Also use it for <b className="text-slate-300">as-you-type suggestions</b>. Leave off on a CPU-only server - suggestions then use the instant rules, and the model is used
                when a request is actually run.
              </span>
            </label>
          </div>
          {status?.reachable && <p className="text-[11px] text-slate-500">Server: {status.base_url} (only reachable from this machine)</p>}
        </div>
      )}

      {choice === "external" && (
        <div className="space-y-3 rounded-2xl border border-sky-500/25 bg-slate-900/60 p-5">
          <h3 className="text-base font-bold text-slate-100">External API</h3>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="text-xs text-slate-400">
              API key
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
                placeholder={settings?.configured ? `${settings.masked_api_key} - type to replace` : settings?.platform_key_available ? "blank = use the platform's key" : "your provider's API key"}
                autoComplete="off"
              />
            </label>
            <label className="text-xs text-slate-400">
              Model
              <input value={model} onChange={(e) => setModel(e.target.value)} className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" placeholder="blank = the platform's default model" />
            </label>
          </div>
          {settings?.configured && (
            <button onClick={clearKey} className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-red-500 hover:text-red-400">
              Remove our key
            </button>
          )}
        </div>
      )}

      <div className="rounded-2xl border border-slate-700 bg-slate-900/60 p-5">
        <h3 className="mb-2 text-base font-bold text-slate-100">Try it before you save</h3>
        <div className="flex flex-wrap gap-2">
          <input value={testText} onChange={(e) => setTestText(e.target.value)} className="min-w-0 flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" />
          <button onClick={runTest} disabled={busy !== ""} className="rounded-lg border border-sky-500/50 px-4 py-2 text-sm font-bold text-sky-300 hover:bg-sky-500/10 disabled:opacity-40">
            {busy === "test" ? "Thinking…" : "⚡ Test"}
          </button>
        </div>
        {busy === "test" && choice === "local" && <p className="mt-2 text-[11px] text-slate-500">On a CPU the first answer can take up to a minute while the model loads.</p>}
        {test && (
          <div className={`mt-3 rounded-lg border p-3 text-xs ${test.ok ? "border-emerald-500/30 bg-emerald-500/5" : "border-rose-500/30 bg-rose-500/10"}`}>
            <p className={`font-semibold ${test.ok ? "text-emerald-300" : "text-rose-300"}`}>
              {test.ok ? "✓ Understood" : "✕ Didn't work"} · {(test.latency_ms / 1000).toFixed(1)} s · {test.provider}
              {test.model && test.provider === "local" ? ` (${test.model})` : ""}
            </p>
            {test.intent_type && (
              <p className="mt-1 text-slate-300">
                Understood as <span className="font-semibold text-sky-300">“{intentLabel(test.intent_type)}”</span> with{" "}
                <span className="font-mono text-slate-400">{JSON.stringify(test.entities)}</span>
              </p>
            )}
            {test.error && <p className="mt-1 text-rose-200">{test.error}</p>}
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button onClick={save} disabled={!dirty || busy !== ""} className="rounded-lg bg-sky-600 px-5 py-2 text-sm font-bold text-white hover:bg-sky-500 disabled:opacity-40">
          {busy === "save" ? "Saving…" : "Save"}
        </button>
        {error && <span className="text-xs text-red-400">{error}</span>}
      </div>

      <div className="rounded-2xl border border-violet-500/25 bg-gradient-to-br from-violet-600/10 to-slate-900/60 p-5">
        <h3 className="text-base font-bold text-slate-100">🎓 Make the model yours - train it on your CRM</h3>
        <p className="mt-1 text-sm text-slate-400">
          Fine-tuning teaches the model your products, your intents and how your teams phrase things. The training file holds every request your users ran successfully plus
          ~1,000 generated telecom examples - it grows as you use the CRM.
        </p>
        <ol className="mt-3 list-decimal space-y-1 pl-5 text-xs text-slate-400">
          <li>Download the training data below (it contains customer wording - keep it safe).</li>
          <li>
            Train on a rented GPU for 1-2 hours: <code className="text-slate-300">python finetune.py --data brt360_training_data.jsonl</code>
          </li>
          <li>
            Install it on the server: <code className="text-slate-300">bash deploy/local_llm.sh import /tmp/brt360-telecom.gguf</code> - this page keeps working, it just gets smarter.
          </li>
        </ol>
        <p className="mt-2 text-[11px] text-slate-500">Step-by-step guide: deploy/llm/README.md</p>
        <button onClick={exportData} disabled={busy !== ""} className="mt-3 rounded-lg bg-violet-600 px-4 py-2 text-sm font-bold text-white hover:bg-violet-500 disabled:opacity-40">
          {busy === "export" ? "Preparing…" : "⬇ Download training data"}
        </button>
      </div>
    </div>
  );
}
