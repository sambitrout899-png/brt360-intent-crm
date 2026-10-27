"""Service inventory, replenishment and repairs.

The inventory is transaction based: every movement of a part is one
FsInventoryTransaction with a product, quantity, source and destination,
and only transactions change FsInventoryLevel. A location of None is the
outside world - a supplier when stock comes in, the customer site when a
part is installed. Good stock may not go below zero; the defective bucket
holds parts on their way to repair.

Repairs follow the RMA loop: receive and tag the defective part, repair it
in-house or ship it to a third-party vendor and receive it back, then
return it to the customer, put it back into good stock or scrap it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.assurance import Technician
from app.models.field_service import FsInventoryLevel, FsInventoryLocation, FsInventoryTransaction, FsRepair
from app.timeutils import utcnow

LOCATION_KINDS = ("warehouse", "field_office", "trunk", "repair_center", "vendor")
TRANSACTION_KINDS = {
    "receive": "Receive from supplier",
    "transfer": "Stock transfer",
    "replenish": "Replenishment",
    "consume": "Installed at customer",
    "return_defective": "Defective part returned",
    "repair": "Repaired - back to good",
    "adjust": "Cycle count adjustment",
    "scrap": "Scrapped",
    "ship_to_vendor": "Shipped to repair vendor",
    "receive_from_vendor": "Received from repair vendor",
    "return_to_customer": "Returned to customer",
}


def _key(name: str) -> str:
    return (name or "").strip().lower()


def location(db: Session, tenant_id: int, location_id: int | None) -> FsInventoryLocation | None:
    if not location_id:
        return None
    loc = db.get(FsInventoryLocation, location_id)
    if loc is None or loc.tenant_id != tenant_id:
        raise ValueError(f"Inventory location {location_id} not found")
    return loc


def level(db: Session, tenant_id: int, location_id: int, product: str, status: str = "good", create: bool = True) -> FsInventoryLevel | None:
    row = (
        db.query(FsInventoryLevel)
        .filter(FsInventoryLevel.tenant_id == tenant_id, FsInventoryLevel.location_id == location_id,
                FsInventoryLevel.status == status, FsInventoryLevel.product_name.ilike(product.strip()))
        .first()
    )
    if row is None and create:
        price = (
            db.query(FsInventoryLevel)
            .filter(FsInventoryLevel.tenant_id == tenant_id, FsInventoryLevel.product_name.ilike(product.strip()))
            .order_by(FsInventoryLevel.unit_price.desc())
            .first()
        )
        row = FsInventoryLevel(tenant_id=tenant_id, location_id=location_id, product_name=product.strip(), status=status,
                               on_hand=0.0, reserved=0.0, unit_cost=price.unit_cost if price else 0.0,
                               unit_price=price.unit_price if price else 0.0)
        db.add(row)
        db.flush()
    return row


def unit_price(db: Session, tenant_id: int, product: str) -> float:
    row = (
        db.query(FsInventoryLevel)
        .filter(FsInventoryLevel.tenant_id == tenant_id, FsInventoryLevel.product_name.ilike(product.strip()), FsInventoryLevel.unit_price > 0)
        .first()
    )
    return row.unit_price if row else 0.0


def move(db: Session, tenant_id: int, kind: str, product: str, quantity: float, *, source_id: int | None = None,
         target_id: int | None = None, source_status: str = "good", target_status: str = "good",
         reference_type: str = "", reference_id: int | None = None, note: str = "") -> FsInventoryTransaction:
    """Commit one inventory transaction."""
    if kind not in TRANSACTION_KINDS:
        raise ValueError(f"Unknown transaction type '{kind}'")
    product = (product or "").strip()
    if not product:
        raise ValueError("Which product?")
    if quantity <= 0:
        raise ValueError("Quantity must be more than zero")
    if source_id is None and target_id is None:
        raise ValueError("A transaction needs a source or a destination location")
    src = location(db, tenant_id, source_id)
    dst = location(db, tenant_id, target_id)
    if src is not None:
        bucket = level(db, tenant_id, src.id, product, source_status, create=src.kind == "vendor")
        available = (bucket.on_hand - bucket.reserved) if bucket else 0.0
        if src.kind != "vendor" and available < quantity:
            raise ValueError(f"Only {available:g} {product} ({source_status}) available at {src.name}")
        bucket.on_hand -= quantity
    if dst is not None:
        bucket = level(db, tenant_id, dst.id, product, target_status)
        bucket.on_hand += quantity
    txn = FsInventoryTransaction(
        tenant_id=tenant_id, kind=kind, product_name=product, quantity=quantity,
        from_location_id=src.id if src else None, from_status=source_status,
        to_location_id=dst.id if dst else None, to_status=target_status,
        reference_type=reference_type, reference_id=reference_id, note=note,
    )
    db.add(txn)
    db.flush()
    return txn


def adjust(db: Session, tenant_id: int, location_id: int, product: str, counted: float, status: str = "good") -> FsInventoryTransaction | None:
    """Cycle count: set what is really on the shelf; the difference becomes an adjustment."""
    loc = location(db, tenant_id, location_id)
    bucket = level(db, tenant_id, loc.id, product, status)
    delta = counted - bucket.on_hand
    if abs(delta) < 1e-9:
        return None
    bucket.on_hand = counted
    txn = FsInventoryTransaction(
        tenant_id=tenant_id, kind="adjust", product_name=bucket.product_name, quantity=abs(delta),
        from_location_id=None if delta > 0 else loc.id, from_status=status,
        to_location_id=loc.id if delta > 0 else None, to_status=status,
        note=f"Counted {counted:g} ({'+' if delta > 0 else ''}{delta:g})",
    )
    db.add(txn)
    db.flush()
    return txn


def trunk_of(db: Session, tenant_id: int, technician_id: int | None) -> FsInventoryLocation | None:
    if not technician_id:
        return None
    return (
        db.query(FsInventoryLocation)
        .filter(FsInventoryLocation.tenant_id == tenant_id, FsInventoryLocation.technician_id == technician_id,
                FsInventoryLocation.kind == "trunk", FsInventoryLocation.active.is_(True))
        .first()
    )


def location_out(db: Session, loc: FsInventoryLocation, levels: list[FsInventoryLevel] | None = None) -> dict[str, Any]:
    tech = db.get(Technician, loc.technician_id) if loc.technician_id else None
    levels = levels if levels is not None else db.query(FsInventoryLevel).filter(FsInventoryLevel.location_id == loc.id).all()
    rows = [level_out(lv) for lv in sorted(levels, key=lambda lv: (lv.status != "good", _key(lv.product_name)))]
    return {
        "id": loc.id, "name": loc.name, "kind": loc.kind, "parent_id": loc.parent_id, "technician_id": loc.technician_id,
        "technician_name": tech.name if tech else None, "region_id": loc.region_id, "address": loc.address, "active": loc.active,
        "levels": rows,
        "value": round(sum(lv.on_hand * lv.unit_cost for lv in levels if lv.status == "good"), 2),
        "below_min": sum(1 for lv in levels if lv.status == "good" and lv.min_qty > 0 and lv.on_hand < lv.min_qty),
    }


def level_out(lv: FsInventoryLevel) -> dict[str, Any]:
    return {
        "id": lv.id, "location_id": lv.location_id, "product_name": lv.product_name, "status": lv.status,
        "on_hand": lv.on_hand, "reserved": lv.reserved, "available": lv.on_hand - lv.reserved,
        "min_qty": lv.min_qty, "max_qty": lv.max_qty, "unit_cost": lv.unit_cost, "unit_price": lv.unit_price,
        "locator": lv.locator, "below_min": lv.status == "good" and lv.min_qty > 0 and lv.on_hand < lv.min_qty,
    }


def locations(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    locs = db.query(FsInventoryLocation).filter(FsInventoryLocation.tenant_id == tenant_id).order_by(FsInventoryLocation.id).all()
    levels = db.query(FsInventoryLevel).filter(FsInventoryLevel.tenant_id == tenant_id).all()
    by_loc: dict[int, list[FsInventoryLevel]] = {}
    for lv in levels:
        by_loc.setdefault(lv.location_id, []).append(lv)
    return [location_out(db, loc, by_loc.get(loc.id, [])) for loc in locs]


def part_browser(db: Session, tenant_id: int, query: str = "") -> list[dict[str, Any]]:
    """Where a part is available - every location holding good stock of it."""
    q = db.query(FsInventoryLevel).filter(FsInventoryLevel.tenant_id == tenant_id, FsInventoryLevel.status == "good")
    if query.strip():
        q = q.filter(FsInventoryLevel.product_name.ilike(f"%{query.strip()}%"))
    locs = {loc.id: loc for loc in db.query(FsInventoryLocation).filter(FsInventoryLocation.tenant_id == tenant_id).all()}
    products: dict[str, dict[str, Any]] = {}
    for lv in q.all():
        loc = locs.get(lv.location_id)
        if loc is None or not loc.active:
            continue
        p = products.setdefault(_key(lv.product_name), {"product_name": lv.product_name, "total": 0.0, "unit_price": lv.unit_price, "locations": []})
        p["total"] += lv.on_hand - lv.reserved
        p["unit_price"] = max(p["unit_price"], lv.unit_price)
        tech = db.get(Technician, loc.technician_id) if loc.technician_id else None
        p["locations"].append({"location_id": loc.id, "location_name": loc.name, "kind": loc.kind,
                               "technician_name": tech.name if tech else None, "available": lv.on_hand - lv.reserved})
    out = sorted(products.values(), key=lambda p: _key(p["product_name"]))
    for p in out:
        p["locations"].sort(key=lambda r: -r["available"])
    return out


def transactions(db: Session, tenant_id: int, limit: int = 100, reference_type: str | None = None, reference_id: int | None = None) -> list[dict[str, Any]]:
    q = db.query(FsInventoryTransaction).filter(FsInventoryTransaction.tenant_id == tenant_id)
    if reference_type:
        q = q.filter(FsInventoryTransaction.reference_type == reference_type)
    if reference_id:
        q = q.filter(FsInventoryTransaction.reference_id == reference_id)
    names = {loc.id: loc.name for loc in db.query(FsInventoryLocation).filter(FsInventoryLocation.tenant_id == tenant_id).all()}
    return [
        {"id": t.id, "kind": t.kind, "label": TRANSACTION_KINDS.get(t.kind, t.kind), "product_name": t.product_name, "quantity": t.quantity,
         "from": names.get(t.from_location_id, "Outside") if t.from_location_id else "Outside", "from_status": t.from_status,
         "to": names.get(t.to_location_id, "Customer") if t.to_location_id else ("Customer" if t.kind in ("consume", "return_to_customer") else "Outside"),
         "to_status": t.to_status, "reference_type": t.reference_type, "reference_id": t.reference_id, "note": t.note, "created_at": t.created_at}
        for t in q.order_by(FsInventoryTransaction.id.desc()).limit(limit).all()
    ]


def _source_for(db: Session, tenant_id: int, loc: FsInventoryLocation, product: str, need: float) -> tuple[FsInventoryLocation | None, float]:
    """Replenishment source: the parent chain first, then any warehouse with stock."""
    seen: set[int] = {loc.id}
    parent = db.get(FsInventoryLocation, loc.parent_id) if loc.parent_id else None
    candidates: list[FsInventoryLocation] = []
    while parent is not None and parent.id not in seen:
        candidates.append(parent)
        seen.add(parent.id)
        parent = db.get(FsInventoryLocation, parent.parent_id) if parent.parent_id else None
    candidates += [
        w for w in db.query(FsInventoryLocation).filter(FsInventoryLocation.tenant_id == tenant_id, FsInventoryLocation.kind.in_(("warehouse", "field_office")),
                                                        FsInventoryLocation.active.is_(True)).all()
        if w.id not in seen
    ]
    for c in candidates:
        lv = level(db, tenant_id, c.id, product, "good", create=False)
        spare = (lv.on_hand - lv.reserved - max(lv.min_qty, 0)) if lv else 0.0
        if spare > 0:
            return c, min(spare, need)
    return None, 0.0


def replenish(db: Session, tenant_id: int, apply: bool = False, location_id: int | None = None) -> list[dict[str, Any]]:
    """Every good-stock level below its minimum, topped up to its maximum from the nearest source."""
    q = db.query(FsInventoryLevel).filter(FsInventoryLevel.tenant_id == tenant_id, FsInventoryLevel.status == "good", FsInventoryLevel.min_qty > 0)
    if location_id:
        q = q.filter(FsInventoryLevel.location_id == location_id)
    out = []
    for lv in q.all():
        if lv.on_hand >= lv.min_qty:
            continue
        loc = db.get(FsInventoryLocation, lv.location_id)
        if loc is None or not loc.active:
            continue
        need = max(lv.max_qty, lv.min_qty) - lv.on_hand
        source, qty = _source_for(db, tenant_id, loc, lv.product_name, need)
        row = {"location_id": loc.id, "location_name": loc.name, "product_name": lv.product_name, "on_hand": lv.on_hand,
               "min_qty": lv.min_qty, "max_qty": lv.max_qty, "need": need,
               "source_id": source.id if source else None, "source_name": source.name if source else None, "quantity": qty,
               "action": "transfer" if source else "order from supplier", "done": False}
        if apply and source and qty > 0:
            move(db, tenant_id, "replenish", lv.product_name, qty, source_id=source.id, target_id=loc.id, note="Replenishment")
            row["done"] = True
        out.append(row)
    return out


# ---------------------------------------------------------------- repairs

REPAIR_FLOW = {
    # action: (allowed from statuses, new status)
    "start_repair": (("received",), "in_repair"),
    "ship_to_vendor": (("received", "in_repair"), "at_vendor"),
    "receive_from_vendor": (("at_vendor",), "repaired"),
    "mark_repaired": (("received", "in_repair"), "repaired"),
    "return_to_customer": (("repaired",), "returned"),
    "return_to_stock": (("repaired",), "in_stock"),
    "scrap": (("received", "in_repair", "at_vendor", "repaired"), "scrapped"),
}
REPAIR_OPEN = ("received", "in_repair", "at_vendor", "repaired")


def receive_repair(db: Session, tenant_id: int, *, product_name: str, location_id: int, party_id: int | None = None,
                   asset_id: int | None = None, ticket_id: int | None = None, serial_number: str = "", symptoms: str = "",
                   kind: str = "internal", vendor: str = "", under_warranty: bool = False) -> FsRepair:
    """Receive and tag a defective product (RMA)."""
    loc = location(db, tenant_id, location_id)
    if loc is None:
        raise ValueError("Which repair location receives it?")
    repair = FsRepair(tenant_id=tenant_id, party_id=party_id, asset_id=asset_id, ticket_id=ticket_id, product_name=product_name.strip(),
                      serial_number=serial_number, symptoms=symptoms, kind=kind if kind in ("internal", "external") else "internal",
                      vendor=vendor, status="received", location_id=loc.id, under_warranty=under_warranty)
    db.add(repair)
    db.flush()
    repair.repair_number = f"RPR-{repair.id:05d}"
    move(db, tenant_id, "return_defective", repair.product_name, 1, target_id=loc.id, target_status="defective",
         reference_type="repair", reference_id=repair.id, note=f"{repair.repair_number} received")
    db.flush()
    return repair


def repair_action(db: Session, tenant_id: int, repair_id: int, action: str, vendor: str = "", resolution: str = "",
                  location_id: int | None = None) -> FsRepair:
    repair = db.get(FsRepair, repair_id)
    if repair is None or repair.tenant_id != tenant_id:
        raise ValueError(f"Repair {repair_id} not found")
    if action not in REPAIR_FLOW:
        raise ValueError(f"Unknown repair action '{action}'")
    allowed, new_status = REPAIR_FLOW[action]
    if repair.status not in allowed:
        raise ValueError(f"A repair that is '{repair.status}' can't '{action.replace('_', ' ')}'")
    ref = {"reference_type": "repair", "reference_id": repair.id}
    now = utcnow()
    if action == "ship_to_vendor":
        repair.kind, repair.vendor = "external", vendor or repair.vendor or "Third-party vendor"
        move(db, tenant_id, "ship_to_vendor", repair.product_name, 1, source_id=repair.location_id, source_status="defective",
             note=f"{repair.repair_number} to {repair.vendor}", **ref)
        repair.shipped_at = now
    elif action == "receive_from_vendor":
        target = location_id or repair.location_id
        move(db, tenant_id, "receive_from_vendor", repair.product_name, 1, target_id=target, note=f"{repair.repair_number} back from {repair.vendor}", **ref)
        repair.location_id = target
    elif action == "mark_repaired":
        move(db, tenant_id, "repair", repair.product_name, 1, source_id=repair.location_id, source_status="defective",
             target_id=repair.location_id, note=f"{repair.repair_number} repaired", **ref)
    elif action == "return_to_customer":
        move(db, tenant_id, "return_to_customer", repair.product_name, 1, source_id=repair.location_id, note=f"{repair.repair_number} returned", **ref)
        repair.returned_at, repair.closed_at = now, now
    elif action == "return_to_stock":
        target = location_id or repair.location_id
        if target != repair.location_id:
            move(db, tenant_id, "transfer", repair.product_name, 1, source_id=repair.location_id, target_id=target, **ref)
            repair.location_id = target
        repair.closed_at = now
    elif action == "scrap":
        if repair.status != "at_vendor":
            status = "good" if repair.status == "repaired" else "defective"
            move(db, tenant_id, "scrap", repair.product_name, 1, source_id=repair.location_id, source_status=status,
                 note=f"{repair.repair_number} scrapped", **ref)
        repair.closed_at = now
    repair.status = new_status
    if resolution:
        repair.resolution = resolution
    db.flush()
    return repair


def repair_out(db: Session, r: FsRepair) -> dict[str, Any]:
    from app.models.crm import Party

    party = db.get(Party, r.party_id) if r.party_id else None
    loc = db.get(FsInventoryLocation, r.location_id) if r.location_id else None
    return {
        "id": r.id, "repair_number": r.repair_number, "party_id": r.party_id, "party_name": party.name if party else None,
        "asset_id": r.asset_id, "ticket_id": r.ticket_id, "product_name": r.product_name, "serial_number": r.serial_number,
        "symptoms": r.symptoms, "kind": r.kind, "vendor": r.vendor, "status": r.status, "location_id": r.location_id,
        "location_name": loc.name if loc else None, "under_warranty": r.under_warranty, "resolution": r.resolution,
        "received_at": r.received_at, "shipped_at": r.shipped_at, "returned_at": r.returned_at, "closed_at": r.closed_at,
        "actions": [a for a, (allowed, _) in REPAIR_FLOW.items() if r.status in allowed],
    }
