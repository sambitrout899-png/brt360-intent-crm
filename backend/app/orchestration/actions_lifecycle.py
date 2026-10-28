"""Phase 7 lifecycle intents, generic across party/lead/opportunity/
quote/order:

- INT-29 "Update this record": save-once-then-modify, allowed only while
  the record's state is editable in its state model.
- INT-30 "Create a new revision of this quote": the post-lock path - the
  locked quote is superseded, never edited.
- INT-31 "Move this record to a state": a lifecycle action from the state
  model (close_won, reopen, a custom sub-state, ...). Transitions a core
  intent owns are refused with the intent to use instead.
- INT-32 "Assign / transfer this record" to a user and/or position.

entities["_actor_role"] is injected by routes_intent (never classified
from text) so INT-31 can honor per-transition role restrictions.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import custom_field_service, lifecycle_service, record_service
from app.data_intelligence import event_bus
from app.models.crm import Quote, QuoteItem, QuoteStatus
from app.orchestration.orchestrator import SagaStep


def _resolve(db: Session, tenant_id: int, entities: dict[str, Any]):
    entity_type = lifecycle_service.normalize_entity_type(entities.get("entity_type"))
    if entity_type is None:
        raise ValueError(f"Unknown entity_type '{entities.get('entity_type')}' (use party, lead, opportunity, quote or order)")
    obj = lifecycle_service.get_record(db, tenant_id, entity_type, entities.get("entity_id"))
    if obj is None:
        raise ValueError(f"{entity_type.title()} {entities.get('entity_id')} not found")
    return entity_type, obj


def build_int29_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-29 - Update this record."""

    fields = record_service.split_update_fields(entities)
    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        entity_type, obj = _resolve(db, tenant_id, entities)
        editable, current = lifecycle_service.is_editable(db, tenant_id, entity_type, obj)
        state.update(entity_type=entity_type, obj=obj)
        return {"entity_type": entity_type, "entity_id": obj.id, "state": current, "editable": editable}

    def step2() -> dict[str, Any]:
        entity_type, obj = state["entity_type"], state["obj"]
        applied = record_service.update_fields(db, tenant_id, entity_type, obj, fields)
        event_bus.publish(db, execution_id, f"{entity_type}.updated", {"entity_id": obj.id, "fields": applied})
        return {"entity_type": entity_type, "entity_id": obj.id, "updated_fields": applied}

    return [SagaStep("check_record_editable", step1), SagaStep("apply_field_updates", step2)]


def build_int30_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-30 - Create a new revision of this quote."""

    quote_id = entities["quote_id"]
    new_discount_pct = entities.get("requested_discount_pct")
    new_quantity = entities.get("quantity")
    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        quote = lifecycle_service.get_record(db, tenant_id, "quote", quote_id)
        if quote is None:
            raise ValueError(f"Quote {quote_id} not found")
        model = lifecycle_service.resolve_state_model(db, tenant_id, "quote")
        current = lifecycle_service.effective_state(db, tenant_id, "quote", quote)
        definition = lifecycle_service.state_def(model, current)
        if definition.is_terminal:
            raise ValueError(f"Quote {quote.id} is {current} - a closed quote cannot be revised")
        if definition.editable:
            raise ValueError(
                f"Quote {quote.id} is still editable (state '{current}') - revise it in place (INT-19) or update it (INT-29)"
            )
        state["quote"] = quote
        return {"quote_id": quote.id, "state": current}

    def step2() -> dict[str, Any]:
        old: Quote = state["quote"]
        discount = float(new_discount_pct) if new_discount_pct is not None else old.requested_discount_pct
        new = Quote(
            tenant_id=tenant_id,
            opportunity_id=old.opportunity_id,
            status=QuoteStatus.draft,
            total_price=old.total_price,
            requested_discount_pct=discount,
            discount_justification=old.discount_justification,
            revision_number=old.revision_number + 1,
            revision_of_quote_id=old.id,
        )
        db.add(new)
        db.flush()
        from app.models.product_config import QuoteItemConfig, QuoteTerms

        for item in old.items:
            quantity = int(new_quantity) if new_quantity is not None and len(old.items) == 1 else item.quantity
            new_item = QuoteItem(quote_id=new.id, product_offering_id=item.product_offering_id, quantity=quantity, unit_price=item.unit_price)
            db.add(new_item)
            db.flush()
            # The product's configuration and the quote's own pricelist carry over.
            old_config = db.query(QuoteItemConfig).filter(QuoteItemConfig.quote_item_id == item.id).first()
            if old_config is not None:
                db.add(QuoteItemConfig(quote_item_id=new_item.id, pricing_attributes_json=old_config.pricing_attributes_json,
                                       technical_attributes_json=old_config.technical_attributes_json,
                                       price_breakdown_json=old_config.price_breakdown_json))
            from app.crm_core import instance_service
            from app.models.catalog import ProductOffering

            offering = db.get(ProductOffering, item.product_offering_id)
            old_instance = instance_service.get(db, tenant_id, "quote", old.id, offering.name) if offering else None
            if old_instance is not None:
                instance_service.take(db, tenant_id, "quote", new.id, offering.name, old_instance, new_item.id)
        old_terms = db.query(QuoteTerms).filter(QuoteTerms.quote_id == old.id).first()
        if old_terms is not None:
            db.add(QuoteTerms(quote_id=new.id, price_list_id=old_terms.price_list_id))
        db.flush()
        db.refresh(new)
        if new_discount_pct is not None or new_quantity is not None:
            subtotal = sum(i.unit_price * i.quantity for i in new.items)
            new.total_price = round(subtotal * (1 - discount / 100), 2)
        else:  # priced as before - a negotiated price comes along
            from app.crm_core import quote_override_service

            quote_override_service.carry_over(db, old, new)
        for key, value in custom_field_service.get_values(db, tenant_id, "quote", old.id).items():
            defn = custom_field_service.definition_by_key(db, tenant_id, "quote", key)
            if defn is not None and value is not None:
                custom_field_service.set_value(db, tenant_id, "quote", new.id, defn, value)
        old.status = QuoteStatus.superseded
        old_meta = lifecycle_service.get_meta(db, tenant_id, "quote", old.id)
        if old_meta.owner_user_id or old_meta.owner_position_id:
            lifecycle_service.assign(
                db, tenant_id, "quote", new.id, old_meta.owner_user_id, old_meta.owner_position_id,
                f"Inherited from quote {old.id} (revision)", "revision",
            )
        db.flush()
        event_bus.publish(db, execution_id, "quote.revised_new_version", {"quote_id": old.id, "new_quote_id": new.id})
        return {
            "quote_id": old.id,
            "new_quote_id": new.id,
            "revision_number": new.revision_number,
            "total_price": new.total_price,
            "status": new.status.value,
        }

    return [SagaStep("check_quote_locked", step1), SagaStep("create_revision", step2)]


def build_int31_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-31 - Move this record to a state (lifecycle action)."""

    role = entities.get("_actor_role")
    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        entity_type, obj = _resolve(db, tenant_id, entities)
        transition, error = lifecycle_service.find_transition(
            db, tenant_id, entity_type, obj, entities.get("action"), entities.get("to_state"), role, entities.get("_actor_user_id")
        )
        if transition is None:
            raise ValueError(error)
        state.update(entity_type=entity_type, obj=obj, transition=transition)
        return {"entity_type": entity_type, "entity_id": obj.id, "action": transition.action, "to_state": transition.to_state}

    def step2() -> dict[str, Any]:
        entity_type, obj = state["entity_type"], state["obj"]
        # A record approval on the target state (Admin -> Approvals): ask
        # for it instead of moving; the approval moves the record.
        from app.orchestration import actions_sales

        request = actions_sales.gate(
            db, tenant_id, entity_type, obj.id, state["transition"].to_state,
            f"Move {entity_type} #{obj.id} to {state['transition'].to_state.replace('_', ' ')}"
            + (f": {entities['reasoning']}" if entities.get("reasoning") else ""),
            actions_sales.actor(entities),
        )
        if request is not None:
            return {
                "entity_type": entity_type, "entity_id": obj.id, "to_state": state["transition"].to_state,
                "from_state": lifecycle_service.effective_state(db, tenant_id, entity_type, obj), **actions_sales.waiting(request),
            }
        outcome = lifecycle_service.apply_transition(db, tenant_id, entity_type, obj, state["transition"], "transition")
        event_bus.publish(
            db, execution_id, f"{entity_type}.state_changed",
            {**outcome, "reasoning": entities.get("reasoning") or ""},
        )
        return outcome

    return [SagaStep("validate_transition", step1), SagaStep("apply_transition", step2)]


def build_int32_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-32 - Assign / transfer this record to a user, a position or a
    department. A lead's opportunity and an opportunity's open quotes move
    with it (include_related, on unless switched off)."""

    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        entity_type, obj = _resolve(db, tenant_id, entities)
        lifecycle_service.normalize_assignee(db, tenant_id, entities)
        user_ref, position_ref, department_ref = entities.get("to_user"), entities.get("to_position"), entities.get("to_department")
        if not user_ref and not position_ref and not department_ref:
            raise ValueError("Say who to assign it to: to_user (email or name), to_position or to_department")
        department = lifecycle_service.resolve_department(db, tenant_id, department_ref) or lifecycle_service.department_named(
            db, tenant_id, department_ref)
        if department_ref and department is None:
            raise ValueError(f"No active department '{department_ref}' in this tenant")
        if department is not None and (user_ref or position_ref):
            raise ValueError("Assign it to a department, or to a user / position - not both")
        state["department"] = department
        user = lifecycle_service.resolve_user(db, tenant_id, user_ref)
        if user_ref and user is None:
            raise ValueError(f"No active user '{user_ref}' in this tenant")
        position = lifecycle_service.resolve_position(db, tenant_id, position_ref)
        if position_ref and position is None:
            raise ValueError(f"No active position '{position_ref}' in this tenant")
        state.update(entity_type=entity_type, obj=obj, user=user, position=position)
        return {
            "entity_type": entity_type,
            "entity_id": obj.id,
            "to_user": user.email if user else None,
            "to_position": position.name if position else None,
            "to_department": state["department"].name if state["department"] else None,
        }

    def step2() -> dict[str, Any]:
        include = entities.get("include_related")
        include = True if include in (None, "") else str(include).strip().lower() not in ("false", "no", "0", "off")
        outcome = lifecycle_service.assign_with_related(
            db, tenant_id, state["entity_type"], state["obj"],
            state["user"].id if state["user"] else None,
            state["position"].id if state["position"] else None,
            state["department"].id if state["department"] else None,
            entities.get("reason") or entities.get("reasoning") or "",
            "manual",
            include_related=include,
        )
        event_bus.publish(db, execution_id, f"{state['entity_type']}.assigned", outcome)
        return outcome

    return [SagaStep("resolve_assignee", step1), SagaStep("transfer_ownership", step2)]


def build_custom_flow_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any], flow) -> list[SagaStep]:
    """A custom-intent flow (FLOW-<id>) - the flow itself is the plan."""
    from app.orchestration import flow_engine

    public_entities = {k: v for k, v in entities.items() if not k.startswith("_")}
    ctx: dict[str, Any] = {"entities": public_entities, "result": {}}
    entity_type = flow.entity_type
    if entity_type:
        entity_id = public_entities.get(f"{entity_type}_id") or public_entities.get("entity_id")
        if entity_id not in (None, ""):
            ctx.update(entity_type=entity_type, entity_id=entity_id)

    def action() -> dict[str, Any]:
        run = flow_engine.run_flow(db, tenant_id, execution_id, flow, ctx)
        out: dict[str, Any] = {"flows_run": [run]}
        if run.get("entity_type") and run.get("entity_id"):
            out.update(entity_type=run["entity_type"], entity_id=run["entity_id"])
        return out

    return [SagaStep(f"flow:{flow.name}", action)]


# The intents this module carries out (app/capabilities.py).
BUILDERS = {
    "INT-29": build_int29_steps,
    "INT-30": build_int30_steps,
    "INT-31": build_int31_steps,
    "INT-32": build_int32_steps,
}
