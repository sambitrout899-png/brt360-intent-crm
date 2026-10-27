import { useRef, useState } from "react";
import type { EntityType, IntentResponse, OutcomeRecord } from "../../api/types";
import DocumentPreview from "../DocumentPreview";
import { SplitHandle } from "../Resizable";
import { useStoredSize } from "../useStoredSize";
import { useWorkspace } from "../../workspace";
import { humanizeCodes } from "../../intentLabels";
import { formatValue, prettyState } from "./intentUi";

const STATUS: Record<string, { title: string; cls: string; icon: string }> = {
  executed: { title: "Done", cls: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300", icon: "✓" },
  needs_review: { title: "Needs your input", cls: "border-amber-500/40 bg-amber-500/10 text-amber-300", icon: "…" },
  failed: { title: "Not done", cls: "border-red-500/40 bg-red-500/10 text-red-300", icon: "✕" },
  verification_failed: { title: "Done, but could not be verified", cls: "border-red-500/40 bg-red-500/10 text-red-300", icon: "!" },
};

const CHANGE_BADGE: Record<string, string> = {
  created: "bg-emerald-500/15 text-emerald-300",
  updated: "bg-sky-500/15 text-sky-300",
  involved: "bg-slate-700/60 text-slate-300",
};

function RecordCard({ record, compact }: { record: OutcomeRecord; compact: boolean }) {
  const { openRecord } = useWorkspace();
  const [all, setAll] = useState(false);
  const shown = all ? record.fields : record.fields.slice(0, compact ? 4 : 6);
  return (
    <div className="flex flex-col rounded-xl border border-slate-700 bg-slate-900/80 p-3">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-bold uppercase tracking-wide text-slate-500">
            {record.type_label} #{record.entity_id}
            {record.brt_id && <span className="ml-2 rounded bg-slate-800 px-1.5 py-0.5 font-mono normal-case tracking-normal text-slate-400" title="BRT_ID">{record.brt_id}</span>}
          </p>
          <p className="truncate text-sm font-semibold text-slate-100">{record.title}</p>
        </div>
        <span className={`rounded-md px-2 py-0.5 text-[10px] font-bold uppercase ${CHANGE_BADGE[record.change]}`}>{record.change}</span>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
        {record.from_state && record.to_state ? (
          <>
            <span className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-400 line-through decoration-slate-600">{prettyState(record.from_state)}</span>
            <span className="text-slate-500">→</span>
            <span className="rounded bg-sky-500/15 px-1.5 py-0.5 font-semibold text-sky-300">{prettyState(record.to_state)}</span>
          </>
        ) : (
          record.state && <span className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-300">{prettyState(record.state)}</span>
        )}
      </div>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
        {shown.map((f) => (
          <div key={f.key} className="contents">
            <dt className="text-slate-500">{f.label}</dt>
            <dd className="truncate text-slate-200" title={formatValue(f.value)}>
              {formatValue(f.value)}
            </dd>
          </div>
        ))}
      </dl>
      <div className="mt-auto flex gap-3 pt-2 text-xs">
        {record.fields.length > shown.length && (
          <button onClick={() => setAll(true)} className="text-slate-400 hover:text-slate-200">
            +{record.fields.length - shown.length} more
          </button>
        )}
        {record.openable && (
          <button onClick={() => openRecord(record.entity_type as EntityType, record.entity_id)} className="ml-auto font-semibold text-sky-400 hover:text-sky-300">
            Open →
          </button>
        )}
      </div>
    </div>
  );
}

/** What happened, for people: status, a one-line summary, every record
 * the action created or changed (with its data and state change), and
 * the recommended next steps - prefilled. Technical details on demand. */
export default function IntentResult({
  response,
  compact = false,
  techView = false,
  resizable = false,
  onNext,
}: {
  response: IntentResponse;
  compact?: boolean;
  techView?: boolean;
  /** shown in a window: the outcome and "What's next" can be resized against each other */
  resizable?: boolean;
  onNext?: (code: string, entities: Record<string, unknown>) => void;
}) {
  const { runIntent } = useWorkspace();
  const outcomePane = useRef<HTMLDivElement>(null);
  const [outcomeHeight, setOutcomeHeight] = useStoredSize("intentOutcome", null);
  const [showTech, setShowTech] = useState(false);
  const status = STATUS[response.status] ?? STATUS.failed;
  const records = response.records ?? [];
  const next = response.next_actions ?? [];
  const flows = (response.result?.flows_run as { flow_name: string }[] | undefined) ?? [];
  const skippedFlows = (response.result?.flows_skipped as { flow_name: string; reason: string }[] | undefined) ?? [];
  // automations that failed and were undone under their "continue" policy - the action itself went through
  const failedFlows = (response.result?.flows_failed as { flow_name: string; error?: { message: string } }[] | undefined) ?? [];
  const error = response.status === "failed" ? response.error ?? null : null;
  const goNext = onNext ?? runIntent;
  type ConfigurationRequest = { intent: string; entities: Record<string, unknown> };
  const configurationRequests: ConfigurationRequest[] = [];
  if (response.status === "executed") {
    if (response.result?.configuration_request) configurationRequests.push(response.result.configuration_request as ConfigurationRequest);
    const runs = (response.result?.flows_run ?? []) as { trace?: { output?: { configuration_request?: ConfigurationRequest } }[] }[];
    for (const run of runs) for (const step of run.trace ?? []) {
      if (step.output?.configuration_request) configurationRequests.push(step.output.configuration_request);
    }
  }
  const headline =
    response.status === "executed"
      ? response.summary || `${response.intent_label ?? "Action"} - done.`
      : humanizeCodes(response.summary || response.rationale);

  // a PDF the action generated and attached to the quote (proposal, agreement) - shown right here
  const out = (response.result ?? {}) as Record<string, unknown>;
  const attached: { id: number; quoteId: number; label: string }[] = [];
  if (response.status === "executed" && Number(out.quote_id) > 0) {
    if (Number(out.document_id) > 0) attached.push({ id: Number(out.document_id), quoteId: Number(out.quote_id), label: "Proposal" });
    if (Number(out.agreement_document_id) > 0) attached.push({ id: Number(out.agreement_document_id), quoteId: Number(out.quote_id), label: "Agreement" });
  }

  // in a window (the drawer): the outcome and the suggested next steps are two panes - drag the bar between them
  const split = resizable && next.length > 0;

  return (
    <div className="space-y-4">
      <div
        ref={outcomePane}
        style={split && outcomeHeight ? { height: outcomeHeight } : undefined}
        className={split ? "space-y-4 overflow-y-auto pr-1" : "contents"}
      >
      <div className={`flex items-start gap-3 rounded-2xl border p-4 ${status.cls}`}>
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-black/20 text-lg font-bold">{response.intent_icon ?? status.icon}</span>
        <div className="min-w-0">
          <p className="text-sm font-bold">
            {configurationRequests.length ? "Needs configuration" : status.title}
            {response.intent_label ? ` · ${response.intent_label}` : ""}
          </p>
          <p className="text-sm text-slate-200/90">{headline}</p>
          {response.status !== "executed" && response.summary && response.rationale && techView && (
            <p className="mt-1 text-xs text-slate-300/70">{humanizeCodes(response.rationale)}</p>
          )}
          {error && (error.flow || error.node) && (
            <p className="mt-1 text-xs text-slate-300/80">
              Stopped in {error.flow ? `rule “${error.flow}”` : "the action"}
              {error.node && error.node_type !== "throw" ? ` at step “${error.node}”` : ""}
            </p>
          )}
          {error && (techView || error.ref) && (
            <p className="mt-1 font-mono text-[11px] text-slate-400">
              {error.code} · {error.category}
              {error.ref ? ` · reference ${error.ref}` : ""}
            </p>
          )}
          {(error?.flows_run ?? []).length > 0 && (
            <p className="mt-1 text-xs text-fuchsia-300">
              ⚙ Handled by: {error!.flows_run!.map((f) => `${f.flow_name}${f.outcome === "failed" ? " (it failed too)" : ""}`).join(", ")}
            </p>
          )}
        </div>
      </div>

      {configurationRequests.map((request, index) => (
        <div key={index} className="rounded-xl border border-sky-500/40 bg-sky-500/10 p-4 text-sm">
          <p>No order has been created. Configure pricing, technical parameters and the order line discount, then submit for provisioning.</p>
          <button className="mt-3 rounded-lg bg-sky-600 px-4 py-2 font-bold text-white" onClick={() => goNext(request.intent, request.entities)}>Configure order →</button>
        </div>
      ))}
      {records.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">Records</p>
          <div className={`grid gap-3 ${compact ? "" : "sm:grid-cols-2 lg:grid-cols-3"}`}>
            {records.map((r) => (
              <RecordCard key={`${r.entity_type}-${r.entity_id}`} record={r} compact={compact} />
            ))}
          </div>
        </div>
      )}

      {attached.map((d) => (
        <DocumentPreview key={d.id} quoteId={d.quoteId} documentId={d.id} height={compact ? 360 : 520}
          title={`${d.label} - generated and attached to quote #${d.quoteId}`} />
      ))}

      {flows.length > 0 && <p className="text-xs text-fuchsia-300">⚙ Your business rules also ran: {flows.map((f) => f.flow_name).join(", ")}</p>}
      {skippedFlows.length > 0 && <p className="text-xs text-slate-400">Rules skipped: {skippedFlows.map((flow) => `${flow.flow_name} (${flow.reason})`).join("; ")}</p>}
      {failedFlows.length > 0 && (
        <p role="alert" className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
          ⚠ Done - but these rules failed and their changes were undone: {failedFlows.map((f) => `${f.flow_name} (${f.error?.message ?? "failed"})`).join("; ")}
        </p>
      )}
      </div>

      {split && <SplitHandle pane={outcomePane} onHeight={setOutcomeHeight} />}

      {next.length > 0 && (
        <div className={split ? "!mt-1" : ""}>
          <p className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">What's next</p>
          <div className={`grid gap-2 ${compact ? "" : "sm:grid-cols-3"}`}>
            {next.map((n) => (
              <button
                key={n.code}
                onClick={() => goNext(n.code, n.entities)}
                className="group flex items-start gap-3 rounded-xl border border-slate-700 bg-slate-900/70 p-3 text-left transition hover:border-sky-500 hover:bg-slate-900"
              >
                <span className="text-xl">{n.icon}</span>
                <span className="min-w-0">
                  <span className="block text-sm font-semibold text-slate-100 group-hover:text-sky-300">{n.label}</span>
                  <span className="block text-xs text-slate-400">{n.reason}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {techView && response.status === "executed" && (
        <div className="rounded-xl border border-slate-800">
          <button onClick={() => setShowTech((s) => !s)} className="flex w-full items-center justify-between px-3 py-2 text-xs font-semibold text-slate-500 hover:text-slate-300">
            <span>Why it ran this way</span>
            <span>{showTech ? "▴" : "▾"}</span>
          </button>
          {showTech && <p className="border-t border-slate-800 p-3 text-xs text-slate-400">{humanizeCodes(response.rationale)}</p>}
        </div>
      )}
    </div>
  );
}
