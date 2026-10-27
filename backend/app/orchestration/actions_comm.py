"""Communications intents INT-72..102 (crm_core/comm_service.py and
comm_billing.py do the work).

Each intent is one checked step: the service refuses anything the record's
state or the tenant's policy does not allow, so a failed run leaves nothing
half done.
"""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy.orm import Session

from app.crm_core import comm_billing as cb, comm_service as cs
from app.data_intelligence import event_bus
from app.orchestration.orchestrator import SagaStep

Builder = Callable[[Session, int, int, dict[str, Any]], list[SagaStep]]


def _step(name: str, event: str, work: Callable[[Session, int, dict[str, Any]], dict[str, Any]]) -> Builder:
    def build(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
        def run() -> dict[str, Any]:
            out = work(db, tenant_id, entities)
            event_bus.publish(db, execution_id, event, {k: v for k, v in out.items() if isinstance(v, (int, float, str, bool)) or v is None})
            return out

        return [SagaStep(name, run)]

    return build


def _party(db: Session, tenant_id: int, e: dict[str, Any]) -> int:
    return cs.party_of(db, tenant_id, e).id


def _bill_run(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    return cb.bill_run(db, tenant_id, _party(db, tenant_id, e), e.get("period_end"))


def _collections(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    party = cs.party_of(db, tenant_id, e, required=False)
    out = cb.run_collections(db, tenant_id, party.id if party else None, e.get("as_of"))
    return {"party_id": party.id if party else None, **out}


def _solutions(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    return cs.solutions_for(db, tenant_id, e)


BUILDERS: dict[str, Builder] = {
    "INT-72": _step("account_hierarchy", "account.hierarchy_changed", cs.set_hierarchy),
    "INT-73": _step("add_contact", "contact.added", cs.add_contact),
    "INT-74": _step("billing_profile", "account.billing_profile_saved", cs.save_billing_profile),
    "INT-75": _step("account_profile", "account.profile_updated", cs.update_profile),
    "INT-76": _step("fraud_thresholds", "account.fraud_thresholds_changed",
                    lambda db, t, e: cs.update_profile(db, t, {**e, "profile_kind": "fraud"})),
    "INT-77": _step("validate_address", "address.validated", cs.validate_address),
    "INT-78": _step("premises_hookup", "premises.hookup_registered", cs.register_hookup),
    "INT-79": _step("special_rating_list", "account.rating_list_changed", cs.manage_rating_list),
    "INT-80": _step("asset_transaction", "asset.transaction_recorded", cs.asset_transaction),
    "INT-81": _step("change_request", "asset.change_requested", cs.raise_change_request),
    "INT-82": _step("opportunity_link", "opportunity.account_linked", cs.link_opportunity),
    "INT-83": _step("opportunity_profile", "opportunity.profiled", cs.profile_opportunity),
    "INT-84": _step("bill_run", "billing.invoice_created", _bill_run),
    "INT-85": _step("bill_payment", "billing.payment_posted", cb.take_payment),
    "INT-86": _step("bill_adjustment", "billing.adjustment_requested", cb.request_adjustment),
    "INT-87": _step("payment_arrangement", "billing.arrangement_requested", cb.request_arrangement),
    "INT-88": _step("customer_decision", "billing.customer_decided", cb.record_decision),
    "INT-89": _step("duplicate_bill", "billing.duplicate_sent", cb.duplicate),
    "INT-90": _step("explain_bill", "billing.bill_explained", cb.explain),
    "INT-91": _step("run_collections", "credit.collections_run", _collections),
    "INT-92": _step("credit_alert", "credit.alert_worked", cb.work_credit_alert),
    "INT-93": _step("fraud_check", "fraud.checked", cb.raise_fraud),
    "INT-94": _step("fraud_alert", "fraud.alert_worked", cb.work_fraud),
    "INT-95": _step("link_tickets", "ticket.children_linked", cs.link_tickets),
    "INT-96": _step("resolve_ticket", "ticket.resolved", cs.resolve_ticket),
    "INT-97": _step("satisfaction_survey", "ticket.survey_recorded", cs.record_survey),
    "INT-98": _step("order_fallout", "order.fallout_logged", cs.log_fallout),
    "INT-99": _step("find_solution", "ticket.solutions_found", _solutions),
    "INT-100": _step("revise_agreement", "agreement.revised", cs.revise_agreement),
    "INT-101": _step("agreement_document", "agreement.document_generated", cs.generate_document),
    "INT-102": _step("provider_allocation", "order.providers_allocated", cs.allocate_providers),
}
