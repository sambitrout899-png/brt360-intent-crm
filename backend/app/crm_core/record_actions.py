"""The buttons a record shows, wherever it is shown (Customer 360, the
record panel, lists) - one source, so they always match the record's
current state.

They come from the tenant's state model (the transitions available from
the record's current state, each run through the intent that owns it, or
"Move to another stage" when none does) plus the process steps that are
not state changes (proposal, PO, order) - each only in the state where it
applies. Intents the user may not run are left out, and so is an approval
decision the user can't make.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import approval_service, lifecycle_service
from app.models.auth import User
from app.models.crm import Quote, QuoteStatus

# action -> extra entities the owning intent needs to make that exact move
_ACTION_ENTITIES: dict[str, dict[str, Any]] = {
    "approve": {"decision": "approve"},
    "submit": {"decision": "approve"},  # a draft quote's approval call submits it into the matrix
    "reject": {"decision": "reject"},
    "pass_feasibility": {"decision": "feasible"},
    "fail_feasibility": {"decision": "not_feasible"},
    "suspend": {"action": "suspend"},
    "cancel": {"action": "cancel"},
    "terminate": {"action": "terminate"},
}

# post-order changes offered on a live service (abo_service.OPERATIONS), modify first - "Change asset #N"
_LIVE_OPS = ("modify", "product_change", "promotion_change", "suspend", "relocate", "customer_transfer", "number_portability",
             "port_out", "terminate")

# moves whose intent decides an approval - shown only to someone who may decide it
_APPROVAL_INTENTS = {"INT-06", "INT-17", "INT-25", "INT-36"}


def _label(action: str, to_label: str) -> str:
    words = action.replace("_", " ")
    return f"{words[:1].upper()}{words[1:]} → {to_label}"


def _process_steps(db: Session, entity_type: str, obj) -> list[dict[str, Any]]:
    """Steps that move a deal on without changing the record's own state."""
    out: list[dict[str, Any]] = []
    if entity_type == "quote" and obj.status == QuoteStatus.approved:
        from app.models.fulfillment import Proposal, ProposalStatus
        from app.models.orders import Order

        if db.query(Order.id).filter(Order.quote_id == obj.id).first() is not None:
            return out  # already ordered
        from app.crm_core import quote_documents

        proposal = db.query(Proposal).filter(Proposal.quote_id == obj.id).order_by(Proposal.id.desc()).first()
        po = obj.purchase_order
        # B2B Sales-to-Order: proposal PDF -> sent to the customer -> their PO -> Match all (Sales Ops Head).
        # A tenant that switched the proposal off (Admin -> Quote Documents) goes from approval to the PO.
        with_proposal = quote_documents.enabled(db, obj.tenant_id, "proposal")
        drafted = quote_documents.latest(db, obj.id, "proposal", source="generated")
        sent_doc = quote_documents.from_ref(db, obj.tenant_id, proposal.document_ref) if proposal is not None else None
        unsent = drafted is not None and (sent_doc is None or drafted.id > sent_doc.id)
        if po is None and (proposal is None or proposal.status == ProposalStatus.customer_rejected):
            if with_proposal and not unsent:
                out.append({"label": "Generate proposal" if proposal is None else "Generate a new proposal", "intent": "INT-109",
                            "entities": {"quote_id": obj.id}})
            out.append({"label": "Send proposal to customer" if with_proposal else "Generate proposal", "intent": "INT-22",
                        "entities": {"quote_id": obj.id}})
            if with_proposal and unsent:
                out.append({"label": "Regenerate proposal", "intent": "INT-109", "entities": {"quote_id": obj.id}})
            out.append({"label": "Attach the customer's PO", "intent": "INT-39", "entities": {"quote_id": obj.id}})
        elif proposal is not None and proposal.status == ProposalStatus.sent:
            out.append({"label": "Attach the customer's PO", "intent": "INT-39", "entities": {"quote_id": obj.id}})
            out.append({"label": "Record customer decision", "intent": "INT-23",
                        "entities": {"proposal_id": proposal.id, "decision": "approve"}})
        if po is None and proposal is not None and proposal.status == ProposalStatus.customer_approved:
            out.append({"label": "Attach the customer's PO", "intent": "INT-39", "entities": {"quote_id": obj.id}})
        if proposal is not None and proposal.status == ProposalStatus.customer_approved:
            out.append({"label": "Generate order", "intent": "INT-24", "entities": {"quote_id": obj.id}})
        if po is not None and po.matched_at is None:
            out.append({"label": "Match all & create the order", "intent": "INT-40", "entities": {"quote_id": obj.id}})
        out.append({"label": "Activate directly", "intent": "INT-07", "entities": {"quote_id": obj.id}})
    if entity_type == "quote" and obj.status == QuoteStatus.draft:
        from app.crm_core import quote_override_service

        out.append({"label": "Override price", "intent": "INT-116", "entities": {"quote_id": obj.id}})
        if quote_override_service.active(db, obj.id) is not None:
            out.append({"label": "Back to the system price", "intent": "INT-116", "entities": {"quote_id": obj.id, "remove_override": True}})
    if entity_type == "order":
        status = obj.status.value
        if status in ("created", "activating"):
            out.append({"label": "Revise", "intent": "INT-21", "entities": {"order_id": obj.id}})
        if status == "suspended":
            out.append({"label": "Resume", "intent": "INT-70", "entities": {"order_id": obj.id}})
        if status == "pending_cancellation":
            out.append({"label": "Retain the customer", "intent": "INT-09", "entities": {"order_id": obj.id, "decision": "retain"}})
    if entity_type == "lead" and obj.status.value == "pending_qualification":
        out.append({"label": "Add product", "intent": "INT-16", "entities": {"lead_id": obj.id}})
    if entity_type == "opportunity" and obj.stage.value in ("draft", "qualified", "feasibility_failed", "configured"):
        out.append({"label": "Configure the product", "intent": "INT-37",
                    "entities": {"opportunity_id": obj.id, "offering_name": obj.offering_name}})
    return out


def _feasible_by_map(db: Session, tenant_id: int, user: User, opp, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A feasible opportunity's buttons when the Functional Map in use re-wired
    what follows Check feasibility: its steps are offered, and the quote only
    if the map still leads there."""
    from app.intent_engine import intent_registry
    from app.orchestration import flow_engine, function_map

    steps = function_map.rewired_steps(db, tenant_id, "INT-18")
    if steps is None:
        return actions
    codes = [c for c, _ in steps]
    out = [a for a in actions if a.get("intent") != "INT-05" or "INT-05" in codes]
    party = flow_engine.customer_of(db, "opportunity", opp)
    for code in codes:
        if any(a.get("intent") == code for a in out) or intent_registry.availability(db, tenant_id, user, code):
            continue
        out.append({"label": intent_registry.label_of(db, tenant_id, code) or code, "intent": code,
                    "entities": {"opportunity_id": opp.id, "offering_name": opp.offering_name, **({"party_id": party.id} if party else {})}})
    return out


def for_record(db: Session, tenant_id: int, user: User, entity_type: str, obj) -> list[dict[str, Any]]:
    from app.intent_engine import intent_registry
    if entity_type == "order" and obj.status.value == "completed":
        from app.models.fulfillment import Asset
        if intent_registry.availability(db, tenant_id, user, "INT-107"):
            return []
        from app.crm_core.abo_service import LABELS
        out = []
        for asset in db.query(Asset).filter(Asset.tenant_id == tenant_id, Asset.order_item_id.in_([i.id for i in obj.items]),
                                            Asset.superseded_by_asset_id.is_(None), Asset.status.in_(("active", "suspended"))):
            # every post-order change the installed service allows now
            ops = _LIVE_OPS if asset.status.value == "active" else ("resume", "terminate")
            for op in ops:
                label = f"Change asset #{asset.id}" if op == "modify" else f"{LABELS[op]} - asset #{asset.id}"
                out.append({"label": label, "intent": "INT-107", "entities": {"asset_id": asset.id, "asset_operation": op}})
            if asset.status.value == "active" and not intent_registry.availability(db, tenant_id, user, "INT-114"):
                out.append({"label": f"Quote a change - asset #{asset.id}", "intent": "INT-114",
                            "entities": {"asset_id": asset.id, "asset_operation": "modify"}})
            if not intent_registry.availability(db, tenant_id, user, "INT-113"):
                out.append({"label": f"History - asset #{asset.id}", "intent": "INT-113", "entities": {"asset_id": asset.id}})
        return out

    role = user.role.value
    actions: list[dict[str, Any]] = []
    key = f"{entity_type}_id"
    for t in lifecycle_service.available_transitions(db, tenant_id, entity_type, obj, role, user.id):
        if t["intent_type"]:
            extra = {"offering_name": obj.offering_name} if entity_type == "opportunity" and t["intent_type"] in ("INT-05", "INT-37") else {}
            actions.append({"label": _label(t["action"], t["to_label"]), "intent": t["intent_type"], "to_state": t["to_state"],
                            "entities": {key: obj.id, **extra, **_ACTION_ENTITIES.get(t["action"], {})}})
        else:
            actions.append({"label": _label(t["action"], t["to_label"]), "intent": "INT-31", "to_state": t["to_state"],
                            "entities": {"entity_type": entity_type, "entity_id": obj.id, "action": t["action"]}})
    actions += _process_steps(db, entity_type, obj)
    if entity_type == "opportunity" and obj.stage.value == "feasible":
        actions = _feasible_by_map(db, tenant_id, user, obj, actions)
    if entity_type == "order" and obj.status.value == "activating" and role == "admin":
        from app.models.abo import AssetChange
        change = db.query(AssetChange).filter(AssetChange.order_id == obj.id, AssetChange.status == "submitted").first()
        if change is not None:  # a change order back from orchestration - confirming it updates the asset
            actions.append({"label": "Confirm fulfillment (updates the asset)", "intent": "INT-108", "entities": {"order_id": obj.id}})
    if entity_type == "order" and obj.status.value == "activating":
        from app.crm_core import abo_service
        if abo_service.change_of_order(db, tenant_id, obj) is not None:  # an asset change order still in flight
            for code, label in (("INT-111", "Revise the change"), ("INT-110", "Cancel the change")):
                if not intent_registry.availability(db, tenant_id, user, code):
                    actions.append({"label": label, "intent": code, "entities": {"order_id": obj.id}})

    # an approval waiting on this record: its buttons only for someone its
    # position lets decide it - whatever their roles (see waiting_on)
    waiting = approval_service.waiting_on(db, tenant_id, entity_type, obj.id, user.id)
    if waiting is not None:
        decider = waiting["can_decide"]
        actions = [a for a in actions if a["intent"] not in _APPROVAL_INTENTS or (decider and a["entities"].get("decision"))]
        if decider:
            offered = {a["entities"].get("decision") for a in actions if a["intent"] in _APPROVAL_INTENTS}
            for decision in ("approve", "reject"):
                if decision in offered:
                    continue
                actions.append({"label": f"{decision.title()} ({waiting['waiting_for']})", "intent": "INT-36",
                                "entities": {"entity_type": entity_type, "entity_id": obj.id, "decision": decision}})
    elif entity_type == "quote" and obj.status == QuoteStatus.draft:
        # a draft quote's "approve" submits it into the matrix - say so
        for a in actions:
            if a["intent"] == "INT-06" and a["entities"].get("decision") == "approve":
                a["label"] = "Submit for approval"

    seen: set[tuple] = set()
    out = []
    for a in actions:
        sig = (a["intent"], tuple(sorted((k, str(v)) for k, v in a["entities"].items())))
        from app.orchestration.process_journey import record_segment
        meta = intent_registry.entry(db, tenant_id, a["intent"]) or {}
        actual_segment = record_segment(obj)
        if meta.get("segment") and actual_segment and meta["segment"] != actual_segment:
            continue
        if sig in seen or intent_registry.availability(db, tenant_id, user, a["intent"], a["entities"]):
            continue
        seen.add(sig)
        out.append(a)
    return out


def for_party(db: Session, tenant_id: int, user: User, party_id: int, visible) -> dict[str, list[dict[str, Any]]]:
    """Buttons for every lead, opportunity, quote and order of a customer, keyed "type:id"."""
    from app.models.crm import Lead
    from app.models.orders import Order

    out: dict[str, list[dict[str, Any]]] = {}
    leads = db.query(Lead).filter(Lead.tenant_id == tenant_id, Lead.party_id == party_id).all()
    opps = [lead.opportunity for lead in leads if lead.opportunity is not None]
    quotes = db.query(Quote).filter(Quote.tenant_id == tenant_id, Quote.opportunity_id.in_([o.id for o in opps] or [-1])).all()
    orders = db.query(Order).filter(Order.tenant_id == tenant_id, Order.party_id == party_id).all()
    for entity_type, rows in (("lead", leads), ("opportunity", opps), ("quote", quotes), ("order", orders)):
        for obj in rows:
            if visible(entity_type, obj.id):
                out[f"{entity_type}:{obj.id}"] = for_record(db, tenant_id, user, entity_type, obj)
    return out
