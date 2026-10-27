"""Compile ordered journeys into page fields and guarded, reusable Studio actions.

Stage completion is an explicit operator attestation, never a claim that an
external installation, identity check or billing operation actually ran.
"""
from __future__ import annotations

import hashlib
import re


def record_segment(record):
    """Resolve the customer segment consistently for customer/deal/order records."""
    from app.crm_core import process_service
    from app.models.crm import Party
    if record is None:
        return None
    if isinstance(record, Party):
        return process_service.party_segment(record)
    for relation in ("party", "lead", "opportunity"):
        parent = getattr(record, relation, None)
        if parent is not None:
            return record_segment(parent)
    if getattr(record, "party_id", None):
        from sqlalchemy.orm import object_session
        session = object_session(record)
        party = session.get(Party, record.party_id) if session is not None else None
        if party is not None and party.tenant_id == record.tenant_id:
            return record_segment(party)
    profile = getattr(record, "profile", None)
    return getattr(profile, "segment", None)


def scope(graph, name):
    if graph.get("segment") in ("B2B", "B2C"):
        return graph["segment"]
    segments = {match.upper() for match in re.findall(r"\b(B2B|B2C)\b", name or "", re.I)}
    if len(segments) == 1:
        return segments.pop()
    return None


def parse(text: str):
    if not re.search(r"(?:->|→|=>)", text):
        return None
    # An imperative chain on actual records belongs to the action agent.
    # Journey configuration must be explicitly requested.
    if not re.search(r"\b(?:process|workflow|journey|lifecycle|procedure)\b", text, re.I):
        return None
    parts = re.split(r"\s*(?:->|→|=>)\s*", text)
    first = re.split(r"\s+[-–—]\s+|:\s*", parts[0])
    title = first[0] if len(first) > 1 else "Business journey"
    parts[0] = first[-1]
    stages = [p.strip(" .\n") for p in parts]
    if len(stages) < 2 or len(stages) > 10 or any(not p or len(p) > 120 for p in stages):
        return None
    if len(set(stages)) != len(stages):
        return None
    title = re.sub(r"^(?:i want to |please )?(?:add|build|create|design|set up)\s+(?:a |an |our )?(?:new )?", "", title, flags=re.I)
    return title[:100], stages


def compile_journey(design, cid: str, text: str):
    from app.crm_core import page_service
    from app.orchestration import flow_ai, flow_engine

    parsed = parse(text)
    if parsed is None:
        design.unclear(cid, text, "Use between two and ten distinct stages separated by arrows.")
        return
    title, stages = parsed
    # Anchor the whole journey on an existing record; do not manufacture records.
    entity = design.answers.get(f"{cid}.record")
    if entity not in ("party", "order", "lead", "opportunity", "quote"):
        design.ask(f"{cid}.record", "Which record should hold this journey's progress?",
                   [("party", "Customer"), ("order", "Order"), ("lead", "Lead"),
                    ("opportunity", "Opportunity"), ("quote", "Quote")],
                   default="party" if re.search(r"customer|b2c", text, re.I) else "order")
        return
    pages = [p for p in page_service.pages(design.db, design.tenant_id) if p["entity_type"] == entity]
    segment = "B2C" if re.search(r"\bb2c\b|\bconsumer\b", text, re.I) else "B2B"
    page = next((p for p in pages if p["segment"] == segment), None)
    if page is None:
        design.unclear(cid, text, f"No {segment} page is available for this record. Enable that process and create its segment-specific page first.")
        return
    key = "journey_" + hashlib.sha256(text.encode()).hexdigest()[:16]
    field = "record." + key
    warning = "Completion buttons record a person's confirmation. They do not perform identity checks, installation, network activation or billing."
    ops = [{"op": "add_field", "key": key, "label": f"{title} — completed stage",
            "data_type": "picklist", "values": stages, "section": title},
           {"op": "readonly", "key": key},
           {"op": "add_text", "text": warning, "section": title}]
    design.add("page", text, f"{title} progress", [" → ".join(stages), "Progress field and completion buttons"],
               {"_cid": cid + "_page", "page_key": page["page_key"], "entity_type": entity,
                "segment": page["segment"], "ops": ops, "page": page["name"]}, [warning])
    for i, stage in enumerate(stages):
        flow_id = f"{cid}_stage_{i}"
        nodes = [
            {"id": "start", "type": "start", "label": "Start", "config": {}},
            {"id": "guard", "type": "condition", "label": "Check previous stage", "config": {"field": field,
                "operator": "eq" if i else "empty", "value": stages[i - 1] if i else ""}},
            {"id": "complete", "type": "set_field", "label": f"Confirm {stage}", "config": {"field": field, "value": stage}},
            {"id": "reject", "type": "reject", "label": "Stage out of sequence", "config": {"message": "Complete the previous stage first; completed stages cannot be repeated."}},
            {"id": "end", "type": "end", "label": "End", "config": {}},
        ]
        edges = [{"id": str(n), "source": a, "target": b, "label": label} for n, (a, b, label) in enumerate([
            ("start", "guard", ""), ("guard", "complete", "true"), ("guard", "reject", "false"), ("complete", "end", "")])]
        graph = flow_ai.layout({"nodes": nodes, "edges": edges})
        graph["segment"] = segment
        design.add("flow", stage, f"Confirm {stage}", [f"After {stages[i - 1]}" if i else "Start the journey", "Record completion on the selected record"],
                   {"_cid": flow_id, "name": f"{title}: confirm {stage}", "entity_type": entity,
                    "trigger": {"trigger_type": "custom_intent", "entity_type": entity},
                    "graph": graph, "valid": not flow_engine.validate_graph(graph), "source": "journey",
                    "required_fields": entity + "_id", "phrases": f"{title} confirm {stage}",
                    "journey": {"field": key, "previous": stages[i - 1] if i else None, "stage": stage}}, [warning])
        ops.append({"op": "add_button", "label": f"Confirm {stage}", "section": title, "flow_component": flow_id})
