import { humanizeCodes } from "../../intentLabels";
import { useEffect, useMemo, useRef, useState } from "react";
import { assistIntent } from "../../api/client";
import type { IntentAssist, IntentContext, IntentDef } from "../../api/types";

/** "Say it in your own words": live understanding while typing (the same
 * classifier a submission uses), the matched action as a card, and other
 * plausible actions - Enter continues to that action's prefilled form. */
export default function AskBox({
  intents,
  context,
  initialText = "",
  compact = false,
  bare = false,
  onPick,
  onProcess,
}: {
  intents: IntentDef[];
  context?: IntentContext;
  initialText?: string;
  compact?: boolean;
  /** sits inside a card already - a plain field instead of the glowing frame */
  bare?: boolean;
  onPick: (code: string, text: string) => void;
  /** a request to set up a process (not to do one thing) goes to the Process Builder */
  onProcess?: (text: string) => void;
}) {
  const [text, setText] = useState(initialText);
  const [lastAssist, setAssist] = useState<IntentAssist | null>(null);
  const [thinking, setThinking] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  const byCode = new Map(intents.map((i) => [i.code, i]));

  useEffect(() => {
    setTimeout(() => ref.current?.focus(), 30);
  }, []);

  const contextKey = JSON.stringify(context ?? null);
  const long = text.trim().length >= 4;
  useEffect(() => {
    if (!long) return;
    // Ask the AI only once typing pauses, and drop an answer that's out of date.
    const controller = new AbortController();
    const handle = setTimeout(() => {
      setThinking(true);
      assistIntent(text, JSON.parse(contextKey) ?? undefined, controller.signal)
        .then(setAssist)
        .catch(() => undefined)
        .finally(() => !controller.signal.aborted && setThinking(false));
    }, 700);
    return () => {
      controller.abort();
      clearTimeout(handle);
    };
  }, [text, long, contextKey]);

  // Instant answer while the AI catches up: the actions whose name, phrases
  // and examples share the most words with what is typed.
  const local = useMemo(() => (long ? localMatches(text, intents) : []), [text, long, intents]);
  // an answer for text that has since been cleared is not shown
  const assist = long ? lastAssist : null;
  const aiMatch = assist?.intent_type ? byCode.get(assist.intent_type) : undefined;
  const match = aiMatch ?? (local[0] && local[0].score >= 2 ? local[0].intent : undefined);
  const others = (aiMatch ? (assist?.suggestions ?? []).map((s) => byCode.get(s.intent_type)) : local.slice(match ? 1 : 0, 4).map((l) => l.intent))
    .filter((i): i is IntentDef => !!i && i.code !== match?.code);

  return (
    <div className={bare ? "space-y-4" : "space-y-3"}>
      <div className={bare ? "rounded-xl border border-slate-700 bg-slate-950/80 transition focus-within:border-sky-500" : "relative rounded-2xl bg-gradient-to-r from-sky-500/60 via-violet-500/60 to-fuchsia-500/60 p-[1px] shadow-lg shadow-sky-950/50"}>
        <div className={bare ? "flex items-start gap-3 px-4 py-3" : "flex items-start gap-3 rounded-2xl bg-slate-950 px-4 py-3"}>
          <span className={bare ? "pt-0.5 text-lg" : "pt-1 text-xl"}>✨</span>
          <textarea
            ref={ref}
            value={text}
            rows={bare ? 3 : 2}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && match) {
                e.preventDefault();
                onPick(match.code, text);
              }
            }}
            placeholder="Tell me what you want to do - e.g. “quote Fiber 800 for Acme with 5% off” or “the customer's internet is down”"
            className={`w-full resize-none bg-transparent text-slate-100 outline-none focus-visible:outline-none placeholder:text-slate-500 ${compact ? "text-sm" : bare ? "text-[15px] leading-relaxed" : "text-base"}`}
          />
          {thinking && <span title="Checking with the AI" className="mt-2 h-2 w-2 shrink-0 animate-pulse rounded-full bg-sky-400" />}
        </div>
      </div>

      {onProcess && PROCESS.test(text) && (
        <button
          onClick={() => onProcess(text)}
          className="flex w-full items-center gap-3 rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-3 text-left transition hover:bg-emerald-500/20"
        >
          <span className="text-2xl">🏗️</span>
          <span className="min-w-0 flex-1">
            <span className="block text-[10px] font-bold uppercase tracking-wide text-emerald-400">This sounds like a new process</span>
            <span className="block text-sm font-semibold text-slate-100">Set it up with the Process Builder</span>
            <span className="block truncate text-xs text-slate-400">Approvals, flows and page changes - I'll ask what I can't tell, rehearse it, and build it on your consent</span>
          </span>
          <span className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white">Build it →</span>
        </button>
      )}

      {match && (
        <button
          onClick={() => onPick(match.code, text)}
          className="flex w-full items-center gap-3 rounded-xl border border-sky-500/40 bg-sky-500/10 p-3 text-left transition hover:bg-sky-500/20"
        >
          <span className="text-2xl">{match.icon}</span>
          <span className="min-w-0 flex-1">
            <span className="block text-[10px] font-bold uppercase tracking-wide text-sky-400">I think you want to</span>
            <span className="block text-sm font-semibold text-slate-100">{match.label}</span>
            <span className="block truncate text-xs text-slate-400">{match.description}</span>
          </span>
          <span className="hidden text-right sm:block">
            <span className="block h-1.5 w-20 overflow-hidden rounded-full bg-slate-800">
              <span className="block h-full rounded-full bg-sky-400" style={{ width: `${aiMatch ? Math.round((assist?.confidence ?? 0) * 100) : 50}%` }} />
            </span>
            <span className="text-[10px] text-slate-500">{aiMatch ? `${Math.round((assist?.confidence ?? 0) * 100)}% sure` : "quick match"}</span>
          </span>
          <span className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-bold text-white">Continue ↵</span>
        </button>
      )}

      {assist && !match && assist.rationale && !thinking && (
        <p className="text-xs text-slate-400">{others.length ? "Not sure yet - did you mean one of these?" : humanizeCodes(assist.rationale)}</p>
      )}

      {others.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {match && <span className="self-center text-xs text-slate-500">or</span>}
          {others.map((i) => (
            <button
              key={i.code}
              onClick={() => onPick(i.code, text)}
              className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-semibold text-slate-300 hover:border-violet-500 hover:text-violet-200"
            >
              {i.icon} {i.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// "set up / automate a ... process", "X needs approval from Y", "whenever X, notify Y" - configuring, not doing
const PROCESS =
  /\b(?:set\s*up|setup|create|build|design|define|configure|automate|introduce|implement)\b[^.]{0,60}\b(?:process|workflow|approval\s+(?:rule|process|chain|matrix|flow)|automation|journey)\b|\bneeds?\s+(?:\w+\s+){0,4}?approval\s+(?:from|by)\b|\bmust\s+be\s+approved\s+by\b|\b(?:whenever|every\s+time)\b[^.]{0,80}\b(?:notify|assign|alert|email|inform)\b/i;

const STOP = new Set(["a", "an", "the", "to", "for", "of", "on", "in", "and", "or", "with", "this", "that", "it", "is", "my", "me", "i", "please", "new"]);

function words(text: string): string[] {
  return text.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length > 1 && !STOP.has(w));
}

function localMatches(text: string, intents: IntentDef[]): { intent: IntentDef; score: number }[] {
  const typed = new Set(words(text));
  if (typed.size === 0) return [];
  return intents
    .map((intent) => {
      const label = new Set(words(intent.label));
      const rest = new Set(words(`${intent.phrases.join(" ")} ${intent.examples.join(" ")} ${intent.description}`));
      let score = 0;
      for (const w of typed) score += label.has(w) ? 2 : rest.has(w) ? 0.5 : 0;
      return { intent, score };
    })
    .filter((m) => m.score >= 1)
    .sort((a, b) => b.score - a.score);
}
