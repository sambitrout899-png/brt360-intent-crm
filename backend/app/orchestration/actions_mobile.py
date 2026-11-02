"""Mobile subscriber intents INT-129..148 (crm_core/comm_mobile.py does the work; see
intent_engine/mobile_intents.py). Each intent is one checked step inside the intent's saga:
a refusal rolls it back whole and is audited like any other run. Online charging (INT-144)
and a wrong voucher PIN (INT-129) answer "refused" without failing, so what the network
asked - and why it was refused - stays on the line's history."""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy.orm import Session

from app.crm_core import comm_mobile as mb
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


BUILDERS: dict[str, Builder] = {
    "INT-129": _step("recharge", "mobile.recharged", mb.recharge),
    "INT-130": _step("manage_pack", "mobile.pack_changed", mb.manage_pack),
    "INT-131": _step("check_balance", "mobile.balance_checked", mb.balance_view),
    "INT-132": _step("transfer_balance", "mobile.balance_transferred", mb.transfer),
    "INT-133": _step("emergency_credit", "mobile.emergency_credit", mb.emergency_credit),
    "INT-134": _step("switch_payment_type", "mobile.payment_type_changed", mb.migrate),
    "INT-135": _step("spend_limit", "mobile.spend_limit_set", mb.spend_limit),
    "INT-136": _step("activate_lines", "mobile.lines_activated", mb.activate),
    "INT-137": _step("swap_sim", "mobile.sim_swapped", mb.swap_sim),
    "INT-138": _step("change_number", "mobile.number_changed", mb.change_number),
    "INT-139": _step("barring", "mobile.barring_changed", mb.barring),
    "INT-140": _step("roaming", "mobile.roaming_changed", mb.roaming),
    "INT-141": _step("shared_plan", "mobile.shared_plan_changed", mb.shared_plan),
    "INT-142": _step("change_plan", "mobile.plan_changed", mb.change_plan),
    "INT-143": _step("line_lifecycle", "mobile.line_status_changed", mb.lifecycle),
    "INT-144": _step("charge_usage", "mobile.usage_charged", mb.charge_usage),
    "INT-145": _step("generate_vouchers", "mobile.vouchers_generated", mb.generate_vouchers),
    "INT-146": _step("wholesale_partner", "mobile.partner_saved", mb.save_partner),
    "INT-147": _step("partner_settlement", "mobile.partner_settled", mb.settle),
    "INT-148": _step("deposit", "mobile.deposit_changed", mb.deposit),
}
