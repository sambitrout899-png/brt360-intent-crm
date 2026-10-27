"""Ask in plain English: "MRR by segment and month for B2B customers",
"top 10 customers by outstanding amount", "average resolution time by
severity last 90 days as a pie" - turned into a report spec.

The tenant's AI model reads it when one is set up (shown the entities and
fields it could mean); its answer is checked against the reporting model.
Without a model - or when its answer doesn't hold - the rules below read it:
the entity from its words, the measure (count / total / average ... of a
number field), "by ..." as the dimensions, dates as month/week/... grains,
"top N", relative dates, segments and picklist values as filters, and the
chart that fits.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from app.analytics import engine, semantic

_ENTITY_WORDS: list[tuple[str, str]] = [
    ("fraud_alert", r"fraud"),
    ("credit_alert", r"collections?\s+cases?|credit\s+alerts?|dunning"),
    ("survey", r"surveys?|csat|nps|satisfaction"),
    ("usage", r"usage|cdrs?|traffic|data\s+volume|voice\s+minutes|sms"),
    ("payment", r"payments?|collected|collections"),
    ("invoice", r"invoices?|billed|billing|bills?|receivables?|ageing|aging|overdue|outstanding|dso"),
    ("work_order", r"work\s+orders?|dispatch\w*|technicians?|field\s+(?:jobs?|visits?)"),
    ("ticket", r"tickets?|faults?|incidents?|complaints?|mttr|resolution"),
    ("order_item", r"(?:by|per)\s+(?:product|offering|plan)s?\b|order\s+lines?|products?\s+sold"),
    ("proposal", r"proposals?"),
    ("quote", r"quotes?|quotations?|discounts?"),
    ("opportunity", r"opportunit\w+|deals?|pipeline|win\s+rate|sales\s+cycle"),
    ("lead", r"leads?"),
    ("asset", r"assets?|circuits?|cpe|devices?"),
    ("order", r"orders?|mrr|arpu|tcv|contract\s+value|recurring\s+revenue|services?|churn\w*|revenue"),
    ("party", r"customers?|prospects?|accounts?|clients?|subscribers?"),
]
# "monthly price", "annual contract value" name a field, not a time grain
_FIELD_NOUN = r"(?!\s+(?:price|charge|fee|rental|recurring|revenue|bill|plan|spend|cost|contract|value|amount|usage|limit|quota))"
_GRAIN_WORDS = [("hour", rf"hourly{_FIELD_NOUN}|by\s+hour|per\s+hour"), ("weekday", r"by\s+(?:week)?day\s+of\s+(?:the\s+)?week|by\s+weekday"),
                ("day", rf"daily{_FIELD_NOUN}|per\s+day|by\s+day|each\s+day"), ("week", rf"weekly{_FIELD_NOUN}|per\s+week|by\s+week|each\s+week"),
                ("quarter", rf"quarterly{_FIELD_NOUN}|per\s+quarter|by\s+quarter"),
                ("year", rf"yearly{_FIELD_NOUN}|annual(?:ly)?{_FIELD_NOUN}|per\s+year|by\s+year|year\s+on\s+year"),
                ("month", rf"monthly{_FIELD_NOUN}|per\s+month|by\s+month|each\s+month|over\s+time|trend|month\s+on\s+month")]
_AGG_WORDS = [("avg", r"\b(?:average|avg|mean|typical)\b"), ("median", r"\bmedian\b"), ("max", r"\b(?:max(?:imum)?|highest|largest|biggest)\b"),
              ("min", r"\b(?:min(?:imum)?|lowest|smallest)\b"), ("count_distinct", r"\b(?:distinct|unique)\b"),
              ("count", r"\b(?:how\s+many|number\s+of|count(?:s)?\s+of|count)\b"), ("sum", r"\b(?:total|sum|revenue|value|amount|spend)\b")]
_STOP = {"the", "by", "per", "of", "and", "for", "with", "in", "a", "an", "show", "me", "what", "is", "are", "each", "all", "my", "our", "as",
         "chart", "graph", "report", "over", "time", "split", "broken", "down", "across", "top", "number", "how", "many", "total", "average"}


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 1}


def _entity(text: str) -> str:
    low = text.lower()
    scores = [(len(re.findall(rf"\b(?:{pat})", low)), -i, key) for i, (key, pat) in enumerate(_ENTITY_WORDS)]
    best = max(scores)
    return best[2] if best[0] else "order"


def _best_field(m: semantic.Model, phrase: str, kinds: tuple[str, ...]) -> semantic.Field | None:
    want = _stems(phrase)
    if not want:
        return None
    best, score = None, 0.0
    for f in m.fields.values():
        if f.kind not in kinds:
            continue
        label = f.label.split("›")[-1]
        have = _stems(label) | _stems(f.key.split(".")[-1])
        hit = len(want & have)
        if not hit:
            continue
        s = hit / len(have) + hit + (0.3 if "." not in f.key else 0) + (0.2 if f.key.startswith("customer_profile") else 0)
        if s > score:
            best, score = f, s
    return best


def rules(db: Session, tenant_id: int, text: str) -> dict[str, Any]:
    low = " ".join(text.lower().split())
    entity = _entity(low)
    m = semantic.model(db, tenant_id, entity)
    notes = [f"About {m.entity.label.lower()}"]
    spec: dict[str, Any] = {"entity": entity, "dimensions": [], "measures": [], "filters": []}

    # the measure
    agg = next((a for a, pat in _AGG_WORDS if re.search(pat, low)), "count")
    head = re.split(r"\b(?:by|per|across|for\s+each|split\s+by|broken\s+down\s+by)\b", low)[0]
    numeric = _best_field(m, re.sub(r"\b(?:top\s+\d+|show|me|what|is|the|total|average|sum|of)\b", " ", head), ("number",))
    if re.search(r"\bmrr\b|recurring\s+revenue", low) and "mrr" in m.fields:
        numeric, agg = m.fields["mrr"], "sum" if agg == "count" else agg
    if agg in ("sum", "avg", "median", "max", "min") and numeric is None:
        money = m.entity.money
        numeric = m.fields.get(money) if money else None
        if numeric is None:
            agg = "count"
    if agg == "count_distinct":
        target = _best_field(m, head, ("dimension",)) or m.fields["id"]
        spec["measures"].append({"field": target.key, "agg": "count_distinct"})
        notes.append(f"Measure: distinct {target.label.lower()}")
    elif agg != "count" and numeric is not None:
        spec["measures"].append({"field": numeric.key, "agg": agg})
        notes.append(f"Measure: {engine._AGG_LABEL[agg].lower()} {numeric.label.lower()}")
    else:
        spec["measures"].append({"agg": "count"})
        notes.append(f"Measure: number of {m.entity.label.lower()}")

    # dimensions: the time grain, then "by X and Y"
    grain = next((g for g, pat in _GRAIN_WORDS if re.search(pat, low)), None)
    tail = re.split(r"\b(?:by|per|across|for\s+each|split\s+by|broken\s+down\s+by)\b", low, maxsplit=1)
    if len(tail) > 1:
        part = re.split(r"\b(?:for|where|with|in\s+the\s+last|last|this|since|as\s+a|as|top|only|excluding)\b", tail[1])[0]
        for chunk in re.split(r"\s*(?:,|\band\b|\bthen\b|&)\s*", part):
            chunk = chunk.strip()
            if not chunk or re.fullmatch(r"(?:month|week|day|quarter|year|hour|weekday|date|time)s?", chunk):
                continue
            f = _best_field(m, chunk, ("dimension", "bool", "date"))
            if f is not None and f.key not in [d["field"] for d in spec["dimensions"]]:
                d: dict[str, Any] = {"field": f.key}
                if f.kind == "date":
                    d["grain"] = grain or "month"
                    grain = None
                spec["dimensions"].append(d)
    if grain:
        spec["dimensions"].insert(0, {"field": m.entity.date, "grain": grain})
    if spec["dimensions"]:
        notes.append("By " + ", ".join(engine._dim_label(m, d).lower() for d in spec["dimensions"]))

    # filters
    if re.search(r"\bb2b\b|\bbusiness\b|\benterprise\b", low) or re.search(r"\bb2c\b|\bconsumer\b|\bretail\b", low):
        seg = "B2B" if re.search(r"\bb2b\b|\bbusiness\b|\benterprise\b", low) else "B2C"
        key = next((k for k in ("customer_profile.segment", "profile.segment") if k in m.fields), None)
        if key:
            spec["filters"].append({"field": key, "op": "contains", "value": seg})
            notes.append(f"Only {seg}")
    rel = re.search(r"\b(?:in\s+the\s+)?(?:last|past)\s+(\d+)\s+(day|week|month|year)s?\b", low)
    date_key = next((d["field"] for d in spec["dimensions"] if d.get("grain")), m.entity.date)
    if rel:
        days = int(rel.group(1)) * {"day": 1, "week": 7, "month": 30, "year": 365}[rel.group(2)]
        spec["filters"].append({"field": date_key, "op": "last_n_days", "value": days})
        notes.append(f"Last {rel.group(1)} {rel.group(2)}s")
    else:
        for phrase, op in (("this month", "this_month"), ("last month", "last_month"), ("this quarter", "this_quarter"),
                           ("this year", "this_year"), ("last year", "last_year"), ("today", "today")):
            if phrase in low:
                spec["filters"].append({"field": date_key, "op": op})
                notes.append(phrase.capitalize())
                break
    for f in m.fields.values():  # "active orders", "critical tickets": a picklist value said outright
        if f.values and "." not in f.key and f.key not in [d["field"] for d in spec["dimensions"]]:
            said = [v for v in f.values if re.search(rf"\b{re.escape(v.lower().replace('_', ' '))}\b", low)]
            if said and len(said) < len(f.values):
                spec["filters"].append({"field": f.key, "op": "in", "value": said})
                notes.append(f"{f.label}: {', '.join(said)}")

    top = re.search(r"\b(?:top|best|bottom|worst)\s+(\d+)\b", low)
    if top and spec["dimensions"]:
        spec["top"] = {"n": int(top.group(1)), "dir": "asc" if re.search(r"\b(?:bottom|worst)\b", low) else "desc", "other": False}
        notes.append(f"Top {top.group(1)}")
    elif top:
        f = _best_field(m, "name", ("dimension",)) or m.fields["id"]
        spec["dimensions"].append({"field": "customer.name" if "customer.name" in m.fields and re.search(r"customer|client", low) else f.key})
        spec["top"] = {"n": int(top.group(1)), "dir": "desc", "other": False}
    spec["chart"] = _chart(low, spec)
    spec["title"] = text.strip().rstrip("?.!")[:120]
    return {"spec": spec, "understood": notes, "by": "rules"}


def _chart(low: str, spec: dict[str, Any]) -> str:
    dims = spec["dimensions"]
    if re.search(r"\b(?:pie|donut|doughnut|share|split|mix)\b", low) and len(dims) == 1:
        return "donut"
    if re.search(r"\btable\b|\blist\b", low):
        return "table"
    if re.search(r"\bheat\s*map\b", low) and len(dims) == 2:
        return "heatmap"
    if re.search(r"\bfunnel\b", low):
        return "funnel"
    if not dims:
        return "kpi"
    if dims[0].get("grain") and dims[0]["grain"] not in ("weekday", "hour"):
        return "area" if re.search(r"\barea\b", low) else "line"
    if len(dims) == 2:
        return "stacked"
    return "hbar" if spec.get("top") else "bar"


_PROMPT = (
    "You turn a business question about a telecom CRM into a report spec. Answer with JSON only: "
    '{"entity": "<key>", "dimensions": [{"field": "<key>", "grain": "year|quarter|month|week|day|weekday|hour (dates only)"}], '
    '"measures": [{"field": "<numeric key, omit for a count>", "agg": "count|count_distinct|sum|avg|min|max|median"}], '
    '"filters": [{"field": "<key>", "op": "eq|neq|in|gt|gte|lt|lte|contains|last_n_days|this_month|last_month|this_year", "value": ...}], '
    '"top": {"n": <int>} or null, "chart": "bar|hbar|stacked|line|area|donut|table|kpi|heatmap|scatter|funnel", "title": "<short title>"}. '
    "Use only the entities and field keys listed. Dates in dimensions need a grain."
)


def ask(db: Session, tenant_id: int, text: str, use_ai: bool = True) -> dict[str, Any]:
    from app.crm_core import llm

    ruled = rules(db, tenant_id, text)
    target = llm.resolve(db, tenant_id) if use_ai else None
    if target is None:
        return ruled
    low = text.lower()
    ranked = sorted(_ENTITY_WORDS, key=lambda kv: -len(re.findall(rf"\b(?:{kv[1]})", low)))
    candidates = list(dict.fromkeys([ruled["spec"]["entity"], *(k for k, _ in ranked[:2])]))
    lines = []
    for key in candidates:
        m = semantic.model(db, tenant_id, key)
        fields = "; ".join(f"{f.key} ({f.kind}{'=' + '|'.join(f.values[:8]) if f.values else ''})" for f in m.fields.values())
        lines.append(f"Entity {key} - {m.entity.label}. Fields: {fields}")
    others = ", ".join(f"{k} ({e.label})" for k, e in semantic.ENTITIES.items() if k not in candidates)
    try:
        with llm.budget(40):
            answer = llm.chat_json(target, _PROMPT, "\n".join(lines) + f"\nOther entities: {others}\n\nQuestion: {text}", max_tokens=600)
        spec = {k: answer.get(k) for k in ("entity", "dimensions", "measures", "filters", "top", "chart", "title")}
        spec["dimensions"] = [d for d in spec.get("dimensions") or [] if isinstance(d, dict) and d.get("field")]
        spec["measures"] = [x for x in spec.get("measures") or [] if isinstance(x, dict)] or [{"agg": "count"}]
        spec["filters"] = [f for f in spec.get("filters") or [] if isinstance(f, dict) and f.get("field") and f.get("op", "eq") in engine.OPERATORS]
        spec["top"] = spec.get("top") if isinstance(spec.get("top"), dict) and spec["top"].get("n") else None
        for d in spec["dimensions"]:
            if not d.get("grain"):
                d.pop("grain", None)
        m = semantic.model(db, tenant_id, str(spec.get("entity")))
        engine.validate(m, spec)
        spec["chart"] = spec.get("chart") if spec.get("chart") in CHARTS else _chart(low, spec)
        spec["title"] = str(spec.get("title") or ruled["spec"]["title"])[:120]
        if not spec["top"]:
            spec.pop("top")
        return {"spec": spec, "understood": [f"Read by {target.label}"], "by": target.label}
    except (llm.LlmUnavailable, ValueError, KeyError, TypeError, AttributeError) as exc:
        ruled["understood"].append(f"AI model not used ({str(exc)[:120]}) - read with the built-in rules")
        return ruled


CHARTS = ("bar", "hbar", "stacked", "stacked100", "line", "area", "donut", "pie", "table", "pivot", "kpi", "heatmap", "scatter", "funnel", "treemap", "combo")
