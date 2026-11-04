"""System test suite - test cases generated from what this tenant has, run in the background, with evidence.

    generate()   reads the tenant's own system and writes test cases (models/system_tests.py):
                 - every intent it can run: understood from its example wording, refused without its required
                   fields, and run end to end with values the copilot fills from the tenant's own data;
                 - every custom record type: its lifecycle (create, change, each move along its states, delete),
                   each mandatory field, a type check, each validation rule (made to break), each lookup and
                   relationship, and creating one in plain English;
                 - every switched-on Flow Studio flow (a dry run) and every page validation rule of the built-in
                   records (customer, lead, opportunity, quote, order).
    start_run()  queues a run of the active cases (or a selection) and execute() works through it in the
                 background - each case in its own transaction that is ROLLED BACK at the end, so nothing a test
                 does is kept (no record, audit entry, notification or number - as in rehearsal).
    report_html() the evidence: a self-contained page - summary, results by area, and for every case each step's
                 request, response, plan, the rows it would write and every check with its expected / actual value.

The runner goes through the same intent pipeline as people do (routes_intent._handle), as the person who
started the run, with their position's data scope.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import threading
import time
from typing import Any, Callable

from sqlalchemy.orm import Session, sessionmaker

from app.models.system_tests import SystemTestCase, SystemTestResult, SystemTestRun, SystemTestSuite
from app.timeutils import utcnow

# intents an automatic end-to-end case leaves alone: they talk to systems outside this database
NO_AUTO_RUN = {"INT-34"}
# the tenant's own record types are covered per type, from their own labels
RECORD_INTENTS = {"INT-151", "INT-152", "INT-153", "INT-154", "INT-155", "INT-156", "INT-157"}
MAX_EXAMPLES = 2
_TEMPLATE = re.compile(r"\{\{\s*(\w+)\.(\w+)\s*\}\}")


# ---------------------------------------------------------------- generation


class _Cases:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, area: str, kind: str, title: str, steps: list[dict[str, Any]], expected: str, target: str = "",
            priority: str = "Medium") -> None:
        self.items.append({"area": area[:100], "kind": kind, "title": title[:300], "steps": steps, "expected": expected,
                           "target": target[:60], "priority": priority})


def _areas_of() -> dict[str, str]:
    from app.orchestration.function_map import PROCESSES

    out: dict[str, str] = {}
    for p in PROCESSES:
        for code in p["steps"]:
            out.setdefault(code, p["label"])
    return out


def _intent_cases(db: Session, tenant_id: int, user, cases: _Cases) -> int:
    from app.intent_engine import intent_registry

    areas = _areas_of()
    categories = dict(intent_registry.CATEGORIES)
    count = 0
    for e in intent_registry.intents_for_user(db, tenant_id, user):
        code = e["code"]
        if code in RECORD_INTENTS or not e.get("is_active", True):
            continue
        count += 1
        area = areas.get(code) or ("Our processes" if code.startswith("FLOW-") else categories.get(e.get("category"), "Other"))
        label = e["label"]
        for text in [x for x in (e.get("examples") or []) if x][:MAX_EXAMPLES]:
            cases.add(area, "understanding", f"{label} - understood from “{text}”",
                      [{"do": "classify", "text": text, "say": f"Type “{text}” in the command bar", "expect": {"intent": code}}],
                      f"Understood as {code} {label}", code, "Medium")
        required = [f for f in intent_registry.requirements(db, tenant_id, code)[0] if " or " not in f]
        if required:
            cases.add(area, "required_fields", f"{label} - refused without {', '.join(required)}",
                      [{"do": "intent", "code": code, "entities": {}, "say": f"Run {code} {label} with nothing filled in",
                        "expect": {"status": ["needs_review"], "missing_any": required}}],
                      f"Not run; asks for {', '.join(required)}", code, "Medium")
        if code not in NO_AUTO_RUN:
            cases.add(area, "end_to_end", f"{label} - runs end to end on your data",
                      [{"do": "intent", "code": code, "entities": "@auto", "skip_on_review": True,
                        "say": f"Run {code} {label} with the values the copilot fills from your records",
                        "expect": {"status": ["executed", "verification_failed"]}}],
                      "Runs through its whole plan (then everything is undone)", code, "High")
    return count


def _sample(defn, today: dt.date) -> Any:
    from app.crm_core import custom_field_service

    kind = defn.data_type
    if kind == "number":
        return 3
    if kind == "date":
        return (today + dt.timedelta(days=7)).isoformat()
    if kind == "boolean":
        return True
    if kind == "picklist":
        values = custom_field_service.allowed_values(defn)
        return values[0] if values else "x"
    if kind == "email":
        return "test.user@example.com"
    if kind == "phone":
        return "+919876543210"
    if kind == "lookup":
        return f"@first:{custom_field_service.lookup_target(defn)}"
    if kind == "textarea":
        return f"Test {defn.label.lower()} written by the system test suite"
    return f"Test {defn.label}"


def _break(cond: dict[str, Any], sample: Any) -> Any:
    """A value that makes a rule's condition false."""
    op, value = cond.get("op", "eq"), cond.get("value")
    today = dt.date.today()

    def num(v: Any, default: float = 0) -> float:
        try:
            return float(str(v).replace(",", ""))
        except (TypeError, ValueError):
            return default
    if op == "eq":
        return "zz-not-allowed" if not isinstance(value, (int, float)) else num(value) + 1
    if op == "ne":
        return value
    if op == "gt":
        return num(value)
    if op == "gte":
        return num(value) - 1
    if op == "lt":
        return num(value)
    if op == "lte":
        return num(value) + 1
    if op == "in":
        return "zz-not-in-list"
    if op == "not_in":
        options = value if isinstance(value, list) else str(value).split(",")
        return str(options[0]).strip() if options else "x"
    if op == "matches":
        return "!!invalid!!"
    if op == "length":
        bounds = value if isinstance(value, list) else [value, value]
        return "x" * (int(num(bounds[-1], 1)) + 3)
    if op == "before_today":
        return (today + dt.timedelta(days=30)).isoformat()
    if op == "after_today":
        return (today - dt.timedelta(days=30)).isoformat()
    if op == "empty":
        return sample or "filled"
    if op == "not_empty":
        return None
    return None


def _make(cond: dict[str, Any], sample: Any) -> Any:
    """A value that makes a rule's 'when' condition true."""
    op, value = cond.get("op", "eq"), cond.get("value")
    if op == "eq":
        return value
    if op == "in":
        options = value if isinstance(value, list) else str(value).split(",")
        return str(options[0]).strip() if options else value
    if op in ("gt", "gte", "lt", "lte"):
        try:
            n = float(value)
        except (TypeError, ValueError):
            return value
        return {"gt": n + 1, "gte": n, "lt": n - 1, "lte": n}[op]
    if op == "empty":
        return None
    if op == "ne":
        return f"not-{value}"
    return sample if sample not in (None, "") else "x"


def _path(model, start: str, target: str) -> list[str] | None:
    """The moves from one state to another (breadth first) - their action names."""
    queue, seen = [(start, [])], {start}
    while queue:
        state, moves = queue.pop(0)
        if state == target:
            return moves
        terminal = getattr(model.states.get(state), "is_terminal", False)
        for t in model.transitions:
            if (t.from_state == state or (t.from_state == "*" and not terminal)) and t.to_state not in seen:
                seen.add(t.to_state)
                queue.append((t.to_state, moves + [t.to_state]))
    return None


def _record_type_cases(db: Session, tenant_id: int, cases: _Cases) -> list[str]:
    from app.crm_core import custom_field_service, lifecycle_service, page_service
    from app.crm_core import custom_record_service as records

    covered = []
    today = dt.date.today()
    for rt in records.list_types(db, tenant_id, active_only=True):
        covered.append(rt.label)
        area = f"Custom: {rt.label}"
        et = rt.entity_type
        defs = custom_field_service.list_definitions(db, tenant_id, et)
        labels = {d.key: d.label for d in defs}
        valid = {d.key: _sample(d, today) for d in defs}
        parent = f"@first:{rt.parent_entity_type}" if rt.parent_entity_type else None
        model = lifecycle_service.resolve_state_model(db, tenant_id, et)
        first = next(iter(model.states), "open")

        def create(name: str, fields: dict[str, Any], expect: dict[str, Any], save: str | None = "rec", with_parent: bool = True) -> dict[str, Any]:
            entities = {"record_type": et, "record_name": name, "fields": fields}
            if parent and with_parent:
                entities["parent_ref"] = parent
            step = {"do": "intent", "code": "INT-151", "entities": entities, "expect": expect,
                    "say": f"Create a {rt.label} “{name}”" + (f" under the first {records.label_of(db, tenant_id, rt.parent_entity_type)}" if parent and with_parent else "")}
            if save:
                step["save_as"] = save
            return step

        ok = {"status": ["executed"]}
        refused = lambda *words: {"status": ["failed"], "message": [w for w in words if w]}  # noqa: E731
        # lifecycle: create -> change -> along its states -> delete
        steps = [create(f"{rt.label} lifecycle test", valid, {**ok, "result": {"state": first}})]
        changeable = next((d for d in defs if d.data_type in ("text", "textarea", "number")), None)
        if changeable is not None:
            steps.append({"do": "intent", "code": "INT-152", "say": f"Change {changeable.label}",
                          "entities": {"entity_type": et, "entity_id": "{{rec.entity_id}}", "fields": {changeable.key: 7 if changeable.data_type == "number" else "Changed by the test"}},
                          "expect": ok})
        state, walked = first, 0
        while walked < 6:
            nxt = next((t for t in model.transitions if t.from_state == state and t.to_state != state), None)
            if nxt is None:
                break
            steps.append({"do": "intent", "code": "INT-153", "say": f"Move it: {nxt.action.replace('_', ' ')} → {nxt.to_state}",
                          "entities": {"entity_type": et, "entity_id": "{{rec.entity_id}}", "record_state": nxt.to_state},
                          "expect": {"status": ["executed", "failed"], "result_or_rule": {"to_state": nxt.to_state}}})
            state, walked = nxt.to_state, walked + 1
            if lifecycle_service.state_def(model, state).is_terminal:
                break
        steps.append({"do": "intent", "code": "INT-157", "say": "Delete it", "entities": {"entity_type": et, "entity_id": "{{rec.entity_id}}"},
                      "expect": {"status": ["executed", "failed"]}})
        cases.add(area, "lifecycle", f"{rt.label} - created, changed, moved along its states and deleted", steps,
                  f"Starts in {first}; each move lands (or a rule for that state explains why not)", et, "High")
        # mandatory fields - the type's own and the page's
        page = page_service.page(db, tenant_id, et)
        mandatory = {f["key"] for s in page["layout"]["sections"] for f in s["fields"] if f.get("mandatory") and not f.get("hidden")}
        mandatory |= {d.key for d in defs if d.required}
        for key in sorted(k for k in mandatory if k in labels):
            cases.add(area, "mandatory", f"{rt.label} - refused without {labels[key]}",
                      [create(f"Missing {labels[key]}", {k: v for k, v in valid.items() if k != key}, refused(labels[key]), save=None)],
                      f"Not created: {labels[key]} is mandatory", et, "High")
        if rt.parent_required:
            cases.add(area, "mandatory", f"{rt.label} - refused without its {records.label_of(db, tenant_id, rt.parent_entity_type)}",
                      [create("No parent", valid, refused("needs"), save=None, with_parent=False)], "Not created: it needs its parent", et, "High")
        number = next((d for d in defs if d.data_type == "number"), None)
        if number is not None:
            cases.add(area, "type_check", f"{rt.label} - {number.label} must be a number",
                      [create("Bad number", {**valid, number.key: "abc"}, refused("number"), save=None)], f"Not created: {number.label} must be a number", et)
        for d in defs:
            if d.data_type == "lookup":
                cases.add(area, "lookup", f"{rt.label} - {d.label} must point to an existing record",
                          [create("Bad link", {**valid, d.key: "ZZ-999999"}, refused("No"), save=None)], f"Not created: no such {d.label}", et)
        # each validation rule, made to break
        for rule in page_service.rules_for(db, tenant_id, et, None):
            if rule.kind != "simple":
                continue
            compiled = page_service.compiled_rule(rule)["compiled"]
            when = compiled.get("when") or []
            state_cond = next((c for c in when if c.get("field") == "state"), None)
            values = dict(valid)
            for c in when:
                if c.get("field") != "state":
                    values[c["field"]] = _make(c, values.get(c["field"]))
            words = [rule.message] if rule.message else []
            for key in compiled.get("require") or []:
                values[key] = None
                words = words or [labels.get(key, key)]
            for c in compiled.get("check") or []:
                values[c["field"]] = _break(c, values.get(c["field"]))
                words = words or [labels.get(c["field"], c["field"])]
            values = {k: v for k, v in values.items() if k in labels}
            title = f"{rt.label} - rule holds: {rule.name}"
            if state_cond is None or state_cond.get("value") == first:
                steps = [create("Breaks a rule", values, refused(*words), save=None)]
            else:
                target = _make(state_cond, None)
                path = _path(model, first, str(target))
                if not path:
                    continue
                steps = [create("Breaks a rule later", {k: v for k, v in valid.items()}, ok)]
                if values != valid:
                    steps.append({"do": "intent", "code": "INT-152", "say": "Set the values the rule checks",
                                  "entities": {"entity_type": et, "entity_id": "{{rec.entity_id}}", "fields": {k: v for k, v in values.items() if v != valid.get(k)}},
                                  "expect": {"status": ["executed", "failed"]}})
                for i, to in enumerate(path):
                    last = i == len(path) - 1
                    steps.append({"do": "intent", "code": "INT-153", "say": f"Move it to {to}" + (" - the rule should stop this" if last else ""),
                                  "entities": {"entity_type": et, "entity_id": "{{rec.entity_id}}", "record_state": to},
                                  "expect": refused(*words) if last else ok})
            cases.add(area, "rule", title, steps, f"Refused with: {rule.message or rule.name}", f"rule:{rule.id}", "High")
        for rel in records.list_relationships(db, tenant_id, et):
            other = rel.to_entity_type if rel.from_entity_type == et else rel.from_entity_type
            cases.add(area, "relationship", f"{rt.label} - linked and unlinked through “{rel.label if rel.from_entity_type == et else rel.reverse_label}”", [
                create(f"{rt.label} link test", valid, ok),
                {"do": "intent", "code": "INT-155", "say": f"Link it to the first {records.label_of(db, tenant_id, other)}",
                 "entities": {"entity_type": et, "entity_id": "{{rec.entity_id}}", "relationship": rel.key, "related_ref": f"@first:{other}"},
                 "expect": {**ok, "result": {"linked": True}}},
                {"do": "intent", "code": "INT-155", "say": "Unlink it again",
                 "entities": {"entity_type": et, "entity_id": "{{rec.entity_id}}", "relationship": rel.key, "related_ref": f"@first:{other}", "unlink": True},
                 "expect": {**ok, "result": {"linked": False}}},
            ], "Linked, then unlinked", f"rel:{rel.key}")
        cases.add(area, "understanding", f"{rt.label} - created in plain English", [
            {"do": "classify", "text": f"Create a {rt.label.lower()} called Plain words test", "say": f"Type “Create a {rt.label.lower()} called Plain words test”",
             "expect": {"intent": "INT-151"}},
            {"do": "classify", "text": f"Show {rt.plural_label.lower()}", "say": f"Type “Show {rt.plural_label.lower()}”", "expect": {"intent": "INT-156"}},
        ], "Understood as Create a record / Find records on this type", et)
    return covered


def _flow_cases(db: Session, tenant_id: int, cases: _Cases) -> int:
    from app.crm_core.custom_record_service import label_of
    from app.models.flows import FlowDefinition

    flows = db.query(FlowDefinition).filter(FlowDefinition.tenant_id == tenant_id, FlowDefinition.is_active.is_(True)).order_by(FlowDefinition.id).all()
    for f in flows:
        trigger = {"state_enter": f"when a record enters {f.trigger_state}", "intent": f"{f.timing} {f.trigger_intent_type}",
                   "custom_intent": "started by people", "validation": "a validation"}.get(f.trigger_type, f.trigger_type)
        cases.add("Flows", "flow", f"Flow “{f.name}” ({trigger}) - dry run", [
            {"do": "flow", "flow_id": f.id, "say": f"Dry-run the flow on the latest {label_of(db, tenant_id, f.entity_type) or 'record'}",
             "expect": {"ok": True}}],
            "Runs to its end without an unexpected error (a Reject step counts as a decision)", f"FLOW-{f.id}")
    return len(flows)


def _page_rule_cases(db: Session, tenant_id: int, cases: _Cases) -> int:
    from app.crm_core import page_service
    from app.models.pages import ValidationRule

    rules = db.query(ValidationRule).filter(ValidationRule.tenant_id == tenant_id, ValidationRule.is_active.is_(True),
                                            ValidationRule.kind == "simple").order_by(ValidationRule.id).all()
    count = 0
    for rule in rules:
        if rule.entity_type.startswith("x_") or rule.entity_type not in page_service.ENTITIES:
            continue  # the tenant's own types: covered with their records
        compiled = page_service.compiled_rule(rule)["compiled"]
        labels = {f["key"]: f["label"] for f in page_service.catalog(db, tenant_id, rule.entity_type, rule.segment)}
        values: dict[str, Any] = {}
        for c in compiled.get("when") or []:
            values[c["field"]] = _make(c, "x")
        words = [rule.message] if rule.message else []
        for key in compiled.get("require") or []:
            values[key] = None
            words = words or [labels.get(key, key)]
        for c in compiled.get("check") or []:
            values[c["field"]] = _break(c, "x")
            words = words or [labels.get(c["field"], c["field"])]
        count += 1
        cases.add(f"Page rules: {page_service.ENTITIES[rule.entity_type]}", "page_rule", f"Rule holds: {rule.name}", [
            {"do": "validate", "entity_type": rule.entity_type, "segment": rule.segment, "values": values,
             "say": f"Save a {page_service.ENTITIES[rule.entity_type].lower()} page with {json.dumps(values, default=str)}",
             "expect": {"errors": words}}], f"Refused with: {rule.message or rule.name}", f"rule:{rule.id}")
    return count


def generate(db: Session, tenant_id: int, user, name: str | None = None) -> SystemTestSuite:
    """Reads the tenant's system and writes a fresh suite of test cases."""
    cases = _Cases()
    intents = _intent_cases(db, tenant_id, user, cases)
    types = _record_type_cases(db, tenant_id, cases)
    flows = _flow_cases(db, tenant_id, cases)
    rules = _page_rule_cases(db, tenant_id, cases)
    by_kind: dict[str, int] = {}
    by_area: dict[str, int] = {}
    for c in cases.items:
        by_kind[c["kind"]] = by_kind.get(c["kind"], 0) + 1
        by_area[c["area"]] = by_area.get(c["area"], 0) + 1
    suite = SystemTestSuite(tenant_id=tenant_id, name=(name or f"System test suite {utcnow():%Y-%m-%d %H:%M}")[:200],
                            case_count=len(cases.items), generated_by=getattr(user, "id", None),
                            scope={"intents": intents, "record_types": types, "flows": flows, "page_rules": rules,
                                   "by_kind": by_kind, "by_area": by_area})
    db.add(suite)
    db.flush()
    for i, c in enumerate(cases.items, start=1):
        db.add(SystemTestCase(tenant_id=tenant_id, suite_id=suite.id, code=f"TC-{i:04d}", is_active=True, **c))
    db.flush()
    return suite


# ---------------------------------------------------------------- running


def start_run(db: Session, tenant_id: int, user, access, suite: SystemTestSuite, selection: dict[str, Any] | None = None) -> SystemTestRun:
    selected = select(db, suite, selection or {})
    if not selected:
        raise ValueError("No active test cases match - generate the suite again or change the selection")
    count = db.query(SystemTestRun.id).filter(SystemTestRun.tenant_id == tenant_id).count()
    run = SystemTestRun(tenant_id=tenant_id, suite_id=suite.id, number=f"RUN-{count + 1:06d}", status="queued", started_by=user.id,
                        position_id=access.active_position.id if access.active_position else None, selection=selection or {},
                        total=len(selected), passed=0, failed=0, errors=0, skipped=0)
    db.add(run)
    db.flush()
    return run


def select(db: Session, suite: SystemTestSuite, selection: dict[str, Any]) -> list[SystemTestCase]:
    query = db.query(SystemTestCase).filter(SystemTestCase.suite_id == suite.id, SystemTestCase.is_active.is_(True))
    if selection.get("case_ids"):
        query = query.filter(SystemTestCase.id.in_([int(i) for i in selection["case_ids"]]))
    if selection.get("areas"):
        query = query.filter(SystemTestCase.area.in_(selection["areas"]))
    if selection.get("kinds"):
        query = query.filter(SystemTestCase.kind.in_(selection["kinds"]))
    return query.order_by(SystemTestCase.id).all()


_running: set[int] = set()
_lock = threading.Lock()


def launch(run_id: int, factory: Callable[[], Session], wait: bool = False) -> None:
    """Runs it now (wait) or on a background thread - one thread per run."""
    with _lock:
        if run_id in _running:
            return
        _running.add(run_id)
    if wait:
        execute(run_id, factory)
    else:
        threading.Thread(target=execute, args=(run_id, factory), name=f"system-tests-{run_id}", daemon=True).start()


def execute(run_id: int, factory: Callable[[], Session]) -> None:
    from app import rls

    db = factory()
    try:
        rls.scope(db, None)
        run = db.get(SystemTestRun, run_id)
        if run is None or run.status not in ("queued", "running"):
            return
        rls.scope(db, run.tenant_id)
        run.status, run.started_at = "running", utcnow()
        db.commit()
        suite = db.get(SystemTestSuite, run.suite_id)
        case_ids = [c.id for c in select(db, suite, run.selection or {})]
        for case_id in case_ids:
            db.refresh(run)
            if run.status == "cancelled":
                break
            case = db.get(SystemTestCase, case_id)
            status, message, evidence, ms = run_case(factory, run, case)
            db.add(SystemTestResult(tenant_id=run.tenant_id, run_id=run.id, case_id=case.id, status=status, message=message[:4000],
                                    duration_ms=ms, evidence=evidence))
            setattr(run, {"passed": "passed", "failed": "failed", "error": "errors", "skipped": "skipped"}[status],
                    getattr(run, {"passed": "passed", "failed": "failed", "error": "errors", "skipped": "skipped"}[status]) + 1)
            db.commit()
        db.refresh(run)
        if run.status != "cancelled":
            run.status = "passed" if run.failed == 0 and run.errors == 0 else "failed"
        run.finished_at = utcnow()
        db.commit()
    finally:
        db.close()
        with _lock:
            _running.discard(run_id)


def run_case(factory: Callable[[], Session], run: SystemTestRun, case: SystemTestCase) -> tuple[str, str, list[dict[str, Any]], int]:
    """One case in its own transaction, rolled back at the end whatever happens."""
    from app import brt_id, rls
    from app.crm_core import access_service
    from app.models.auth import User
    from app.orchestration import rehearsal

    db = factory()
    started = time.monotonic()
    evidence: list[dict[str, Any]] = []
    marker = brt_id.current_user.set({"user_id": run.started_by})
    try:
        rls.scope(db, run.tenant_id)
        user = db.get(User, run.started_by) if run.started_by else None
        if user is None:
            return "error", "The person who started the run no longer exists", evidence, 0
        access = access_service.resolve(db, user, run.position_id, None)
        saved: dict[str, dict[str, Any]] = {}
        status, message = "passed", "All checks passed"
        with rehearsal.capture(db) as rows:
            for i, step in enumerate(case.steps or [], start=1):
                before = len(rows)
                entry: dict[str, Any] = {"step": i, "say": step.get("say", ""), "do": step.get("do")}
                t0 = time.monotonic()
                try:
                    outcome = _step(db, run.tenant_id, user, access, step, saved, entry, bool((run.selection or {}).get("use_ai")))
                except _Skip as exc:
                    entry.update(status="skipped", note=str(exc))
                    evidence.append(entry)
                    status, message = "skipped", str(exc)
                    break
                except Exception as exc:  # noqa: BLE001 - an unexpected error is the case's result, with its step
                    entry.update(status="error", error=f"{type(exc).__name__}: {exc}"[:2000])
                    evidence.append(entry)
                    status, message = "error", f"Step {i}: {type(exc).__name__}: {exc}"
                    break
                entry["ms"] = round((time.monotonic() - t0) * 1000)
                entry["writes"] = _writes(rows[before:])
                if step.get("skip_on_review") and outcome.get("status") == "needs_review":
                    # the platform asked for more than the tenant's data could fill - a data gap, not a defect
                    why = ", ".join(outcome.get("missing") or []) or "more input"
                    entry.update(status="skipped", note=f"Needs {why}")
                    evidence.append(entry)
                    status, message = "skipped", f"Your data doesn't give it everything it needs ({why}) - add the record it needs, or test it by hand"
                    break
                checks = _check(step.get("expect") or {}, outcome)
                entry["checks"] = checks
                entry["status"] = "passed" if all(c["passed"] for c in checks) else "failed"
                evidence.append(entry)
                if step.get("save_as") and isinstance(outcome.get("result"), dict):
                    saved[step["save_as"]] = outcome["result"]
                if entry["status"] == "failed":
                    bad = next(c for c in checks if not c["passed"])
                    status, message = "failed", f"Step {i}: expected {bad['check']} {bad['expected']} - got {bad['actual']}"
                    break
        return status, message, evidence, round((time.monotonic() - started) * 1000)
    finally:
        db.rollback()  # nothing a test does is kept
        brt_id.current_user.reset(marker)
        db.close()


class _Skip(Exception):
    """The tenant has no data a case needs (no customer to hang a record off yet) - skipped, not failed."""


def _resolve(db: Session, access, value: Any, saved: dict[str, dict[str, Any]]) -> Any:
    from app.crm_core import custom_record_service

    if isinstance(value, dict):
        return {k: _resolve(db, access, v, saved) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(db, access, v, saved) for v in value]
    if isinstance(value, str) and value.startswith("@first:"):
        target = value.split(":", 1)[1]
        found = custom_record_service.search(db, access, target, "", 1)
        if not found:
            raise _Skip(f"Needs at least one {custom_record_service.label_of(db, access.tenant_id, target)} you can see")
        return found[0]["id"]
    if isinstance(value, str) and _TEMPLATE.search(value):
        whole = _TEMPLATE.fullmatch(value.strip())
        if whole:
            return (saved.get(whole.group(1)) or {}).get(whole.group(2))
        return _TEMPLATE.sub(lambda m: str((saved.get(m.group(1)) or {}).get(m.group(2), "")), value)
    return value


def _auto(db: Session, tenant_id: int, user, access, code: str) -> dict[str, Any]:
    """The values the copilot fills for an intent from the tenant's own data - then the first suggestion or
    option for anything still required."""
    from app.intent_engine import copilot

    prepared = copilot.prepare(db, tenant_id, user, access, code, {})
    if prepared is None:
        raise _Skip(f"{code} isn't available to you")
    values = {k: v for k, v in (prepared.get("values") or {}).items() if v not in (None, "", [], {})}
    for f in prepared["fields"]:
        if not f.get("required") or values.get(f["key"]) not in (None, "", [], {}):
            continue
        if f.get("suggestions"):
            values[f["key"]] = f["suggestions"][0]["id"]
        elif f.get("options"):
            usable = [o for o in f["options"] if not o.get("via")]
            if usable:
                values[f["key"]] = usable[0]["value"]
    again = copilot.prepare(db, tenant_id, user, access, code, values)
    missing = [m for m in (again or {}).get("missing", []) if " or " not in m]
    if missing:
        raise _Skip(f"No data to fill {', '.join(missing)} - add a record it can use, or test it by hand")
    return {**(again or {}).get("values", {}), **values}


def _step(db: Session, tenant_id: int, user, access, step: dict[str, Any], saved: dict[str, dict[str, Any]], entry: dict[str, Any],
          use_ai: bool = False) -> dict[str, Any]:
    kind = step.get("do")
    if kind == "classify":
        from app.intent_engine import classifier

        # the built-in rules by default (fast, no model cost); with use_ai, exactly what people get - the tenant's AI model first
        result = classifier.classify(step["text"], db, tenant_id, None, use_model=use_ai)
        entry["request"] = {"text": step["text"], "engine": "your AI model" if use_ai else "built-in rules"}
        entry["response"] = {"intent": result.intent_type, "confidence": round(result.confidence, 2), "rationale": result.rationale,
                             "entities": result.entities}
        return {"intent": result.intent_type}
    if kind == "intent":
        from app.experience.routes_intent import _handle
        from app.schemas.intent import IntentRequest

        entities = _auto(db, tenant_id, user, access, step["code"]) if step.get("entities") == "@auto" else _resolve(db, access, step.get("entities") or {}, saved)
        entry["request"] = {"intent": step["code"], "entities": entities}
        response = _handle(IntentRequest(text=f"[system test] {step.get('say', step['code'])}", channel="agent",
                                         structured={"intent_type": step["code"], "entities": entities}), db, tenant_id, user, access, hold=True)
        error = (response.error or {}).get("message") if response.error else None
        entry["response"] = {
            "status": response.status, "intent": response.intent_type, "summary": response.summary or "", "error": error,
            "rationale": (response.rationale or "")[:1500], "missing": response.missing_fields or [],
            "plan": [{"action": p.action, "status": p.status} for p in (response.plan or [])],
            "records": [{"type": r.get("type_label"), "title": r.get("title"), "state": r.get("state"), "change": r.get("change")}
                        for r in (getattr(response, "records", None) or [])][:10],
            "result": _short(response.result or {}),
        }
        return {"status": response.status, "missing": response.missing_fields or [], "result": response.result or {},
                "message": " ".join(x for x in (error, response.summary, response.rationale) if x)}
    if kind == "flow":
        from app.data_intelligence import audit
        from app.models.flows import FlowDefinition
        from app.orchestration import flow_engine, flow_errors

        flow = db.get(FlowDefinition, step["flow_id"])
        if flow is None or flow.tenant_id != tenant_id:
            raise _Skip("The flow no longer exists")
        ctx: dict[str, Any] = {"entities": {}, "result": {}, "trigger": {"intent": flow.trigger_intent_type, "input": {}, "output": {}}}
        if flow.entity_type:
            from app.crm_core import custom_record_service

            found = custom_record_service.search(db, access, flow.entity_type, "", 1)
            if not found and flow.trigger_type != "validation":
                raise _Skip(f"Needs at least one {custom_record_service.label_of(db, tenant_id, flow.entity_type)} you can see to run the flow on")
            if found:
                ctx.update(entity_type=flow.entity_type, entity_id=found[0]["id"])
        entry["request"] = {"flow": flow.name, "record": ctx.get("entity_id")}
        log = audit.start_execution(db, tenant_id, f"[system test] flow {flow.name}", channel="agent")
        try:
            out = flow_engine.run_flow(db, tenant_id, log.id, flow, ctx)
            entry["response"] = {"ok": True, "outcome": out.get("outcome"), "trace": [
                {"step": t.get("label"), "status": t.get("status")} for t in out.get("trace", [])][:30]}
            return {"ok": True}
        except flow_engine.FlowRejected as exc:  # a decision of the flow, not a failure
            entry["response"] = {"ok": True, "outcome": "rejected", "message": str(exc)}
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001 - the flow's failure is the result
            err = flow_errors.classify(exc, flow)
            entry["response"] = {"ok": False, "error": err.as_dict()}
            return {"ok": False, "message": str(err)}
    if kind == "validate":
        from app.crm_core import page_service

        errors = page_service.validate(db, tenant_id, step["entity_type"], step.get("segment"), step.get("values") or {})
        entry["request"] = {"entity": step["entity_type"], "values": step.get("values")}
        entry["response"] = {"errors": errors}
        return {"errors": errors}
    raise ValueError(f"Unknown step '{kind}'")


def _short(value: Any, depth: int = 0) -> Any:
    if depth > 2:
        return "…"
    if isinstance(value, dict):
        return {k: _short(v, depth + 1) for k, v in list(value.items())[:25] if k not in ("flows_run", "trace")}
    if isinstance(value, list):
        return [_short(v, depth + 1) for v in value[:10]]
    if isinstance(value, str) and len(value) > 300:
        return value[:300] + "…"
    return value if isinstance(value, (int, float, bool, str)) or value is None else str(value)


def _writes(rows: list[tuple[str, Any, dict]]) -> dict[str, int]:
    """What the step would have written, by table - rolled back."""
    out: dict[str, int] = {}
    for op, obj, _ in rows:
        key = f"{op} {getattr(obj, '__tablename__', type(obj).__name__)}"
        out[key] = out.get(key, 0) + 1
    return out


def _check(expect: dict[str, Any], outcome: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, expected: Any, actual: Any, passed: bool) -> None:
        checks.append({"check": name, "expected": expected, "actual": actual, "passed": bool(passed)})

    if "intent" in expect:
        add("understood as", expect["intent"], outcome.get("intent"), outcome.get("intent") == expect["intent"])
    if "status" in expect:
        add("status", " or ".join(expect["status"]), outcome.get("status"), outcome.get("status") in expect["status"])
    for key in expect.get("missing") or []:
        add("asks for", key, ", ".join(outcome.get("missing") or []) or "nothing", any(key in m for m in outcome.get("missing") or []))
    if expect.get("missing_any"):
        wanted, got = expect["missing_any"], outcome.get("missing") or []
        add("asks for one of", ", ".join(wanted), ", ".join(got) or "nothing", any(k in m for k in wanted for m in got))
    message = str(outcome.get("message") or "").lower()
    for word in expect.get("message") or []:
        add("message mentions", word, (outcome.get("message") or "")[:300], word.lower() in message)
    result = outcome.get("result") or {}
    for key, value in (expect.get("result") or {}).items():
        add(f"result {key}", value, result.get(key), result.get(key) == value)
    for key, value in (expect.get("result_or_rule") or {}).items():
        # a move lands - or a rule tied to that state refuses it, with its reason
        landed = result.get(key) == value
        add(f"moved to {value}" if outcome.get("status") == "executed" else "refused with a reason", value,
            result.get(key) if outcome.get("status") == "executed" else (outcome.get("message") or "")[:300],
            landed if outcome.get("status") == "executed" else bool(outcome.get("message")))
    if "ok" in expect:
        add("runs without error", expect["ok"], outcome.get("ok"), outcome.get("ok") == expect["ok"])
    for word in expect.get("errors") or []:
        errors = outcome.get("errors") or []
        add("refused with", word, "; ".join(errors) or "no error", any(word.lower() in e.lower() for e in errors))
    return checks


# ---------------------------------------------------------------- output


def suite_out(db: Session, suite: SystemTestSuite) -> dict[str, Any]:
    active = db.query(SystemTestCase.id).filter(SystemTestCase.suite_id == suite.id, SystemTestCase.is_active.is_(True)).count()
    return {"id": suite.id, "name": suite.name, "case_count": suite.case_count, "active": active, "scope": suite.scope or {},
            "created_at": suite.created_at}


def case_out(c: SystemTestCase) -> dict[str, Any]:
    return {"id": c.id, "code": c.code, "area": c.area, "kind": c.kind, "title": c.title, "target": c.target, "priority": c.priority,
            "steps": [s.get("say") or s.get("do") for s in c.steps or []], "expected": c.expected, "is_active": c.is_active}


def run_out(r: SystemTestRun) -> dict[str, Any]:
    done = r.passed + r.failed + r.errors + r.skipped
    return {"id": r.id, "number": r.number, "suite_id": r.suite_id, "status": r.status, "total": r.total, "done": done,
            "passed": r.passed, "failed": r.failed, "errors": r.errors, "skipped": r.skipped, "selection": r.selection or {},
            "created_at": r.created_at, "started_at": r.started_at, "finished_at": r.finished_at,
            "seconds": round((r.finished_at - r.started_at).total_seconds(), 1) if r.finished_at and r.started_at else None}


def results(db: Session, run: SystemTestRun, status: str = "") -> list[dict[str, Any]]:
    query = db.query(SystemTestResult, SystemTestCase).join(SystemTestCase, SystemTestCase.id == SystemTestResult.case_id).filter(
        SystemTestResult.run_id == run.id)
    if status:
        query = query.filter(SystemTestResult.status == status)
    return [{"case": case_out(c), "status": r.status, "message": r.message, "duration_ms": r.duration_ms, "evidence": r.evidence or []}
            for r, c in query.order_by(SystemTestResult.id)]


def cases_csv(db: Session, suite: SystemTestSuite) -> str:
    import csv
    import io

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["code", "area", "kind", "priority", "title", "steps", "expected", "target", "active"])
    for c in db.query(SystemTestCase).filter(SystemTestCase.suite_id == suite.id).order_by(SystemTestCase.id):
        w.writerow([c.code, c.area, c.kind, c.priority, c.title, " | ".join(f"{i}. {s.get('say') or s.get('do')}" for i, s in enumerate(c.steps or [], 1)),
                    c.expected, c.target, "yes" if c.is_active else "no"])
    return out.getvalue()


def report_pdf(db: Session, run: SystemTestRun, passed_detail: bool = False) -> bytes:
    """The run's evidence as a PDF: summary tiles, results by area, then every failure, error and skip with each
    step's checks (expected / actual), request and response - and the full list of cases. passed_detail: the
    steps of passed cases too."""
    from app.documents import STATUS, Pdf
    from app.models.auth import User
    from app.models.tenant import Tenant

    suite = db.get(SystemTestSuite, run.suite_id)
    tenant = db.get(Tenant, run.tenant_id)
    starter = db.get(User, run.started_by) if run.started_by else None
    rows = results(db, run)
    seconds = run_out(run)["seconds"]
    doc = Pdf(f"System test evidence - {run.number}",
              f"{tenant.name if tenant else ''} · suite: {suite.name if suite else ''} · started by "
              f"{(starter.name or starter.email) if starter else 'unknown'} · {str(run.started_at or run.created_at)[:16]} UTC"
              + (f" · {seconds} s" if seconds is not None else ""))
    doc.tiles([("Cases", run.total, None), ("Passed", run.passed, STATUS["passed"]), ("Failed", run.failed, STATUS["failed"]),
               ("Errors", run.errors, STATUS["error"]), ("Skipped", run.skipped, STATUS["skipped"])])
    doc.note(f"Status: {run.status.upper()}. Every case ran through the real platform - the intent pipeline with its checks, flows and rules - as "
             "the person who started the run, inside a transaction that was rolled back: nothing was kept. Plain-English cases were understood by "
             + ("the tenant's AI model." if (run.selection or {}).get("use_ai") else "the built-in rules (no AI model)."))
    areas: dict[str, dict[str, int]] = {}
    for r in rows:
        bucket = areas.setdefault(r["case"]["area"], {"passed": 0, "failed": 0, "error": 0, "skipped": 0})
        bucket[r["status"]] += 1
    doc.heading("Results by area")
    doc.table(["Area", "Passed", "Failed", "Errors", "Skipped"],
              [[a, b["passed"], b["failed"], b["error"], b["skipped"]] for a, b in sorted(areas.items())], widths=[4, 1, 1, 1, 1], right={1, 2, 3, 4})
    order = {"failed": 0, "error": 1, "skipped": 2, "passed": 3}
    detailed = [r for r in sorted(rows, key=lambda x: (order.get(x["status"], 9), x["case"]["code"]))
                if r["status"] != "passed" or passed_detail]
    if detailed:
        doc.heading("Evidence - " + ("every case" if passed_detail else "failures, errors and skips"))
    for r in detailed:
        c = r["case"]
        doc.subheading(f"{c['code']} · {c['title']}", STATUS.get(r["status"]))
        doc.facts([("Result", r["status"].upper()), ("Area", c["area"]), ("Kind", c["kind"].replace("_", " ")), ("Time", f"{r['duration_ms']} ms"),
                   ("Expected", c["expected"]), ("Outcome", r["message"])])
        for s in r["evidence"]:
            doc.paragraph(f"Step {s.get('step')}: {s.get('say') or ''}  [{s.get('status', '')}{', ' + str(s['ms']) + ' ms' if s.get('ms') is not None else ''}]",
                          9.5, STATUS.get(s.get("status", ""), (0.1, 0.12, 0.16)))
            if s.get("checks"):
                doc.table(["Check", "Expected", "Actual", "Result"],
                          [[k["check"], json.dumps(k["expected"], default=str)[:200] if not isinstance(k["expected"], str) else k["expected"][:200],
                            json.dumps(k["actual"], default=str)[:300] if not isinstance(k["actual"], str) else k["actual"][:300],
                            "passed" if k["passed"] else "failed"] for k in s["checks"]], widths=[1.3, 2, 3, 0.8], status_col=3, size=7.5)
            if s.get("note") or s.get("error"):
                doc.note(s.get("note") or s.get("error"), (0.6, 0.45, 0.04), (1.0, 0.97, 0.9))
            if s.get("request") is not None:
                doc.code(json.dumps(s["request"], indent=1, default=str, ensure_ascii=False)[:1500], label="REQUEST")
            if s.get("response") is not None:
                doc.code(json.dumps(s["response"], indent=1, default=str, ensure_ascii=False)[:2500], label="RESPONSE")
            if s.get("writes"):
                doc.code(", ".join(f"{k}: {v}" for k, v in s["writes"].items()), label="WOULD HAVE WRITTEN (ROLLED BACK)")
    doc.heading("All cases")
    doc.table(["Case", "Title", "Area", "Kind", "Result", "ms"],
              [[r["case"]["code"], r["case"]["title"], r["case"]["area"], r["case"]["kind"].replace("_", " "), r["status"], r["duration_ms"]]
               for r in sorted(rows, key=lambda x: x["case"]["code"])], widths=[0.9, 4, 1.8, 1.2, 0.9, 0.6], right={5}, status_col=4, size=7.5)
    return doc.bytes(f"BRT 360 · System test evidence {run.number}")


def results_xlsx(db: Session, run: SystemTestRun) -> bytes:
    from app.documents import xlsx

    rows = results(db, run)
    flat = [["Case", "Area", "Kind", "Priority", "Title", "Result", "Message", "ms", "Expected", "Steps"]]
    for r in rows:
        c = r["case"]
        flat.append([c["code"], c["area"], c["kind"], c["priority"], c["title"], r["status"], r["message"], r["duration_ms"], c["expected"],
                     " | ".join(f"{s.get('step')}. {s.get('say') or ''} [{s.get('status')}]" for s in r["evidence"])])
    checks = [["Case", "Step", "Check", "Expected", "Actual", "Passed"]]
    for r in rows:
        for s in r["evidence"]:
            for k in s.get("checks") or []:
                checks.append([r["case"]["code"], s.get("step"), k["check"], json.dumps(k["expected"], default=str), json.dumps(k["actual"], default=str)[:500],
                               "yes" if k["passed"] else "no"])
    summary_rows = [["Item", "Value"], ["Run", run.number], ["Status", run.status], ["Cases", run.total], ["Passed", run.passed], ["Failed", run.failed],
                    ["Errors", run.errors], ["Skipped", run.skipped], ["Started", str(run.started_at or "")], ["Finished", str(run.finished_at or "")]]
    return xlsx([("Summary", summary_rows), ("Results", flat), ("Checks", checks)])


_STYLE = """
:root{--bg:#f7f8fa;--card:#fff;--ink:#16202e;--muted:#5d6b7e;--line:#e3e7ee;--pass:#0f8a5f;--fail:#c2372b;--skip:#8a6d0f;--err:#9b2c8c}
@media (prefers-color-scheme:dark){:root{--bg:#0b111c;--card:#121a28;--ink:#e6ebf2;--muted:#94a3b8;--line:#243044;--pass:#34d399;--fail:#f87171;--skip:#fbbf24;--err:#e879f9}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px}
.muted{color:var(--muted)}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:16px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}.tile b{display:block;font-size:24px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{font-size:12px;color:var(--muted);text-transform:uppercase}
.passed{color:var(--pass)}.failed{color:var(--fail)}.skipped{color:var(--skip)}.error{color:var(--err)}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0;padding:10px 12px}
summary{cursor:pointer;font-weight:600}.step{border-left:3px solid var(--line);margin:10px 0 0;padding:4px 0 4px 12px}
.step.passed{border-color:var(--pass)}.step.failed{border-color:var(--fail)}.step.error{border-color:var(--err)}.step.skipped{border-color:var(--skip)}
pre{white-space:pre-wrap;word-break:break-word;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px;font-size:12px;margin:6px 0}
.badge{display:inline-block;border:1px solid currentColor;border-radius:999px;padding:0 8px;font-size:12px;font-weight:600}
@media print{details{break-inside:avoid}details>*{display:block}}
"""


def report_html(db: Session, run: SystemTestRun) -> str:
    """The run's evidence as one self-contained page - open it, share it, or print it to PDF."""
    from app.models.auth import User
    from app.models.tenant import Tenant

    suite = db.get(SystemTestSuite, run.suite_id)
    tenant = db.get(Tenant, run.tenant_id)
    starter = db.get(User, run.started_by) if run.started_by else None
    rows = results(db, run)
    e = html.escape

    def fmt(value: Any) -> str:
        return e(json.dumps(value, indent=2, default=str, ensure_ascii=False)) if not isinstance(value, str) else e(value)

    areas: dict[str, dict[str, int]] = {}
    for r in rows:
        bucket = areas.setdefault(r["case"]["area"], {"passed": 0, "failed": 0, "error": 0, "skipped": 0})
        bucket[r["status"]] += 1
    order = {"failed": 0, "error": 1, "skipped": 2, "passed": 3}
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>Test evidence {e(run.number)}</title><style>{_STYLE}</style></head><body><main>",
             f"<h1>System test evidence - {e(run.number)}</h1>",
             f"<p class='muted'>{e(tenant.name if tenant else '')} · suite “{e(suite.name if suite else '')}” · started by "
             f"{e((starter.name or starter.email) if starter else 'unknown')} · {e(str(run.started_at or run.created_at)[:19])} UTC"
             f"{' · ' + str(run_out(run)['seconds']) + ' s' if run.finished_at else ''} · status <b class='{e(run.status)}'>{e(run.status)}</b></p>",
             "<p class='muted'>Every case ran through the real platform - the intent pipeline, its checks, flows and rules - as the person "
             "who started the run, inside a transaction that was rolled back: nothing was kept.</p>"]
    parts.append(f"<p class='muted'>Plain-English cases were understood by {'the tenant&#39;s AI model' if (run.selection or {}).get('use_ai') else 'the built-in rules (no AI model)'}.</p><div class='tiles'>")
    for label, value, css in (("Cases", run.total, ""), ("Passed", run.passed, "passed"), ("Failed", run.failed, "failed"),
                              ("Errors", run.errors, "error"), ("Skipped", run.skipped, "skipped")):
        parts.append(f"<div class='tile'><span class='muted'>{label}</span><b class='{css}'>{value}</b></div>")
    parts.append("</div><h2>By area</h2><table><tr><th>Area</th><th>Passed</th><th>Failed</th><th>Errors</th><th>Skipped</th></tr>")
    for area, b in sorted(areas.items()):
        parts.append(f"<tr><td>{e(area)}</td><td class='passed'>{b['passed']}</td><td class='failed'>{b['failed']}</td>"
                     f"<td class='error'>{b['error']}</td><td class='skipped'>{b['skipped']}</td></tr>")
    parts.append("</table><h2>Cases - failures first</h2>")
    for r in sorted(rows, key=lambda x: (order.get(x["status"], 9), x["case"]["code"])):
        c = r["case"]
        parts.append(f"<details{' open' if r['status'] in ('failed', 'error') else ''}><summary><span class='badge {e(r['status'])}'>{e(r['status'])}</span> "
                     f"{e(c['code'])} · {e(c['title'])} <span class='muted'>· {e(c['area'])} · {e(c['kind'].replace('_', ' '))} · {r['duration_ms']} ms</span></summary>"
                     f"<p><b>Expected:</b> {e(c['expected'])}<br><b>Result:</b> {e(r['message'])}</p>")
        for s in r["evidence"]:
            parts.append(f"<div class='step {e(s.get('status', ''))}'><b>Step {s.get('step')}:</b> {e(s.get('say') or '')} "
                         f"<span class='{e(s.get('status', ''))}'>({e(s.get('status', ''))}{', ' + str(s['ms']) + ' ms' if s.get('ms') is not None else ''})</span>")
            if s.get("request") is not None:
                parts.append(f"<div class='muted'>Request</div><pre>{fmt(s['request'])}</pre>")
            if s.get("response") is not None:
                parts.append(f"<div class='muted'>Response</div><pre>{fmt(s['response'])}</pre>")
            if s.get("writes"):
                parts.append(f"<div class='muted'>Would have written (rolled back)</div><pre>{fmt(s['writes'])}</pre>")
            if s.get("checks"):
                parts.append("<table><tr><th>Check</th><th>Expected</th><th>Actual</th><th></th></tr>" + "".join(
                    f"<tr><td>{e(k['check'])}</td><td>{fmt(k['expected'])}</td><td>{fmt(k['actual'])}</td>"
                    f"<td class='{'passed' if k['passed'] else 'failed'}'>{'✔' if k['passed'] else '✘'}</td></tr>" for k in s["checks"]) + "</table>")
            if s.get("note") or s.get("error"):
                parts.append(f"<pre>{e(s.get('note') or s.get('error'))}</pre>")
            parts.append("</div>")
        parts.append("</details>")
    parts.append("</main></body></html>")
    return "".join(parts)
