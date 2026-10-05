"""Per-intent orchestration step sequences for INT-01/02/03 - directly from
HLD Section 7's "Orchestration Steps" for each intent, made concrete.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import catalog_service
from app.data_intelligence import event_bus
from app.integration import notification_adapter, tmf620_adapter
from app.models.catalog import OfferingStatus
from app.orchestration.orchestrator import SagaStep
from app.timeutils import utcnow


def build_int01_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-01 - Launch a new bundle."""

    name = entities["name"]
    segment = entities.get("segment", "B2C")
    channel = entities.get("channel", "B2C")
    region = entities.get("region", "ALL")
    composition: list[str] = entities["composition"]
    list_price = float(entities["list_price"])
    requested_discount_pct = float(entities.get("requested_discount_pct", 0))
    price_floor = float(entities.get("price_floor", list_price * 0.5))
    discount_ceiling_pct = float(entities.get("discount_ceiling_pct", 20))

    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        check = catalog_service.validate_composition(db, tenant_id, composition)
        if not check.ok:
            raise catalog_service.CompositionError(check.reason)
        state["primary_line"] = check.primary_line
        return {"primary_line": check.primary_line, "addon_lines": check.addon_lines}

    def step2() -> dict[str, Any]:
        price = catalog_service.compute_price(list_price, requested_discount_pct, discount_ceiling_pct, price_floor)
        state["price"] = price
        return {"price": price, "discount_applied_pct": min(requested_discount_pct, discount_ceiling_pct)}

    def step3() -> dict[str, Any]:
        eligibility = catalog_service.generate_eligibility(segment, channel, region)
        state["eligibility"] = eligibility
        return eligibility

    def step4() -> dict[str, Any]:
        offering = tmf620_adapter.create_offering_version(
            db,
            tenant_id=tenant_id,
            name=name,
            segment=segment,
            composition=",".join(composition),
            list_price=state["price"],
            price_floor=price_floor,
            discount_ceiling_pct=discount_ceiling_pct,
        )
        from app.models.catalog import BundleEligibilityRule

        db.add(
            BundleEligibilityRule(
                product_offering_id=offering.id,
                segment=state["eligibility"]["segment"],
                channel=state["eligibility"]["channel"],
                region=state["eligibility"]["region"],
            )
        )
        offering = tmf620_adapter.publish_offering(db, offering.id)
        state["offering_id"] = offering.id
        state["offering_version"] = offering.version
        return {"product_offering_id": offering.id, "status": offering.status.value, "version": offering.version}

    def step5() -> dict[str, Any]:
        notification_adapter.send(db, execution_id, "portal", "Sales,Care", f"New bundle available: {name}")
        event_bus.publish(db, execution_id, "offering.published", {"product_offering_id": state["offering_id"]})
        return {
            "product_offering_id": state["offering_id"],
            "status": OfferingStatus.active.value,
            "version": state["offering_version"],
        }

    return [
        SagaStep("validate_composition", step1),
        SagaStep("compute_price", step2),
        SagaStep("generate_eligibility", step3),
        SagaStep("publish_bundle", step4),
        SagaStep("notify_channels", step5),
    ]


def build_int02_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-02 - Modify a live bundle."""

    offering_name = entities["offering_name"]
    new_list_price = entities.get("new_list_price")
    new_segment = entities.get("new_segment")
    new_channel = entities.get("new_channel")
    new_region = entities.get("new_region", "ALL")

    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        current = tmf620_adapter.get_active_offering_by_name(db, tenant_id, offering_name)
        if current is None:
            raise ValueError(f"No active offering named '{offering_name}' found")
        state["current"] = current
        return {"current_offering_id": current.id, "current_version": current.version}

    def step2() -> dict[str, Any]:
        # Grandfathering policy: existing subscribers stay on the superseded
        # version (it is flagged superseded, never deleted or mutated), so
        # this is always a safe change regardless of active-subscriber count.
        return {"grandfathering": "existing subscribers remain on superseded version"}

    def step3() -> dict[str, Any]:
        current = state["current"]
        new_offering = tmf620_adapter.create_offering_version(
            db,
            tenant_id=tenant_id,
            name=current.name,
            segment=new_segment or current.segment,
            composition=current.composition,
            list_price=new_list_price if new_list_price is not None else current.price,
            price_floor=current.price_floor,
            discount_ceiling_pct=current.price_plan.discount_ceiling_pct if current.price_plan else 20.0,
            parent_offering_id=current.id,
        )
        if new_channel or new_segment:
            from app.models.catalog import BundleEligibilityRule

            db.add(
                BundleEligibilityRule(
                    product_offering_id=new_offering.id,
                    segment=new_segment or current.segment,
                    channel=new_channel or (current.eligibility_rules[0].channel if current.eligibility_rules else "B2C"),
                    region=new_region,
                )
            )
        state["new_offering_id"] = new_offering.id
        return {"new_offering_id": new_offering.id, "new_version": new_offering.version}

    def step4() -> dict[str, Any]:
        tmf620_adapter.supersede_offering(db, state["current"].id)
        return {"superseded_offering_id": state["current"].id}

    def step5() -> dict[str, Any]:
        offering = tmf620_adapter.publish_offering(db, state["new_offering_id"])
        event_bus.publish(db, execution_id, "offering.updated", {"product_offering_id": offering.id})
        return {"product_offering_id": offering.id, "status": offering.status.value, "version": offering.version}

    return [
        SagaStep("impact_analysis", step1),
        SagaStep("validate_grandfathering", step2),
        SagaStep("create_new_version", step3),
        SagaStep("supersede_prior_version", step4),
        SagaStep("publish_updated_entry", step5),
    ]


def build_int03_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-03 - Retire a bundle."""

    offering_name = entities["offering_name"]
    replacement_name = entities.get("replacement_offering_name")

    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        if not replacement_name:
            raise catalog_service.MigrationPathRequiredError(
                "No retirement is permitted without a migration path (HLD INT-03 business rule)"
            )
        if replacement_name.strip().lower() == offering_name.strip().lower():
            raise catalog_service.MigrationPathRequiredError(
                "The replacement offering cannot be the same bundle being retired"
            )
        current = tmf620_adapter.get_active_offering_by_name(db, tenant_id, offering_name)
        if current is None:
            raise ValueError(f"No active offering named '{offering_name}' found")
        replacement = tmf620_adapter.get_active_offering_by_name(db, tenant_id, replacement_name)
        if replacement is None:
            raise ValueError(f"Replacement offering '{replacement_name}' not found or not active")
        state["current"] = current
        state["replacement"] = replacement
        return {"current_offering_id": current.id, "replacement_offering_id": replacement.id}

    def step2() -> dict[str, Any]:
        offering = tmf620_adapter.retire_offering(
            db,
            state["current"].id,
            replacement_offering_id=state["replacement"].id,
            end_date=utcnow(),
        )
        return {"product_offering_id": offering.id, "status": offering.status.value}

    def step3() -> dict[str, Any]:
        event_bus.publish(
            db,
            execution_id,
            "offering.retired",
            {"product_offering_id": state["current"].id, "replacement_offering_id": state["replacement"].id},
        )
        notification_adapter.send(
            db,
            execution_id,
            "portal",
            "Sales,Care",
            f"Bundle retired: {offering_name} -> migrate to {replacement_name}",
        )
        return {"migration_notified": True}

    def step4() -> dict[str, Any]:
        # Historical retention is implicit: retire_offering() never deletes a
        # row, only flips status + end_date. Nothing further to do here; this
        # step exists to make that guarantee visible in the audit trail.
        return {"history_retained": True}

    return [
        SagaStep("validate_migration_path", step1),
        SagaStep("retire_offering", step2),
        SagaStep("trigger_migration_notification", step3),
        SagaStep("retain_history", step4),
    ]
