"""The fulfillment functions an orchestration plan is built from, one closed catalog per order layer - the
pattern designer only ever chooses from these, and so does the LLM planner.

Each function does one thing against one system and returns an Outcome:
done now (with the response it collected), or waiting - for a child order,
an engineer closing a work order, or an asynchronous inventory response.
Every request it sends and response it gets is kept on the task.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models.fulfillment_orders import FulfillmentOrder, FulfillmentTask

# ------------------------------------------------------------------ catalog
# function -> spec. "presets" are the palette entries (one function, typical settings).

FUNCTIONS: dict[str, dict[str, Any]] = {
    # ---------------- COM - Customer Order Management (eTOM 1.1.1 CRM)
    "sync_customer": {
        "layer": "COM", "label": "Register customer in billing", "etom": "1.1.1.5 Order Handling -> 1.1.1.10 Bill Invoice/Billing Account Mgmt",
        "description": "Registers the order's customer with the billing system and keeps its reference.",
        "config": [],
    },
    "create_billing_account": {
        "layer": "COM", "label": "Create billing account", "etom": "1.1.1.10 Billing Account Management",
        "description": "The customer's billing account - created if it has none - registered with billing.",
        "config": ["rate_plan"],
    },
    "create_billing_profile": {
        "layer": "COM", "label": "Create billing profile", "etom": "1.1.1.10 Billing Account Management",
        "description": "How the account is billed (frequency, payment method, bill media) - the primary profile, created if missing.",
        "config": ["frequency", "payment_method", "bill_media", "payment_type"],
    },
    "route_to_som": {
        "layer": "COM", "label": "Route to service orchestration (SOM)", "etom": "1.1.1.5 Order Handling -> 1.1.2.2 Service Configuration & Activation",
        "description": "Decomposes the customer order into a service order and waits until the service order is complete.",
        "config": [], "route": "SOM", "waits": True,
    },
    "start_billing": {
        "layer": "COM", "label": "Start billing", "etom": "1.1.1.10 Bill Invoice Management - Billing start",
        "description": "Tells billing to start charging the service account, with the order's monthly price.",
        "config": [],
    },
    "create_assets": {
        "layer": "COM", "label": "Create installed assets", "etom": "1.1.1.5 Order Handling - Close Customer Order (asset)",
        "description": "Each ordered product becomes an installed asset in the CRM, synced to the inventory system.",
        "config": [],
    },
    "complete_order": {
        "layer": "COM", "label": "Complete the customer order", "etom": "1.1.1.5 Order Handling - Close Customer Order",
        "description": "Completes the CRM order and makes a prospect a customer.",
        "config": [],
    },
    # ---------------- SOM - Service Order Management (eTOM 1.1.2.2 Service Configuration & Activation)
    "design_service": {
        "layer": "SOM", "label": "Design service", "etom": "1.1.2.2 Design Solution",
        "description": "Asks inventory to design the service - its service and circuit ids.",
        "config": ["resources", "response"], "inventory": "design", "default_resources": ["service_id", "circuit_id"],
    },
    "assign_logical": {
        "layer": "SOM", "label": "Assign logical resources", "etom": "1.1.2.2 Allocate Specific Service Parameters to Services",
        "description": "Logical inventory: IP block, VLAN, numbers - whatever the service needs.",
        "config": ["resources", "response"], "inventory": "assign", "default_resources": ["ip_block", "vlan"],
    },
    "reserve_physical": {
        "layer": "SOM", "label": "Reserve physical resources", "etom": "1.1.3.2 Allocate & Install Resource (reservation)",
        "description": "Physical inventory: the access port, ONT / CPE, fibre - reserved for the field team.",
        "config": ["resources", "response"], "inventory": "reserve", "default_resources": ["port", "ont"],
    },
    "activate_service": {
        "layer": "SOM", "label": "Activate service in the network", "etom": "1.1.2.2 Activate Service",
        "description": "The network activation command; the order's resources become active.",
        "config": ["resources", "response"], "inventory": "activate", "default_resources": [],
    },
    "release_resources": {
        "layer": "SOM", "label": "Release resources", "etom": "1.1.2.2 / 1.1.3.2 Recover Service & Resources",
        "description": "Gives back everything the order holds in inventory - ports and equipment become available, VLANs, "
                       "addresses and numbers return to their pools (cancel, cease and move orders).",
        "config": ["resources", "response"], "inventory": "release", "default_resources": [],
    },
    "route_to_tom": {
        "layer": "SOM", "label": "Route to technical orchestration (TOM)", "etom": "1.1.2.2 -> 1.1.3.2 Resource Provisioning / Workforce Management",
        "description": "Hands the field work to technical order management and waits until it is done.",
        "config": [], "route": "TOM", "waits": True,
    },
    # ---------------- TOM - Technical Order Management (eTOM 1.1.3 Resource Mgmt / Workforce Mgmt)
    "field_work_order": {
        "layer": "TOM", "label": "Field work order", "etom": "1.1.3.2 Allocate & Install Resource / Workforce Management",
        "description": "A Field Service work order for an engineer - done when the engineer closes it. The reserved "
                       "resources (port, ONT, VLAN, IP ...) go on the job.",
        "config": ["kind", "template", "subject", "skills", "duration_minutes", "instructions"], "waits": True,
        "presets": [
            {"label": "Installation", "config": {"kind": "installation", "subject": "Installation", "skills": "fiber_install", "duration_minutes": "180"}},
            {"label": "Connectivity test", "config": {"kind": "connectivity_test", "subject": "Connectivity test", "duration_minutes": "60"}},
            {"label": "Acceptance signing", "config": {"kind": "acceptance", "subject": "Customer acceptance signing", "duration_minutes": "30"}},
            {"label": "Site survey", "config": {"kind": "site_survey", "subject": "Site survey", "duration_minutes": "60"}},
        ],
    },
    # ---------------- any layer
    "milestone": {
        "layer": "*", "label": "Record a milestone", "etom": "1.1.1.5 Track & Manage Customer Order Handling",
        "description": "Adds a milestone to the CRM order's timeline - what the customer and sales see.",
        "config": ["label"],
    },
}
WORK_ORDER_KINDS = ("installation", "connectivity_test", "acceptance", "site_survey", "repair", "custom")
TOM_WORK_ORDER_KIND = "tom"  # assurance.WorkOrder.kind of a TOM work order - closed in Field Service, moves the order


def catalog() -> list[dict[str, Any]]:
    out = []
    for name, spec in FUNCTIONS.items():
        presets = spec.get("presets") or [{"label": spec["label"], "config": (
            {"resources": ", ".join(spec["default_resources"])} if spec.get("default_resources") else {})}]
        out.append({"function": name, **{k: v for k, v in spec.items() if k not in ("presets",)}, "presets": presets})
    return out


# ------------------------------------------------------------------ runtime


@dataclass
class Outcome:
    status: str = "completed"  # completed | waiting
    response: dict[str, Any] = field(default_factory=dict)
    request: dict[str, Any] = field(default_factory=dict)
    work_order_id: int | None = None
    external_ref: str = ""
    child_layer: str | None = None
    note: str = ""


@dataclass
class Runtime:
    db: Session
    tenant_id: int
    fulfillment_order: FulfillmentOrder
    task: FulfillmentTask
    config: dict[str, Any]
    data: dict[str, Any]  # the order's data - read and add to
    execution_id: int | None = None

    @property
    def crm_order(self):
        from app.models.orders import Order

        return self.db.get(Order, self.fulfillment_order.crm_order_id)


def _milestone(rt: Runtime, code: str, label: str, detail: str = "") -> None:
    from app.orchestration import actions_sales

    actions_sales.milestone(rt.db, rt.fulfillment_order.crm_order_id, code, label, detail)


def _billing(rt: Runtime) -> dict[str, Any]:
    return rt.data.setdefault("billing", {})


# ---------------- COM


def sync_customer(rt: Runtime) -> Outcome:
    from app.integration import billing_initiation_adapter
    from app.models.crm import Party

    order = rt.crm_order
    party = rt.db.get(Party, order.party_id)
    request = {"party_id": party.id, "name": party.name, "email": party.email}
    ref = billing_initiation_adapter.register_customer(party.id, party.name)
    _billing(rt)["customer_ref"] = ref
    return Outcome(request=request, response={"customer_ref": ref})


def create_billing_account(rt: Runtime) -> Outcome:
    from app.integration import billing_initiation_adapter, order_adapter
    from app.models.accounts import ServiceAccount
    from app.models.orders import BillingAccount

    order = rt.crm_order
    service_account = rt.db.get(ServiceAccount, order.service_account_id)
    account = rt.db.get(BillingAccount, service_account.billing_account_id) if service_account else None
    created = account is None
    if account is None:
        account = order_adapter.get_or_create_billing_account(rt.db, rt.tenant_id, order.party_id)
    if rt.config.get("rate_plan"):
        account.rate_plan = str(rt.config["rate_plan"])
    ref = billing_initiation_adapter.register_billing_account(account.id, _billing(rt).get("customer_ref", ""))
    _billing(rt).update(billing_account_id=account.id, billing_account_ref=ref)
    rt.db.flush()
    return Outcome(request={"party_id": order.party_id, "customer_ref": _billing(rt).get("customer_ref")},
                   response={"billing_account_id": account.id, "billing_account_ref": ref, "created": created, "rate_plan": account.rate_plan})


def create_billing_profile(rt: Runtime) -> Outcome:
    from app.integration import billing_initiation_adapter, order_adapter
    from app.models.comms import CmBillingProfile
    from app.models.crm import Party

    order = rt.crm_order
    account_id = _billing(rt).get("billing_account_id") or order_adapter.get_or_create_billing_account(rt.db, rt.tenant_id, order.party_id).id
    profile = (rt.db.query(CmBillingProfile).filter(CmBillingProfile.tenant_id == rt.tenant_id, CmBillingProfile.billing_account_id == account_id)
               .order_by(CmBillingProfile.is_primary.desc(), CmBillingProfile.id).first())
    created = profile is None
    if profile is None:
        party = rt.db.get(Party, order.party_id)
        profile = CmBillingProfile(tenant_id=rt.tenant_id, party_id=order.party_id, billing_account_id=account_id,
                                   name=f"{party.name} - billing", is_primary=True, email=party.email or "")
        for key in ("frequency", "payment_method", "bill_media", "payment_type"):
            if rt.config.get(key):
                setattr(profile, key, str(rt.config[key]))
        address = (rt.data.get("order") or {}).get("address")
        if address:
            profile.address = str(address)[:400]
        rt.db.add(profile)
        rt.db.flush()
    ref = billing_initiation_adapter.register_billing_profile(profile.id, _billing(rt).get("billing_account_ref", ""))
    _billing(rt).update(billing_profile_id=profile.id, billing_profile_ref=ref)
    return Outcome(request={"billing_account_id": account_id},
                   response={"billing_profile_id": profile.id, "billing_profile_ref": ref, "created": created,
                             "frequency": profile.frequency, "payment_method": profile.payment_method, "bill_media": profile.bill_media})


def route(rt: Runtime) -> Outcome:
    layer = FUNCTIONS[rt.task.function]["route"]
    return Outcome(status="waiting", child_layer=layer, note=f"Waiting for the {layer} order")


def start_billing(rt: Runtime) -> Outcome:
    from app.integration import billing_initiation_adapter

    order = rt.crm_order
    ref = billing_initiation_adapter.initiate_billing(order.service_account_id)
    _billing(rt)["billing_start_ref"] = ref
    _milestone(rt, "billing_started", "Billing started", ref)
    return Outcome(request={"service_account_id": order.service_account_id, "monthly_price": order.monthly_price,
                            "billing_account_ref": _billing(rt).get("billing_account_ref")},
                   response={"billing_initiation_ref": ref})


def create_assets(rt: Runtime) -> Outcome:
    from app.models.catalog import ProductOffering
    from app.orchestration import fulfillment_ops

    order = rt.crm_order
    assets = []
    for item in order.items:
        offering = rt.db.get(ProductOffering, item.product_offering_id)
        made = fulfillment_ops.op_create_asset(rt.db, rt.tenant_id, order.service_account_id, item.id,
                                               offering.name if offering else "Service", f"ORDER-{order.id}-ITEM-{item.id}")
        assets.append(made)
    rt.data["asset_ids"] = [a.get("asset_id") for a in assets]
    from app.crm_core import inventory_service

    inventory_service.link_assets(rt.db, rt.tenant_id, order.id, rt.data["asset_ids"])  # the service record knows its CRM assets
    _milestone(rt, "assets_created", "Installed assets created", ", ".join(str(a.get("inventory_system_ref") or "") for a in assets))
    return Outcome(response={"assets": assets})


def complete_order(rt: Runtime) -> Outcome:
    from app.crm_core import abo_service
    from app.orchestration import fulfillment_ops

    order = rt.crm_order
    abo_service.complete(rt.db, order)
    party = fulfillment_ops.op_convert_prospect_to_customer(rt.db, rt.tenant_id, order.party_id)
    _milestone(rt, "live", "Service live - order complete")
    return Outcome(response={"order_id": order.id, "order_status": order.status.value, "party_type": party["party_type"]})


# ---------------- SOM


def _resources(rt: Runtime) -> list[str]:
    raw = rt.config.get("resources")
    if raw in (None, ""):
        return list(FUNCTIONS[rt.task.function].get("default_resources") or [])
    return [r.strip() for r in (raw if isinstance(raw, list) else str(raw).split(",")) if str(r).strip()]


def inventory_context(rt: Runtime) -> dict[str, Any]:
    order = rt.crm_order
    info = rt.data.get("order") or {}
    return {"order_id": order.id, "order_item_id": order.items[0].id if order.items else 0, "address": info.get("address") or "",
            "products": info.get("products") or [], "technical": info.get("technical") or {}, "party_id": order.party_id,
            "reference": f"{rt.fulfillment_order.number} · {rt.task.label}"[:120]}


def record_resources(rt: Runtime, resources: list[dict[str, Any]], activation_ref: str = "") -> None:
    """Inventory's answer on the CRM order: its service order and resources - and on the SOM order's data."""
    from app.models.orders import Resource, ResourceStatus, ServiceOrder, ServiceOrderStatus

    order = rt.crm_order
    service_order = next((s for s in order.service_orders if s.site_label == "Primary"), None) or (order.service_orders[0] if order.service_orders else None)
    if service_order is None:
        service_order = ServiceOrder(order_id=order.id, site_label="Primary")
        rt.db.add(service_order)
        rt.db.flush()
    known = {(r.resource_type, r.identifier): r for r in service_order.resources}
    for res in resources:
        row = known.get((res["type"], res["identifier"]))
        status = ResourceStatus.active if res.get("status") == "active" else ResourceStatus.released if res.get("status") == "released" \
            else ResourceStatus.reserved
        if row is None:
            rt.db.add(Resource(service_order_id=service_order.id, resource_type=res["type"], identifier=str(res["identifier"])[:200], status=status))
        else:
            row.status = status
    if activation_ref:
        for row in service_order.resources:
            row.status = ResourceStatus.active
        service_order.status = ServiceOrderStatus.active
    elif service_order.status == ServiceOrderStatus.pending and resources:
        service_order.status = ServiceOrderStatus.provisioned
    collected = rt.data.setdefault("resources", [])
    for res in resources:
        existing = next((c for c in collected if c["type"] == res["type"]), None)
        if existing is None:
            collected.append(dict(res))
        else:
            existing.update(res)
    rt.db.flush()


def inventory_action(rt: Runtime) -> Outcome:
    from app.integration import inventory_adapter, network_activation_adapter

    spec = FUNCTIONS[rt.task.function]
    action = spec["inventory"]
    resources = _resources(rt)
    context = inventory_context(rt)
    if action == "activate" and not resources:
        resources = [r["type"] for r in rt.data.get("resources") or []]
    request = {"action": action, "resources": resources, "address": context["address"], "order_id": context["order_id"]}
    if str(rt.config.get("response") or "sync") == "async":
        # sent; the inventory system answers later through the orchestration callback
        sent = inventory_adapter.technical_action(action, [], context)
        return Outcome(status="waiting", request=request, external_ref=sent["request_id"],
                       note=f"Waiting for inventory to answer request {sent['request_id']}")
    # the tenant's BRT 360 Inventory allocates what it keeps; the rest comes from the integrated system
    answer = inventory_adapter.technical_action(action, resources, context, db=rt.db, tenant_id=rt.tenant_id)
    activation_ref = ""
    if action == "activate":
        activation_ref = network_activation_adapter.activate_connection(context["order_item_id"])
        for res in answer["resources"]:
            res["status"] = "active"
    record_resources(rt, answer["resources"], activation_ref)
    response = {"request_id": answer["request_id"], "resources": answer["resources"]}
    if answer.get("location"):
        response["location"] = answer["location"]
    if answer.get("system"):
        response["system"] = answer["system"]
    if action == "release":
        _milestone(rt, "resources_released", "Network resources released", ", ".join(r["identifier"] for r in answer["resources"])[:300])
    if activation_ref:
        response["network_activation_ref"] = activation_ref
        _milestone(rt, "network_activated", "Service activated in the network", activation_ref)
    return Outcome(request=request, response=response, external_ref=answer["request_id"])


# ---------------- TOM


def _job_sheet(rt: Runtime) -> str:
    lines = [f"{r['kind']} {r['type'].replace('_', ' ')}: {r['identifier']}" for r in rt.data.get("resources") or []]
    technical = (rt.data.get("order") or {}).get("technical") or {}
    lines += [f"{k}: {v}" for k, v in technical.items()]
    return "\n".join(lines)


def field_work_order(rt: Runtime) -> Outcome:
    from app.crm_core import fs_service
    from app.models.assurance import WorkOrder
    from app.models.field_service import FsActivityTemplate

    order = rt.crm_order
    kind = str(rt.config.get("kind") or "installation")
    if kind not in WORK_ORDER_KINDS:
        raise ValueError(f"Unknown work order kind '{kind}' - use {', '.join(WORK_ORDER_KINDS)}")
    template = None
    if rt.config.get("template"):
        template = (rt.db.query(FsActivityTemplate).filter(FsActivityTemplate.tenant_id == rt.tenant_id,
                                                           FsActivityTemplate.name.ilike(str(rt.config["template"]).strip())).first())
        if template is None:
            raise ValueError(f"No Field Service activity template '{rt.config['template']}' (Field Service -> Setup)")
    skills = [s.strip() for s in str(rt.config.get("skills") or "").split(",") if s.strip()] or None
    sheet = _job_sheet(rt)
    subject = str(rt.config.get("subject") or kind.replace("_", " ").capitalize())
    info = rt.data.get("order") or {}
    act = fs_service.create_activity(
        rt.db, rt.tenant_id, party_id=order.party_id, template_id=template.id if template else None,
        subject=f"{subject} - order #{order.id} ({rt.fulfillment_order.number})", category="installation",
        duration_minutes=int(rt.config["duration_minutes"]) if str(rt.config.get("duration_minutes") or "").isdigit() else None,
        skills=skills, address=str(info.get("address") or ""), billable=False,
        instructions="\n".join(x for x in (str(rt.config.get("instructions") or ""), sheet) if x),
    )
    work_order = rt.db.get(WorkOrder, act.work_order_id)
    work_order.kind = TOM_WORK_ORDER_KIND
    work_order.order_id = order.id
    work_order.technical_parameters = sheet
    rt.db.flush()
    _milestone(rt, f"wo_{kind}_raised", f"{subject} work order raised", f"WO-{work_order.id}")
    return Outcome(status="waiting", work_order_id=work_order.id,
                   request={"kind": kind, "subject": subject, "activity_id": act.id, "work_order_id": work_order.id, "job_sheet": sheet},
                   note=f"Waiting for the engineer to close work order {work_order.id} (Field Service activity {act.id})")


def work_order_response(db: Session, work_order) -> dict[str, Any]:
    """What the field reports back when a TOM work order closes."""
    from app.crm_core import fs_service
    from app.models.field_service import FsActivity

    act = db.query(FsActivity).filter(FsActivity.work_order_id == work_order.id).first()
    tech = fs_service.assigned_technician(db, work_order)
    evidence = work_order.completion_evidence
    return {"work_order_id": work_order.id, "status": work_order.status, "technician": tech.name if tech else None,
            "resolution_code": act.resolution_code if act else "", "notes": act.notes if act else "",
            "signature": evidence.signature if evidence else "", "checklist": evidence.checklist if evidence else "",
            "technical_parameters": work_order.technical_parameters, "completed_at": str(act.actual_end) if act and act.actual_end else None}


# ---------------- any layer


def milestone(rt: Runtime) -> Outcome:
    label = str(rt.config.get("label") or rt.task.label or "Milestone")
    _milestone(rt, f"fulfillment_{rt.task.node_id}", label, rt.fulfillment_order.number)
    return Outcome(response={"milestone": label})


IMPLEMENTATIONS: dict[str, Callable[[Runtime], Outcome]] = {
    "sync_customer": sync_customer, "create_billing_account": create_billing_account, "create_billing_profile": create_billing_profile,
    "route_to_som": route, "start_billing": start_billing, "create_assets": create_assets, "complete_order": complete_order,
    "design_service": inventory_action, "assign_logical": inventory_action, "reserve_physical": inventory_action,
    "activate_service": inventory_action, "release_resources": inventory_action, "route_to_tom": route, "field_work_order": field_work_order, "milestone": milestone,
}
assert set(IMPLEMENTATIONS) == set(FUNCTIONS)


def dumps(value: Any) -> str:
    return json.dumps(value, default=str)
