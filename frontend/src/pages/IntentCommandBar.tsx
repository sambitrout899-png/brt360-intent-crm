import { useCallback, useEffect, useState } from "react";
import { assistantOutcome, intentCatalog, recentIntents } from "../api/client";
import type { IntentCatalog, IntentResponse, RecentIntent } from "../api/types";
import AgentPanel from "../components/intent/AgentPanel";
import AssistantPanel from "../components/intent/AssistantPanel";
import AskBox from "../components/intent/AskBox";
import IntentForm from "../components/intent/IntentForm";
import IntentGallery from "../components/intent/IntentGallery";
import IntentResult from "../components/intent/IntentResult";
import ProcessBuilder from "../components/intent/ProcessBuilder";
import { useTechView } from "../components/intent/intentUi";
import { notifyIntentExecuted } from "../workspace";

type View =
  | { kind: "home" }
  | { kind: "form"; key: number; code: string; entities: Record<string, unknown>; text: string }
  | { kind: "result"; response: IntentResponse; code: string; entities: Record<string, unknown> };

const MODE_KEY = "brt360.intent.mode";
type Mode = "anything" | "ask" | "agent" | "build";

function readMode(): Mode {
  try {
    const saved = localStorage.getItem(MODE_KEY);
    return saved === "ask" || saved === "agent" || saved === "build" ? saved : "anything";
  } catch {
    return "anything";
  }
}

function timeAgo(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso.endsWith("Z") ? iso : `${iso}Z`).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 1440) return `${Math.round(minutes / 60)} h ago`;
  return `${Math.round(minutes / 1440)} d ago`;
}

/** The command center: say what you want or pick an action, get a form the
 * copilot has already filled in, run it, see what changed and what to do
 * next. Only the actions the user's roles allow appear here. */
export default function IntentCommandBar() {
  const [catalog, setCatalog] = useState<IntentCatalog | null>(null);
  const [recent, setRecent] = useState<RecentIntent[]>([]);
  const [error, setError] = useState("");
  const [view, setView] = useState<View>({ kind: "home" });
  const [techView, setTechView] = useTechView();
  const [mode, setMode] = useState<Mode>(readMode);
  // a process request spotted in the Ask box, handed to the Process Builder
  const [processText, setProcessText] = useState({ key: 0, text: "" });

  function buildProcess(text: string) {
    setProcessText((p) => ({ key: p.key + 1, text }));
    pickMode("build");
  }

  function pickMode(m: Mode) {
    setMode(m);
    try {
      localStorage.setItem(MODE_KEY, m);
    } catch {
      /* private window - the choice lasts for this visit */
    }
  }

  const loadRecent = useCallback(() => {
    recentIntents().then(setRecent).catch(() => setRecent([]));
  }, []);

  useEffect(() => {
    intentCatalog()
      .then(setCatalog)
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load your actions"));
    loadRecent();
  }, [loadRecent]);

  // the Ask-anything request a form was opened for - its outcome goes to the journal
  const [journalId, setJournalId] = useState<number | null>(null);

  const open = (code: string, entities: Record<string, unknown> = {}, text = "", fromJournal: number | null = null) => {
    setJournalId(fromJournal);
    setView({ kind: "form", key: Date.now(), code, entities, text });
  };

  function done(response: IntentResponse, code: string, entities: Record<string, unknown>) {
    setView({ kind: "result", response, code, entities });
    if (response.status === "executed") notifyIntentExecuted();
    if (journalId !== null) {
      assistantOutcome(journalId, response.status === "executed" ? "done" : response.status, response.summary ?? response.rationale ?? "").catch(() => undefined);
      setJournalId(null);
    }
    loadRecent();
  }

  const intents = catalog?.intents ?? [];
  const label = (code: string) => intents.find((i) => i.code === code)?.label ?? code;

  return (
    <div className="page space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-[1.75rem] font-semibold leading-tight text-slate-50">What would you like to do?</h1>
          <p className="mt-2 max-w-3xl text-[15px] leading-relaxed text-slate-400">
            Describe it in your own words or pick an action. I'll fill in what I can, check it before it runs, and show you exactly what changed.
          </p>
        </div>
        <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-slate-800 px-3 py-2 text-xs font-medium text-slate-400 hover:border-slate-700">
          <input type="checkbox" checked={techView} onChange={(e) => setTechView(e.target.checked)} className="accent-sky-500" />
          Explain results
        </label>
      </div>

      {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">{error}</p>}

      {view.kind === "home" && catalog && (
        <>
          {/* one place to start: a single action, or a goal the agent breaks into steps */}
          <section className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/50 shadow-xl shadow-black/20">
            <div className="flex flex-wrap items-center gap-4 border-b border-slate-800 px-6 pt-4 sm:px-8">
              <div role="tablist" aria-label="How to start" className="-mb-px flex gap-6">
                {(
                  [
                    ["anything", "🧠", "Ask anything", "border-fuchsia-400"],
                    ["ask", "✨", "Do one thing", "border-sky-400"],
                    ["agent", "🤖", "Multi-step agent", "border-violet-400"],
                    ["build", "🏗️", "Build a process", "border-emerald-400"],
                  ] as const
                ).map(([key, icon, name, tone]) => {
                  const on = mode === key;
                  return (
                    <button
                      key={key}
                      role="tab"
                      aria-selected={on}
                      onClick={() => pickMode(key)}
                      className={`flex items-center gap-2 border-b-2 pb-3.5 text-sm font-semibold transition ${
                        on ? `${tone} text-slate-50` : "border-transparent text-slate-500 hover:text-slate-300"
                      }`}
                    >
                      <span className="text-base">{icon}</span>
                      {name}
                    </button>
                  );
                })}
              </div>
              <p className="ml-auto hidden pb-3.5 text-xs text-slate-500 lg:block">
                {mode === "anything"
                  ? "Any request in plain English - I work out whether to do it, chain it or set it up, and record it."
                  : mode === "ask"
                  ? "Say it in your words - I find the action and fill the form for you."
                  : mode === "agent"
                    ? "Give a goal - I plan the steps, rehearse them on live data, and run all or none."
                    : "Describe a process - I set it up with approvals, flows and pages, asking where I must."}
              </p>
            </div>
            <div className="px-6 py-6 sm:px-8 sm:py-7">
              {mode === "anything" ? (
                <AssistantPanel intents={intents} onAction={(code, entities, text, id) => open(code, entities, text, id)} />
              ) : mode === "ask" ? (
                <AskBox bare intents={intents} onPick={(code, text) => open(code, {}, text)} onProcess={buildProcess} />
              ) : mode === "agent" ? (
                <AgentPanel bare intents={intents} />
              ) : (
                <ProcessBuilder key={processText.key} initialText={processText.text} intents={intents} />
              )}
            </div>
          </section>

          <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_320px]">
            <section className="min-w-0 space-y-5">
              <div className="flex items-baseline justify-between gap-3">
                <h2 className="text-lg font-semibold text-slate-100">All actions</h2>
                <span className="text-xs text-slate-500">{intents.length} available to you</span>
              </div>
              <IntentGallery intents={intents} categories={catalog.categories} processes={catalog.processes} techView={techView} onPick={(code) => open(code)} />
            </section>
            <aside className="space-y-4 xl:sticky xl:top-24 xl:self-start">
              <h2 className="text-lg font-semibold text-slate-100">Recent</h2>
              <div className="overflow-hidden rounded-xl border border-slate-800 bg-slate-900/40">
                {recent.length === 0 && <p className="p-4 text-xs leading-relaxed text-slate-500">Nothing yet - your last actions appear here so you can repeat them in one click.</p>}
                {recent.map((r) => (
                  <button
                    key={r.code}
                    onClick={() => open(r.code, r.entities)}
                    className="group flex w-full items-center gap-3 border-b border-slate-800/80 px-4 py-3.5 text-left transition last:border-b-0 hover:bg-slate-800/40"
                  >
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-800/80 text-base">{r.icon}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-slate-200">{r.label}</span>
                      <span className="block text-xs text-slate-500">
                        {timeAgo(r.at)} · {r.status === "executed" ? "done" : r.status.replace("_", " ")}
                      </span>
                    </span>
                    <span className="text-xs font-semibold text-sky-400 opacity-70 transition group-hover:opacity-100">Again →</span>
                  </button>
                ))}
              </div>
            </aside>
          </div>
        </>
      )}

      {view.kind === "form" && (
        <div className="space-y-3">
          <button onClick={() => setView({ kind: "home" })} className="text-sm text-slate-400 hover:text-slate-200">
            ← All actions
          </button>
          <IntentForm
            key={view.key}
            code={view.code}
            initialEntities={view.entities}
            text={view.text}
            onDone={(response) => done(response, view.code, response.entities)}
            onSwitch={(code, entities) => open(code, entities)}
            onCancel={() => setView({ kind: "home" })}
          />
        </div>
      )}

      {view.kind === "result" && (
        <div className="space-y-4">
          <IntentResult response={view.response} techView={techView} onNext={(code, entities) => open(code, entities)} />
          <div className="flex flex-wrap gap-2">
            <button onClick={() => setView({ kind: "home" })} className="rounded-lg bg-slate-100 px-4 py-2 text-sm font-bold text-slate-900 hover:bg-white">
              Do something else
            </button>
            {view.response.status !== "executed" && (
              <button onClick={() => open(view.code, view.entities)} className="rounded-lg border border-slate-700 px-4 py-2 text-sm font-semibold text-slate-300 hover:border-slate-500">
                Back to “{label(view.code)}”
              </button>
            )}
          </div>
        </div>
      )}

      {!catalog && !error && <p className="text-sm text-slate-500">Loading your actions…</p>}
    </div>
  );
}
