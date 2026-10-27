"""Asset based ordering. Submitted changes never mutate the installed asset."""
from __future__ import annotations
import calendar
import json
from sqlalchemy import update, or_
from app.models.abo import AssetChange, AssetRevision
from app.models.fulfillment import Asset, AssetStatus
from app.models.orders import Order, OrderItem, OrderStatus, OrderType, BillingAccount
from app.models.accounts import ServiceAccount
from app.models.product_model import ProductInstance
from app.models.catalog import ProductOffering
from app.crm_core import instance_service, product_config_service as pc

OPERATIONS = ("modify", "disconnect", "terminate", "suspend", "resume", "relocate", "customer_transfer", "number_portability",
              "product_change", "promotion_change", "port_out")

# What people call a post-order change -> the operation that makes it.
ALIASES = {"upgrade": "modify", "downgrade": "modify", "change": "modify", "reconfigure": "modify",
           "cancel": "disconnect", "cease": "disconnect", "relocation": "relocate", "move": "relocate", "shift": "relocate",
           "transfer": "customer_transfer", "account_transfer": "customer_transfer", "ownership_transfer": "customer_transfer",
           "port_in": "number_portability", "mnp_in": "number_portability", "mnp": "number_portability",
           "mnp_out": "port_out", "port-out": "port_out", "plan_change": "product_change", "change_product": "product_change",
           "promotion": "promotion_change", "change_promotion": "promotion_change"}

LABELS = {"modify": "Modify (upgrade / downgrade)", "disconnect": "Disconnect", "terminate": "Terminate", "suspend": "Suspend",
          "resume": "Resume", "relocate": "Relocate", "customer_transfer": "Account transfer", "number_portability": "MNP port-in",
          "product_change": "Product change", "promotion_change": "Promotion change", "port_out": "MNP port-out"}

# operations whose completion takes the service away for good
ENDING = ("disconnect", "terminate", "port_out")


def operation_of(raw, values=None) -> str:
    op = str(raw or "").strip().lower().replace(" ", "_")
    op = ALIASES.get(op, op)
    current = (values or {}).get("_current_offering")
    if op == "modify" and (values or {}).get("offering_name") and current and \
            pc._key(values["offering_name"]) != pc._key(current):
        return "product_change"  # "upgrade to Fiber 900" names another product
    return op


def asset_of_order(db, tenant_id, order_id):
    """The installed service an order made - the live one when it made several."""
    order = db.get(Order, int(order_id))
    if order is None or order.tenant_id != tenant_id:
        raise ValueError(f"Order {order_id} not found")
    change = db.query(AssetChange).filter_by(order_id=order.id, tenant_id=tenant_id).first()
    ids = [change.asset_id] if change else []
    ids += [r.asset_id for r in db.query(AssetRevision).filter(AssetRevision.tenant_id == tenant_id,
                                                                AssetRevision.order_item_id.in_([i.id for i in order.items] or [-1]))]
    ids += [a.id for a in db.query(Asset).filter(Asset.tenant_id == tenant_id, Asset.order_item_id.in_([i.id for i in order.items] or [-1]))]
    assets = [a for a in (db.get(Asset, i) for i in dict.fromkeys(ids)) if a is not None and not a.superseded_by_asset_id]
    live = [a for a in assets if a.status != AssetStatus.decommissioned]
    if not live:
        raise ValueError(f"Order {order.id} has no installed service to change" + (" - it is decommissioned" if assets else ""))
    if len(live) > 1:
        raise ValueError(f"Order {order.id} installed {len(live)} services (assets {', '.join(str(a.id) for a in live)}) - pick the asset")
    return live[0]


def customer_assets(db, tenant_id, party_id):
    return db.query(Asset).join(ServiceAccount, Asset.service_account_id == ServiceAccount.id).join(
        BillingAccount, ServiceAccount.billing_account_id == BillingAccount.id).filter(
        Asset.tenant_id == tenant_id, Asset.superseded_by_asset_id.is_(None),
        or_(ServiceAccount.party_id == party_id, (ServiceAccount.party_id.is_(None) & (BillingAccount.party_id == party_id)))).all()


def recurring_price(asset):
    data = json.loads(asset.instance_json or "{}")
    return round(float(data.get("net_unit_price") or 0) * float(data.get("quantity") or 1), 2) if asset.status == AssetStatus.active else 0


def account(db, tenant_id, account_id, party_id=None):
    row = db.get(ServiceAccount, account_id)
    billing = db.get(BillingAccount, row.billing_account_id) if row else None
    owner_id = (row.party_id or billing.party_id) if row and billing else None
    if not row or row.tenant_id != tenant_id or not billing or billing.tenant_id != tenant_id or (party_id and owner_id != party_id):
        raise ValueError("Service account does not belong to the selected customer/workspace")
    return row, owner_id


def snapshot(db, order, item):
    offering = db.get(ProductOffering, item.product_offering_id)
    inst = db.query(ProductInstance).filter_by(tenant_id=order.tenant_id, context_type="order", context_id=order.id, item_id=item.id).order_by(ProductInstance.id.desc()).first()
    if inst is None:
        inst = instance_service.take(db, order.tenant_id, "order", order.id, offering.name, item_id=item.id)
        pricing, technical = pc.order_item_attributes(db, item.id) or ({}, {})
        inst.configuration_json = json.dumps({"pricing": pricing, "technical": technical, "quantity": item.quantity})
    net_price = item.net_unit_price
    if net_price is None and len(order.items) == 1:
        net_price = order.monthly_price / max(item.quantity, 1)
    out = {"definition": json.loads(inst.definition_json), "configuration": json.loads(inst.configuration_json),
           "price": json.loads(inst.price_json or "{}"), "source_instance_id": inst.id, "model_version": inst.model_version,
           "offering_name": inst.offering_name, "product_offering_id": item.product_offering_id,
           "quantity": item.quantity, "unit_price": item.unit_price, "discount_pct": item.discount_pct,
           "net_unit_price": net_price, "service_account_id": order.service_account_id,
           "party_id": order.party_id, "order_id": order.id, "order_item_id": item.id}
    promo = _ordered_promotion(db, order, inst.offering_name)
    if promo is not None:  # its commitment starts as the service is installed
        out["promotion"] = promotion_terms(promo, float(item.discount_pct or 0))
    return out


def _ordered_promotion(db, order, offering_name):
    """The promotion the order was sold with, when it covers this product."""
    from app.models.order_mgmt import OmOrderDetail, OmPromotion
    detail = db.query(OmOrderDetail).filter(OmOrderDetail.order_id == order.id).first()
    promo = db.get(OmPromotion, detail.promotion_id) if detail is not None and detail.promotion_id else None
    if promo is None or promo.tenant_id != order.tenant_id:
        return None
    needed = [p.strip() for p in (promo.products or "").split(",") if p.strip()]
    return promo if not needed or any(pc._key(p) == pc._key(offering_name) for p in needed) else None


def assetize(db, order, item):
    existing = db.query(AssetRevision).filter_by(order_item_id=item.id).first()
    if existing:
        return db.get(Asset, existing.asset_id)
    account(db, order.tenant_id, order.service_account_id, order.party_id)
    asset = db.query(Asset).filter_by(tenant_id=order.tenant_id, order_item_id=item.id).first()
    data = snapshot(db, order, item)
    if not asset and len(order.items) == 1:
        # Old acceptance work orders omitted their line reference. Reuse that
        # exact placeholder instead of inventing a second installed service.
        asset = db.query(Asset).filter_by(tenant_id=order.tenant_id, service_account_id=order.service_account_id,
            order_item_id=None, asset_type="Service", identifier=f"ORDER-{order.id}-ITEM-0", superseded_by_asset_id=None).first()
        if asset and not json.loads(asset.instance_json or "{}"):
            asset.order_item_id, asset.asset_type = item.id, data["offering_name"]
        else:
            asset = None
    if not asset:
        asset = Asset(tenant_id=order.tenant_id, service_account_id=order.service_account_id, order_item_id=item.id,
                      asset_type=data["offering_name"], identifier=f"ORDER-{order.id}-ITEM-{item.id}", status=AssetStatus.active)
        db.add(asset)
        db.flush()
    asset.instance_json = json.dumps(data)
    asset.attributes = json.dumps(data["configuration"].get("technical", {}))
    db.add(AssetRevision(tenant_id=order.tenant_id, asset_id=asset.id, order_item_id=item.id,
                         version=asset.version, snapshot_json=asset.instance_json))
    item.status = "completed"
    db.flush()
    return asset


def _technical_picks(design, technical):
    """An installed service's technical settings, split into its technical
    attributes (re-checked on a change) and the provisioning components the
    order copied in beside them (product_config_service.carry_to_order) -
    those are bundle components, chosen with the pricing picks, not
    technical attributes."""
    components = {pc._key(c["name"]) for c in (design or {}).get("components", [])}
    attributes = {pc._key(a["name"]) for a in (design or {}).get("attributes", [])}
    own, provisioning = {}, {}
    for name, value in (technical or {}).items():
        key = pc._key(name)
        (provisioning if key in components and key not in attributes else own)[name] = value
    return own, provisioning


def _sorted_for(design, picks, carried=False):
    """Settings moving to another product, sorted by what each is on THAT
    product: 'Contract term' may be technical on the old one and a pricing
    attribute on the new. A component is picked with the pricing; anything
    the new product doesn't have is left behind - and so is a carried value
    the new product doesn't offer (its own default applies instead)."""
    kinds = {pc._key(a["name"]): (a["name"], a["kind"], a.get("values") or []) for a in (design or {}).get("attributes", [])}
    for c in (design or {}).get("components", []):
        kinds.setdefault(pc._key(c["name"]), (c["name"], "pricing", []))
    pricing, technical = {}, {}
    for name, value in (picks or {}).items():
        found = kinds.get(pc._key(name))
        if found is None:
            continue
        allowed = {pc._key(v) for v in found[2] if str(v).strip()}
        if carried and allowed and pc._key(value) not in allowed:
            continue
        (technical if found[1] == "technical" else pricing)[found[0]] = value
    return pricing, technical


def _new_line(db, tenant_id, data, values, party):
    """A product change as Siebel builds it: the old product's line is
    deleted and the new product's line added - configured from the new
    product's defaults, every setting of the old service the new product
    also has (and allows), then what was entered. Returns the offering, its
    design, the proposed settings, the verified configuration (None when it
    doesn't verify yet) and why not."""
    from app.crm_core import product_model_service
    from app.integration import tmf620_adapter
    new = tmf620_adapter.get_active_offering_by_name(db, tenant_id, str(values.get("offering_name") or ""))
    if new is None:
        raise ValueError(tmf620_adapter.not_sellable(db, tenant_id, str(values.get("offering_name") or "")) + " - pick another product to change to")
    if new.id == data["product_offering_id"]:
        raise ValueError(f"The service is already {new.name} - use modify to change its options")
    design = pc.schema(db, tenant_id, new.name)
    picks = data.get("configuration") or {}
    carried_p, carried_t = _sorted_for(design, {**picks.get("pricing", {}), **picks.get("technical", {})}, carried=True)
    entered = {}
    for key in ("pricing_attributes", "technical_attributes"):
        raw = values.get(key) or {}
        entered.update(raw if isinstance(raw, dict) else pc.parse_against(design.get("attributes", []), str(raw)))
    entered_p, entered_t = _sorted_for(design, entered)
    defaults_p, defaults_t = {}, {}
    for attr in design.get("attributes", []):
        if attr.get("default") not in (None, ""):
            (defaults_t if attr["kind"] == "technical" else defaults_p)[attr["name"]] = attr["default"]
    proposed = {"pricing": {**defaults_p, **carried_p, **entered_p}, "technical": {**defaults_t, **carried_t, **entered_t}}
    errors = product_model_service.customer_rule_errors(db, tenant_id, new.name, party)
    if errors:
        raise ValueError("; ".join(errors))  # this customer can't have the new product at all
    configured = None
    price_list = pc.price_list_for(db, tenant_id, party)
    base, _ = pc.base_price(db, tenant_id, price_list, new, values.get("quantity") or data.get("quantity") or 1)
    # what the old service had is only a head start - when it breaks the new product's
    # rules, the line is configured from what was entered (and the new product's defaults)
    for pricing, technical in (({**carried_p, **entered_p}, {**carried_t, **entered_t}), (entered_p, entered_t)):
        try:
            configured = pc.configure(db, tenant_id, new.name, pricing, technical, base,
                                      require_technical=True, party=party, price_list=price_list)
            return new, design, proposed, configured, []
        except ValueError as exc:  # what the proposed settings break - as the form shows them
            errors = errors or [e.strip() for e in str(exc).split(";") if e.strip()]
    return new, design, proposed, configured, errors


def product_change_preview(db, tenant_id, asset_id, values):
    """Verify a product change before it is submitted (Siebel's Verify):
    the new line's proposed settings and what still stops it."""
    from app.models.crm import Party
    try:
        asset = db.query(Asset).filter_by(tenant_id=tenant_id, id=int(asset_id)).first()
        data = json.loads(asset.instance_json or "{}") if asset is not None else {}
        if not data:
            return None
        _, party_id = account(db, tenant_id, asset.service_account_id)
        new, _, proposed, configured, errors = _new_line(db, tenant_id, data, values, db.get(Party, party_id))
    except ValueError as exc:
        return {"offering_name": values.get("offering_name"), "pricing": {}, "technical": {}, "errors": [str(exc)]}
    return {"offering_name": new.name, "previous_offering": data.get("offering_name"), "pricing": proposed["pricing"],
            "technical": proposed["technical"], "configured_price": configured["configured"] if configured else None,
            "errors": errors}


# changes Siebel makes as a delta order: the old service's line deleted, a new one added - fulfilment
# closes the old asset and installs a new one (a new product, or the same product at the new site)
REPLACING = ("product_change", "relocate")


def _site(db, d):
    """Where a service is - its site, else its service account's name."""
    if d.get("site"):
        return str(d["site"])
    row = db.get(ServiceAccount, d.get("service_account_id")) if d.get("service_account_id") else None
    return row.name if row is not None else "the current site"


def _delta_lines(db, tenant_id, order, item, asset, before, data, operation="product_change"):
    """The change order's lines, as Siebel shows them: the old line deleted
    (its charge stops) and the new one added - a product change replaces the
    product, a relocation disconnects at the old site and connects at the new."""
    from app.models.order_mgmt import OmLine
    if operation == "relocate":
        old_note, new_note = f"Disconnect at {_site(db, before)}", f"Connect at {_site(db, data)}"
    else:
        old_note, new_note = f"Replaced by {data['offering_name']}", f"Replaces {before['offering_name']}"
    db.add(OmLine(tenant_id=tenant_id, context_type="order", context_id=order.id, line_number=1, product_name=before["offering_name"],
                  product_offering_id=before["product_offering_id"], quantity=before.get("quantity") or 1,
                  list_price=float(before.get("unit_price") or before.get("net_unit_price") or 0),
                  discount_pct=float(before.get("discount_pct") or 0), net_price=float(before.get("net_unit_price") or 0),
                  action_code="Delete", status="open", asset_id=asset.id, integration_id=f"SVC-{order.id}-{asset.id}-OLD",
                  note=old_note[:300]))
    db.add(OmLine(tenant_id=tenant_id, context_type="order", context_id=order.id, line_number=2, core_item_id=item.id,
                  product_name=data["offering_name"], product_offering_id=data["product_offering_id"], quantity=data["quantity"],
                  list_price=float(data.get("unit_price") or 0), discount_pct=float(data.get("discount_pct") or 0),
                  net_price=float(data.get("net_unit_price") or 0), action_code="Add", status="open", asset_id=asset.id,
                  integration_id=f"SVC-{order.id}-{item.id}", note=new_note[:300]))
    db.flush()


# the action code of the service's line on a change order (Siebel's delta); a product change and a
# relocation are a Delete and an Add (_delta_lines)
ACTION_CODE = {"suspend": "Suspend", "resume": "Resume", **{op: "Delete" for op in ENDING}}


def _picks(d):
    cfg = d.get("configuration") or {}
    return {**(cfg.get("technical") or {}), **(cfg.get("pricing") or {})}


def _present(value):
    """A component's quantity from its pick ('2', 'Yes', 'No', a choice)."""
    qty = pc._qty(value)
    return (0 if value in (None, "") else 1) if qty is None else qty


def delta(before, after, operation):
    """What a change does to the installed service, as Siebel's delta order
    shows it: the service's action code, every attribute that changes (old
    and new value), and every bundle component Added, Deleted, Updated or
    left as it is ('-'). Disconnecting, suspending or resuming the service
    takes its components with it."""
    action = ACTION_CODE.get(operation, "Update")
    design = after.get("definition") or before.get("definition") or {}
    components = {pc._key(c["name"]): c["name"] for c in design.get("components", [])}
    old, new = _picks(before), _picks(after)
    old_c = {pc._key(k): v for k, v in old.items() if pc._key(k) in components}
    new_c = {pc._key(k): v for k, v in new.items() if pc._key(k) in components}
    attributes = []
    for name in dict.fromkeys([*old, *new]):
        if pc._key(name) in components:
            continue
        was, now = old.get(name), new.get(name)
        if str(was if was is not None else "") != str(now if now is not None else ""):
            attributes.append({"name": name, "old": was, "new": now})
    parts = []
    for key, name in components.items():
        was, now = _present(old_c.get(key)), _present(new_c.get(key))
        if not was and not now:
            continue
        if action in ("Delete", "Suspend", "Resume"):
            part = action if was else None
        else:
            part = "Add" if not was else "Delete" if not now else "Update" if was != now else "-"
        if part:
            parts.append({"name": name, "action": part, "old_quantity": was, "new_quantity": now})
    out = {"action": action, "attributes": attributes, "components": parts}
    if (before.get("quantity") or 1) != (after.get("quantity") or 1):
        out["quantity"] = {"old": before.get("quantity") or 1, "new": after.get("quantity") or 1}
    for key in ("service_account_id", "party_id"):
        if before.get(key) != after.get(key):
            out[key] = {"old": before.get(key), "new": after.get(key)}
    return out


def _change_note(operation, change, data):
    said = [f"{a['name']}: {a['old'] if a['old'] not in (None, '') else '-'} -> {a['new'] if a['new'] not in (None, '') else '-'}"
            for a in change["attributes"]]
    if change.get("quantity"):
        said.append(f"quantity {change['quantity']['old']} -> {change['quantity']['new']}")
    if change.get("party_id"):
        said.append(f"to customer {change['party_id']['new']}")
    elif change.get("service_account_id"):
        said.append(f"to service account {change['service_account_id']['new']}")
    if operation == "promotion_change":
        said.append(f"promotion {(data.get('promotion') or {}).get('name') or 'removed'}")
    if operation in ("number_portability", "port_out") and data.get("portability"):
        said.append(f"number {data['portability'].get('port_number')}")
    return f"{LABELS[operation]}" + (": " + "; ".join(said) if said else "")


def _change_lines(db, tenant_id, order, item, asset, data, operation, change):
    """The change order's lines for a change made on the service itself: its
    line with the action code, and a line per bundle component (its price is
    in the service's line)."""
    from app.models.order_mgmt import OmLine
    ending = operation in ENDING
    db.add(OmLine(tenant_id=tenant_id, context_type="order", context_id=order.id, line_number=1, core_item_id=item.id,
                  product_name=data["offering_name"], product_offering_id=data["product_offering_id"], quantity=data["quantity"],
                  list_price=float(data.get("unit_price") or data.get("net_unit_price") or 0), discount_pct=float(data.get("discount_pct") or 0),
                  net_price=float(data.get("net_unit_price") or 0), action_code=change["action"], status="open", asset_id=asset.id,
                  integration_id=f"SVC-{order.id}-{item.id}", note=_change_note(operation, change, data)[:300]))
    for n, part in enumerate(change["components"], start=2):
        db.add(OmLine(tenant_id=tenant_id, context_type="order", context_id=order.id, line_number=n, product_name=part["name"],
                      kind="component", price_type="recurring", quantity=part["old_quantity"] if ending or part["action"] == "Delete"
                      else part["new_quantity"], list_price=0, net_price=0, action_code=part["action"], status="open", asset_id=asset.id,
                      integration_id=f"CMP-{order.id}-{asset.id}-{n}",
                      note=(f"Part of {data['offering_name']} (priced in its line)" + (
                          f": {part['old_quantity']} -> {part['new_quantity']}" if part["action"] == "Update" else ""))[:300]))
    db.flush()


def _next_line(db, order):
    from sqlalchemy import func
    from app.models.order_mgmt import OmLine
    top = db.query(func.max(OmLine.line_number)).filter(OmLine.context_type == "order", OmLine.context_id == order.id).scalar()
    return (top or 0) + 1


def _due(db, tenant_id, order, due):
    from app.crm_core import om_service
    from app.models.order_mgmt import OmLine
    db.flush()
    for line in db.query(OmLine).filter(OmLine.context_type == "order", OmLine.context_id == order.id):
        line.requested_date = due
    om_service.order_detail(db, tenant_id, order).requested_date = due


def _date(value):
    """A due date - today or later."""
    import datetime as dt
    from app.timeutils import utcnow
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        value = value.date()
    if not isinstance(value, dt.date):
        try:
            value = dt.date.fromisoformat(str(value).strip()[:10])
        except ValueError:
            raise ValueError(f"Due date '{value}' isn't a date (YYYY-MM-DD)") from None
    if value < utcnow().date():
        raise ValueError(f"The due date {value} has passed - a change is due today or later")
    return value


# a change still to be fulfilled: the open one, and those queued behind it
OPEN = ("submitted", "queued")


def open_change(db, asset_id):
    """The last change still to be fulfilled on an asset - a new one starts from what it makes."""
    return db.query(AssetChange).filter(AssetChange.asset_id == asset_id, AssetChange.status.in_(OPEN)) \
        .order_by(AssetChange.id.desc()).first()


def _check_queue(prior, due):
    import datetime as dt
    from app.timeutils import utcnow
    when = f", due {prior.due_date}" if prior.due_date else ""
    if prior.operation in ENDING + REPLACING:
        raise ValueError(f"This asset already has an unfulfilled change order (order {prior.order_id}{when}) that "
                         + ("disconnects" if prior.operation in ENDING else "replaces") + " it - nothing can be queued after it")
    floor = prior.due_date or utcnow().date()
    if due is None or due <= floor:
        raise ValueError(f"This asset already has an unfulfilled change order (order {prior.order_id}{when}) - fulfil or cancel it, "
                         f"or give this change a due date from {floor + dt.timedelta(days=1)} to queue it behind")


def _status_after(operation, status):
    return (AssetStatus.suspended if operation == "suspend" else AssetStatus.active if operation == "resume"
            else AssetStatus.decommissioned if operation in ENDING else status)


def _entered(values):
    """What was asked for - kept with the change so a revision starts from it."""
    skip = ("fulfil_now", "asset_id", "order_id", "asset_operation", "reason", "quote_number", "quote_id")
    return json.dumps({k: v for k, v in (values or {}).items() if not str(k).startswith("_") and k not in skip
                       and v not in (None, "", [], {})}, default=str)


def _with_provisioning(design, configured, provisioning):
    """The changed configuration's technical settings, with each provisioning
    component carried on at its quantity in the new picks (dropped when the
    change removed it)."""
    technical = dict(configured["technical"])
    picked = {pc._key(k): v for k, v in configured["pricing"].items()}
    for name, value in provisioning.items():
        now = picked.get(pc._key(name), value)
        if pc._qty(now):
            technical.setdefault(name, now)
    return technical


def _target_account(db, tenant_id, operation, values, data, party_id, billing_account_id):
    """The destination service account - the one named, else one made for
    the new site (relocation) or the new owner (account transfer)."""
    from app.crm_core import account_service
    from app.integration import order_adapter
    if values.get("target_service_account_id"):
        return int(values["target_service_account_id"])
    if operation == "relocate":
        site = str(values.get("target_site") or "").strip()
        if not site:
            raise ValueError("Relocation needs the destination site (or the destination service account)")
        return account_service.get_or_create_service_account(db, tenant_id, billing_account_id, f"{data['offering_name']} - {site}"[:150]).id
    from app.models.crm import Party
    try:
        target = db.get(Party, int(values.get("target_party_id") or 0))
    except (TypeError, ValueError):
        target = None
    if target is None or target.tenant_id != tenant_id:
        raise ValueError("Account transfer needs the new owner (target_party_id) or their service account")
    if target.id == party_id:
        raise ValueError("The service already belongs to that customer - use relocation to move it to another site")
    billing = order_adapter.get_or_create_billing_account(db, tenant_id, target.id)
    return account_service.get_or_create_service_account(db, tenant_id, billing.id, f"{data['offering_name']} Service").id


def _promotion(db, tenant_id, data, key):
    """The promotion asked for (None/"none" removes it) - checked like an order's."""
    from app.models.order_mgmt import OmPromotion
    from app.timeutils import utcnow
    key = str(key or "").strip()
    if key.lower() in ("", "none", "remove", "no promotion"):
        return None
    promo = db.query(OmPromotion).filter(OmPromotion.tenant_id == tenant_id, OmPromotion.active.is_(True)) \
        .filter((OmPromotion.code.ilike(key)) | (OmPromotion.name.ilike(key))).first()
    if promo is None:
        raise ValueError(f"No active promotion '{key}'")
    today = utcnow().date()
    if (promo.valid_from and today < promo.valid_from) or (promo.valid_to and today > promo.valid_to):
        raise ValueError(f"Promotion {promo.name} is not valid today")
    needed = [p.strip() for p in (promo.products or "").split(",") if p.strip()]
    if needed and not any(pc._key(p) == pc._key(data["offering_name"]) for p in needed):
        raise ValueError(f"Promotion {promo.name} is for {', '.join(needed)} - not {data['offering_name']}")
    return promo


def promotion_terms(promo, base_discount, start=None):
    """A promotion as an installed service carries it: its discount and its
    commitment - from when it started to when it ends - and the penalty for
    breaking it (Siebel's promotion commitment)."""
    from app.timeutils import utcnow
    start = start or utcnow().date()
    months = int(promo.commitment_months or 0)
    end = None
    if months:
        year, month = divmod(start.month - 1 + months, 12)
        end = start.replace(year=start.year + year, month=month + 1,
                            day=min(start.day, calendar.monthrange(start.year + year, month + 1)[1]))
    return {"code": promo.code, "name": promo.name, "discount_pct": promo.discount_pct, "discount_amount": promo.discount_amount,
            "commitment_months": months, "early_termination_fee": promo.early_termination_fee, "base_discount_pct": base_discount,
            "start_date": start.isoformat(), "commitment_end": end.isoformat() if end else None}


def penalty(promotion, today=None):
    """The early termination charge for breaking a promotion's commitment
    now - none once the commitment has ended (or when it never had one)."""
    import datetime as dt
    from app.timeutils import utcnow
    promotion = promotion or {}
    fee = float(promotion.get("early_termination_fee") or 0)
    end = promotion.get("commitment_end")
    if not fee or not end:
        return 0.0
    return round(fee, 2) if (today or utcnow().date()) < dt.date.fromisoformat(str(end)[:10]) else 0.0


def submit(db, tenant_id, asset_id, operation, values):
    from app.integration import order_adapter, orchestration_adapter
    values = dict(values or {})
    if not asset_id and values.get("order_id"):
        asset_id = asset_of_order(db, tenant_id, values["order_id"]).id  # the service a (completed) order installed
    if not asset_id:
        raise ValueError("Pick the installed service (asset) or the order that installed it")
    peek = db.query(Asset).filter_by(tenant_id=tenant_id, id=int(asset_id)).first()
    if peek is not None and peek.instance_json:
        values["_current_offering"] = json.loads(peek.instance_json).get("offering_name")
    operation = operation_of(operation, values)
    if operation not in OPERATIONS:
        raise ValueError("Unknown asset change operation - one of " + ", ".join(LABELS.values()))
    asset = db.query(Asset).filter_by(tenant_id=tenant_id, id=asset_id).with_for_update().first()
    if asset is None:
        raise ValueError("Asset not found")
    if asset.superseded_by_asset_id:
        raise ValueError(f"This legacy placeholder was superseded by asset #{asset.superseded_by_asset_id}")
    if asset.status == AssetStatus.decommissioned:
        raise ValueError("This asset is decommissioned")
    due = _date(values.get("due_date"))
    prior = open_change(db, asset.id)  # an open order on the asset: a later-dated change queues behind it
    status = asset.status
    if prior is not None:
        _check_queue(prior, due)
        status = _status_after(prior.operation, status)  # the service as it will be once that order is fulfilled
    if operation == "resume" and status != AssetStatus.suspended:
        raise ValueError("Only a suspended asset can be resumed")
    if operation == "suspend" and status == AssetStatus.suspended:
        raise ValueError("Asset is already suspended" if prior is None else f"Order {prior.order_id} already suspends it")
    if not json.loads(asset.instance_json or "{}"):
        source = db.get(OrderItem, asset.order_item_id)
        if not source:
            raise ValueError("Asset has no originating order line to snapshot")
        assetize(db, source.order, source)
    if operation not in ENDING + ("suspend", "resume") and status != AssetStatus.active:
        raise ValueError(f"The service is {status.value} - resume it before changing it" if prior is None else
                         f"The service will be {status.value} after order {prior.order_id} - queue a resume before changing it")
    # Siebel's requested asset: a change queued behind an open order starts from what that order makes
    data = json.loads(prior.requested_json if prior is not None else asset.instance_json)
    before = dict(data)  # the service as installed - the delta order's deleted line
    old_monthly = round(float(data.get("net_unit_price") or 0) * float(data.get("quantity") or 1), 2)
    service_account_id = asset.service_account_id
    source_account, party_id = account(db, tenant_id, service_account_id)
    if operation not in ENDING + ("suspend",):  # a blocked account can still be disconnected or suspended
        from app.crm_core import account_service
        account_service.check_standing(db, tenant_id, party_id)
    if operation in ("relocate", "customer_transfer"):
        service_account_id = _target_account(db, tenant_id, operation, values, data, party_id, source_account.billing_account_id)
        _, target_party = account(db, tenant_id, service_account_id)
        if operation == "relocate" and target_party != party_id:
            raise ValueError("Relocation must stay with the same customer; use customer transfer")
        if service_account_id == asset.service_account_id:
            raise ValueError("Select a different target service account")
        party_id = target_party
        if operation == "customer_transfer":
            from app.crm_core import product_model_service
            from app.models.crm import Party
            errors = product_model_service.customer_rule_errors(db, tenant_id, data["offering_name"], db.get(Party, party_id))
            if errors:
                raise ValueError("; ".join(errors))
        data["service_account_id"], data["party_id"] = service_account_id, party_id
    if operation == "relocate":
        if not str(values.get("target_site") or "").strip():
            raise ValueError("Relocation needs the destination site")
        data["site"] = values["target_site"]
    if operation == "number_portability":
        if not values.get("port_number") or not values.get("donor_operator"):
            raise ValueError("Number portability needs a number and donor operator")
        data["portability"] = {**{k: values[k] for k in ("port_number", "donor_operator")}, "direction": "in"}
    if operation == "port_out":
        number = values.get("port_number") or (data.get("portability") or {}).get("port_number")
        if not number or not values.get("recipient_operator"):
            raise ValueError("Port-out needs the number and the operator it moves to (recipient operator)")
        data["portability"] = {"port_number": str(number), "recipient_operator": values["recipient_operator"], "direction": "out"}
    if operation == "product_change":
        from app.crm_core import catalog_service
        from app.models.crm import Party
        new, design, _, configured, errors = _new_line(db, tenant_id, data, values, db.get(Party, party_id))
        if configured is None:
            raise ValueError(f"{new.name}: {'; '.join(errors)} - set these in the new product's attributes")
        provisioning = {c["name"]: configured["pricing"][c["name"]] for c in design.get("components", [])
                        if c.get("affects_provisioning") and c["name"] in configured["pricing"]}
        configured["technical"] = _with_provisioning(design, configured, provisioning)  # as an order carries them
        quantity = int(values.get("quantity") or data["quantity"])
        ceiling = new.price_plan.discount_ceiling_pct if new.price_plan else 100
        discount = float(values.get("requested_discount_pct") or 0)
        net = catalog_service.compute_price(configured["configured"], discount, ceiling, new.price_floor)
        data.update(previous_offering=data["offering_name"], offering_name=new.name, product_offering_id=new.id,
                    definition=design, model_version=design.get("version"), source_instance_id=None,
                    configuration={"pricing": configured["pricing"], "technical": configured["technical"], "quantity": quantity},
                    price=configured, quantity=quantity, unit_price=configured["configured"], discount_pct=discount, net_unit_price=net,
                    promotion=None)
    if operation == "promotion_change":
        promo = _promotion(db, tenant_id, data, values.get("promotion"))
        unit = float(data.get("unit_price") or data.get("net_unit_price") or 0)
        base_discount = float(data.get("discount_pct") or 0) if not (data.get("promotion") or {}).get("code") else \
            float((data.get("promotion") or {}).get("base_discount_pct") or 0)
        if promo is None:
            if not data.get("promotion"):
                raise ValueError("The service has no promotion to remove")
            data.update(promotion=None, discount_pct=base_discount, net_unit_price=round(unit * (1 - base_discount / 100), 2))
        else:
            pct = max(base_discount, promo.discount_pct or 0)
            net = max(unit * (1 - pct / 100) - (promo.discount_amount or 0), 0.0)
            data.update(promotion=promotion_terms(promo, base_discount), discount_pct=pct, net_unit_price=round(net, 2))
    if operation == "modify" and values.get("new_monthly_price") not in (None, ""):
        # a price agreed for the changed service (Change a live service, INT-08)
        quantity = int(values.get("quantity") or data["quantity"])
        data.update(quantity=quantity, net_unit_price=round(float(values["new_monthly_price"]) / max(quantity, 1), 2))
        data["configuration"] = {**data["configuration"], "quantity": quantity}
    elif operation == "modify":
        from app.models.crm import Party
        from app.crm_core import catalog_service
        party = db.get(Party, party_id)
        offering = db.get(ProductOffering, data["product_offering_id"])
        quantity = int(values.get("quantity", data["quantity"]))
        if quantity < 1:
            raise ValueError("Quantity must be positive")
        picks = data["configuration"]
        carried_t, provisioning = _technical_picks(data["definition"], picks.get("technical", {}))
        price_list = pc.price_list_for(db, tenant_id, party)
        base, _ = pc.base_price(db, tenant_id, price_list, offering, quantity)
        configured = pc.configure(db, tenant_id, offering.name, {**picks.get("pricing", {}), **(values.get("pricing_attributes") or {})},
                                  {**carried_t, **(values.get("technical_attributes") or {})}, base,
                                  require_technical=True, party=party, price_list=price_list, design=data["definition"])
        configured["technical"] = _with_provisioning(data["definition"], configured, provisioning)
        discount = float(values.get("requested_discount_pct", data.get("discount_pct") or 0))
        if not 0 <= discount <= 100:
            raise ValueError("Invalid discount")
        ceiling = offering.price_plan.discount_ceiling_pct if offering.price_plan else 100
        net = catalog_service.compute_price(configured["configured"], discount, ceiling, offering.price_floor)
        from app.crm_core import approval_service
        if discount and approval_service.resolve_monetary(db, tenant_id, "order", discount, getattr(party.profile, "segment", None), net * quantity):
            raise ValueError("This change requires discount approval; submit an approved discount before fulfillment")
        data.update(configuration={"pricing": configured["pricing"], "technical": configured["technical"], "quantity": quantity},
                    price=configured, quantity=quantity, unit_price=configured["configured"], discount_pct=discount, net_unit_price=net)
    monthly = float(data.get("net_unit_price") or 0) * data["quantity"]
    order = order_adapter.create_order(db, tenant_id=tenant_id, party_id=party_id, service_account_id=service_account_id,
                                       quote_id=None, order_type=OrderType.change, monthly_price=monthly)
    item = order_adapter.add_order_item(db, order.id, data["product_offering_id"], data["quantity"])
    item.unit_price, item.discount_pct, item.net_unit_price = data.get("unit_price"), data.get("discount_pct"), data.get("net_unit_price")
    item.status, order.status = "activating", OrderStatus.activating
    db.add(ProductInstance(tenant_id=tenant_id, context_type="order", context_id=order.id, item_id=item.id,
        offering_name=data["offering_name"], offering_id=data["product_offering_id"], model_version=data.get("model_version"),
        definition_json=json.dumps(data["definition"]), configuration_json=json.dumps(data["configuration"]),
        price_json=json.dumps(data["price"]), source_instance_id=data.get("source_instance_id")))
    data.update(order_id=order.id, order_item_id=item.id)
    if operation == "relocate":
        data.update(previous_site=_site(db, before), previous_service_account_id=before.get("service_account_id"))
    change_delta = None
    if operation in REPLACING:
        _delta_lines(db, tenant_id, order, item, asset, before, data, operation)
    else:  # every other change is a delta too: the service's line Updated / Suspended / Resumed / Deleted, and its components
        change_delta = delta(before, data, operation)
        _change_lines(db, tenant_id, order, item, asset, data, operation, change_delta)
    # breaking a promotion's commitment (disconnecting, or dropping the promotion) costs its penalty
    breaks = operation in ENDING or (operation == "promotion_change" and before.get("promotion") and not data.get("promotion"))
    charge = penalty(before.get("promotion")) if breaks else 0.0
    waived = bool(charge) and str(values.get("waive_penalty") or "").lower() in ("1", "true", "yes", "y")
    if charge and not waived:
        from app.models.order_mgmt import OmLine
        promo = before["promotion"]
        db.add(OmLine(tenant_id=tenant_id, context_type="order", context_id=order.id, line_number=_next_line(db, order),
                      product_name="Early termination charge",
                      kind="one_time", price_type="one_time", quantity=1, list_price=charge, net_price=charge, action_code="Add",
                      status="open", asset_id=asset.id, integration_id=f"ETF-{order.id}-{asset.id}",
                      note=f"{promo.get('name')}: committed until {promo.get('commitment_end')}"[:300]))
        data["early_termination_charge"] = charge
    if due is not None:  # a future-dated change: its lines are due then
        _due(db, tenant_id, order, due)
    ref = orchestration_adapter.submit_order_for_orchestration(order.id)
    db.add(AssetChange(tenant_id=tenant_id, asset_id=asset.id, order_id=order.id, operation=operation,
                       base_version=prior.base_version + 1 if prior is not None else asset.version,
                       requested_json=json.dumps(data), orchestration_ref=ref, status="queued" if prior is not None else "submitted",
                       due_date=due, after_change_id=prior.id if prior is not None else None, values_json=_entered(values)))
    db.flush()
    new_monthly = 0.0 if operation in ENDING else round(monthly, 2)
    out = {"order_id": order.id, "order_item_id": item.id, "asset_id": asset.id, "operation": operation,
           "operation_label": LABELS[operation], "status": order.status.value, "orchestration_ref": ref,
           "offering_name": data["offering_name"], "current_monthly": old_monthly, "new_monthly": new_monthly,
           "monthly_change": round(new_monthly - old_monthly, 2)}
    if change_delta is not None:
        out["delta"] = change_delta
    if due is not None:
        out["due_date"] = due.isoformat()
    if prior is not None:  # Siebel's open orders: this one is fulfilled after the one it was built on
        out.update(change_status="queued", queued_behind_order_id=prior.order_id)
    if operation in ("modify", "product_change", "promotion_change"):
        out["direction"] = "upgrade" if new_monthly > old_monthly else "downgrade" if new_monthly < old_monthly else "no price change"
    if operation == "product_change":
        out.update(previous_offering=before["offering_name"],
                   lines=[{"action": "Delete", "product": before["offering_name"], "monthly": old_monthly},
                          {"action": "Add", "product": data["offering_name"], "monthly": new_monthly}])
    if charge:
        out.update(promotion_penalty=0.0 if waived else charge, penalty_waived=waived,
                   commitment_end=before["promotion"].get("commitment_end"))
    if operation == "relocate":
        out.update(previous_site=data["previous_site"], site=data["site"],
                   lines=[{"action": "Delete", "product": before["offering_name"], "site": data["previous_site"], "monthly": old_monthly},
                          {"action": "Add", "product": data["offering_name"], "site": data["site"], "monthly": new_monthly}])
    if values.get("fulfil_now"):
        complete(db, order)  # fulfilled in the same step (the live-service intents INT-08/09/69/70)
        out.update(status=order.status.value, asset_status=asset.status.value)
    return out


def installed(order) -> bool:
    """A completed order's service lives on as an asset - changes go through it."""
    return order is not None and order.status == OrderStatus.completed


def live_change(db, tenant_id, order, operation, values=None):
    """A live-service intent on a completed order (Change a live service,
    Suspend / cancel / terminate, Resume, Apply a promotion): a change order
    from the service it installed, fulfilled in the same step - as those
    intents always acted at once."""
    return submit(db, tenant_id, None, operation, {**(values or {}), "order_id": order.id, "fulfil_now": True})


def complete(db, order):
    """Called only by fulfillment. Idempotent; optimistic revision check."""
    if order.status == OrderStatus.completed:
        return
    change = db.query(AssetChange).filter_by(order_id=order.id, tenant_id=order.tenant_id).first()
    if change:
        asset = db.query(Asset).filter_by(id=change.asset_id, tenant_id=order.tenant_id).with_for_update().one()
        if change.status == "completed":
            return
        if change.status == "queued":  # open orders are fulfilled in the order they were built on each other
            prior = db.get(AssetChange, change.after_change_id) if change.after_change_id else None
            raise ValueError(f"Order {order.id} is queued behind order {prior.order_id if prior else '?'}"
                             + (f" (due {change.due_date})" if change.due_date else "") + " - fulfil that one first")
        if change.status != "submitted":
            raise ValueError(f"Order {order.id}'s asset change was {change.status} - it can't be fulfilled")
        data = json.loads(change.requested_json)
        changed = db.execute(update(Asset).where(Asset.id == asset.id, Asset.version == change.base_version)
                             .values(version=change.base_version + 1)).rowcount
        if changed != 1:
            raise ValueError("Asset changed since order submission; reconcile before fulfillment")
        if change.operation in REPLACING:
            _replace_asset(db, order, asset, data, change.base_version + 1, change.operation)
        else:
            _update_asset(db, order, change, asset, data)
        from app.timeutils import utcnow
        change.status, change.completed_at = "completed", utcnow()
        db.flush()  # (one open change per asset) the next one queued behind this becomes the open one
        for queued in db.query(AssetChange).filter_by(after_change_id=change.id, status="queued"):
            queued.status = "submitted"
        if change.operation not in REPLACING:  # Auto-Asset: its delta lines are done
            from app.models.order_mgmt import OmLine
            for line in db.query(OmLine).filter(OmLine.tenant_id == order.tenant_id, OmLine.context_type == "order",
                                                OmLine.context_id == order.id, OmLine.status == "open", OmLine.asset_id == asset.id):
                line.status = "complete"
        if change.operation in REPLACING:
            from app.models.order_mgmt import OmLine  # Auto-Asset: the delta lines are done
            for line in db.query(OmLine).filter(OmLine.tenant_id == order.tenant_id, OmLine.context_type == "order",
                                                OmLine.context_id == order.id, OmLine.action_code.in_(("Add", "Delete"))):
                line.status = "complete"
                if line.action_code == "Add":
                    line.asset_id = data["asset_id"]
    elif order.order_type != OrderType.new and (source := _follow_on_source(db, order)) is not None:
        _complete_follow_on(db, order, source)
    else:
        if order.order_type != OrderType.new:
            raise ValueError("Change orders must originate from an asset")
        for item in order.items:
            assetize(db, order, item)
    for item in order.items:
        item.status = "completed"
    order.status = OrderStatus.completed
    db.flush()


def _replace_asset(db, order, asset, data, version, operation):
    """Siebel's Auto-Asset for a product change or a relocation: the old
    asset is closed (kept, decommissioned, pointing at its replacement) and
    the new product - or the same one at the new site - is installed as a
    new asset that points back at it."""
    item = order.items[0]
    new = Asset(tenant_id=order.tenant_id, service_account_id=data["service_account_id"], order_item_id=item.id,
                asset_type=data["offering_name"], identifier=f"ORDER-{order.id}-ITEM-{item.id}", status=AssetStatus.active)
    db.add(new)
    db.flush()
    data.update(status=AssetStatus.active.value, replaces_asset_id=asset.id, asset_id=new.id)
    new.instance_json = json.dumps(data)
    new.attributes = json.dumps(data["configuration"].get("technical", {}))
    db.add(AssetRevision(tenant_id=order.tenant_id, asset_id=new.id, order_item_id=item.id, version=new.version,
                         snapshot_json=new.instance_json))
    # the old asset keeps its last revision as it was installed; its closing is the completed
    # change (asset_changes) - a revision is one per order line, and this line made the new asset
    old = json.loads(asset.instance_json or "{}")
    old.update(status=AssetStatus.decommissioned.value, replaced_by_asset_id=new.id, replaced_by_offering=data["offering_name"],
               closed_by_order_id=order.id, closed_version=version)
    if operation == "relocate":
        old["moved_to_site"] = data.get("site")
    asset.status = AssetStatus.decommissioned
    asset.instance_json = json.dumps(old)


def _update_asset(db, order, change, asset, data):
    """Every other change updates the installed asset as a new revision."""
    asset.service_account_id = data["service_account_id"]
    asset.status = (AssetStatus.decommissioned if change.operation in ENDING else
                    AssetStatus.suspended if change.operation == "suspend" else
                    AssetStatus.active if change.operation == "resume" else asset.status)
    data["status"] = asset.status.value
    asset.instance_json = json.dumps(data)
    asset.attributes = json.dumps(data["configuration"].get("technical", {}))
    db.add(AssetRevision(tenant_id=order.tenant_id, asset_id=asset.id, order_item_id=order.items[0].id,
                         version=change.base_version + 1, snapshot_json=asset.instance_json))


def _follow_on_source(db, order):
    """The order a follow-on order (INT-63) continues, when it is one."""
    from app.models.order_mgmt import OmOrderDetail
    detail = db.query(OmOrderDetail).filter(OmOrderDetail.order_id == order.id).first()
    source = db.get(Order, detail.source_order_id) if detail is not None and detail.source_order_id else None
    return source if source is not None and source.tenant_id == order.tenant_id else None


def _complete_follow_on(db, order, source):
    """A follow-on order changes the services its source order installed:
    a product still on it updates that asset (quantity, price) as a new
    revision; a product new to it is installed as a new asset; a product
    whose quantity fell to zero is decommissioned."""
    installed = {}
    for item in source.items:
        for asset in db.query(Asset).filter(Asset.tenant_id == order.tenant_id, Asset.order_item_id == item.id,
                                            Asset.superseded_by_asset_id.is_(None)):
            if asset.status != AssetStatus.decommissioned:
                installed.setdefault(item.product_offering_id, asset)
    for item in order.items:
        asset = installed.get(item.product_offering_id)
        if asset is None:
            if item.quantity > 0:
                assetize(db, order, item)
            continue
        data = json.loads(asset.instance_json or "{}") or snapshot(db, source, db.get(OrderItem, asset.order_item_id))
        data.update(quantity=item.quantity, order_id=order.id, order_item_id=item.id)
        data["configuration"] = {**data.get("configuration", {}), "quantity": item.quantity}
        if item.net_unit_price is not None:
            data.update(unit_price=item.unit_price, discount_pct=item.discount_pct, net_unit_price=item.net_unit_price)
        if item.quantity <= 0:
            asset.status = AssetStatus.decommissioned
        data["status"] = asset.status.value
        asset.version += 1
        asset.instance_json = json.dumps(data)
        db.add(AssetRevision(tenant_id=order.tenant_id, asset_id=asset.id, order_item_id=item.id, version=asset.version,
                             snapshot_json=asset.instance_json))
    db.flush()


def backfill(db, tenant_id):
    """Adopt already fulfilled legacy orders without executing provisioning."""
    from app.models.comms import CmBillingProfile
    from app.integration import order_adapter
    for profile in db.query(CmBillingProfile).filter_by(tenant_id=tenant_id, billing_account_id=None).all():
        profile.billing_account_id = order_adapter.get_or_create_billing_account(db, tenant_id, profile.party_id).id
    for service in db.query(ServiceAccount).filter_by(tenant_id=tenant_id, party_id=None).all():
        billing = db.get(BillingAccount, service.billing_account_id)
        if billing and billing.tenant_id == tenant_id:
            service.party_id = billing.party_id
    db.flush()
    for order in db.query(Order).filter(Order.tenant_id == tenant_id, Order.order_type == OrderType.new,
                                        Order.status.in_([OrderStatus.active, OrderStatus.changed, OrderStatus.suspended])):
        suspended = order.status == OrderStatus.suspended
        for item in order.items:
            asset = assetize(db, order, item)
            if suspended:
                asset.status = AssetStatus.suspended
        order.status = OrderStatus.completed
        db.flush()
    reconcile_placeholders(db, tenant_id)


def reconcile_placeholders(db, tenant_id):
    """Retire only exact order-level placeholders with an unambiguous product asset.

    Keep their IDs, attributes and external references for audit; never merge
    different subscriptions just because they have the same product/account.
    """
    import re
    for old in db.query(Asset).filter_by(tenant_id=tenant_id, asset_type="Service", order_item_id=None, superseded_by_asset_id=None).all():
        match = re.fullmatch(r"ORDER-(\d+)-ITEM-0", old.identifier or "")
        if not match or json.loads(old.instance_json or "{}") or old.revisions or old.changes:
            continue
        order = db.get(Order, int(match[1]))
        if not order or order.tenant_id != tenant_id or order.service_account_id != old.service_account_id or order.status != OrderStatus.completed or len(order.items) != 1:
            continue
        targets = db.query(Asset).filter_by(tenant_id=tenant_id, order_item_id=order.items[0].id, superseded_by_asset_id=None).all()
        if len(targets) != 1 or targets[0].id == old.id:
            continue
        target = targets[0]
        if target.service_account_id != old.service_account_id:
            continue
        if not target.inventory_system_ref:
            target.inventory_system_ref = old.inventory_system_ref
        old.superseded_by_asset_id, old.status = target.id, AssetStatus.decommissioned
    db.flush()


# ------------------------------------------------- in-flight change orders: cancel and revise (Siebel's Revise)


def change_of_order(db, tenant_id, order):
    """The asset change a change order makes, while it is still to be fulfilled."""
    return db.query(AssetChange).filter(AssetChange.tenant_id == tenant_id, AssetChange.order_id == order.id,
                                        AssetChange.status.in_(OPEN)).first()


def _open_change(db, tenant_id, order):
    change = db.query(AssetChange).filter_by(tenant_id=tenant_id, order_id=order.id).first()
    if change is None:
        raise ValueError(f"Order {order.id} isn't an asset change order")
    if change.status not in OPEN:
        raise ValueError(f"Order {order.id}'s asset change is already {change.status}")
    from app.crm_core import ponr_service
    if ponr_service.has_reached_ponr(db, tenant_id, "order", order.status.value):
        raise ValueError(f"Order {order.id} has passed the point of no return - change the installed service with a new change order")
    later = db.query(AssetChange).filter(AssetChange.after_change_id == change.id, AssetChange.status.in_(OPEN)).first()
    if later is not None:
        raise ValueError(f"Order {later.order_id} is queued behind this change (it was built on it) - cancel that one first")
    return change


def _close(db, tenant_id, order, change, status, reason):
    """Stop a change order: the installed service stays as it is."""
    from app.crm_core import om_service
    from app.models.order_mgmt import OmLine
    change.status, change.reason = status, str(reason)[:300]
    order.status = OrderStatus.cancelled
    for item in order.items:
        item.status = "cancelled"
    for line in db.query(OmLine).filter(OmLine.context_type == "order", OmLine.context_id == order.id):
        line.status = "cancelled"
    detail = om_service.order_detail(db, tenant_id, order)
    detail.om_status, detail.cancel_reason = "cancelled", str(reason)[:200]
    db.flush()


def cancel(db, tenant_id, order, reason=""):
    """Cancel a change order before it is fulfilled - the installed service is
    left exactly as it is (and the next change can be ordered)."""
    change = _open_change(db, tenant_id, order)
    reason = str(reason or "").strip() or "Cancelled at the customer's request"
    _close(db, tenant_id, order, change, "cancelled", reason)
    asset = db.get(Asset, change.asset_id)
    return {"order_id": order.id, "asset_id": asset.id, "operation": change.operation, "operation_label": LABELS.get(change.operation, change.operation),
            "status": "cancelled", "reason": reason, "asset_status": asset.status.value, "asset_version": asset.version,
            "note": "The installed service is unchanged"}


def revise(db, tenant_id, order, values, reason=""):
    """Siebel's Revise of an in-flight change order: a new revision of the
    change, worked out again from the installed service with what was asked
    for and what changes now - it replaces the old one, which is closed."""
    change = _open_change(db, tenant_id, order)
    given = {k: v for k, v in (values or {}).items() if not str(k).startswith("_") and v not in (None, "", [], {})}
    operation = given.pop("asset_operation", None) or change.operation
    for key in ("order_id", "asset_id", "reason"):
        given.pop(key, None)
    if not given and operation == change.operation:
        raise ValueError("Say what changes in the revision - a quantity, attributes, a due date, another product or site")
    asked = {**json.loads(change.values_json or "{}"), **given}
    revision = 2
    seen = change
    while seen.revision_of_change_id:
        revision += 1
        seen = db.get(AssetChange, seen.revision_of_change_id)
    _close(db, tenant_id, order, change, "revised", str(reason or "").strip() or f"Revised (revision {revision})")
    out = submit(db, tenant_id, change.asset_id, operation, asked)
    new = db.query(AssetChange).filter_by(order_id=out["order_id"]).one()
    new.revision_of_change_id = change.id
    db.flush()
    return {**out, "revision": revision, "revision_of_order_id": order.id, "replaced_order_status": "cancelled"}


# ------------------------------------------------- verify, quote, bulk


def preview(db, tenant_id, asset_id, operation, values):
    """Verify a change without placing it: the change order it would make -
    its delta lines, the monthly change, any penalty - worked out and rolled back."""
    from app.crm_core import om_service
    savepoint = db.begin_nested()
    try:
        out = submit(db, tenant_id, asset_id, operation, {**(values or {}), "fulfil_now": False})
        order = db.get(Order, out["order_id"])
        rows = [om_service.line_out(r) for r in om_service.lines(db, tenant_id, "order", order)]
        totals = om_service.totals(db, tenant_id, "order", order)
    finally:
        savepoint.rollback()
    shown = {k: v for k, v in out.items() if k not in ("order_id", "order_item_id", "orchestration_ref", "status", "lines")}
    shown["order_lines"] = [{"line": r["line_number"], "action": r["action_code"], "product": r["product_name"], "quantity": r["quantity"],
                             "net_price": r["net_price"], "price_type": r["price_type"], "note": r["note"]} for r in rows]
    shown["totals"] = {k: totals[k] for k in ("recurring", "one_time", "due_now")}
    return shown


QUOTE_VALID_DAYS = 30


def quote(db, tenant_id, asset_id, operation, values):
    """Siebel's Modify into a quote: the change priced and held for the
    customer, ordered later from the quote (order_quote)."""
    import datetime as dt
    from app.models.abo import AssetQuote
    from app.timeutils import utcnow
    values = dict(values or {})
    if not asset_id and values.get("order_id"):
        asset_id = asset_of_order(db, tenant_id, values["order_id"]).id
    if not asset_id:
        raise ValueError("Pick the installed service (asset) or the order that installed it")
    shown = preview(db, tenant_id, int(asset_id), operation, values)
    asset = db.get(Asset, int(asset_id))
    row = AssetQuote(tenant_id=tenant_id, asset_id=asset.id, operation=shown["operation"], base_version=asset.version,
                     values_json=_entered(values), preview_json=json.dumps(shown, default=str),
                     valid_until=utcnow().date() + dt.timedelta(days=QUOTE_VALID_DAYS))
    db.add(row)
    db.flush()
    row.quote_number = f"AQ-{row.id}"
    db.flush()
    return {"quote_id": row.id, "quote_number": row.quote_number, "valid_until": row.valid_until.isoformat(), "quote_status": row.status, **shown}


def _asset_quote(db, tenant_id, ref):
    import re
    from app.models.abo import AssetQuote
    m = re.search(r"\d+", str(ref or ""))
    row = db.get(AssetQuote, int(m.group(0))) if m else None
    if row is None or row.tenant_id != tenant_id:
        raise ValueError(f"Change quote {ref} not found")
    return row


def order_quote(db, tenant_id, ref):
    """Order a quoted change: still valid, and the service unchanged since it was quoted."""
    from app.timeutils import utcnow
    row = _asset_quote(db, tenant_id, ref)
    if row.status != "quoted":
        raise ValueError(f"Change quote {row.quote_number} is already {row.status}" + (f" (order {row.order_id})" if row.order_id else ""))
    if row.valid_until < utcnow().date():
        raise ValueError(f"Change quote {row.quote_number} expired on {row.valid_until} - quote the change again")
    asset = db.get(Asset, row.asset_id)
    if asset.version != row.base_version:
        raise ValueError(f"The service changed since {row.quote_number} was quoted (version {row.base_version} -> {asset.version}) - "
                         "quote the change again")
    out = submit(db, tenant_id, asset.id, row.operation, json.loads(row.values_json or "{}"))
    row.status, row.order_id = "ordered", out["order_id"]
    quoted = json.loads(row.preview_json or "{}").get("new_monthly")
    if quoted is not None and round(float(quoted), 2) != round(out["new_monthly"], 2):
        out["price_note"] = f"Quoted at {quoted}/month - priced at {out['new_monthly']}/month today"
    db.flush()
    return {"quote_number": row.quote_number, **out}


def _ids(raw):
    import re
    if raw in (None, ""):
        return []
    items = raw if isinstance(raw, (list, tuple)) else re.findall(r"\d+", str(raw))
    return list(dict.fromkeys(int(i) for i in items))


def bulk(db, tenant_id, operation, values, asset_ids=None, party_id=None, current_offering=None):
    """Siebel's bulk request: one change applied to many installed services -
    each its own change order; one that can't be changed is reported, the
    rest go ahead."""
    ids = _ids(asset_ids)
    if not ids and party_id:
        ids = [a.id for a in customer_assets(db, tenant_id, int(party_id)) if a.status != AssetStatus.decommissioned
               and (not current_offering or pc._key(a.asset_type) == pc._key(current_offering))]
    if not ids:
        raise ValueError("Name the installed services (asset ids), or the customer whose services change"
                         + (f" - customer {party_id} has no live {current_offering or 'services'}" if party_id else ""))
    results = []
    for asset_id in ids:
        savepoint = db.begin_nested()
        try:
            out = submit(db, tenant_id, asset_id, operation, dict(values or {}))
            savepoint.commit()
            results.append({"asset_id": asset_id, "status": "submitted", "order_id": out["order_id"],
                            "offering_name": out["offering_name"], "monthly_change": out["monthly_change"]})
        except ValueError as exc:
            savepoint.rollback()
            results.append({"asset_id": asset_id, "status": "failed", "error": str(exc)})
    done = [r for r in results if r["status"] == "submitted"]
    if not done:
        raise ValueError("No service was changed - " + "; ".join(f"asset {r['asset_id']}: {r['error']}" for r in results))
    return {"operation": operation_of(operation), "requested": len(ids), "submitted": len(done), "failed": len(results) - len(done),
            "order_ids": [r["order_id"] for r in done], "monthly_change": round(sum(r["monthly_change"] for r in done), 2), "results": results}


# ------------------------------------------------- the asset's history, as of a date, and what it will be


def _summary(d, status=None):
    promo = d.get("promotion") or {}
    return {"offering_name": d.get("offering_name"), "quantity": d.get("quantity") or 1,
            "monthly": round(float(d.get("net_unit_price") or 0) * float(d.get("quantity") or 1), 2),
            "status": status or d.get("status") or "active", "site": d.get("site"), "service_account_id": d.get("service_account_id"),
            "pricing": (d.get("configuration") or {}).get("pricing") or {}, "technical": (d.get("configuration") or {}).get("technical") or {},
            "promotion": promo.get("name"), "commitment_end": promo.get("commitment_end")}


def components(d):
    """The service's bundle components and their quantities (Siebel's asset hierarchy)."""
    design = d.get("definition") or {}
    picks = {pc._key(k): v for k, v in _picks(d).items()}
    return [{"name": c["name"], "quantity": _present(picks.get(pc._key(c["name"])))} for c in design.get("components", [])
            if _present(picks.get(pc._key(c["name"])))]


def lineage(db, asset):
    """The assets a service has been, oldest first: a product change or a
    relocation closes one asset and installs the next."""
    chain, seen = [asset], {asset.id}
    while True:
        back = json.loads(chain[0].instance_json or "{}").get("replaces_asset_id")
        row = db.get(Asset, back) if back else None
        if row is None or row.id in seen:
            break
        chain.insert(0, row)
        seen.add(row.id)
    while True:
        on = json.loads(chain[-1].instance_json or "{}").get("replaced_by_asset_id")
        row = db.get(Asset, on) if on else None
        if row is None or row.id in seen:
            break
        chain.append(row)
        seen.add(row.id)
    return chain


def history(db, tenant_id, asset_id, as_of=None):
    """An installed service's life: each revision (the order that made it),
    each change order and where it stands, the assets it has been, what it
    was on a date, and what it will be once its open orders are fulfilled."""
    import datetime as dt
    asset = db.query(Asset).filter_by(tenant_id=tenant_id, id=int(asset_id)).first()
    if asset is None:
        raise ValueError(f"Asset {asset_id} not found")
    data = json.loads(asset.instance_json or "{}")
    by_id = {c.id: c for c in asset.changes}
    out = {"asset_id": asset.id, "product": asset.asset_type, "status": asset.status.value, "version": asset.version,
           "current": _summary(data, asset.status.value), "components": components(data),
           "revisions": [{"version": r.version, "order_id": json.loads(r.snapshot_json).get("order_id"), "at": r.created_at.isoformat(),
                          **_summary(json.loads(r.snapshot_json))} for r in asset.revisions],
           "changes": [{"order_id": c.order_id, "operation": c.operation, "label": LABELS.get(c.operation, c.operation), "status": c.status,
                        "due_date": c.due_date.isoformat() if c.due_date else None, "submitted_at": c.created_at.isoformat(),
                        "completed_at": c.completed_at.isoformat() if c.completed_at else None, "reason": c.reason,
                        "queued_behind_order_id": by_id[c.after_change_id].order_id if c.after_change_id in by_id else None,
                        "revision_of_order_id": by_id[c.revision_of_change_id].order_id if c.revision_of_change_id in by_id else None}
                       for c in asset.changes],
           "lineage": [{"asset_id": a.id, "product": a.asset_type, "status": a.status.value, "current": a.id == asset.id,
                        "site": json.loads(a.instance_json or "{}").get("site")} for a in lineage(db, asset)]}
    pending = [c for c in asset.changes if c.status in OPEN]
    if pending:  # Siebel's requested asset: the service once every open order is fulfilled, in turn
        status = asset.status
        for c in pending:
            status = _status_after(c.operation, status)
        last = pending[-1]
        out["requested"] = {"after_order_id": last.order_id, "due_date": last.due_date.isoformat() if last.due_date else None,
                            **_summary(json.loads(last.requested_json), status.value)}
    if as_of not in (None, ""):
        try:
            when = as_of if isinstance(as_of, dt.date) else dt.date.fromisoformat(str(as_of).strip()[:10])
        except ValueError:
            raise ValueError(f"'{as_of}' isn't a date (YYYY-MM-DD)") from None
        events = sorted(((r.created_at, a, r) for a in lineage(db, asset) for r in a.revisions), key=lambda e: e[0])
        seen = [e for e in events if e[0].date() <= when]
        if not seen:
            out["as_of"] = {"date": when.isoformat(), "note": f"The service wasn't installed yet on {when}"}
        else:
            at, owner, rev = seen[-1]
            out["as_of"] = {"date": when.isoformat(), "asset_id": owner.id, "version": rev.version,
                            **_summary(json.loads(rev.snapshot_json))}
    return out
