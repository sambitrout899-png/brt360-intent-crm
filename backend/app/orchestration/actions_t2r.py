"""Per-intent orchestration step sequences for INT-10/11/12 (Sprint 4,
Trouble-to-Resolve & Field) - directly from HLD Section 7's "Orchestration Steps".
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import field_service, ticket_service
from app.data_intelligence import event_bus
from app.integration import party_adapter, ticket_adapter
from app.models.assurance import TroubleTicket
from app.orchestration.orchestrator import SagaStep

_ROOT_CAUSE_CONFIDENCE_THRESHOLD = 0.7


def build_int10_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-10 - Log & classify this complaint."""

    description = entities["description"]
    company_name = entities.get("company_name")
    sla_class = entities.get("sla_class", "standard")
    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        classification = ticket_service.classify(description)
        state["classification"] = classification
        return classification

    def step2() -> dict[str, Any]:
        # Stand-in correlation against known active incidents (HLD: network/
        # service state) - a regional-outage keyword is treated as a known
        # incident so this remains deterministic and testable.
        incident_ref = "INC-REGIONAL-OUTAGE-01" if "outage" in description.lower() else None
        state["incident_ref"] = incident_ref
        return {"correlated_incident_ref": incident_ref}

    def step3() -> dict[str, Any]:
        party = party_adapter.get_or_create_party(db, tenant_id, company_name) if company_name else None
        ticket = ticket_adapter.create_ticket(
            db,
            tenant_id=tenant_id,
            party_id=party.id if party else None,
            category=state["classification"]["category"],
            severity=state["classification"]["severity"],
            product_line=state["classification"]["product_line"],
            sla_class=sla_class,
            priority="unassigned",
            description=description,
        )
        ticket_adapter.correlate_incident(db, ticket.id, state["incident_ref"])
        state["ticket_id"] = ticket.id
        return {"ticket_id": ticket.id}

    def step4() -> dict[str, Any]:
        priority = ticket_service.assign_priority(sla_class, state["classification"]["severity"])
        ticket = db.get(TroubleTicket, state["ticket_id"])
        ticket.priority = priority
        db.flush()
        return {"priority": priority}

    def step5() -> dict[str, Any]:
        event_bus.publish(db, execution_id, "ticket.created", {"ticket_id": state["ticket_id"]})
        return {"ticket_id": state["ticket_id"], "routed_to_queue": True}

    return [
        SagaStep("classify_complaint", step1),
        SagaStep("correlate_known_incidents", step2),
        SagaStep("create_trouble_ticket", step3),
        SagaStep("assign_priority", step4),
        SagaStep("route_to_resolver_queue", step5),
    ]


def build_int11_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-11 - Diagnose the root cause."""

    ticket_id = entities["ticket_id"]
    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        ticket = db.get(TroubleTicket, ticket_id)
        if ticket is None:
            raise ValueError(f"Ticket {ticket_id} not found")
        state["ticket"] = ticket
        # Stand-in query across OSS/NMS network/service state.
        return {"network_state_queried": True}

    def step2() -> dict[str, Any]:
        # Stand-in correlation to topology + known fault signatures.
        signature = f"fault-signature::{state['ticket'].product_line}::{state['ticket'].severity}"
        state["signature"] = signature
        return {"fault_signature": signature}

    def step3() -> dict[str, Any]:
        ticket = state["ticket"]
        if ticket.severity == "critical":
            confidence, cause, field_dispatch = 0.6, f"Likely upstream {ticket.product_line} outage segment fault", True
        else:
            confidence, cause, field_dispatch = 0.85, f"Likely localized {ticket.product_line} configuration issue", False
        state.update(confidence=confidence, cause=cause, field_dispatch=field_dispatch)
        return {"probable_cause": cause, "confidence": confidence}

    def step4() -> dict[str, Any]:
        escalate = state["confidence"] < _ROOT_CAUSE_CONFIDENCE_THRESHOLD
        remediation = (
            "Escalated to a human specialist - confidence below threshold"
            if escalate
            else ("Dispatch a field technician" if state["field_dispatch"] else "Apply remote remediation and monitor")
        )
        ticket_adapter.record_diagnosis(
            db,
            ticket_id,
            probable_cause=state["cause"],
            confidence=state["confidence"],
            remediation_suggestion=remediation,
            field_dispatch_required=state["field_dispatch"],
            escalated_to_human=escalate,
        )
        event_bus.publish(db, execution_id, "ticket.diagnosed", {"ticket_id": ticket_id, "confidence": state["confidence"]})
        return {
            "ticket_id": ticket_id,
            "probable_cause": state["cause"],
            "confidence": state["confidence"],
            "field_dispatch_required": state["field_dispatch"],
            "escalated_to_human": escalate,
        }

    return [
        SagaStep("query_network_service_state", step1),
        SagaStep("correlate_topology_and_signatures", step2),
        SagaStep("recommend_root_cause", step3),
        SagaStep("suggest_remediation_or_escalate", step4),
    ]


def build_int12_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-12 - Assign & track the field team.

    Two modes based on entities: dispatch a new work order (default), or
    record completion for an existing one (work_order_id + action=complete).
    """

    if entities.get("work_order_id") and entities.get("action") == "complete":
        return _build_int12_completion_steps(db, execution_id, entities)
    return _build_int12_dispatch_steps(db, tenant_id, execution_id, entities)


def _build_int12_dispatch_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    ticket_id = entities.get("ticket_id")
    order_id = entities.get("order_id")
    required_skill = entities.get("required_skill", "general")
    region = entities.get("region", "ALL")
    scheduled_window = entities.get("scheduled_window", "next business day")
    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        technician = field_service.match_technician(db, tenant_id, required_skill, region)
        if technician is None:
            raise ValueError(f"No available technician with skill '{required_skill}' in region '{region}'")
        state["technician"] = technician
        return {"technician_id": technician.id, "technician_name": technician.name}

    def step2() -> dict[str, Any]:
        wo = ticket_adapter.create_work_order(
            db, tenant_id=tenant_id, ticket_id=ticket_id, order_id=order_id, scheduled_window=scheduled_window
        )
        ticket_adapter.assign_technician(db, wo.id, state["technician"].id)
        state["work_order_id"] = wo.id
        return {"work_order_id": wo.id, "scheduled_window": scheduled_window}

    def step3() -> dict[str, Any]:
        wo = ticket_adapter.update_work_order_status(db, state["work_order_id"], "en_route")
        event_bus.publish(db, execution_id, "workorder.dispatched", {"work_order_id": wo.id})
        return {"work_order_id": wo.id, "status": wo.status}

    return [
        SagaStep("match_technician", step1),
        SagaStep("dispatch_work_order", step2),
        SagaStep("track_technician_status", step3),
    ]


def _build_int12_completion_steps(db: Session, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    work_order_id = entities["work_order_id"]

    def step1() -> dict[str, Any]:
        ticket_adapter.update_work_order_status(db, work_order_id, "on_site")
        return {"work_order_id": work_order_id, "status": "on_site"}

    def step2() -> dict[str, Any]:
        evidence = ticket_adapter.record_completion(db, work_order_id, checklist="Install/repair verified", signature="field-tech")
        event_bus.publish(db, execution_id, "workorder.completed", {"work_order_id": work_order_id})
        return {"work_order_id": work_order_id, "status": "complete", "completion_evidence_id": evidence.id}

    return [
        SagaStep("arrive_on_site", step1),
        SagaStep("capture_completion_evidence_and_close", step2),
    ]


# The intents this module carries out (app/capabilities.py).
BUILDERS = {
    "INT-10": build_int10_steps,
    "INT-11": build_int11_steps,
    "INT-12": build_int12_steps,
}
