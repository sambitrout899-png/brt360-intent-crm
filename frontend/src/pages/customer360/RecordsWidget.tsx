import { useCallback, useEffect, useState, type ReactNode } from "react";
import { partyRecordActions } from "../../api/client";
import type { EntityType, Party360, RecordButton } from "../../api/types";
import { useIntentRefresh, useWorkspace } from "../../workspace";
import { StatusChip } from "./charts";
import { formatValue } from "./format";
import InstalledAssets from "./InstalledAssets";
import { QuoteDocumentChips } from "../../components/QuoteDocuments";

/** The record-by-record workbench (the original Customer 360) as a widget:
 * leads, deals, quotes, accounts and tickets, each with the one-click
 * intents that move it forward. */

function RecordLink({ entityType, id }: { entityType: EntityType; id: number }) {
  const { openRecord } = useWorkspace();
  return (
    <button onClick={() => openRecord(entityType, id)} className="font-mono text-xs hover:underline" style={{ color: "var(--c-accent)" }}>
      #{id}
    </button>
  );
}

function Act({ label, onClick, disabled, title }: { label: string; onClick: () => void; disabled?: boolean; title?: string }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className="rounded-md border px-2 py-0.5 text-[11px] font-bold transition hover:brightness-125 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:brightness-100"
      style={{ borderColor: "color-mix(in srgb, var(--c-accent) 45%, transparent)", color: "var(--c-accent)", background: "color-mix(in srgb, var(--c-accent) 10%, transparent)" }}
    >
      {label}
    </button>
  );
}

function Row({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg px-3 py-2 text-sm" style={{ background: "color-mix(in srgb, var(--c-bg) 70%, transparent)" }}>
      {children}
    </div>
  );
}

function Group({ title, count, children, empty }: { title: string; count: number; children: ReactNode; empty: string }) {
  return (
    <div className="space-y-1.5">
      <h4 className="text-[11px] font-bold uppercase tracking-wider" style={{ color: "var(--c-muted)" }}>
        {title} <span className="opacity-60">({count})</span>
      </h4>
      {count === 0 ? (
        <p className="text-xs" style={{ color: "var(--c-muted)" }}>
          {empty}
        </p>
      ) : (
        children
      )}
    </div>
  );
}

const T = ({ children }: { children: ReactNode }) => <span style={{ color: "var(--c-text)" }}>{children}</span>;

/** The record's buttons - worked out on the server from its current state
 * (the same list the record panel shows), so a step already done never
 * offers its button again. */
function Buttons({ list }: { list: RecordButton[] | undefined }) {
  const { runIntent } = useWorkspace();
  if (!list || list.length === 0) return null;
  return (
    <span className="ml-auto flex flex-wrap gap-1.5">
      {list.map((b) => (
        <Act key={`${b.intent}-${b.label}`} label={b.label} onClick={() => runIntent(b.intent, b.entities)} disabled={b.disabled} title={b.reason} />
      ))}
    </span>
  );
}

function useRecordButtons(partyId: number | undefined) {
  const [buttons, setButtons] = useState<Record<string, RecordButton[]>>({});
  const load = useCallback(() => {
    if (partyId) partyRecordActions(partyId).then(setButtons).catch(() => setButtons({}));
  }, [partyId]);
  useEffect(load, [load]);
  useIntentRefresh(load);
  return buttons;
}

export default function RecordsWidget({
  detail,
  sections,
  onRequestIntent,
}: {
  detail: Party360 | null;
  sections: string[];
  onRequestIntent: (text: string) => void;
}) {
  const buttons = useRecordButtons(detail?.party.id);
  const { canRun } = useWorkspace();
  if (!detail) return <p className="c360-pulse text-xs" style={{ color: "var(--c-muted)" }}>Loading records…</p>;
  const ask = onRequestIntent;
  return (
    <div className="space-y-4">
      {sections.includes("identity") && (
        <Group title="Identity" count={detail.identity_verifications.length} empty="Not verified yet.">
          {detail.identity_verifications.map((v) => (
            <Row key={v.id}>
              <T>{v.provider_name}</T>
              <span className="text-xs" style={{ color: "var(--c-muted)" }}>
                {v.id_type} ····{v.id_last4}
              </span>
              <span className="ml-auto">
                <StatusChip value={v.status} />
              </span>
            </Row>
          ))}
        </Group>
      )}
      {sections.includes("identity") && canRun("INT-13") && detail.identity_verifications.at(-1)?.status !== "verified" && (
        <Act label="Verify identity" onClick={() => ask(`Verify customer identity. party_id: ${detail.party.id}, id_number: `)} />
      )}

      {sections.includes("leads") && (
        <Group title="Leads" count={detail.leads.length} empty="No leads.">
          {detail.leads.map((l) => (
            <Row key={l.id}>
              <RecordLink entityType="lead" id={l.id} />
              <T>{l.interested_offering_name || "no product yet"}</T>
              <StatusChip value={l.status} />
              <Buttons list={buttons[`lead:${l.id}`]} />
            </Row>
          ))}
        </Group>
      )}

      {sections.includes("opportunities") && (
        <Group title="Opportunities" count={detail.opportunities.length} empty="No opportunities.">
          {detail.opportunities.map((o) => (
            <Row key={o.id}>
              <RecordLink entityType="opportunity" id={o.id} />
              <T>{o.name}</T>
              <StatusChip value={o.stage} />
              {o.feasibility_assessments.length > 0 && (
                <span className="text-[11px]" style={{ color: "var(--c-muted)" }}>
                  feasibility: {o.feasibility_assessments[0].outcome}
                </span>
              )}
              <Buttons list={buttons[`opportunity:${o.id}`]} />
            </Row>
          ))}
        </Group>
      )}

      {sections.includes("quotes") && (
        <Group title="Quotes" count={detail.quotes.length} empty="No quotes.">
          {detail.quotes.map((q) => (
            <Row key={q.id}>
              <RecordLink entityType="quote" id={q.id} />
              <T>{formatValue(q.total_price, "money")}</T>
              <StatusChip value={q.status} />
              {q.order_id ? (
                <span className="text-[11px]" style={{ color: "var(--c-muted)" }}>
                  → order #{q.order_id}
                </span>
              ) : null}
              <QuoteDocumentChips quoteId={q.id} documents={q.documents} />
              <Buttons list={buttons[`quote:${q.id}`]} />
            </Row>
          ))}
        </Group>
      )}

      {sections.includes("billing") && detail.billing_account && (
        <Group title="Billing account" count={1} empty="">
          <Row>
            <StatusChip value={detail.billing_account.status} />
            <T>{detail.billing_account.rate_plan || "—"}</T>
            <span className="ml-auto font-semibold" style={{ color: "var(--c-text)" }}>
              Balance {formatValue(detail.billing_account.balance, "money")}
            </span>
          </Row>
        </Group>
      )}

      {sections.includes("service_accounts") && <InstalledAssets key={detail.party.id} accounts={detail.service_accounts} />}

      {sections.includes("tickets") && (
        <Group title="Tickets" count={detail.tickets.length} empty="No tickets.">
          {detail.tickets.map((t) => (
            <Row key={t.id}>
              <span className="font-mono text-xs" style={{ color: "var(--c-muted)" }}>
                #{t.id}
              </span>
              <T>{t.category}</T>
              <StatusChip value={t.priority} />
              <StatusChip value={t.status} />
              {!t.root_cause && t.status !== "resolved" && canRun("INT-11") && (
                <span className="ml-auto">
                  <Act label="Diagnose" onClick={() => ask(`Diagnose the root cause. ticket_id: ${t.id}`)} />
                </span>
              )}
            </Row>
          ))}
        </Group>
      )}
      {sections.includes("tickets") && canRun("INT-10") && (
        <Act label="+ Log a complaint" onClick={() => ask(`Log a complaint for ${detail.party.name}: `)} />
      )}
    </div>
  );
}
