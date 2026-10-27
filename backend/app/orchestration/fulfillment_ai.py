"""Fulfillment AI assist - drafts a whole fulfillment pattern from a plain-English
description: its COM / SOM / TOM steps, their settings, conditions and dependencies,
which orders it fulfils and which intents start it. Flow Studio's assist (flow_ai.py),
for Fulfillment.

Primary path: the tenant's model - its own on-server model or an external API,
whatever Admin -> AI says (llm.resolve) - answers with steps chosen ONLY from the
fulfillment function catalog. Fallback (no model, or an unusable answer): the
built-in reader, which knows the fulfillment vocabulary ("register the customer in
billing", "reserve a port and an ONT", "install", "connectivity test", "the customer
signs acceptance", "start billing" ...).

Either draft goes through normalise() - the sure fixes: a step's lane from its
function, the Route steps a used lane needs, a missing "Complete the customer
order", products / segment / intents matched to the tenant's real ones - and
fulfillment_engine.validate_graph. A model draft that still fails is discarded for
the reader's. The admin always gets an editable DRAFT, never an active pattern.
"""

from __future__ import annotations

import difflib
import copy
import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.fulfillment_orders import LAYERS
from app.orchestration import fulfillment_engine
from app.orchestration import fulfillment_functions as fx

SEGMENTS = ("B2C", "B2B", "B2B-SMB", "B2B-Enterprise")
ORDER_TYPES = ("new", "change", "cancel", "suspend")
# COM steps that belong after the order comes back from SOM
_COM_AFTER = ("start_billing", "create_assets", "complete_order")
# intents that already decide fulfillment themselves - never pattern triggers
_NOT_TRIGGERS = ("INT-117", "INT-118", "INT-119")
_LANE_H = 170  # the designer canvas (AdminFulfillmentSection.tsx LANE_H)


# ================================================================== context


def context(db: Session, tenant_id: int) -> dict[str, Any]:
    """What a draft may refer to - the tenant's real products, templates and intents."""
    from app.integration import inventory_adapter, tmf620_adapter
    from app.models.field_service import FsActivityTemplate

    return {
        "products": tmf620_adapter.active_offering_names(db, tenant_id),
        "templates": [t.name for t in db.query(FsActivityTemplate).filter(FsActivityTemplate.tenant_id == tenant_id,
                                                                         FsActivityTemplate.active.is_(True)).order_by(FsActivityTemplate.name)],
        "intents": trigger_intents(db, tenant_id),
        "logical": list(inventory_adapter.LOGICAL_RESOURCES),
        "physical": list(inventory_adapter.PHYSICAL_RESOURCES),
    }


def trigger_intents(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    """Intents that can start a pattern: vanilla ones on an order, and the tenant's custom intents."""
    from app.intent_engine import intent_registry

    return [{"code": e["code"], "label": e["label"], "kind": e.get("kind", "builtin"), "target": e.get("target")}
            for e in intent_registry.all_intents(db, tenant_id)
            if e["code"] not in _NOT_TRIGGERS and (e.get("kind") == "custom" or e.get("target") == "order")]


# ================================================================== normalise (both drafts)


def _deps(edges: list[dict[str, Any]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for e in edges:
        out.setdefault(e["target"], []).append(e["source"])
    return out


def _edges(deps: dict[str, list[str]]) -> list[dict[str, Any]]:
    return [{"id": f"e-{s}-{t}", "source": s, "target": t} for t, sources in deps.items() for s in dict.fromkeys(sources)]


def _sinks(ids: list[str], deps: dict[str, list[str]]) -> list[str]:
    """Of these nodes, the ones none of the others waits for."""
    waited = {d for i in ids for d in deps.get(i, []) if d in ids}
    return [i for i in ids if i not in waited]


def _new_id(nodes: list[dict[str, Any]], layer: str) -> str:
    taken = {n["id"] for n in nodes}
    i = 1
    while f"{layer[0].lower()}{i}" in taken:
        i += 1
    return f"{layer[0].lower()}{i}"


def _node(nodes: list[dict[str, Any]], layer: str, function: str, config: dict[str, Any] | None = None, label: str = "") -> dict[str, Any]:
    node = {"id": _new_id(nodes, layer), "layer": layer, "function": function, "config": config or {},
            "label": label or (config or {}).get("subject") or fx.FUNCTIONS[function]["label"]}
    nodes.append(node)
    return node


def _insert_route(nodes: list[dict[str, Any]], deps: dict[str, list[str]], layer: str, router: str, after: tuple[str, ...]) -> dict[str, Any]:
    """The Route step of a lane: after the lane's own work, before the steps that need the child order done
    (`after` - e.g. start billing); those - and whatever waited for them - now wait for the Route step."""
    ids = [n["id"] for n in nodes if n["layer"] == layer]
    by_id = {n["id"]: n for n in nodes}
    post: set[str] = {i for i in ids if by_id[i]["function"] in after}
    changed = True
    while changed:  # anything waiting on a post step is post too
        changed = False
        for i in ids:
            if i not in post and any(d in post for d in deps.get(i, [])):
                post.add(i)
                changed = True
    pre = [i for i in ids if i not in post]
    route = _node(nodes, layer, router)
    deps[route["id"]] = _sinks(pre, deps)
    for i in post:
        if not any(d in post for d in deps.get(i, [])):
            deps[i] = [route["id"]]
    return route


def normalise(graph: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The sure fixes, each reported. Returns (graph, fixes)."""
    fixes: list[str] = []
    nodes: list[dict[str, Any]] = []
    renamed: dict[str, str] = {}
    for raw in graph.get("nodes") or []:
        if not isinstance(raw, dict):
            continue
        function = str(raw.get("function") or "")
        spec = fx.FUNCTIONS.get(function)
        label = str(raw.get("label") or (spec or {}).get("label") or function)
        if spec is None:
            fixes.append(f"Left out '{label}' - '{function}' is not a fulfillment function")
            continue
        layer = str(raw.get("layer") or "").upper()
        if spec["layer"] != "*" and layer != spec["layer"]:
            if layer in LAYERS:
                fixes.append(f"Moved '{label}' to the {spec['layer']} lane - that is where {spec['label'].lower()} belongs")
            layer = spec["layer"]
        if layer not in LAYERS:
            layer = "COM"
        config = {k: v for k, v in (raw.get("config") or {}).items() if v not in (None, "")} if isinstance(raw.get("config"), dict) else {}
        config = {k: (", ".join(map(str, v)) if isinstance(v, list) else v if isinstance(v, str) else str(v) if not isinstance(v, bool) else v)
                  for k, v in config.items()}
        node_id = str(raw.get("id") or "")
        if not node_id or node_id in renamed.values() or any(n["id"] == node_id for n in nodes):
            new = _new_id(nodes, layer)
            if node_id:
                renamed[node_id] = new
            node_id = new
        nodes.append({"id": node_id, "layer": layer, "function": function, "label": label[:150], "config": config,
                      "mandatory": bool(raw.get("mandatory"))})
    by_id = {n["id"]: n for n in nodes}
    deps: dict[str, list[str]] = {}
    for e in graph.get("edges") or []:
        if not isinstance(e, dict):
            continue
        s, t = renamed.get(str(e.get("source")), str(e.get("source"))), renamed.get(str(e.get("target")), str(e.get("target")))
        if s in by_id and t in by_id and s != t and (by_id[s]["layer"] == by_id[t]["layer"] or
                                                                  {by_id[s]["layer"], by_id[t]["layer"]} == {"SOM", "TOM"}):
            deps.setdefault(t, []).append(s)
    # a lane's routes: exactly one, and only to a lane that has steps
    for router, child, parent, after in (("route_to_tom", "TOM", "SOM", ()), ("route_to_som", "SOM", "COM", _COM_AFTER)):
        routes = [n for n in nodes if n["function"] == router]
        has_child = any(n["layer"] == child for n in nodes)
        for extra in routes[1:] if has_child else routes:
            nodes.remove(extra)
            deps.pop(extra["id"], None)
            for k in deps:
                deps[k] = [d for d in deps[k] if d != extra["id"]]
            fixes.append(f"Left out a second '{fx.FUNCTIONS[router]['label']}'" if has_child else
                         f"Left out '{fx.FUNCTIONS[router]['label']}' - the {child} lane has no steps")
        if has_child and not routes:
            _insert_route(nodes, deps, parent, router, after)
            fixes.append(f"Added '{fx.FUNCTIONS[router]['label']}' to {parent} - it hands the order to the {child} lane and waits for it")
    if not any(n["function"] == "complete_order" for n in nodes):
        com = [n["id"] for n in nodes if n["layer"] == "COM"]
        sinks = _sinks(com, deps)
        done = _node(nodes, "COM", "complete_order")
        deps[done["id"]] = sinks
        fixes.append("Added 'Complete the customer order' at the end of COM - without it the CRM order never completes")
    for n in nodes:
        n["mandatory"] = n.get("mandatory", False) or n["function"].startswith("route_") or n["function"] == "complete_order"
    return {"nodes": nodes, "edges": _edges({k: v for k, v in deps.items() if v})}, fixes


def layout(graph: dict[str, Any]) -> dict[str, Any]:
    """Canvas positions: per lane, left to right by dependency depth; steps that run side by side stack."""
    for row, layer in enumerate(LAYERS):
        nodes, deps = fulfillment_engine.lane(graph, layer)
        order = fulfillment_engine.topo(nodes, deps) or [n["id"] for n in nodes]
        depth: dict[str, int] = {}
        for i in order:
            depth[i] = max((depth.get(d, 0) + 1 for d in deps.get(i, [])), default=0)
        stacked: dict[int, int] = {}
        by_id = {n["id"]: n for n in nodes}
        for i in order:
            k = stacked.get(depth[i], 0)
            stacked[depth[i]] = k + 1
            by_id[i]["x"] = 30 + depth[i] * 200 + (k // 2) * 90
            by_id[i]["y"] = row * _LANE_H + 40 + (k % 2) * 62
    return graph


# ================================================================== pattern settings (both drafts)


def _match_product(name: str, products: list[str]) -> str | None:
    lowered = {p.lower(): p for p in products}
    if name.lower() in lowered:
        return lowered[name.lower()]
    close = difflib.get_close_matches(name.lower(), list(lowered), n=1, cutoff=0.8)
    return lowered[close[0]] if close else None


def products_in(text: str, products: list[str]) -> list[str]:
    return [p for p in sorted(products, key=len, reverse=True) if re.search(rf"(?<!\w){re.escape(p)}(?!\w)", text, re.I)]


def _segment(text: str) -> str:
    t = text.lower()
    if re.search(r"\benterprise\b", t):
        return "B2B-Enterprise"
    if re.search(r"\b(?:smb|small business|sme)\b", t):
        return "B2B-SMB"
    if re.search(r"\b(?:b2b|business|corporate)\b", t):
        return "B2B"
    if re.search(r"\b(?:b2c|consumer|residential|home|retail)\b", t):
        return "B2C"
    return ""


_SEGMENT_SAID = re.compile(r"\b(?:for|to|of)\s+(?:all\s+|our\s+)?(?:b2b|b2c|business|enterprise|smb|small business|sme|corporate|consumer|residential|home|retail)"
                           r"(?:\s+(?:customers?|clients?|orders?|accounts?|users?|segment))?\b", re.I)


def _order_type(text: str) -> str:
    t = text.lower()
    if re.search(r"\b(?:cancel\w*|disconnect\w*|terminat\w*|cease)\s+(?:orders?|requests?)\b|\bwhen\s+\w+\s+cancels?\b", t):
        return "cancel"
    if re.search(r"\bsuspen\w*\s+(?:orders?|requests?)\b", t):
        return "suspend"
    if re.search(r"\b(?:change|modify|modification|upgrade|downgrade|move|mac)\s+orders?\b", t):
        return "change"
    return "new"


def _mode(text: str) -> str:
    return "llm" if re.search(r"\b(?:let|have)\s+(?:the\s+)?(?:ai|model)\b|\bai[- ]?(?:planned|plans?|decides?|picks?|chooses?)\b"
                              r"|\b(?:ai|model)\s+(?:decides?|picks?|chooses?|plans?)\b|\bdecide(?:s|d)?\s+per\s+order\b", text, re.I) else "deterministic"


def intents_in(text: str, intents: list[dict[str, Any]]) -> list[str]:
    """The intents the description says start it: their code, or 'when <intent label> ...'."""
    known = {i["code"] for i in intents}
    found = [c.upper() for c in re.findall(r"\b(?:INT|FLOW)-\d+\b", text, re.I) if c.upper() in known]
    trigger = r"\b(?:when(?:ever)?|after|once|on|upon|triggered\s+by|started\s+by|starts?\s+(?:on|with|from)|runs?\s+(?:on|after))\s+(?:the\s+|an?\s+|someone\s+(?:runs|uses)\s+)?['\"“]?"
    for i in sorted(intents, key=lambda i: len(i["label"]), reverse=True):
        if len(i["label"]) >= 6 and i["code"] not in found and re.search(trigger + re.escape(i["label"]), text, re.I):
            found.append(i["code"])
    if re.search(r"\b(?:order\s+control|when\s+(?:an?\s+|the\s+)?orders?\s+(?:is\s+|gets\s+|are\s+)?approved)\b", text, re.I) and found:
        found.insert(0, "INT-25")  # said alongside others: approval stays one of the triggers
    return list(dict.fromkeys(found))


def settings_from_text(text: str, ctx: dict[str, Any]) -> dict[str, Any]:
    products = products_in(text, ctx["products"])
    # the segment the whole pattern is for - not one a step is limited to ("only for business customers, ...")
    said = next((m for m in _SEGMENT_SAID.finditer(text) if not re.search(r"\b(?:only|if|when|where)\s*$", text[max(0, m.start() - 12): m.start()], re.I)), None)
    return {"products": products, "segment": _segment(said.group(0)) if said else "", "order_type": _order_type(text), "mode": _mode(text),
            "trigger_intents": intents_in(text, ctx["intents"])}


def _name(settings: dict[str, Any], text: str, intents: list[dict[str, Any]] | None = None) -> str:
    if m := re.search(r"\b(?:called|named)\s+['\"“]?([^'\"”.,;:]{3,80})", text, re.I):
        return m.group(1).strip()
    labels = {i["code"]: i["label"] for i in intents or []}
    if settings["products"]:
        name = f"{', '.join(settings['products'])} fulfillment"
    elif settings["trigger_intents"] and settings["trigger_intents"][-1] in labels:
        name = f"{labels[settings['trigger_intents'][-1]]} fulfillment"
    else:
        name = f"{settings['order_type'].capitalize()} orders fulfillment"
    if settings["segment"]:
        name += f" ({settings['segment']})"
    return name[:150]


# ================================================================== the built-in reader


_LOGICAL_WORDS = [(r"\bipv6\b", "ipv6_prefix"), (r"\bip\s*(?:address(?:es)?|block|range|subnet)?s?\b|\bstatic\s+ips?\b", "ip_block"),
                  (r"\bvlans?\b", "vlan"), (r"\b(?:phone|telephone|mobile)\s+numbers?\b|\bdids?\b|\bmsisdns?\b", "phone_number"),
                  (r"\bsip\s+trunks?\b", "sip_trunk"), (r"\bservice\s+ids?\b", "service_id"), (r"\bcircuit(?:\s+ids?)?\b", "circuit_id")]
_PHYSICAL_WORDS = [(r"\bsplitter(?:\s+ports?)?\b", "splitter_port"), (r"(?<!splitter )\b(?:access\s+|olt\s+|switch\s+)?ports?\b", "port"),
                   (r"\bfib(?:re|er)\s+(?:strands?|pairs?|cores?)\b", "fibre_strand"), (r"\bonts?\b|\bonus?\b", "ont"), (r"\bcpes?\b", "cpe"),
                   (r"\brouters?\b", "router"), (r"\bsims?\b|\bsim\s+cards?\b", "sim")]
_INVENTORY_VERB = re.compile(r"\b(?:reserv|assign|allocat|book|allot|pick|grab|get|gets|need|request|ask|take|hold|block|design)\w*", re.I)

# (pattern, function, config) - checked in each clause
_STEPS: list[tuple[re.Pattern, str, dict[str, Any]]] = [
    (re.compile(r"\b(?:register|create|sync|set\s*up|onboard|add|enrol)\w*\b[^.;]{0,40}\bcustomer\b(?![^.;]{0,15}\bsign)|\bcustomer\b[^.;]{0,25}\b(?:in|with|into|to)\s+(?:the\s+)?billing\b(?!\s+(?:account|profile))", re.I),
     "sync_customer", {}),
    (re.compile(r"\bbilling\s+accounts?\b|\bbill(?:ing)?\s+acc(?:oun)?t\b", re.I), "create_billing_account", {}),
    (re.compile(r"\bbilling\s+profiles?\b|\bbill(?:ing)?\s+(?:cycle|frequency|preferences?)\b", re.I), "create_billing_profile", {}),
    (re.compile(r"\bdesign\w*\b[^.;]{0,25}\b(?:service|circuit|solution|network)\b", re.I), "design_service", {}),
    (re.compile(r"\bactivat\w*|\bprovision\w*\s+(?:it\s+|the\s+service\s+)?(?:in|on)\s+the\s+network|\bturn\w*\s+(?:the\s+service\s+)?on\b|\bgo(?:es)?\s+live\s+in\s+the\s+network", re.I),
     "activate_service", {}),
    (re.compile(r"\bsite\s+surveys?\b|\bsurvey\s+(?:the\s+)?site\b", re.I), "field_work_order", {"kind": "site_survey"}),
    (re.compile(r"\binstall\w*|\bfit\s+(?:the\s+)?(?:ont|cpe|router)\b", re.I), "field_work_order", {"kind": "installation"}),
    (re.compile(r"\b(?:connectivity|speed|line|service|link|throughput)\s+tests?\b|\btest\w*\b[^.;]{0,25}\b(?:connection|connectivity|link|line|speed|service)\b", re.I),
     "field_work_order", {"kind": "connectivity_test"}),
    (re.compile(r"\baccept\w*|\bsign[- ]?off\b|\bsigns?\b[^.;]{0,30}\b(?:completion|job\s*sheet|work\s*order|delivery)\b|\bhand[- ]?over\s+certificate\b", re.I),
     "field_work_order", {"kind": "acceptance"}),
    (re.compile(r"\b(?:start|begin|kick[- ]?start|kick\s+off|trigger|initiate|turn\s+on)\w*\s+(?:the\s+)?(?:billing|charging|invoicing)\b|\bbilling\s+(?:starts|begins|kicks\s+off)\b|\bstart\s+charging\b", re.I),
     "start_billing", {}),
    (re.compile(r"\b(?:installed\s+)?assets?\b|\binstalled\s+base\b", re.I), "create_assets", {}),
    (re.compile(r"\b(?:complete|close|finish|fulfil)\w*\b[^.;]{0,15}\b(?:the\s+|customer\s+)?orders?\b|\borders?\b[^.;]{0,12}\b(?:is\s+)?(?:complete|completed|closed)\b", re.I),
     "complete_order", {}),
    (re.compile(r"\bmilestones?\b|\b(?:tell|notify|inform|update|let)\b[^.;]{0,15}\b(?:the\s+)?customers?\b(?:\s+know)?", re.I), "milestone", {}),
]


_CUT = (r"\.\s+|\.$|;\s*|\n+|:\s+|,\s*(?:and\s+)?then\s+|\s+and\s+then\s+|,\s*after\s+that\s*,?\s*|\s+then\s+|\s+followed\s+by\s+"
        r"|,\s*(?=(?:next|finally|lastly|afterwards)\b)")


def _clauses(text: str) -> list[tuple[int, str]]:
    """The description cut into clauses, with each one's position in it."""
    out, pos = [], 0
    for i, part in enumerate(re.split(f"({_CUT})", text)):
        if i % 2 == 0 and part.strip():  # even parts are clauses, odd ones the separators
            out.append((pos + len(part) - len(part.lstrip()), part.strip()))
        pos += len(part or "")
    return out


def _resources(clause: str, words: list[tuple[str, str]]) -> list[str]:
    return list(dict.fromkeys(kind for pattern, kind in words if re.search(pattern, clause, re.I)))


def _condition(clause: str, products: list[str]) -> dict[str, Any]:
    """'only for business customers', 'if the monthly price is over 500' ... -> the step's 'only when'."""
    m = re.search(r"\b(?:only\s+)?(?:for|if|when|where)\s+(?:the\s+)?(?:customer\s+is\s+)?(?:an?\s+)?(b2b|b2c|business|enterprise|smb|sme|small business|corporate|consumer|residential|home)\b",
                  clause, re.I)
    if m and re.search(r"\bonly\b|\bif\b|\bwhen\b|\bwhere\b", clause[: m.end()], re.I):
        seg = _segment(m.group(1))
        return {"when_field": "order.segment", "when_operator": "contains" if seg == "B2B" else "eq", "when_value": seg}
    m = re.search(r"\b(?:if|when|only\s+if|where)\s+(?:the\s+)?(?:monthly\s+)?(price|mrc|monthly\s+charge|contract(?:\s+length|\s+term)?)\s+(?:is\s+)?"
                  r"(above|over|more\s+than|greater\s+than|exceeds|at\s+least|below|under|less\s+than|at\s+most)\s+\$?([\d,.]+)", clause, re.I)
    if m:
        field = "order.contract_months" if m.group(1).lower().startswith("contract") else "order.monthly_price"
        op = {"above": "gt", "over": "gt", "more than": "gt", "greater than": "gt", "exceeds": "gt", "at least": "gte",
              "below": "lt", "under": "lt", "less than": "lt", "at most": "lte"}[re.sub(r"\s+", " ", m.group(2).lower())]
        return {"when_field": field, "when_operator": op, "when_value": m.group(3).replace(",", "").rstrip(".")}
    if re.search(r"\bonly\s+(?:for|if|when)\b", clause, re.I):
        named = products_in(clause, products)
        if named:
            return {"when_field": "order.products", "when_operator": "contains", "when_value": named[0]}
    if m := re.search(r"\b(?:if|when|only\s+if)\s+(?:the\s+)?(?:order\s+)?(?:has\s+an?\s+|needs\s+an?\s+)?(?:address|site)\b", clause, re.I):
        return {"when_field": "order.address", "when_operator": "not_empty", "when_value": ""}
    return {}


def _work_order_config(kind: str, clause: str, templates: list[str]) -> dict[str, Any]:
    preset = next(p["config"] for p in fx.FUNCTIONS["field_work_order"]["presets"] if p["config"]["kind"] == kind)
    config = dict(preset)
    if m := re.search(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h\b|minutes?|mins?)", clause, re.I):
        n = float(m.group(1))
        config["duration_minutes"] = str(int(n * 60 if m.group(2).lower().startswith("h") else n))
    for t in templates:
        if re.search(rf"(?<!\w){re.escape(t)}(?!\w)", clause, re.I):
            config["template"] = t
    if m := re.search(r"\b(?:skills?|skilled\s+in|certified\s+(?:for|in))\s+(?:in\s+|of\s+)?([a-z_ ,]+?)(?:[.;]|$|\s+(?:and|then|to)\b)", clause, re.I):
        config["skills"] = ", ".join(s.strip().replace(" ", "_") for s in m.group(1).split(",") if s.strip())
    return config


def _profile_config(clause: str) -> dict[str, Any]:
    c, t = {}, clause.lower()
    if m := re.search(r"\b(monthly|quarterly|annual|annually|yearly)\b", t):
        c["frequency"] = {"annually": "annual", "yearly": "annual"}.get(m.group(1), m.group(1))
    if re.search(r"\bcredit\s*card\b", t):
        c["payment_method"] = "credit_card"
    elif re.search(r"\b(?:auto|direct)[- ]?debit\b", t):
        c["payment_method"] = "auto_debit"
    elif re.search(r"\bbill\s+me\b|\binvoice\b", t):
        c["payment_method"] = "bill_me"
    if m := re.search(r"\b(paper|e-?mail|portal|sms)\s+(?:bills?|invoices?|statements?)\b|\bbills?\s+(?:by|via|on)\s+(paper|e-?mail|portal|sms)\b", t):
        c["bill_media"] = (m.group(1) or m.group(2)).replace("-", "")
    if m := re.search(r"\b(prepaid|postpaid|pre-paid|post-paid)\b", t):
        c["payment_type"] = m.group(1).replace("-", "")
    return c


def heuristic_generate(text: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """The draft from the built-in reader: steps in the order said, per lane; 'in parallel' clauses side by side."""
    products = ctx["products"]
    scrubbed = text
    for p in products:  # a product's name ("Fibre 500 Router") is not a step
        scrubbed = re.sub(rf"(?<!\w){re.escape(p)}(?!\w)", lambda m: "x" * len(m.group(0)), scrubbed, flags=re.I)
    found: list[dict[str, Any]] = []  # {"pos", "clause", "function", "config", "label", "parallel"}
    seen: set[tuple[str, str]] = set()
    unread: list[str] = []
    for index, (pos, clause) in enumerate(_clauses(scrubbed)):
        original = text[pos: pos + len(clause)]
        steps: list[dict[str, Any]] = []
        for pattern, function, base in _STEPS:
            m = pattern.search(clause)
            if not m:
                continue
            config = dict(base)
            if function == "field_work_order":
                config = _work_order_config(base["kind"], clause, ctx["templates"])
            elif function == "create_billing_profile":
                config = _profile_config(clause)
            elif function == "create_billing_account" and (rp := re.search(r"\bon\s+(?:the\s+|an?\s+)?['\"“]?([\w-]+(?:\s+[\w-]+){0,3}?)['\"”]?\s+rate\s+plan\b|\brate\s+plan\s+['\"“]?([\w-]{2,40})", original, re.I)):
                config = {"rate_plan": (rp.group(1) or rp.group(2)).strip()}
            elif function == "design_service":
                config = {"resources": ", ".join(_resources(clause, _LOGICAL_WORDS[-2:]) or ["service_id", "circuit_id"])}
            elif function == "milestone":
                said = re.search(r"\b(?:that|saying|:)\s+(.+)$", original, re.I) or re.search(r"\bmilestones?\s+['\"“]([^'\"”]+)", original, re.I)
                config = {"label": (said.group(1).strip(" .'\"”") if said else original.strip(" ."))[:120]}
            key = (function, str(config.get("kind", "")))
            if key in seen and function != "milestone":
                continue
            seen.add(key)
            steps.append({"pos": pos + m.start(), "function": function, "config": config, "clause": index})
        if _INVENTORY_VERB.search(clause):
            logical = [r for r in _resources(clause, _LOGICAL_WORDS) if not (any(s["function"] == "design_service" for s in steps) and r in ("service_id", "circuit_id"))]
            physical = _resources(clause, _PHYSICAL_WORDS)
            for function, kinds in (("assign_logical", logical), ("reserve_physical", physical)):
                if kinds and (function, "") not in seen:
                    seen.add((function, ""))
                    steps.append({"pos": pos + min(m.start() for p, k in (_LOGICAL_WORDS if function == "assign_logical" else _PHYSICAL_WORDS)
                                                   if k in kinds for m in [re.search(p, clause, re.I)] if m),
                                  "function": function, "config": {"resources": ", ".join(kinds)}, "clause": index})
        condition = _condition(original, products)
        parallel = bool(re.search(r"\b(?:in\s+parallel|at\s+the\s+same\s+time|simultaneously|side\s+by\s+side|together)\b", clause, re.I))
        for s in steps:
            s["config"].update(condition)
            s["parallel"] = parallel and len(steps) > 1
        if not steps and not re.search(r"\b(?:fulfil|pattern|process|orders?\s+for|products?|called|named|when|whenever|start(?:s|ed)?\s+by)\b", clause, re.I) \
                and not _SEGMENT_SAID.search(clause) and _mode(clause) != "llm" and len(clause.split()) > 2:
            unread.append(original.strip())
        found.extend(sorted(steps, key=lambda s: s["pos"]))

    # the lane each step runs in (a milestone: the lane of the step said before it)
    lane_of: list[str] = []
    for s in found:
        spec_layer = fx.FUNCTIONS[s["function"]]["layer"]
        lane_of.append(spec_layer if spec_layer != "*" else (lane_of[-1] if lane_of else "COM"))
    first_in = {layer: next((s["pos"] for s, l in zip(found, lane_of) if l == layer), None) for layer in LAYERS}

    nodes: list[dict[str, Any]] = []
    deps: dict[str, list[str]] = {}
    tails: dict[str, list[str]] = {layer: [] for layer in LAYERS}
    group_of: dict[str, tuple[int, bool]] = {}
    after_route: dict[str, list[dict[str, Any]]] = {"COM": [], "SOM": []}
    for s, layer in zip(found, lane_of):
        # COM steps said after the order went down to SOM / TOM - and billing start, assets, completion -
        # run once the order is back; so do SOM steps said after TOM's (activate after the install)
        child = {"COM": "SOM", "SOM": "TOM"}.get(layer)
        later = child is not None and ((first_in[child] is not None and s["pos"] > first_in[child])
                                       or (layer == "COM" and first_in.get("TOM") is not None and s["pos"] > first_in["TOM"])
                                       or (layer == "COM" and s["function"] in _COM_AFTER))
        if later:
            after_route[layer].append(s)
            continue
        _chain(nodes, deps, tails, group_of, layer, s)
    for router, layer, child in (("route_to_tom", "SOM", "TOM"), ("route_to_som", "COM", "SOM")):  # SOM first: its route makes COM's needed
        if any(n["layer"] == child for n in nodes) or (layer == "COM" and after_route["SOM"]):
            _chain(nodes, deps, tails, group_of, layer, {"function": router, "config": {}, "clause": -1, "parallel": False})
        for s in after_route[layer]:
            _chain(nodes, deps, tails, group_of, layer, s)

    graph = {"nodes": nodes, "edges": _edges(deps)}
    notes = "Drafted by the built-in reader."
    if not found:
        notes += " It found no fulfillment steps in the description - started from Complete the customer order; add steps from the palette."
    if unread:
        notes += f" Not understood: {' | '.join(u[:80] for u in unread[:5])} - add those steps by hand."
    return {"graph": graph, "notes": notes}


def _chain(nodes, deps, tails, group_of, layer: str, s: dict[str, Any]) -> None:
    """Adds a step after the lane's last ones - beside the previous step when both were said 'in parallel'."""
    config = s["config"]
    node = _node(nodes, layer, s["function"], config, label=_label(s["function"], config))
    prev = tails[layer]
    group = (s["clause"], s["parallel"])
    if s["parallel"] and prev and group_of.get(prev[-1]) == group:
        deps[node["id"]] = list(deps.get(prev[-1], []))
        tails[layer] = prev + [node["id"]]
    else:
        deps[node["id"]] = list(prev)
        tails[layer] = [node["id"]]
    group_of[node["id"]] = group


def _label(function: str, config: dict[str, Any]) -> str:
    if function == "field_work_order":
        return str(config.get("subject") or "Field work order")
    if function == "milestone":
        return f"Milestone: {config.get('label', '')}"[:150]
    if function in ("assign_logical", "reserve_physical") and config.get("resources"):
        return f"{'Assign' if function == 'assign_logical' else 'Reserve'} {config['resources'].replace('_', ' ')}"
    return fx.FUNCTIONS[function]["label"]


# ================================================================== the model


def _llm_generate(target: Any, text: str, ctx: dict[str, Any]) -> dict[str, Any]:
    from app.crm_core import llm

    functions = [{"function": f["function"], "lane": f["layer"], "what": f["description"], "config": f["config"]}
                 for f in fx.catalog()]
    system = (
        "You design telecom ORDER FULFILLMENT patterns on eTOM, in three lanes: COM (customer order management: billing registration, "
        "then - after the service order is back - billing start, installed assets, order completion), SOM (service order management: "
        "technical actions to the inventory system), TOM (technical order management: Field Service work orders). Output strict JSON "
        '{"name": "...", "products": ["..."], "segment": "", "order_type": "new", "mode": "deterministic", "trigger_intents": [], '
        '"nodes": [{"id": "c1", "layer": "COM", "function": "...", "label": "...", "config": {...}}], '
        '"edges": [{"source": "c1", "target": "c2"}], "notes": "one sentence"}. '
        f"Allowed functions ONLY (lane '*' = any lane): {json.dumps(functions)}. "
        "An edge means target waits for source; edges may also connect SOM and TOM tasks. COM hands over to SOM ONLY through one route_to_som step, "
        "SOM to TOM ONLY through one route_to_tom step - a route step completes when its child lane is done, so start_billing, "
        "create_assets and complete_order wait for route_to_som. Steps that can run side by side share a source. "
        f"field_work_order config kind is one of {list(fx.WORK_ORDER_KINDS)} (installation, connectivity_test, acceptance, site_survey ...), "
        "with subject, duration_minutes, skills (comma-separated), template, instructions. "
        f"Inventory resources (comma-separated string): logical {ctx['logical']}, physical {ctx['physical']}; response sync or async. "
        "create_billing_profile config: frequency monthly|quarterly|annual, payment_method bill_me|credit_card|auto_debit, bill_media email|paper|portal|sms, "
        "payment_type postpaid|prepaid. A step that applies only to some orders gets config when_field (order.segment, order.type, "
        "order.monthly_price, order.contract_months, order.address, order.products), when_operator "
        f"({', '.join(fulfillment_engine.OPERATORS)}) and when_value. "
        f"products: only from {json.dumps(ctx['products'][:80])} (empty = any product). segment: one of {list(SEGMENTS)} or empty. "
        f"order_type: one of {list(ORDER_TYPES)}. mode 'llm' only if the description asks the AI to decide the steps per order. "
        f"Field Service templates: {json.dumps(ctx['templates'][:40])}. "
        "trigger_intents: the intents whose completion starts this pattern on the order, only when the description says so, from "
        f"{json.dumps([{'code': i['code'], 'label': i['label']} for i in ctx['intents']][:80])} - empty = when Order Control approves the order. "
        "Use only what the description asks for; never invent products or intents."
    )
    if ctx.get("source_pattern"):
        system += " Modify this source pattern, preserving all steps and settings except the requested changes: " + json.dumps(ctx["source_pattern"])
    # A whole three-lane graph is longer than the local model's usual 1024-token
    # intent answer. Override that limit to avoid truncating otherwise valid JSON.
    return llm.chat_json(target, system, text, max_tokens=4096)


def _settings(draft: dict[str, Any], said: dict[str, Any], ctx: dict[str, Any], fixes: list[str]) -> dict[str, Any]:
    """The model's pattern settings, kept to real values - else what the description itself says."""
    products = []
    for p in draft.get("products") or []:
        real = _match_product(str(p), ctx["products"])
        if real is None:
            fixes.append(f"Left out product '{p}' - not a product on sale")
        elif real not in products:
            if real != p:
                fixes.append(f"Product '{p}' matched to '{real}'")
            products.append(real)
    codes = {i["code"] for i in ctx["intents"]}
    triggers = [str(c).upper() for c in draft.get("trigger_intents") or [] if str(c).upper() in codes]
    return {
        "products": products or said["products"],
        "segment": draft.get("segment") if draft.get("segment") in SEGMENTS else said["segment"],
        "order_type": draft.get("order_type") if draft.get("order_type") in ORDER_TYPES else said["order_type"],
        "mode": draft.get("mode") if draft.get("mode") in ("deterministic", "llm") else said["mode"],
        "trigger_intents": triggers or said["trigger_intents"],
    }


def _activation_after_work(graph: dict[str, Any], text: str) -> dict[str, Any] | None:
    """Preserve a source design while moving activation after a numbered field job."""
    match = re.search(r"\bafter\s+(?:the\s+)?(first|1st|second|2nd|third|3rd)\s+(?:TOM\s+)?work\s*order\b", text, re.I)
    if not match or not re.search(r"\bactivat(?:e|ion)\b", text, re.I):
        return None
    ordinal = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2}[match[1].lower()]
    graph = copy.deepcopy(graph)
    nodes, deps = fulfillment_engine.lane(graph, "TOM")
    order = fulfillment_engine.topo(nodes, deps) or []
    jobs = [i for i in order if next(n for n in nodes if n["id"] == i)["function"] == "field_work_order"]
    activations = [n for n in graph["nodes"] if n["function"] == "activate_service"]
    routes = [n for n in graph["nodes"] if n["function"] == "route_to_tom"]
    if len(activations) != 1 or len(routes) != 1 or ordinal >= len(jobs):
        raise ValueError("The source needs one network activation, one TOM route and the requested work order")
    activation, job = activations[0]["id"], jobs[ordinal]
    edges = graph["edges"]
    incoming = [e["source"] for e in edges if e["target"] == activation]
    outgoing = [e["target"] for e in edges if e["source"] == activation]
    edges = [e for e in edges if activation not in (e["source"], e["target"])]
    # Bypass activation's old position so the TOM order can open first.
    edges += [{"source": s, "target": t} for s in incoming for t in outgoing]
    edges.append({"source": job, "target": activation})
    # Keep the remaining field work behind network activation.
    successors = [e["target"] for e in edges if e["source"] == job]
    edges += [{"source": activation, "target": t} for t in successors if t != activation]
    # If this was the final job, SOM still cannot complete before activation.
    graph["edges"] = [{"id": f"e-{s}-{t}", "source": s, "target": t}
                      for s, t in dict.fromkeys((e["source"], e["target"]) for e in edges)]
    errors = fulfillment_engine.validate_graph(graph)
    if errors:
        raise ValueError("; ".join(errors))
    return graph


def generate(db: Session, tenant_id: int, text: str, source_pattern: dict[str, Any] | None = None) -> dict[str, Any]:
    """{"pattern": {name, description, products, segment, order_type, priority, mode, trigger_intents, graph},
    "source": "llm" | "rules", "model", "notes", "fixes", "validation_errors"}."""
    from app.crm_core import llm

    text = text.strip()
    ctx = context(db, tenant_id)
    # Template references must resolve to real steps, even when no model is up.
    if re.search(r"\bstandard\b", text, re.I) and re.search(r"\b(?:fixed\s+broadband|eTOM)\b", text, re.I):
        if source_pattern is None:
            source_pattern = {"name": "Fixed broadband - eTOM standard", "graph": fulfillment_engine.standard_graph()}
    ctx["source_pattern"] = source_pattern
    said = settings_from_text(text, ctx)
    edited_graph = _activation_after_work(source_pattern["graph"], text) if source_pattern else None
    target = llm.resolve(db, tenant_id)
    rejected = ""
    if target is not None:
        try:
            draft = _llm_generate(target, text, ctx)
            if edited_graph is not None:
                draft["nodes"], draft["edges"] = edited_graph["nodes"], edited_graph["edges"]
            fixes: list[str] = []
            graph, graph_fixes = normalise({"nodes": draft.get("nodes") or [], "edges": draft.get("edges") or []})
            fixes += graph_fixes
            errors = fulfillment_engine.validate_graph(graph)
            if not errors and any(n["function"] != "complete_order" for n in graph["nodes"]):
                settings = _settings(draft, said, ctx, fixes)
                name = str(draft.get("name") or "").strip()[:150] or _name(settings, text, ctx["intents"])
                result = _out(name, text, settings, layout(graph), "llm", target.label, str(draft.get("notes") or ""), fixes)
                if source_pattern:
                    for key in ("products", "segment", "order_type", "priority", "mode", "trigger_intents"):
                        if key in source_pattern:
                            result["pattern"][key] = source_pattern[key]
                return result
            rejected = f"The model's draft did not hold together ({'; '.join(errors[:2]) or 'no steps'}) - the built-in reader drafted it instead. "
        except Exception as exc:  # noqa: BLE001 - any model failure falls back to the reader
            detail = str(exc) if isinstance(exc, llm.LlmUnavailable) else ""
            rejected = f"The AI model was unavailable ({type(exc).__name__}){': ' + detail if detail else ''} - the built-in reader drafted it instead. "
    draft = ({"graph": edited_graph, "notes": "Copied the source pattern and moved network activation after the requested TOM work order; subsequent field work waits for activation."}
             if edited_graph is not None else heuristic_generate(text, ctx))
    graph, fixes = normalise(draft["graph"])
    notes = rejected + draft["notes"] + ("" if target is not None or rejected else " Configure a model under Admin -> AI (your own on-server model or an API) for richer drafts.")
    result = _out(_name(said, text, ctx["intents"]), text, said, layout(graph), "rules", None, notes, fixes)
    if source_pattern:
        for key in ("products", "segment", "order_type", "priority", "mode", "trigger_intents"):
            if key in source_pattern:
                result["pattern"][key] = source_pattern[key]
        if edited_graph is None:
            result["validation_errors"].append("The fallback cannot safely apply this edit to the source pattern. The original design has been preserved.")
            result["pattern"]["graph"] = copy.deepcopy(source_pattern["graph"])
    return result


def _out(name: str, text: str, settings: dict[str, Any], graph: dict[str, Any], source: str, model: str | None, notes: str,
         fixes: list[str]) -> dict[str, Any]:
    pattern = {"name": name, "description": text[:2000], "products": ", ".join(settings["products"]), "segment": settings["segment"],
               "order_type": settings["order_type"], "priority": 100, "mode": settings["mode"],
               "trigger_intents": ", ".join(settings["trigger_intents"]), "graph": graph}
    return {"pattern": pattern, "source": source, "model": model, "notes": notes.strip(), "fixes": fixes,
            "validation_errors": fulfillment_engine.validate_graph(graph)}
