# BRT 360 — Sprints 2–4 Addendum (INT-04 through INT-12)

Extends `SPRINT1_IMPLEMENTATION_PLAN.md`'s architecture to all remaining HLD
intents — same 6-layer pattern, same deterministic-steps-with-LLM-for-NLU
design decision, no structural changes to Sprint 1's foundation.

## New modules per sprint

| Sprint | Intents | crm_core | integration | orchestration |
|---|---|---|---|---|
| 2 (L2Q) | INT-04/05/06 | `lead_service.py`, `approval_service.py` | `party_adapter.py` | `actions_l2q.py` |
| 3 (O2A) | INT-07/08/09 | `order_service.py` | `order_adapter.py` | `actions_o2a.py` |
| 4 (T2R) | INT-10/11/12 | `ticket_service.py`, `field_service.py` | `ticket_adapter.py` | `actions_t2r.py` |

New data model: `models/crm.py` (Party, TerritoryRule, Lead, Opportunity,
Quote, QuoteItem, ApprovalRequest, ApprovalDecision, FeasibilityResult),
`models/orders.py` (Order, OrderItem, ServiceOrder, Resource, BillingAccount,
BillingAdjustment), `models/assurance.py` (Technician, TroubleTicket,
IncidentCorrelation, RootCauseAnalysis, WorkOrder, TechnicianAssignment,
CompletionEvidence).

## Notable design calls

- **INT-05's discount-justification threshold** reuses `approval_service.DISCOUNT_MATRIX[0][0]`
  (the "Auto-Approved" ceiling) rather than a separate magic number, so the
  two rules (when a justification is required, and which tier approves it)
  can never drift apart.
- **INT-06 models the approver's decision as its own intent call** ("Approve
  quote 123 - reasoning: ...") rather than a separate approval-queue UI —
  consistent with the HLD's intent-first philosophy: the approver states the
  outcome, the system assembles context (deal size, discount band) and
  records it.
- **INT-08 does not create a change-order chain** the way INT-02 versions a
  catalog entry. It mutates the order's price in place and records the delta
  as a `BillingAdjustment` — simpler, and still fully auditable via the
  adjustment row + `IntentExecutionLog`.
- **INT-12 is one step-builder with two modes**, branching on whether
  `work_order_id` + `action: complete` is present (completion) or not
  (dispatch) — avoids a 13th intent type for what the HLD describes as two
  steps (3 and 4) of the same orchestration.
- **INT-11's confidence threshold (0.7)** for escalating to a human is a
  named constant in `actions_t2r.py`, directly implementing HLD 4.6/8.5's
  "escalate whenever confidence falls below the defined threshold."
- Enrichment, firmographic scoring, network/OSS state, and fault signatures
  are all deterministic stand-ins (hash- or keyword-based) — same philosophy
  as Sprint 1's integration adapters: testable without live vendor systems,
  swappable behind the same function signatures later.

## Cross-intent chain (what a real walkthrough looks like)

```
INT-01 Launch bundle
  -> INT-04 Qualify lead            (opportunity_id)
    -> INT-05 Configure & quote      (quote_id, using the launched offering)
      -> INT-06 Approve              (quote.status -> approved)
        -> INT-07 Activate order     (order_id, billing + resources provisioned)
          -> INT-08 Change service   (price delta + billing adjustment)
          -> INT-09 Suspend/Cancel   (resource release + termination fee)

INT-10 Log complaint -> INT-11 Diagnose -> INT-12 Dispatch -> INT-12 Complete
```

All 19 backend tests (`pytest -q`) exercise this chain end-to-end, not just
each intent in isolation — the dependency between intents is itself part of
what's being verified.
