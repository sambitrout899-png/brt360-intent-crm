"""Fulfillment engine - layered COM / SOM / TOM order orchestration (see models/fulfillment_orders.py).

  design   validate_graph / preview / standard_graph - what the pattern designer uses
  receive  a CRM order -> the matching fulfillment pattern -> its COM order
  plan     each layer's orchestration plan: the designed one (deterministic) or
           one an LLM chose from the designed functions (llm), validated, with
           the designed plan as the fallback
  run      advance(): every task whose dependencies are done runs, under its own
           savepoint; a task may wait - for its child order (route to SOM / TOM),
           an engineer closing a work order, or an inventory response
  return   a child order completing completes the parent's routing task, so the
           order moves back up: TOM -> SOM -> COM
  fallout  a failed task holds its order in fallout until someone retries it,
           skips it or completes it by hand (resolve)
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.fulfillment_orders import LAYERS, PLAN_MODES, FulfillmentOrder, FulfillmentPattern, FulfillmentTask
from app.orchestration import flow_errors
from app.orchestration import fulfillment_functions as fx
from app.timeutils import utcnow

DONE = ("completed", "skipped")
OPERATORS = ("eq", "ne", "gt", "gte", "lt", "lte", "contains", "in", "empty", "not_empty")


def _j(text: str | None, default: Any) -> Any:
    try:
        value = json.loads(text) if text else default
    except ValueError:
        return default
    return default if value is None else value


# ================================================================== design


def load_graph(pattern: FulfillmentPattern) -> dict[str, Any]:
    graph = _j(pattern.graph_json, {})
    graph.setdefault("nodes", [])
    graph.setdefault("edges", [])
    return graph


def lane(graph: dict[str, Any], layer: str) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """A layer's nodes and, per node, the nodes it waits for."""
    nodes = [n for n in graph.get("nodes") or [] if n.get("layer") == layer]
    ids = {n["id"] for n in nodes}
    deps: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    for e in graph.get("edges") or []:
        if e.get("source") in ids and e.get("target") in ids:
            deps[e["target"]].append(e["source"])
    return nodes, deps


def topo(nodes: list[dict[str, Any]], deps: dict[str, list[str]]) -> list[str] | None:
    """Node ids in an order that respects the dependencies (left to right on ties); None if they loop."""
    pos = {n["id"]: (n.get("x", 0), n.get("y", 0)) for n in nodes}
    remaining = {k: set(v) for k, v in deps.items()}
    ordered: list[str] = []
    while remaining:
        ready = sorted((k for k, v in remaining.items() if not v), key=lambda k: pos.get(k, (0, 0)))
        if not ready:
            return None
        nxt = ready[0]
        ordered.append(nxt)
        remaining.pop(nxt)
        for v in remaining.values():
            v.discard(nxt)
    return ordered


def known_resources(db: Session, tenant_id: int) -> set[str]:
    """Resource types a step may ask inventory for: the standard ones and the tenant's own (Inventory item types)."""
    from app.crm_core import inventory_service
    from app.integration import inventory_adapter

    return set(inventory_adapter.LOGICAL_RESOURCES) | set(inventory_adapter.PHYSICAL_RESOURCES) | set(inventory_service.resource_types(db, tenant_id))


def validate_graph(graph: dict[str, Any], resources: set[str] | None = None) -> list[str]:
    from app.integration import inventory_adapter

    errors: list[str] = []
    nodes = graph.get("nodes") or []
    ids = [n.get("id") for n in nodes]
    if len(set(ids)) != len(ids) or not all(ids):
        errors.append("Every step needs its own id")
    by_id = {n.get("id"): n for n in nodes}
    known_resources = resources or (set(inventory_adapter.LOGICAL_RESOURCES) | set(inventory_adapter.PHYSICAL_RESOURCES))
    for n in nodes:
        name = n.get("label") or n.get("id")
        spec = fx.FUNCTIONS.get(n.get("function"))
        if n.get("layer") not in LAYERS:
            errors.append(f"'{name}' is in no lane - put it in COM, SOM or TOM")
            continue
        if spec is None:
            errors.append(f"'{name}' has an unknown function '{n.get('function')}'")
            continue
        if spec["layer"] not in ("*", n["layer"]):
            errors.append(f"'{name}' ({spec['label']}) belongs in the {spec['layer']} lane, not {n['layer']}")
        config = n.get("config") or {}
        if config.get("when_field") and (config.get("when_operator") or "eq") not in OPERATORS:
            errors.append(f"'{name}': unknown condition operator '{config.get('when_operator')}'")
        if n.get("function") == "field_work_order" and (config.get("kind") or "installation") not in fx.WORK_ORDER_KINDS:
            errors.append(f"'{name}': work order kind must be one of {', '.join(fx.WORK_ORDER_KINDS)}")
        if spec.get("inventory") and config.get("resources"):
            raw = config["resources"] if isinstance(config["resources"], list) else str(config["resources"]).split(",")
            unknown = [r.strip() for r in raw if r.strip() and r.strip() not in known_resources]
            if unknown:
                errors.append(f"'{name}': unknown resource type(s) {', '.join(unknown)} - known: {', '.join(sorted(known_resources))}")
        if config.get("response") not in (None, "", "sync", "async"):
            errors.append(f"'{name}': response must be sync or async")
    for e in graph.get("edges") or []:
        a, b = by_id.get(e.get("source")), by_id.get(e.get("target"))
        if a is None or b is None:
            errors.append(f"Dependency {e.get('id')} points at a missing step")
        elif a.get("layer") != b.get("layer") and (
                {a.get("layer"), b.get("layer")} != {"SOM", "TOM"}
                or a.get("function", "").startswith("route_") or b.get("function", "").startswith("route_")):
            errors.append(f"'{a.get('label') or a['id']}' -> '{b.get('label') or b['id']}' crosses lanes - a lane hands over "
                          "through its Route step; only non-routing SOM and TOM tasks may depend on each other")
        elif a["id"] == b["id"]:
            errors.append(f"'{a.get('label') or a['id']}' can't wait for itself")
    for layer in LAYERS:
        lane_nodes, deps = lane(graph, layer)
        if lane_nodes and topo(lane_nodes, deps) is None:
            errors.append(f"The {layer} lane's dependencies go round in a circle")
    com = [n for n in nodes if n.get("layer") == "COM"]
    if not com:
        errors.append("The COM lane needs at least one step - customer order management receives the CRM order")
    for router, child, parent in (("route_to_som", "SOM", "COM"), ("route_to_tom", "TOM", "SOM")):
        count = sum(1 for n in nodes if n.get("function") == router)
        has_child = any(n.get("layer") == child for n in nodes)
        if has_child and count != 1:
            errors.append(f"The {child} lane has steps - the {parent} lane needs exactly one '{fx.FUNCTIONS[router]['label']}' step")
        if count and not has_child:
            errors.append(f"'{fx.FUNCTIONS[router]['label']}' routes to an empty {child} lane")
    # Model the start and completion of routing separately. A child may signal
    # its parent before it finishes, but cannot wait for its own route to finish.
    expanded = [{"id": i} for i in ids if i]
    waits = {i: [] for i in ids if i}
    for e in graph.get("edges") or []:
        if e.get("source") in waits and e.get("target") in waits:
            waits[e["target"]].append(e["source"])
    for n in nodes:
        child = (fx.FUNCTIONS.get(n.get("function")) or {}).get("route")
        if not child or not n.get("id"):
            continue
        start = f"__start__{n['id']}"
        while start in waits:
            start += "_"
        expanded.append({"id": start})
        waits[start] = waits[n["id"]]
        children = [c["id"] for c in nodes if c.get("layer") == child]
        waits[n["id"]] = [start, *children]
        for c in children:
            waits[c].append(start)
    if topo(expanded, waits) is None:
        errors.append("Dependencies would deadlock across the routing and task steps")
    return errors


def standard_graph() -> dict[str, Any]:
    """The eTOM fixed-broadband pattern: COM registers billing and routes to SOM; SOM designs,
    assigns logical, reserves physical, activates and routes to TOM; TOM installs, tests and
    gets the acceptance signed; back in COM billing starts, assets are created, the order completes."""
    lanes = {
        "COM": [("c1", "sync_customer", {}), ("c2", "create_billing_account", {}), ("c3", "create_billing_profile", {}),
                ("c4", "route_to_som", {}), ("c5", "start_billing", {}), ("c6", "create_assets", {}), ("c7", "complete_order", {})],
        "SOM": [("s1", "design_service", {"resources": "service_id, circuit_id"}), ("s2", "assign_logical", {"resources": "ip_block, vlan"}),
                ("s3", "reserve_physical", {"resources": "port, ont"}), ("s4", "activate_service", {}), ("s5", "route_to_tom", {})],
        "TOM": [("t1", "field_work_order", {"kind": "installation", "subject": "Installation", "skills": "fiber_install", "duration_minutes": "180"}),
                ("t2", "field_work_order", {"kind": "connectivity_test", "subject": "Connectivity test", "duration_minutes": "60"}),
                ("t3", "field_work_order", {"kind": "acceptance", "subject": "Customer acceptance signing", "duration_minutes": "30"})],
    }
    nodes, edges = [], []
    for row, (layer, steps) in enumerate(lanes.items()):
        for col, (node_id, function, config) in enumerate(steps):
            label = config.get("subject") or fx.FUNCTIONS[function]["label"]
            nodes.append({"id": node_id, "layer": layer, "function": function, "label": label, "x": 30 + col * 200, "y": 50 + row * 170,
                          "config": config, "mandatory": function.startswith("route_") or function == "complete_order"})
            if col:
                edges.append({"id": f"e-{node_id}", "source": steps[col - 1][0], "target": node_id})
    return {"nodes": nodes, "edges": edges}


# ================================================================== matching + order data


def order_data(db: Session, order) -> dict[str, Any]:
    """What a CRM order hands to orchestration: products, configuration, segment, site."""
    from app.crm_core import product_config_service as product_config
    from app.models.catalog import ProductOffering
    from app.models.crm import Party, Quote

    party = db.get(Party, order.party_id)
    products, technical, pricing = [], {}, {}
    for item in order.items:
        offering = db.get(ProductOffering, item.product_offering_id)
        products.append(offering.name if offering else f"#{item.product_offering_id}")
        attrs = product_config.order_item_attributes(db, item.id)
        if attrs is not None:
            pricing.update(attrs[0] or {})
            technical.update(attrs[1] or {})
    address = ""
    quote = db.get(Quote, order.quote_id) if order.quote_id else None
    if quote is not None and quote.opportunity is not None and quote.opportunity.config is not None:
        address = quote.opportunity.config.site_address or ""
    if not address:
        from app.models.order_mgmt import OmOrderDetail

        detail = db.query(OmOrderDetail).filter(OmOrderDetail.order_id == order.id).first()
        address = getattr(detail, "ship_to_address", "") or ""
    segment = (order.profile.segment if order.profile else None) or (party.profile.segment if party and party.profile else None) or ""
    return {"order": {"id": order.id, "type": order.order_type.value, "segment": segment, "monthly_price": order.monthly_price,
                      "contract_months": order.contract_months, "party_id": order.party_id, "party_name": party.name if party else "",
                      "service_account_id": order.service_account_id, "products": products, "technical": technical,
                      "pricing": pricing, "address": address}}


APPROVAL_INTENT = "INT-25"  # Order Control approval - what starts a pattern that names no intents


def triggers(pattern: FulfillmentPattern) -> list[str]:
    """The intents that start the pattern (vanilla INT-n, custom FLOW-n)."""
    return [c.strip().upper() for c in (pattern.trigger_intents or "").split(",") if c.strip()] or [APPROVAL_INTENT]


def fits(pattern: FulfillmentPattern, data: dict[str, Any]) -> bool:
    """Do the pattern's products / segment / order type fit the order (order_data(...)["order"])?"""
    names = {p.lower() for p in data["products"]}
    wanted = {p.strip().lower() for p in (pattern.products or "").split(",") if p.strip()}
    if wanted and not (wanted & names):
        return False
    if pattern.segment and pattern.segment.lower() != (data["segment"] or "").lower():
        return False
    return not (pattern.order_type and pattern.order_type != data["type"])


def matching_pattern(db: Session, tenant_id: int, order, intent: str | None = None) -> FulfillmentPattern | None:
    """The highest-priority active pattern that fits the order - and, given the intent that
    finished on it, one that intent starts. intent=None (sent by hand): any pattern that fits."""
    data = order_data(db, order)["order"]
    for pattern in (db.query(FulfillmentPattern).filter(FulfillmentPattern.tenant_id == tenant_id, FulfillmentPattern.is_active.is_(True))
                    .order_by(FulfillmentPattern.priority.desc(), FulfillmentPattern.id)):
        if (intent is None or intent.upper() in triggers(pattern)) and fits(pattern, data):
            return pattern
    return None


# ================================================================== planning


class PlanUnusable(ValueError):
    pass


def _closure(node_id: str, deps: dict[str, list[str]], kept: set[str]) -> list[str]:
    """A kept node's dependencies once some designed steps are left out: it waits for what they waited for."""
    out: list[str] = []
    stack = list(deps.get(node_id, []))
    seen: set[str] = set()
    while stack:
        d = stack.pop()
        if d in seen:
            continue
        seen.add(d)
        if d in kept:
            out.append(d)
        else:
            stack.extend(deps.get(d, []))
    return sorted(set(out))


def _llm_plan(db: Session, tenant_id: int, layer: str, nodes: list[dict[str, Any]], deps: dict[str, list[str]],
              data: dict[str, Any]) -> tuple[list[str], dict[str, list[str]], str]:
    from app.crm_core import llm

    target = llm.resolve(db, tenant_id)
    if target is None:
        raise PlanUnusable("no AI model configured")
    functions = [{"id": n["id"], "function": n["function"], "label": n.get("label"), "mandatory": bool(n.get("mandatory")) or n["function"].startswith("route_")
                  or n["function"] == "complete_order", "what": fx.FUNCTIONS[n["function"]]["description"], "designed_depends_on": deps[n["id"]],
                  "config": n.get("config") or {}} for n in nodes]
    try:
        answer = llm.chat_json(
            target,
            "You plan telecom order fulfillment on eTOM, in customer / service / technical order layers. For ONE order layer you get the functions the tenant "
            "designed and the order. Decide which functions THIS order needs and what each waits for. Keep every mandatory "
            "function. Use only the given ids. Answer strictly as JSON "
            '{"include": ["id", ...], "depends_on": {"id": ["id", ...]}, "note": "one sentence why"}.',
            json.dumps({"layer": layer, "order": data.get("order"), "functions": functions}, default=str),
        )
    except Exception as exc:  # noqa: BLE001 - any model failure falls back to the designed plan
        raise PlanUnusable(f"AI unavailable ({type(exc).__name__})") from exc
    ids = {n["id"] for n in nodes}
    include = [i for i in answer.get("include") or [] if i in ids] if isinstance(answer, dict) else []
    mandatory = {f["id"] for f in functions if f["mandatory"]}
    if not include or not mandatory <= set(include):
        raise PlanUnusable("the AI plan dropped a mandatory step")
    kept = set(include)
    given = answer.get("depends_on") if isinstance(answer.get("depends_on"), dict) else {}
    plan_deps = {}
    for node_id in include:
        raw = given.get(node_id)
        plan_deps[node_id] = sorted({d for d in raw if d in kept and d != node_id}) if isinstance(raw, list) else _closure(node_id, deps, kept)
    if topo([n for n in nodes if n["id"] in kept], plan_deps) is None:
        raise PlanUnusable("the AI plan's dependencies loop")
    return include, plan_deps, str(answer.get("note") or "")[:500]


def plan_layer(db: Session, tenant_id: int, pattern: FulfillmentPattern, layer: str, data: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, list[str]], str, str]:
    """(nodes, depends_on, planned_by, note) for one layer of an order."""
    nodes, deps = lane(load_graph(pattern), layer)
    graph = load_graph(pattern)
    by_id = {n["id"]: n for n in graph["nodes"]}
    cross = [e for e in graph["edges"] if e["source"] in by_id and e["target"] in by_id
             and by_id[e["source"]]["layer"] != by_id[e["target"]]["layer"]]
    if cross:
        # A per-lane AI planner cannot drop a task another lane depends on.
        for e in cross:
            if e["target"] in deps:
                deps[e["target"]].append(e["source"])
        return nodes, deps, "deterministic", "Designed dependencies between SOM and TOM are preserved."
    if pattern.mode != "llm" or not nodes:
        return nodes, deps, "deterministic", ""
    try:
        include, plan_deps, note = _llm_plan(db, tenant_id, layer, nodes, deps, data)
    except PlanUnusable as exc:
        return nodes, deps, "llm_fallback", f"Designed plan used: {exc}"
    left_out = [n.get("label") or n["id"] for n in nodes if n["id"] not in include]
    note = note + (f" Left out: {', '.join(left_out)}." if left_out else "")
    return [n for n in nodes if n["id"] in include], plan_deps, "llm", note.strip()


# ================================================================== run


def _data(f_order: FulfillmentOrder) -> dict[str, Any]:
    return _j(f_order.data_json, {})


def _milestone(db: Session, f_order: FulfillmentOrder, code: str, label: str, detail: str = "") -> None:
    from app.orchestration import actions_sales

    actions_sales.milestone(db, f_order.crm_order_id, code, label, detail)


def _open(db: Session, tenant_id: int, layer: str, crm_order, pattern: FulfillmentPattern, data: dict[str, Any],
          parent: FulfillmentOrder | None = None, parent_task: FulfillmentTask | None = None, execution_id: int | None = None) -> FulfillmentOrder:
    nodes, deps, planned_by, note = plan_layer(db, tenant_id, pattern, layer, data)
    f_order = FulfillmentOrder(tenant_id=tenant_id, layer=layer, crm_order_id=crm_order.id, parent_id=parent.id if parent else None,
                   parent_task_id=parent_task.id if parent_task else None, pattern_id=pattern.id, pattern_version=pattern.version,
                   planned_by=planned_by, plan_note=note, status="in_progress",
                   data_json=fx.dumps({**data, "execution_id": execution_id}))
    db.add(f_order)
    db.flush()
    f_order.number = f"{layer}-{f_order.id}"
    local_ids = {n["id"] for n in nodes}
    order = topo(nodes, {k: [d for d in v if d in local_ids] for k, v in deps.items()}) or [n["id"] for n in nodes]
    by_id = {n["id"]: n for n in nodes}
    for seq, node_id in enumerate(order, start=1):
        n = by_id[node_id]
        db.add(FulfillmentTask(tenant_id=tenant_id, fulfillment_order_id=f_order.id, node_id=node_id, function=n["function"],
                       label=n.get("label") or fx.FUNCTIONS[n["function"]]["label"], sequence=seq,
                       depends_on_json=fx.dumps(deps.get(node_id, [])), config_json=fx.dumps(n.get("config") or {})))
    db.flush()
    labels = {"COM": "Customer order received (COM)", "SOM": "Service order opened (SOM)", "TOM": "Technical order opened (TOM)"}
    _milestone(db, f_order, f"fulfillment_{layer.lower()}_opened", labels[layer], f"{f_order.number} · {pattern.name}" + (f" · planned by AI" if planned_by == "llm" else ""))
    advance(db, f_order)
    return f_order


def receive(db: Session, tenant_id: int, crm_order, execution_id: int | None = None, pattern: FulfillmentPattern | None = None) -> FulfillmentOrder:
    """COM receives a CRM order from the intent CRM."""
    from app.models.orders import OrderStatus

    existing = (db.query(FulfillmentOrder).filter(FulfillmentOrder.tenant_id == tenant_id, FulfillmentOrder.crm_order_id == crm_order.id, FulfillmentOrder.layer == "COM",
                                          FulfillmentOrder.status != "cancelled").first())
    if existing is not None:
        raise ValueError(f"Order {crm_order.id} is already being fulfilled ({existing.number}, {existing.status.replace('_', ' ')})")
    pattern = pattern or matching_pattern(db, tenant_id, crm_order)
    if pattern is None:
        raise ValueError(f"No active fulfillment pattern fits order {crm_order.id} - design one under Admin -> Fulfillment")
    errors = validate_graph(load_graph(pattern), known_resources(db, tenant_id))
    if errors:
        raise ValueError(f"Fulfillment pattern '{pattern.name}' is invalid: {'; '.join(errors)}")
    if crm_order.status.value in ("created", "pending_approval"):
        crm_order.status = OrderStatus.activating
    return _open(db, tenant_id, "COM", crm_order, pattern, order_data(db, crm_order), execution_id=execution_id)


def advance(db: Session, f_order: FulfillmentOrder) -> None:
    """Advance this order tree until stable, including signals between SOM and TOM."""
    if db.info.get("fulfillment_advancing"):
        return
    root = f_order
    while root.parent_id:
        root = db.get(FulfillmentOrder, root.parent_id)
    if root.status in ("completed", "cancelled"):
        return
    db.info["fulfillment_advancing"] = True
    try:
        while True:
            orders, frontier = [root], [root.id]
            while frontier:
                children = db.query(FulfillmentOrder).filter(
                    FulfillmentOrder.tenant_id == root.tenant_id,
                    FulfillmentOrder.parent_id.in_(frontier)).all()
                orders.extend(children)
                frontier = [c.id for c in children]
            ids = [o.id for o in orders]
            tasks = db.query(FulfillmentTask).filter(FulfillmentTask.fulfillment_order_id.in_(ids)).all()
            before = sorted((t.id, t.status) for t in tasks)
            done = {t.node_id for t in tasks if t.status in DONE}
            for order in orders:
                _advance_order(db, order, done)
            db.flush()
            after = sorted((t.id, t.status) for t in db.query(FulfillmentTask).filter(
                FulfillmentTask.fulfillment_order_id.in_(ids)).all())
            # Opening a child also changes a routing task's status, so the next
            # pass discovers newly opened child orders before declaring stability.
            if before == after:
                break
    finally:
        db.info.pop("fulfillment_advancing", None)


def _advance_order(db: Session, f_order: FulfillmentOrder, tree_done: set[str]) -> None:
    """Runs every task whose dependencies are done, until nothing more can run now."""
    if f_order.status in ("completed", "cancelled"):
        return
    if f_order.parent_id:
        parent_data = _data(db.get(FulfillmentOrder, f_order.parent_id))
        if "resources" in parent_data:
            data = _data(f_order)
            data["resources"] = parent_data["resources"]
            f_order.data_json = fx.dumps(data)
    while True:
        tasks = db.query(FulfillmentTask).filter(FulfillmentTask.fulfillment_order_id == f_order.id).order_by(FulfillmentTask.sequence).all()
        done = tree_done | {t.node_id for t in tasks if t.status in DONE}
        runnable = next((t for t in tasks if t.status == "pending" and set(_j(t.depends_on_json, [])) <= done), None)
        if runnable is None:
            break
        _run(db, f_order, runnable)
        if f_order.status in ("completed", "cancelled"):
            return
    _settle(db, f_order)


def _when_ok(config: dict[str, Any], data: dict[str, Any]) -> bool:
    if not config.get("when_field"):
        return True
    from app.orchestration import flow_engine

    outcome, _ = flow_engine.evaluate_condition(
        {"field": f"record.{str(config['when_field']).removeprefix('record.')}", "operator": config.get("when_operator") or "eq",
         "value": config.get("when_value")}, {"record": data})
    return outcome


def _run(db: Session, f_order: FulfillmentOrder, task: FulfillmentTask) -> None:
    config = _j(task.config_json, {})
    data = _data(f_order)
    if not _when_ok(config, data):
        task.status, task.completed_at = "skipped", utcnow()
        task.note = f"Not needed for this order ({config.get('when_field')} {config.get('when_operator') or 'eq'} {config.get('when_value') or ''})".strip()
        db.flush()
        return
    task.status, task.started_at, task.attempts = "running", utcnow(), task.attempts + 1
    before = f_order.data_json
    savepoint = db.begin_nested()
    try:
        outcome = fx.IMPLEMENTATIONS[task.function](fx.Runtime(db, f_order.tenant_id, f_order, task, config, data, data.get("execution_id")))
        savepoint.commit()
    except Exception as exc:  # noqa: BLE001 - a failed task is fallout, classified, for someone to resolve
        if savepoint.is_active:
            savepoint.rollback()
        err = flow_errors.classify(exc)
        err.node_id, err.node_label, err.node_type = task.node_id, task.label, task.function
        task.status, task.error_json, f_order.data_json = "failed", fx.dumps(err.as_dict()), before
        task.note = err.message
        _milestone(db, f_order, "fulfillment_fallout", f"Fallout in {f_order.number}: {task.label}", err.message)
        db.flush()
        return
    task.request_json, task.response_json = fx.dumps(outcome.request), fx.dumps(outcome.response)
    task.external_ref = outcome.external_ref or task.external_ref
    task.work_order_id = outcome.work_order_id or task.work_order_id
    task.note, task.error_json = outcome.note, ""
    f_order.data_json = fx.dumps(data)
    if outcome.child_layer:
        task.status = "waiting"
        db.flush()
        pattern = db.get(FulfillmentPattern, f_order.pattern_id)
        child = _open(db, f_order.tenant_id, outcome.child_layer, _crm_order(db, f_order), pattern, data,
                      parent=f_order, parent_task=task, execution_id=data.get("execution_id"))
        task.child_order_id = child.id
        db.flush()
        return
    if outcome.status == "waiting":
        task.status = "waiting"
    else:
        task.status, task.completed_at = "completed", utcnow()
    db.flush()


def _crm_order(db: Session, f_order: FulfillmentOrder):
    from app.models.orders import Order

    return db.get(Order, f_order.crm_order_id)


def _settle(db: Session, f_order: FulfillmentOrder) -> None:
    tasks = db.query(FulfillmentTask).filter(FulfillmentTask.fulfillment_order_id == f_order.id).all()
    statuses = [t.status for t in tasks]
    if all(s in DONE for s in statuses):
        if f_order.status != "completed":
            f_order.status, f_order.completed_at = "completed", utcnow()
            _milestone(db, f_order, f"fulfillment_{f_order.layer.lower()}_completed", f"{f_order.number} completed")
            db.flush()
            _return_to_parent(db, f_order)
        return
    f_order.status = "fallout" if "failed" in statuses else "waiting" if any(s in ("waiting", "running") for s in statuses) else "in_progress"
    db.flush()


def _return_to_parent(db: Session, child: FulfillmentOrder) -> None:
    """A completed child order completes the parent's route task, with what it collected."""
    if not child.parent_task_id:
        return
    task = db.get(FulfillmentTask, child.parent_task_id)
    parent = db.get(FulfillmentOrder, child.parent_id)
    if task is None or parent is None or task.status != "waiting":
        return
    child_data, parent_data = _data(child), _data(parent)
    for key in ("resources", "work"):
        if child_data.get(key):
            parent_data[key] = child_data[key]
    parent.data_json = fx.dumps(parent_data)
    task.status, task.completed_at = "completed", utcnow()
    task.response_json = fx.dumps({"order": child.number, "status": child.status, "resources": child_data.get("resources", []),
                                   "work": child_data.get("work", {})})
    task.note = f"{child.number} completed"
    db.flush()
    advance(db, parent)


def _complete_waiting(db: Session, task: FulfillmentTask, response: dict[str, Any], note: str = "") -> None:
    f_order = db.get(FulfillmentOrder, task.fulfillment_order_id)
    data = _data(f_order)
    if task.function == "field_work_order":
        kind = _j(task.config_json, {}).get("kind") or "installation"
        data.setdefault("work", {})[kind] = response
        _milestone(db, f_order, f"wo_{kind}_closed", f"{task.label} done", response.get("signature") or response.get("technician") or "")
    f_order.data_json = fx.dumps(data)
    task.response_json = fx.dumps({**_j(task.response_json, {}), **response})
    task.status, task.completed_at, task.note, task.error_json = "completed", utcnow(), note or task.note, ""
    db.flush()
    advance(db, f_order)


# ================================================================== responses from outside


def on_work_order_closed(db: Session, work_order) -> dict[str, Any] | None:
    """Field Service closed a TOM work order - its task is done and the order moves on."""
    task = (db.query(FulfillmentTask).filter(FulfillmentTask.tenant_id == work_order.tenant_id, FulfillmentTask.work_order_id == work_order.id,
                                     FulfillmentTask.status == "waiting").first())
    if task is None:
        return None
    _complete_waiting(db, task, fx.work_order_response(db, work_order), f"Work order {work_order.id} closed")
    f_order = db.get(FulfillmentOrder, task.fulfillment_order_id)
    return {"fulfillment_order": f_order.number, "task": task.label, "fulfillment_status": f_order.status}


def on_work_order_cancelled(db: Session, work_order) -> None:
    task = (db.query(FulfillmentTask).filter(FulfillmentTask.tenant_id == work_order.tenant_id, FulfillmentTask.work_order_id == work_order.id,
                                     FulfillmentTask.status == "waiting").first())
    if task is None:
        return
    f_order = db.get(FulfillmentOrder, task.fulfillment_order_id)
    err = flow_errors.FlowError("ACTION_FAILED", f"Work order {work_order.id} was cancelled in Field Service")
    err.node_id, err.node_label, err.node_type = task.node_id, task.label, task.function
    task.status, task.error_json, task.note = "failed", fx.dumps(err.as_dict()), err.message
    _milestone(db, f_order, "fulfillment_fallout", f"Fallout in {f_order.number}: {task.label}", err.message)
    db.flush()
    _settle(db, f_order)


def inventory_callback(db: Session, tenant_id: int, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The inventory system answers an asynchronous technical action:
    {"status": "completed" | "failed", "resources": [{"type", "identifier", "kind"?, "status"?}], "message"?}"""
    from app.integration import inventory_adapter

    task = (db.query(FulfillmentTask).filter(FulfillmentTask.tenant_id == tenant_id, FulfillmentTask.external_ref == str(request_id),
                                     FulfillmentTask.status == "waiting").first())
    if task is None:
        raise ValueError(f"No orchestration task is waiting for inventory request '{request_id}'")
    f_order = db.get(FulfillmentOrder, task.fulfillment_order_id)
    if str(payload.get("status") or "completed") == "failed":
        err = flow_errors.FlowError("ACTION_FAILED", f"Inventory refused: {payload.get('message') or 'no reason given'}")
        err.node_id, err.node_label, err.node_type = task.node_id, task.label, task.function
        task.status, task.error_json, task.note = "failed", fx.dumps(err.as_dict()), err.message
        _milestone(db, f_order, "fulfillment_fallout", f"Fallout in {f_order.number}: {task.label}", err.message)
        db.flush()
        _settle(db, f_order)
        return view(db, f_order)
    resources = []
    for raw in payload.get("resources") or []:
        if not isinstance(raw, dict) or not raw.get("type") or not raw.get("identifier"):
            raise ValueError("Each resource needs a type and an identifier")
        rtype = str(raw["type"])
        kind = raw.get("kind") or ("physical" if rtype in inventory_adapter.PHYSICAL_RESOURCES else "logical")
        resources.append({"type": rtype, "kind": kind, "identifier": str(raw["identifier"]), "status": str(raw.get("status") or "reserved")})
    data = _data(f_order)
    rt = fx.Runtime(db, tenant_id, f_order, task, _j(task.config_json, {}), data)
    from app.crm_core import inventory_service

    inventory_service.register_answer(db, tenant_id, resources, fx.inventory_context(rt))  # what it handed out, held for the order here too
    activation = ""
    if fx.FUNCTIONS[task.function].get("inventory") == "activate":
        from app.integration import network_activation_adapter

        activation = network_activation_adapter.activate_connection(fx.inventory_context(rt)["order_item_id"])
    fx.record_resources(rt, resources, activation)
    f_order.data_json = fx.dumps(data)
    _complete_waiting(db, task, {"request_id": request_id, "resources": resources, **({"network_activation_ref": activation} if activation else {})},
                      f"Inventory answered {request_id}")
    return view(db, f_order)


def resolve(db: Session, tenant_id: int, task_id: int, action: str, note: str = "", response: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fallout management: retry a failed task, skip it, or complete a failed / waiting one by hand."""
    task = db.get(FulfillmentTask, task_id)
    if task is None or task.tenant_id != tenant_id:
        raise ValueError(f"Orchestration task {task_id} not found")
    f_order = db.get(FulfillmentOrder, task.fulfillment_order_id)
    if f_order.status in ("completed", "cancelled"):
        raise ValueError(f"{f_order.number} is {f_order.status} - nothing to resolve")
    routes = task.function.startswith("route_")
    if action == "retry":
        if task.status != "failed":
            raise ValueError(f"'{task.label}' is {task.status} - only a failed step can be retried")
        task.status, task.error_json, task.note = "pending", "", note or "Retried"
        # fallout correction: a fix made on the CRM order (site address, configuration) is picked up
        data = _data(f_order)
        data["order"] = order_data(db, _crm_order(db, f_order))["order"]
        f_order.data_json = fx.dumps(data)
        f_order.status = "in_progress"
        db.flush()
        advance(db, f_order)
    elif action in ("skip", "complete"):
        if task.status in DONE:
            raise ValueError(f"'{task.label}' is already {task.status}")
        if routes:
            raise ValueError(f"'{task.label}' completes with its {fx.FUNCTIONS[task.function]['route']} order - resolve that order instead")
        if task.status == "pending":
            raise ValueError(f"'{task.label}' hasn't run yet - only a failed or waiting step can be {action}ped" if action == "skip"
                             else f"'{task.label}' hasn't run yet - only a failed or waiting step can be completed by hand")
        if action == "skip":
            task.status, task.completed_at, task.error_json = "skipped", utcnow(), ""
            task.note = f"Skipped by hand{': ' + note if note else ''}"
            db.flush()
            advance(db, f_order)
        else:
            _complete_waiting(db, task, {**(response or {}), "completed_by_hand": True}, f"Completed by hand{': ' + note if note else ''}")
    else:
        raise ValueError("Use retry, skip or complete")
    db.refresh(f_order)
    return view(db, top_of(db, f_order))


def top_of(db: Session, f_order: FulfillmentOrder) -> FulfillmentOrder:
    while f_order.parent_id:
        f_order = db.get(FulfillmentOrder, f_order.parent_id)
    return f_order


# ================================================================== intents (INT-117 / INT-118 / INT-119, and the intent triggers)

# These decide fulfillment themselves - their completion never starts a pattern again.
_SELF_ROUTING = (APPROVAL_INTENT, "INT-117", "INT-118", "INT-119")


def started_by_intents(db: Session, tenant_id: int, intent_type: str | None) -> list[FulfillmentPattern]:
    """The active patterns this intent starts (it names them in their trigger intents)."""
    code = (intent_type or "").upper()
    if not code or code in _SELF_ROUTING:
        return []
    candidates = (db.query(FulfillmentPattern).filter(FulfillmentPattern.tenant_id == tenant_id, FulfillmentPattern.is_active.is_(True),
                                                      FulfillmentPattern.trigger_intents.ilike(f"%{code}%")).all())
    return [p for p in candidates if code in triggers(p)]  # (FLOW-1 is in "FLOW-12" - the LIKE only narrows)


def on_intent(db: Session, tenant_id: int, execution_id: int, intent_type: str, entities: dict[str, Any],
              result: dict[str, Any]) -> dict[str, Any]:
    """An intent - vanilla or custom - finished: each order it acted on goes to the active pattern
    that intent starts and that fits the order, unless the order is already being fulfilled."""
    from app.crm_core import lifecycle_service
    from app.models.orders import Order

    if not started_by_intents(db, tenant_id, intent_type):
        return {}
    code = intent_type.upper()
    order_ids: list[int] = []
    for raw in (entities.get("order_id"), result.get("order_id")):
        if str(raw or "").isdigit() and int(raw) not in order_ids:
            order_ids.append(int(raw))
    order_ids += [i for t, i in lifecycle_service.records_in_result(result) if t == "order" and i not in order_ids]
    started = []
    for order_id in order_ids:
        order = db.get(Order, order_id)
        if order is None or order.tenant_id != tenant_id:
            continue
        busy = (db.query(FulfillmentOrder.id).filter(FulfillmentOrder.tenant_id == tenant_id, FulfillmentOrder.crm_order_id == order.id,
                                                     FulfillmentOrder.layer == "COM", FulfillmentOrder.status != "cancelled").first())
        if busy is not None:
            continue
        pattern = matching_pattern(db, tenant_id, order, code)
        if pattern is None:
            continue
        com = receive(db, tenant_id, order, execution_id, pattern)
        started.append({"order_id": order.id, "fulfillment_order": com.number, "fulfillment_status": com.status,
                        "fulfillment_pattern": pattern.name, "planned_by": com.planned_by})
    return {"fulfillment_started": started} if started else {}


def build_int119_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]):
    """INT-119 - Design a fulfillment process: the description, drafted (by the tenant's model or the
    built-in reader) into a fulfillment pattern - saved as a DRAFT for the admin to review and activate."""
    from app.orchestration import fulfillment_ai
    from app.orchestration.orchestrator import SagaStep

    def step() -> dict[str, Any]:
        text = str(entities.get("process_description") or "").strip()
        if len(text) < 10:
            raise ValueError("Describe the fulfillment process - e.g. register the customer in billing, reserve a port and an ONT, "
                             "install, test, then start billing")
        draft = fulfillment_ai.generate(db, tenant_id, text)
        spec = draft["pattern"]
        if draft["validation_errors"]:
            raise ValueError(f"The drafted pattern is not valid: {'; '.join(draft['validation_errors'])}")
        pattern = FulfillmentPattern(tenant_id=tenant_id, name=str(entities.get("pattern_name") or spec["name"]).strip()[:150],
                                     description=spec["description"], products=spec["products"], segment=spec["segment"],
                                     order_type=spec["order_type"], priority=spec["priority"], mode=spec["mode"],
                                     trigger_intents=spec["trigger_intents"], graph_json=json.dumps(spec["graph"]), is_active=False)
        db.add(pattern)
        db.flush()
        graph = spec["graph"]
        return {"fulfillment_pattern_id": pattern.id, "fulfillment_pattern": pattern.name, "pattern_status": "draft",
                "steps": {layer: [n["label"] for n in lane(graph, layer)[0]] for layer in LAYERS},
                "products": pattern.products or "any", "started_by": ", ".join(triggers(pattern)),
                "drafted_by": draft["model"] or "built-in reader", "notes": draft["notes"], "fixes": draft["fixes"],
                "next": "Review it under Admin -> Fulfillment, then Activate"}

    return [SagaStep("design_fulfillment_pattern", step)]


def build_int117_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]):
    """INT-117 - Send an order to Fulfillment."""
    from app.models.orders import Order
    from app.orchestration.orchestrator import SagaStep

    def step() -> dict[str, Any]:
        order = db.get(Order, int(entities["order_id"]))
        if order is None or order.tenant_id != tenant_id:
            raise ValueError(f"Order {entities['order_id']} not found")
        pattern = None
        if entities.get("fulfillment_pattern"):
            pattern = (db.query(FulfillmentPattern).filter(FulfillmentPattern.tenant_id == tenant_id, FulfillmentPattern.is_active.is_(True),
                                                   FulfillmentPattern.name.ilike(str(entities["fulfillment_pattern"]).strip())).first())
            if pattern is None:
                raise ValueError(f"No active fulfillment pattern called '{entities['fulfillment_pattern']}'")
        com = receive(db, tenant_id, order, execution_id, pattern)
        return {"order_id": order.id, "fulfillment_order": com.number, "fulfillment_status": com.status, "planned_by": com.planned_by,
                "fulfillment_pattern": db.get(FulfillmentPattern, com.pattern_id).name, "summary": summary(db, com)}

    return [SagaStep("send_to_orchestration", step)]


def build_int118_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]):
    """INT-118 - Resolve an orchestration fallout on an order."""
    from app.orchestration.orchestrator import SagaStep

    def step() -> dict[str, Any]:
        orders = db.query(FulfillmentOrder).filter(FulfillmentOrder.tenant_id == tenant_id, FulfillmentOrder.crm_order_id == int(entities["order_id"])).all()
        failed = [t for o in sorted(orders, key=lambda o: LAYERS.index(o.layer))
                  for t in db.query(FulfillmentTask).filter(FulfillmentTask.fulfillment_order_id == o.id, FulfillmentTask.status == "failed").order_by(FulfillmentTask.sequence)]
        if not failed:
            raise ValueError(f"Order {entities['order_id']} has no orchestration fallout")
        wanted = str(entities.get("step") or "").strip().lower()
        task = next((t for t in failed if wanted and (wanted in t.label.lower() or wanted == t.node_id.lower())), None) if wanted else failed[0]
        if task is None:
            raise ValueError(f"No failed step '{entities['step']}' - failed: {', '.join(t.label for t in failed)}")
        action = str(entities.get("fallout_action") or "retry")
        out = resolve(db, tenant_id, task.id, action, str(entities.get("note") or ""))
        db.refresh(task)
        return {"order_id": int(entities["order_id"]), "fulfillment_order": out["number"], "step": task.label, "action": action,
                "step_status": task.status, "fulfillment_status": out["status"]}

    return [SagaStep("resolve_orchestration_fallout", step)]


# ================================================================== views


def task_out(t: FulfillmentTask) -> dict[str, Any]:
    spec = fx.FUNCTIONS.get(t.function, {})
    return {"id": t.id, "node_id": t.node_id, "function": t.function, "label": t.label, "sequence": t.sequence, "status": t.status,
            "depends_on": _j(t.depends_on_json, []), "config": _j(t.config_json, {}), "request": _j(t.request_json, {}),
            "response": _j(t.response_json, {}), "error": _j(t.error_json, None), "work_order_id": t.work_order_id,
            "external_ref": t.external_ref, "child_order_id": t.child_order_id, "attempts": t.attempts, "note": t.note,
            "etom": spec.get("etom"), "started_at": t.started_at, "completed_at": t.completed_at}


def view(db: Session, f_order: FulfillmentOrder, children: bool = True) -> dict[str, Any]:
    pattern = db.get(FulfillmentPattern, f_order.pattern_id) if f_order.pattern_id else None
    data = _data(f_order)
    out = {"id": f_order.id, "number": f_order.number, "layer": f_order.layer, "status": f_order.status, "crm_order_id": f_order.crm_order_id,
           "parent_id": f_order.parent_id, "pattern": pattern.name if pattern else None, "pattern_id": f_order.pattern_id,
           "pattern_version": f_order.pattern_version, "planned_by": f_order.planned_by, "plan_note": f_order.plan_note,
           "created_at": f_order.created_at, "completed_at": f_order.completed_at,
           "billing": data.get("billing", {}), "resources": data.get("resources", []), "work": data.get("work", {}),
           "order": data.get("order", {}),
           "tasks": [task_out(t) for t in db.query(FulfillmentTask).filter(FulfillmentTask.fulfillment_order_id == f_order.id).order_by(FulfillmentTask.sequence)]}
    if children:
        out["children"] = [view(db, c) for c in db.query(FulfillmentOrder).filter(FulfillmentOrder.parent_id == f_order.id).order_by(FulfillmentOrder.id)]
    return out


def summary(db: Session, f_order: FulfillmentOrder) -> dict[str, Any]:
    tasks = db.query(FulfillmentTask).filter(FulfillmentTask.fulfillment_order_id == f_order.id).all()
    fallout = [t.label for t in tasks if t.status == "failed"]
    waiting = [t.label for t in tasks if t.status == "waiting"]
    deepest = f_order
    for child in db.query(FulfillmentOrder).filter(FulfillmentOrder.crm_order_id == f_order.crm_order_id, FulfillmentOrder.id != f_order.id).all():
        if child.status in ("fallout",):
            fallout.append(f"{child.number}")
        if child.status not in ("completed", "cancelled") and LAYERS.index(child.layer) > LAYERS.index(deepest.layer):
            deepest = child
    return {"id": f_order.id, "number": f_order.number, "status": f_order.status, "crm_order_id": f_order.crm_order_id, "created_at": f_order.created_at,
            "completed_at": f_order.completed_at, "pattern": (db.get(FulfillmentPattern, f_order.pattern_id).name if f_order.pattern_id else None),
            "planned_by": f_order.planned_by, "stage": deepest.layer, "stage_status": deepest.status, "fallout": fallout, "waiting": waiting,
            "tasks_done": sum(1 for t in tasks if t.status in DONE), "tasks_total": len(tasks)}


def preview(db: Session, tenant_id: int, pattern: FulfillmentPattern, crm_order=None) -> dict[str, Any]:
    """The orchestration plan the pattern makes - for an order, with the steps its conditions leave out.
    Read-only: nothing is created."""
    graph = load_graph(pattern)
    data = order_data(db, crm_order) if crm_order is not None else {"order": {}}
    layers = []
    for layer in LAYERS:
        nodes, deps = lane(graph, layer)
        ordered = topo(nodes, deps) or []
        for edge in graph["edges"]:
            if edge.get("target") in deps and edge.get("source") not in deps:
                deps[edge["target"]].append(edge["source"])
        by_id = {n["id"]: n for n in nodes}
        layers.append({"layer": layer, "steps": [
            {"id": i, "label": by_id[i].get("label"), "function": by_id[i]["function"], "depends_on": deps[i],
             "etom": fx.FUNCTIONS[by_id[i]["function"]]["etom"],
             "runs": crm_order is None or _when_ok(by_id[i].get("config") or {}, data)} for i in ordered]})
    return {"pattern": pattern.name, "mode": pattern.mode, "errors": validate_graph(graph), "layers": layers,
            "order": data.get("order"),
            "note": "In AI mode the model picks, per order and per layer, which of these steps run and in what order - "
                    "within this design; mandatory steps always run." if pattern.mode == "llm" else ""}


# The intents this module carries out (app/capabilities.py).
BUILDERS = {
    "INT-117": build_int117_steps,
    "INT-118": build_int118_steps,
    "INT-119": build_int119_steps,
}
