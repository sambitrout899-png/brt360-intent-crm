"""Internal adapter shaped like TMF621 (Trouble Ticket) / TMF733 (Alarm) /
Field Service dispatch - HLD 6.2. Same seam-isolation rationale as the other
*_adapter modules.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.assurance import (
    CompletionEvidence,
    IncidentCorrelation,
    RootCauseAnalysis,
    TechnicianAssignment,
    TroubleTicket,
    WorkOrder,
)


def create_ticket(
    db: Session,
    *,
    tenant_id: int,
    party_id: int | None,
    category: str,
    severity: str,
    product_line: str,
    sla_class: str,
    priority: str,
    description: str,
) -> TroubleTicket:
    ticket = TroubleTicket(
        tenant_id=tenant_id,
        party_id=party_id,
        category=category,
        severity=severity,
        product_line=product_line,
        sla_class=sla_class,
        priority=priority,
        description=description,
        status="open",
    )
    db.add(ticket)
    db.flush()
    return ticket


def correlate_incident(db: Session, ticket_id: int, correlated_incident_ref: str | None) -> IncidentCorrelation:
    correlation = IncidentCorrelation(
        ticket_id=ticket_id,
        correlated_incident_ref=correlated_incident_ref,
        matched=correlated_incident_ref is not None,
    )
    db.add(correlation)
    db.flush()
    return correlation


def record_diagnosis(
    db: Session,
    ticket_id: int,
    *,
    probable_cause: str,
    confidence: float,
    remediation_suggestion: str,
    field_dispatch_required: bool,
    escalated_to_human: bool,
) -> RootCauseAnalysis:
    analysis = RootCauseAnalysis(
        ticket_id=ticket_id,
        probable_cause=probable_cause,
        confidence=confidence,
        remediation_suggestion=remediation_suggestion,
        field_dispatch_required=field_dispatch_required,
        escalated_to_human=escalated_to_human,
    )
    db.add(analysis)
    ticket = db.get(TroubleTicket, ticket_id)
    ticket.status = "diagnosing"
    db.flush()
    return analysis


def create_work_order(
    db: Session, *, tenant_id: int, ticket_id: int | None, order_id: int | None, scheduled_window: str
) -> WorkOrder:
    wo = WorkOrder(
        tenant_id=tenant_id, ticket_id=ticket_id, order_id=order_id, scheduled_window=scheduled_window, status="scheduled"
    )
    db.add(wo)
    db.flush()
    return wo


def assign_technician(db: Session, work_order_id: int, technician_id: int) -> TechnicianAssignment:
    assignment = TechnicianAssignment(work_order_id=work_order_id, technician_id=technician_id)
    db.add(assignment)
    db.flush()
    return assignment


def update_work_order_status(db: Session, work_order_id: int, status: str) -> WorkOrder:
    wo = db.get(WorkOrder, work_order_id)
    if wo is None:
        raise ValueError(f"WorkOrder {work_order_id} not found")
    wo.status = status
    db.flush()
    return wo


def record_completion(db: Session, work_order_id: int, *, checklist: str, signature: str) -> CompletionEvidence:
    evidence = CompletionEvidence(work_order_id=work_order_id, checklist=checklist, signature=signature)
    db.add(evidence)
    wo = db.get(WorkOrder, work_order_id)
    wo.status = "complete"
    if wo.ticket_id:
        ticket = db.get(TroubleTicket, wo.ticket_id)
        ticket.status = "resolved"
    db.flush()
    return evidence
