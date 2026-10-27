"""Configurator engine - Siebel-style interactive configuration over a
product's design (product_config_service.schema).

The design compiles into a finite-domain constraint problem:

  variables   - child products (quantity), relationship slots (which catalog
                product fills them), attributes (list values, or candidate
                points of a range); "" / 0 means "not chosen".
  constraints - mandatory attributes (when visible), attribute rules
                (required / requires / excludes), product constraints
                (when -> require / exclude / limit), resources
                (provide / consume) and child visibility.

On every request the engine propagates the user's picks: each variable's
remaining domain (what is still selectable, what is excluded and why),
forced choices (auto-added, with the rule that forced them), recommendations
and messages. Inconsistent picks get a minimal conflict - the fewest of the
user's choices that clash, the rules involved - and ranked repairs. It can
also complete a configuration at lowest monthly cost (optionally within a
budget) and propose picks from plain English (the AI only proposes; the
engine decides what is valid).

Stateless: a request carries the picks; nothing is kept between calls.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.crm_core import product_config_service as pc

_key = pc._key
NODE_LIMIT = 4000
TIME_BUDGET = 2.5  # seconds per request


@dataclass
class Var:
    name: str
    kind: str  # attribute | component | slot
    values: list[str]
    label: str
    required: bool = False
    component: str | None = None
    unit: str | None = None
    numeric: bool = False
    default: str | None = None
    scope: str = "pricing"  # pricing | technical
    help: str = ""


@dataclass
class Constraint:
    id: str
    label: str
    vars: set[str]
    violated: Callable[[dict[str, Any]], bool]


@dataclass
class Model:
    design: dict[str, Any]
    base: float
    vars: dict[str, Var] = field(default_factory=dict)
    constraints: list[Constraint] = field(default_factory=list)
    mode: str = "sales"


class Budget:
    def __init__(self, seconds: float = TIME_BUDGET):
        self.deadline = time.monotonic() + seconds

    def out(self) -> bool:
        return time.monotonic() > self.deadline


# ---------------------------------------------------------------- compiling a design


def _fmt(n: float, unit: str | None) -> str:
    return f"{n:g} {unit}".strip() if unit else f"{n:g}"


def _range_points(attr: dict[str, Any], design: dict[str, Any], extra: list[Any]) -> list[str]:
    unit = attr.get("unit")
    lo, hi = attr.get("min"), attr.get("max")
    points: set[float] = set()
    for v in (lo, hi):
        if v is not None:
            points.add(float(v))
    for rule in design.get("constraints", []):
        for c in (rule.get("when") or []) + [t for t in rule.get("then") or [] if "field" in t]:
            if _key(c.get("field", "")) == _key(attr["name"]) and c.get("value") not in (None, "") and not isinstance(c.get("value"), list):
                n = pc.in_unit(c["value"], unit)
                if n is not None:
                    points.add(n)
    for v in [attr.get("default"), *extra]:
        n = pc.in_unit(v, unit) if v not in (None, "") else None
        if n is not None:
            points.add(n)
    inside = sorted(p for p in points if (lo is None or p >= lo) and (hi is None or p <= hi))
    return [_fmt(p, unit) for p in inside]


def compile_model(design: dict[str, Any], base: float, picks: dict[str, Any] | None = None, mode: str = "sales") -> Model:
    """mode "sales": pricing attributes and child products; "order": the
    provisioning (technical) attributes too, and they become mandatory."""
    picks = picks or {}
    model = Model(design=design, base=base, mode=mode)
    attrs = design.get("attributes", [])
    for comp in design.get("components", []):
        if comp.get("slot"):
            values = [p["name"] for p in comp["slot"].get("products", [])]
            if comp["min_qty"] == 0:
                values.append("No")
            default = comp["slot"].get("default") or ("No" if comp["min_qty"] == 0 else None)
            model.vars[comp["name"]] = Var(comp["name"], "slot", values, " › ".join(comp["path"]), comp["min_qty"] > 0,
                                           comp.get("parent"), default=default, help=comp.get("description") or "")
        else:
            hi = max(comp["max_qty"], comp["min_qty"], 1)
            qtys = list(range(comp["min_qty"], hi + 1)) if hi <= 10 else sorted({comp["min_qty"], comp["default_qty"], 1, hi})
            model.vars[comp["name"]] = Var(comp["name"], "component", [str(q) for q in qtys], " › ".join(comp["path"]), comp["min_qty"] > 0,
                                           comp.get("parent"), default=str(max(comp["min_qty"], comp["default_qty"])),
                                           help=comp.get("description") or "")
    for a in attrs:
        technical_only = a["kind"] == "technical"
        if technical_only and mode != "order":
            continue
        picked = [v for k, v in picks.items() if _key(k) == _key(a["name"])]
        if a.get("min") is not None or a.get("max") is not None:
            values, numeric = _range_points(a, design, picked), True
        elif a["values"]:
            values, numeric = list(a["values"]), False
        elif picked or a.get("default"):
            values, numeric = [str(v) for v in picked] + ([a["default"]] if a.get("default") and a["default"] not in picked else []), False
        else:
            values, numeric = [], False  # free text / inventory references: not reasoned about
        required = bool(a["required"]) and (not technical_only or mode == "order")
        if not values and not required:
            continue
        if not required or a.get("component"):
            values = values + [""]
        model.vars[a["name"]] = Var(a["name"], "attribute", values, f"{a['component']} › {a['name']}" if a.get("component") else a["name"],
                                    required, a.get("component"), a.get("unit"), numeric, a.get("default"),
                                    "technical" if technical_only else "pricing", a.get("help") or a.get("description") or "")
    _constraints(model)
    return model


def _chosen(model: Model, assign: dict[str, str]) -> dict[str, Any]:
    """An assignment as the selection dict product_config understands."""
    out: dict[str, Any] = {}
    for name, value in assign.items():
        var = model.vars.get(name)
        if var is None or value in ("", None):
            continue
        out[name] = value
    return out


def _chain(model: Model, var: Var) -> set[str]:
    out, comp = set(), var.component
    while comp and comp in model.vars and comp not in out:
        out.add(comp)
        comp = model.vars[comp].component
    return out


def _constraints(model: Model) -> None:
    d = model.design
    names = set(model.vars)

    def design_with(**overrides: Any) -> dict[str, Any]:
        return {**d, "attributes": [{**a, "required": False} for a in d.get("attributes", [])], "rules": [], "constraints": [], **overrides}

    # mandatory attributes, and attributes of child products that are not chosen
    for var in list(model.vars.values()):
        if var.kind != "attribute":
            continue
        chain = _chain(model, var)
        if var.required:
            model.constraints.append(Constraint(
                f"required:{var.name}", f"{var.label} is mandatory", {var.name} | chain,
                lambda a, v=var: pc.component_qty(d, _chosen(model, a), v.component) > 0 and a.get(v.name) in ("", None)))
        if var.component:
            model.constraints.append(Constraint(
                f"belongs:{var.name}", f"{var.label} only applies with {var.component}", {var.name} | chain,
                lambda a, v=var: a.get(v.name) not in ("", None) and pc.component_qty(d, _chosen(model, a), v.component) == 0))
    # a slot / child of an unchosen parent stays empty
    for var in list(model.vars.values()):
        if var.kind in ("component", "slot") and var.component:
            model.constraints.append(Constraint(
                f"parent:{var.name}", f"{var.label} needs {var.component}", {var.name, var.component} | _chain(model, var),
                lambda a, v=var: pc.component_qty(d, _chosen(model, a), v.component) == 0 and a.get(v.name) not in ("", None, "0", "No")))
    # attribute rules
    for rule in d.get("rules", []):
        involved = {n for n in names if _key(n) in (_key(rule["if_attribute"]), _key(rule["then_attribute"]))}
        if not involved:
            continue
        label = rule.get("message") or f"If {rule['if_attribute']} {rule['operator']} {rule.get('if_value') or ''} then {rule['then_attribute']} " + \
            ("is required" if rule["effect"] == "required" else f"{rule['effect']} {rule.get('then_value')}")
        single = design_with(rules=[rule])
        model.constraints.append(Constraint(f"rule:{rule['id']}", label, involved,
                                            lambda a, s=single: bool(pc.validate(s, _chosen(model, a), {}, model.mode == "order"))))
    # product constraints and resources
    resources: dict[str, list[dict[str, Any]]] = {}
    for rule in d.get("constraints", []):
        if rule.get("recommend") or rule.get("say"):
            continue
        if rule.get("resource"):
            resources.setdefault(rule["resource"], []).append(rule)
            continue
        fields = {c.get("field") for c in rule.get("when") or []} | {t.get("require") or t.get("exclude") or t.get("field") for t in rule.get("then") or []}
        involved = {n for n in names if _key(n) in {_key(f or "") for f in fields}}
        if not involved:
            continue
        single = design_with(constraints=[rule])
        model.constraints.append(Constraint(f"constraint:{rule.get('id')}", rule.get("message") or rule.get("text") or "product rule", involved,
                                            lambda a, s=single: bool(pc.constraint_errors(s, _chosen(model, a), model.mode == "order"))))
    for resource, rules in resources.items():
        fields = {r.get("by") for r in rules if r.get("by")} | {c.get("field") for r in rules for c in r.get("when") or []}
        involved = {n for n in names if _key(n) in {_key(f or "") for f in fields}}
        single = design_with(constraints=rules)
        model.constraints.append(Constraint(f"resource:{resource}", f"{resource}: what is used can't exceed what is provided", involved,
                                            lambda a, s=single: bool(pc.resource_errors(s, _chosen(model, a)))))


# ---------------------------------------------------------------- search


def _order(model: Model) -> list[str]:
    def rank(v: Var) -> tuple:
        return (0 if v.kind in ("component", "slot") else 1, len(_chain(model, v)), len(v.values))
    return [v.name for v in sorted(model.vars.values(), key=rank)]


def _consistent(model: Model, assign: dict[str, str], only: Constraint | None = None, skip: set[str] | None = None) -> bool:
    for c in ([only] if only else model.constraints):
        if skip and c.id in skip:
            continue
        if c.vars <= assign.keys() and c.violated(assign):
            return False
    return True


def solve(model: Model, fixed: dict[str, str], prefer: dict[str, str] | None = None, skip: set[str] | None = None,
          budget: Budget | None = None, limit: int = NODE_LIMIT) -> dict[str, str] | None:
    """One complete consistent assignment extending `fixed`, or None."""
    prefer = prefer or {}
    order = [n for n in _order(model) if n not in fixed]
    assign = dict(fixed)
    if not _consistent(model, assign, skip=skip):
        return None
    nodes = [0]

    def values_of(name: str) -> list[str]:
        var = model.vars[name]
        first = [prefer[name]] if prefer.get(name) in var.values else []
        if var.default in var.values and var.default not in first:
            first.append(var.default)
        empty = [v for v in var.values if v in ("", "0", "No") and v not in first]
        return first + [v for v in var.values if v not in first and v not in empty] + empty

    def walk(i: int) -> bool:
        nodes[0] += 1
        if nodes[0] > limit or (budget and budget.out()):
            raise TimeoutError
        if i == len(order):
            return True
        name = order[i]
        for value in values_of(name):
            assign[name] = value
            if _consistent(model, assign, skip=skip) and walk(i + 1):
                return True
        del assign[name]
        return False

    try:
        return dict(assign) if walk(0) else None
    except TimeoutError:
        return None


def price_of(model: Model, assign: dict[str, str]) -> tuple[float, float, list[dict[str, Any]]]:
    chosen = _chosen(model, assign)
    monthly, lines = pc.price(model.base, model.design, chosen)
    one_time = round(sum(line["amount"] for line in lines if line.get("one_time")), 2)
    return monthly, one_time, lines


def complete(model: Model, fixed: dict[str, str], objective: str = "cost", max_monthly: float | None = None,
             budget: Budget | None = None, limit: int = 20000) -> dict[str, str] | None:
    """The best complete configuration: lowest monthly price (then one-time)
    for objective "cost", else the defaults-first one."""
    if objective != "cost":
        found = solve(model, fixed, budget=budget)
        return found if found is None or max_monthly is None or price_of(model, found)[0] <= max_monthly else None
    order = [n for n in _order(model) if n not in fixed]
    assign = dict(fixed)
    best: list[Any] = [None, (float("inf"), float("inf"))]
    nodes = [0]

    def walk(i: int) -> None:
        nodes[0] += 1
        if nodes[0] > limit or (budget and budget.out()):
            raise TimeoutError
        if i == len(order):
            monthly, one_time, _ = price_of(model, assign)
            if (max_monthly is None or monthly <= max_monthly) and (monthly, one_time) < best[1]:
                best[0], best[1] = dict(assign), (monthly, one_time)
            return
        name = order[i]
        var = model.vars[name]
        for value in sorted(var.values, key=lambda v: (v not in ("", "0", "No"), v)):
            assign[name] = value
            if _consistent(model, assign):
                walk(i + 1)
        del assign[name]

    try:
        walk(0)
    except TimeoutError:
        pass
    return best[0]


# ---------------------------------------------------------------- picks


def normalize_picks(model: Model, raw: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    """User picks as variable values; what can't be read is reported."""
    picks: dict[str, str] = {}
    problems: list[str] = []
    for name, value in (raw or {}).items():
        var = next((v for v in model.vars.values() if _key(v.name) == _key(name)), None)
        if var is None:
            continue  # not reasoned about (free text / technical in sales) - kept by the caller
        text = "" if value is None else str(value).strip()
        if var.kind == "component":
            qty = pc._qty(text) if text else 0
            match = str(qty) if qty is not None and str(qty) in var.values else None
        elif var.kind == "slot":
            match = "No" if pc._qty(text) == 0 and text.lower() in ("no", "none", "0", "without") else next((v for v in var.values if _key(v) == _key(text)), None)
        elif var.numeric:
            n = pc.in_unit(text, var.unit) if text else None
            match = "" if not text else next((v for v in var.values if pc.in_unit(v, var.unit) == n), None)
            if match is None and n is not None:
                var.values.insert(0, _fmt(n, var.unit))  # a point the user chose inside the range
                match = var.values[0]
        else:
            match = "" if not text else next((v for v in var.values if _key(v) == _key(text)), None)
        if match is None:
            shown = ", ".join(v for v in var.values if v) or "nothing"
            problems.append(f"{var.label} can be {shown} - not '{text}'")
        else:
            picks[var.name] = match
    return picks, problems


# ---------------------------------------------------------------- explanation


def minimal_conflict(model: Model, picks: dict[str, str], base: dict[str, str] | None = None, budget: Budget | None = None) -> list[str]:
    """The fewest of the picks that can't be had together (with `base` fixed)."""
    base = base or {}
    keep = list(picks)
    for name in list(keep):
        trial = {k: picks[k] for k in keep if k != name}
        if solve(model, {**trial, **base}, budget=budget) is None:
            keep.remove(name)
    return keep


def rules_behind(model: Model, fixed: dict[str, str], budget: Budget | None = None) -> list[Constraint]:
    """Constraints whose removal alone makes `fixed` satisfiable."""
    return [c for c in model.constraints if solve(model, fixed, skip={c.id}, budget=budget, limit=1500) is not None]


def why_not(model: Model, picks: dict[str, str], name: str, value: str, budget: Budget | None = None) -> str:
    conflict = minimal_conflict(model, {k: v for k, v in picks.items() if k != name}, {name: value}, budget)
    fixed = {**{k: picks[k] for k in conflict}, name: value}
    rules = rules_behind(model, fixed, budget)
    because = "; ".join(dict.fromkeys(c.label for c in rules)) or "the product's rules"
    if conflict:
        return f"{because} - with " + ", ".join(f"{model.vars[k].label} = {_shown(picks[k])}" for k in conflict)
    return because


def _shown(v: str) -> str:
    return v if v not in ("", None) else "(none)"


# ---------------------------------------------------------------- the session request


def session(design: dict[str, Any], base: float, raw_picks: dict[str, Any], mode: str = "sales", explain: bool = True,
            complete_objective: str | None = None, max_monthly: float | None = None) -> dict[str, Any]:
    """One configurator request: propagate the picks and say what's possible."""
    budget = Budget()
    model = compile_model(design, base, raw_picks, mode)
    picks, problems = normalize_picks(model, raw_picks)
    # attributes the engine doesn't reason about (free text, inventory references) pass through as given
    loose = {_key(a["name"]) for a in design.get("attributes", [])}
    extra = {k: v for k, v in (raw_picks or {}).items()
             if not any(_key(k) == _key(n) for n in model.vars) and _key(k) in loose and v not in (None, "")}
    out: dict[str, Any] = {"product": design.get("offering_name"), "version": design.get("version"), "mode": mode,
                           "picks": picks, "problems": problems, "auto": [], "conflict": None, "complete": False}

    found = solve(model, picks, budget=budget)
    if found is None:
        conflict = minimal_conflict(model, picks, budget=budget)
        fixed = {k: picks[k] for k in conflict}
        rules = rules_behind(model, fixed, budget)
        repairs = []
        for name in conflict:
            others = {k: v for k, v in picks.items() if k != name}
            alt = complete(model, others, "cost", budget=budget, limit=4000)
            if alt is not None:
                monthly, _, _ = price_of(model, alt)
                repairs.append({"label": f"Change {model.vars[name].label} from {_shown(picks[name])} to {_shown(alt[name])}",
                                "changes": {name: alt[name]}, "monthly": monthly, "changes_count": 1})
        repairs.sort(key=lambda r: (r["changes_count"], r["monthly"]))
        out["conflict"] = {
            "picks": [{"name": k, "label": model.vars[k].label, "value": picks[k]} for k in conflict],
            "rules": [{"id": c.id, "label": c.label} for c in rules],
            "message": "These choices can't go together: " + ", ".join(f"{model.vars[k].label} = {_shown(picks[k])}" for k in conflict)
                       + (f" ({'; '.join(dict.fromkeys(c.label for c in rules))})" if rules else ""),
            "repairs": repairs[:5],
        }
        instance = dict(picks)
    else:
        instance = dict(found)
    # feasible values per variable (sharing every solution found)
    feasible: dict[str, set[str]] = {n: set() for n in model.vars}
    unknown: set[tuple[str, str]] = set()
    if found is not None:
        for n, v in found.items():
            feasible[n].add(v)
        for name, var in model.vars.items():
            for value in var.values:
                if value in feasible[name]:
                    continue
                trial = solve(model, {**{k: v for k, v in picks.items() if k != name}, name: value}, prefer=found, budget=budget, limit=1500)
                if trial is None and budget.out():
                    unknown.add((name, value))
                elif trial is not None:
                    if name in picks:
                        feasible[name].add(value)  # an alternative to a pick: says nothing about the rest
                    else:
                        for n, v in trial.items():
                            feasible[n].add(v)
        # forced choices = Siebel's auto-add
        for name, var in model.vars.items():
            if name not in picks and len(feasible[name]) == 1:
                value = next(iter(feasible[name]))
                if value not in ("", "0", "No") and value != var.default:
                    reason = why_not(model, picks, name, next((v for v in var.values if v in ("", "0", "No")), var.values[0]), budget) if explain else ""
                    out["auto"].append({"name": name, "label": var.label, "value": value, "reason": reason})
    # defaults where nothing was picked, else the solution's choice
    shown_instance: dict[str, str] = {}
    for name, var in model.vars.items():
        if name in picks:
            shown_instance[name] = picks[name]
        elif found is not None:
            forced = len(feasible[name]) == 1
            if forced or (var.default in feasible[name]):
                shown_instance[name] = next(iter(feasible[name])) if forced else var.default or ""
    variables = []
    for name, var in model.vars.items():
        values = []
        for value in var.values:
            state = ("selected" if shown_instance.get(name) == value else "available") if found is None or value in feasible[name] or (name, value) in unknown else "excluded"
            entry: dict[str, Any] = {"value": value, "state": state}
            if state == "excluded" and explain and found is not None and len(values) < 40:
                entry["reason"] = why_not(model, picks, name, value, budget)
            values.append(entry)
        empty_excluded = found is not None and "" in var.values and "" not in feasible[name] and (name, "") not in unknown
        info: dict[str, Any] = {"name": name, "label": var.label, "kind": var.kind, "component": var.component,
                                "required": var.required or empty_excluded,
                                "unit": var.unit, "numeric": var.numeric, "scope": var.scope, "help": var.help, "values": values,
                                "picked": name in picks, "value": shown_instance.get(name)}
        if var.numeric:
            ok = [pc.in_unit(v["value"], var.unit) for v in values if v["value"] and v["state"] != "excluded"]
            ok = [n for n in ok if n is not None]
            info["range"] = {"min": min(ok), "max": max(ok)} if ok else None
        if var.kind == "slot":
            prices = {p["name"]: p["price"] for p in next((c["slot"]["products"] for c in design.get("components", []) if c["name"] == name), [])}
            for v in values:
                if v["value"] in prices:
                    v["price"] = prices[v["value"]]
        variables.append(info)
    out["variables"] = variables
    target = None
    if complete_objective and found is not None:
        target = complete(model, {k: v for k, v in shown_instance.items() if k in picks}, complete_objective, max_monthly, budget)
        if target is None and max_monthly is not None:
            out["problems"].append(f"Nothing fits within {max_monthly:,.0f} a month - the cheapest valid configuration is shown")
            target = complete(model, {k: v for k, v in picks.items()}, "cost", None, budget)
    final = target or shown_instance
    chosen = {**_chosen(model, final), **extra}
    out["instance"] = chosen
    monthly, one_time, lines = price_of(model, final)
    out["price"] = {"base": base, "monthly": monthly, "one_time": one_time, "lines": lines}
    missing = [model.vars[n].label for n, v in model.vars.items() if model.vars[n].required and final.get(n) in (None, "")
               and pc.component_qty(design, chosen, model.vars[n].component) > 0]
    out["missing"] = missing
    out["valid"] = out["conflict"] is None and not problems
    out["complete"] = out["valid"] and not missing and all(n in final for n in model.vars)
    out["messages"] = pc.soft_messages(design, chosen)
    out["resources"] = pc.resource_use(design, chosen)
    out["incomplete_search"] = budget.out()
    return out


# ---------------------------------------------------------------- changes to a configuration (asset-based ordering)


def delta(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """Line-by-line difference with action codes: add / update / delete / - (unchanged)."""
    lines = []
    keys = list(dict.fromkeys([*before, *after]))
    for k in keys:
        old, new = before.get(k), after.get(k)
        empty = lambda v: v in (None, "", "0", 0, "No")  # noqa: E731
        if empty(old) and empty(new):
            continue
        if empty(old):
            action = "add"
        elif empty(new):
            action = "delete"
        elif _key(old) != _key(new):
            action = "update"
        else:
            action = "-"
        lines.append({"element": k, "action": action, "from": old, "to": new})
    return lines


# ---------------------------------------------------------------- AI: configure from plain English


_NL_PROMPT = """You turn a customer's need into product configuration choices. Answer strictly as JSON:
{"picks": {"<variable name>": "<one of its values>"}, "max_monthly": number|null, "objective": "cost"|"default", "reason": "one sentence"}
Use only the variables and values listed. Pick only what the need implies; leave the rest out (the configurator completes it)."""


def propose(db, tenant_id: int, design: dict[str, Any], text: str) -> dict[str, Any]:
    """Picks for a plain-English need - the AI (or the built-in reader) only
    proposes; session() decides what is valid."""
    from app.crm_core import llm

    model = compile_model(design, 0.0)
    picks: dict[str, Any] = {}
    max_monthly = None
    objective = "cost" if re.search(r"\b(cheap|cheapest|lowest|budget|economical|minimum cost|low cost)\b", text, re.I) else "default"
    engine, reason = "rules", ""
    target = llm.resolve(db, tenant_id)
    if target is not None:
        try:
            listing = "\n".join(f"- {v.name} ({v.kind}): {', '.join(x for x in v.values if x) or 'free'}" for v in model.vars.values())
            reply = llm.chat_json(target, _NL_PROMPT, f"Product: {design.get('offering_name')}\nVariables:\n{listing}\n\nNeed: {text}")
            if isinstance(reply.get("picks"), dict):
                picks = {str(k): str(v) for k, v in reply["picks"].items()}
                max_monthly = float(reply["max_monthly"]) if reply.get("max_monthly") not in (None, "") else None
                objective = reply.get("objective") if reply.get("objective") in ("cost", "default") else objective
                engine, reason = "ai", str(reply.get("reason") or "")
        except Exception:  # noqa: BLE001 - the built-in reader takes over
            picks = {}
    if engine == "rules":
        names = [{"name": v.name} for v in model.vars.values()]
        picks = {k: v for k, v in pc.parse_against(names, text).items() if any(_key(k) == _key(n) for n in model.vars)}
        lowered = f" {text.lower()} "
        for var in model.vars.values():
            if var.name in picks:
                continue
            hit = next((v for v in sorted((x for x in var.values if x and x not in ("0", "No")), key=len, reverse=True)
                        if len(v) > 1 and re.search(rf"(?<![\w]){re.escape(v.lower())}(?![\w])", lowered)), None)
            if hit and var.kind != "component":
                picks[var.name] = hit
            elif var.kind in ("component", "slot") and re.search(rf"\b(with|need|needs|include|plus|and)\s+(an?\s+|the\s+)?{re.escape(var.name.lower())}\b", lowered):
                picks[var.name] = hit or ("1" if var.kind == "component" else picks.get(var.name, ""))
            elif var.kind in ("component", "slot") and re.search(rf"\b(no|without)\s+(an?\s+|the\s+)?{re.escape(var.name.lower())}\b", lowered):
                picks[var.name] = "0" if var.kind == "component" else "No"
            if var.numeric and var.name not in picks:
                m = re.search(r"(\d[\d.,]*)\s*(kbps|mbps|gbps)\b", lowered)
                if m and _key(var.unit or "") in ("kbps", "mbps", "gbps"):
                    picks[var.name] = f"{m.group(1)} {m.group(2)}"
        m = re.search(r"\b(?:budget|under|below|max(?:imum)?|within|up to)\s*(?:of\s*)?(?:₹|rs\.?|inr)?\s*(\d[\d,]*)", text, re.I)
        if m:
            max_monthly = float(m.group(1).replace(",", ""))
            objective = "cost"
        picks = {k: v for k, v in picks.items() if v not in (None, "")}
        reason = "Read from the words of the need"
    picks = {k: v for k, v in picks.items() if any(_key(k) == _key(n) for n in model.vars)}
    return {"picks": picks, "max_monthly": max_monthly, "objective": objective, "engine": engine, "reason": reason}


def ai_configure(db, tenant_id: int, design: dict[str, Any], base: float, text: str, current: dict[str, Any] | None = None) -> dict[str, Any]:
    """Need -> proposed picks -> the engine keeps what is valid, completes the
    rest (lowest cost within the budget, when one is given)."""
    proposal = propose(db, tenant_id, design, text)
    picks = {**(current or {}), **proposal["picks"]}
    result = session(design, base, picks, complete_objective=proposal["objective"], max_monthly=proposal["max_monthly"])
    dropped: list[str] = []
    if result["conflict"] is not None:
        # the AI's proposals give way first; what the user had already chosen stays
        for p in result["conflict"]["picks"]:
            if p["name"] in proposal["picks"]:
                picks.pop(p["name"], None)
                dropped.append(f"{p['label']} = {p['value']}")
        result = session(design, base, picks, complete_objective=proposal["objective"], max_monthly=proposal["max_monthly"])
    result["proposal"] = proposal
    result["dropped"] = dropped
    return result


# ---------------------------------------------------------------- model QA


def lint(design: dict[str, Any]) -> list[dict[str, str]]:
    """Report-only checks of a model: inconsistencies, dead options,
    unreachable child products, rules naming nothing, duplicates, gaps."""
    findings: list[dict[str, str]] = []
    add = lambda level, text: findings.append({"level": level, "text": text})  # noqa: E731
    names = {_key(a["name"]) for a in design.get("attributes", [])} | {_key(c["name"]) for c in design.get("components", [])}
    seen: dict[str, str] = {}
    for rule in design.get("constraints", []):
        fields = [c.get("field") for c in rule.get("when") or []] + [t.get("require") or t.get("exclude") or t.get("field") for t in rule.get("then") or []]
        fields += [rule.get("by"), rule.get("recommend")]
        unknown = [f for f in fields if f and _key(f) not in names]
        if unknown:
            add("error", f"Rule \"{rule.get('text') or rule.get('id')}\" names {', '.join(unknown)}, which the product doesn't have")
        sig = json.dumps({k: v for k, v in rule.items() if k not in ("id", "message", "text")}, sort_keys=True)
        if sig in seen:
            add("warning", f"Rule \"{rule.get('text')}\" repeats \"{seen[sig]}\"")
        seen[sig] = rule.get("text") or str(rule.get("id"))
    from app.crm_core import price_formula

    in_formulas: set[str] = set()
    for rule in design.get("price_rules") or []:
        try:
            used = price_formula.references(price_formula.parse(rule["expression"]))
        except price_formula.FormulaError as exc:
            add("error", f"Price formula \"{rule.get('label') or rule['expression']}\" no longer reads: {exc}")
            continue
        in_formulas |= {_key(u) for u in used}
        unknown = [u for u in used if _key(u) not in names]
        if unknown:
            add("error", f"Price formula \"{rule.get('label') or rule['expression']}\" uses {', '.join(unknown)}, which the product doesn't have - it counts as 0")
    for a in design.get("attributes", []):
        if a["kind"] == "pricing" and not a["price_impacts"] and not a.get("affects_provisioning") and _key(a["name"]) not in in_formulas:
            add("info", f"{a['name']} is marked as affecting the price but has no price impact")
        if a.get("required") and a.get("component") and not a.get("default"):
            add("info", f"{a['name']} is mandatory inside optional {a['component']} - users must fill it whenever they add {a['component']}")
    for c in design.get("components", []):
        if c.get("slot") and not c["slot"].get("products"):
            add("error", f"{c['name']} can be filled by no active product ({c['slot'].get('class') or ', '.join(c['slot'].get('listed') or []) or 'empty list'})")
    model = compile_model(design, 0.0)
    budget = Budget(4.0)
    first = solve(model, {}, budget=budget)
    if first is None:
        add("error", "No valid configuration exists at all - the rules contradict each other" if not budget.out() else "The model is too large to check fully")
        return findings
    feasible: dict[str, set[str]] = {n: set() for n in model.vars}
    for n, v in first.items():
        feasible[n].add(v)
    for name, var in model.vars.items():
        for value in var.values:
            if value in feasible[name]:
                continue
            trial = solve(model, {name: value}, prefer=first, budget=budget, limit=1500)
            if trial is not None:
                for n, v in trial.items():
                    feasible[n].add(v)
            elif not budget.out():
                if var.kind == "component" and value != "0":
                    add("warning", f"{var.label} can never be added (quantity {value} is ruled out)")
                elif value not in ("", "No", "0"):
                    add("warning", f"{var.label} = {value} can never be chosen - a dead option")
    return findings
