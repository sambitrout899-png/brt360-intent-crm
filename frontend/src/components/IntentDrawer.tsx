import { useEffect, useRef, useState } from "react";
import { intentCatalog } from "../api/client";
import { EdgeHandle } from "./Resizable";
import { useStoredSize } from "./useStoredSize";
import type { IntentCatalog, IntentContext, IntentResponse } from "../api/types";
import { notifyIntentExecuted } from "../workspace";
import AskBox from "./intent/AskBox";
import IntentForm from "./intent/IntentForm";
import IntentGallery from "./intent/IntentGallery";
import IntentResult from "./intent/IntentResult";
import { useTechView } from "./intent/intentUi";

type Stage =
  | { kind: "ask" }
  | { kind: "browse" }
  | { kind: "form"; key: number; code: string; entities: Record<string, unknown>; text: string }
  | { kind: "result"; response: IntentResponse };

/** The intent drawer (Ctrl/⌘+K, or any "do this" button): the same
 * ask -> prefilled form -> result -> next step loop as the command bar,
 * sliding over whatever page you're on, aware of the record you have open. */
export default function IntentDrawer({
  open,
  initialText,
  intent,
  nonce,
  context,
  onClose,
}: {
  open: boolean;
  initialText: string;
  intent?: { code: string; entities: Record<string, unknown> } | null;
  nonce: number;
  context?: IntentContext;
  onClose: () => void;
}) {
  const [catalog, setCatalog] = useState<IntentCatalog | null>(null);
  const [stage, setStage] = useState<Stage>({ kind: "ask" });
  const [useCtx, setUseCtx] = useState(true);
  const [techView] = useTechView();
  // the drawer's width - drag its left edge (remembered)
  const panel = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useStoredSize("intentDrawer", null);

  const activeContext = useCtx && context?.entity_type ? context : context?.page ? { page: context.page } : undefined;

  // A new request (nonce) replaces whatever was in the drawer.
  useEffect(() => {
    setUseCtx(true);
    setStage(intent ? { kind: "form", key: Date.now(), code: intent.code, entities: intent.entities, text: "" } : { kind: "ask" });
    // intent is read with the nonce that announced it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce]);

  useEffect(() => {
    if (open && !catalog) intentCatalog().then(setCatalog).catch(() => setCatalog({ intents: [], categories: [] }));
  }, [open, catalog]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const openForm = (code: string, entities: Record<string, unknown> = {}, text = "") =>
    setStage({ kind: "form", key: Date.now(), code, entities, text });

  const intents = catalog?.intents ?? [];

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/60 backdrop-blur-[1px]" onClick={onClose}>
      <div
        ref={panel}
        style={width ? { width } : undefined}
        className={`relative flex h-full max-w-[calc(100vw-40px)] flex-col border-l border-slate-700 bg-slate-900 shadow-2xl ${width ? "" : "w-full sm:w-[576px]"}`}
        onClick={(e) => e.stopPropagation()}
      >
        <EdgeHandle panel={panel} width={width} onWidth={setWidth} />
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-5">
        <div className="flex items-center gap-2">
          {stage.kind !== "ask" && (
            <button onClick={() => setStage({ kind: "ask" })} className="rounded-md px-2 py-1 text-sm text-slate-400 hover:bg-slate-800 hover:text-slate-200">
              ←
            </button>
          )}
          <h2 className="text-lg font-bold text-slate-100">⚡ What do you want to do?</h2>
          <button onClick={onClose} className="ml-auto rounded-md px-2 py-1 text-sm text-slate-400 hover:bg-slate-800 hover:text-slate-200">
            ✕
          </button>
        </div>

        {context?.entity_type && context.entity_id && (
          <div className="flex items-center gap-2 text-xs">
            <span className={`rounded-md border px-2 py-0.5 ${useCtx ? "border-sky-500/40 bg-sky-500/10 text-sky-300" : "border-slate-700 text-slate-500 line-through"}`}>
              📌 “this” = {context.label ?? `${context.entity_type} #${context.entity_id}`}
            </span>
            <button onClick={() => setUseCtx((u) => !u)} className="text-slate-500 hover:text-slate-300">
              {useCtx ? "ignore" : "use"}
            </button>
          </div>
        )}

        {stage.kind === "ask" && (
          <>
            <AskBox key={nonce} intents={intents} context={activeContext} initialText={initialText} compact onPick={(code, text) => openForm(code, {}, text)} />
            <button onClick={() => setStage({ kind: "browse" })} className="self-start text-xs font-semibold text-sky-400 hover:text-sky-300">
              Browse all {intents.length} actions →
            </button>
          </>
        )}

        {stage.kind === "browse" && catalog && (
          <IntentGallery intents={intents} categories={catalog.categories} processes={catalog.processes} compact techView={techView} onPick={(code) => openForm(code)} />
        )}

        {stage.kind === "form" && (
          <IntentForm
            key={stage.key}
            code={stage.code}
            initialEntities={stage.entities}
            text={stage.text}
            context={activeContext}
            compact
            onDone={(response) => {
              setStage({ kind: "result", response });
              if (response.status === "executed") notifyIntentExecuted();
            }}
            onSwitch={(code, entities) => openForm(code, entities)}
            onCancel={() => setStage({ kind: "ask" })}
          />
        )}

        {stage.kind === "result" && (
          <>
            <IntentResult response={stage.response} compact resizable techView={techView} onNext={(code, entities) => openForm(code, entities)} />
            <button onClick={() => setStage({ kind: "ask" })} className="self-start rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-300 hover:border-slate-500">
              Do something else
            </button>
          </>
        )}
        </div>
      </div>
    </div>
  );
}
