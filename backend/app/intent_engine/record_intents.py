"""Intents for the tenant's own record types (Admin -> Record Types; crm_core/custom_record_service.py does the
work, orchestration/actions_records.py builds the steps). Custom Development licence (licensing.RECORD_INTENTS).

  INT-151 Create a record          a record of one of your types - its page's mandatory fields and rules hold
  INT-152 Update a record          its name, parent and fields, while its state allows editing
  INT-153 Move a record            a move of its type's state model (rules for the new state hold first)
  INT-154 Assign a record          to a person, a position or a department
  INT-155 Link records             relate two records (a relationship the admin defined) - or unlink them
  INT-156 Find records             records of a type: by words, state, parent or field values
  INT-157 Delete a record          once nothing hangs off it or points to it
  INT-158 Design a record type     plain English -> the type, its fields, states, relationships and rules (admins)

The tenant's type names aren't known to a static phrase rule, so classifier.record_step (db-aware) recognises
"create a site survey for Acme", "move SS-0004 to completed", "link SS-0004 to contact CT-0002" from the
tenant's own labels and numbers before any model runs; RULES below catch the generic wording.
"""
from __future__ import annotations

import re
from typing import Any

CODES = ("INT-151", "INT-152", "INT-153", "INT-154", "INT-155", "INT-156", "INT-157", "INT-158")

SPECS: dict[str, tuple[str, str, str, str, str | None, list[str], list[str]]] = {
    "INT-151": ("Create a record", "Create a record of one of your own record types (a site survey, an asset inspection, a "
                "contract ...) - its page's mandatory fields and validation rules are checked, and it starts in its first state.",
                "records", "🆕", None, ["record_type", "record_name", "parent_ref", "record_fields"],
                ["Create a site survey 'Rooftop check' for Acme Towers with survey date 2026-11-20", "New contract for Globex"]),
    "INT-152": ("Update a record", "Change a record of your own types - its name, what it hangs off, its fields - while its "
                "state allows editing.", "records", "✏️", None, ["record_ref", "record_name", "parent_ref", "record_fields"],
                ["Update SS-0004 floors=12; outcome=Feasible", "Set the surveyor of SS-0004 to Ravi"]),
    "INT-153": ("Move a record", "Move a record of your own types to another state of its state model - rules for the new "
                "state are checked first, and state flows run once it's there.", "records", "➡️", None,
                ["record_ref", "record_state", "reasoning"], ["Move SS-0004 to completed", "Close CT-0012"]),
    "INT-154": ("Assign a record", "Hand a record of your own types to a person, a position or a department.", "records", "👥", None,
                ["record_ref", "to_user", "to_position", "to_department", "reason"], ["Assign SS-0004 to Ravi", "Give SS-0004 to the Survey team"]),
    "INT-155": ("Link records", "Relate two records through a relationship your administrator defined (a site and its "
                "contacts) - or unlink them.", "records", "🔗", None, ["record_ref", "relationship", "related_ref", "unlink", "link_note"],
                ["Link SS-0004 to contact CT-0002", "Unlink CT-0002 from site ST-0001"]),
    "INT-156": ("Find records", "List records of one of your types - by words, state, what they hang off or field values.", "records", "🔎",
                None, ["record_type", "record_query", "record_state", "parent_ref", "record_fields"],
                ["Show completed site surveys", "Which site surveys of Acme Towers are open?"]),
    "INT-157": ("Delete a record", "Delete a record of your own types - once nothing hangs off it or points to it.", "records", "🗑️", None,
                ["record_ref", "reasoning"], ["Delete SS-0007"]),
    "INT-158": ("Design a record type", "Describe a kind of record in plain words - the AI drafts the record type with its "
                "fields, lookups, states, relationships and validation rules, and builds it (administrators).", "workspace", "🧩", None,
                ["design_text", "apply_design"],
                ["Design a record type: Site Survey for a customer, with survey date (date, required), floors (number), "
                 "outcome (Pending, Feasible, Not feasible); states scheduled -> in progress -> completed, cancelled"]),
}
LABELS: dict[str, str] = {code: spec[0] for code, spec in SPECS.items()}
LIB: dict[str, dict[str, Any]] = {
    "record_type": {"label": "Record type", "type": "picklist", "source": "@record_types", "help": "One of your own record types (Admin -> Record Types)"},
    "record_ref": {"label": "Record", "placeholder": "e.g. SS-0004", "help": "Its number - or pick it on its page"},
    "record_name": {"label": "Name", "placeholder": "e.g. Rooftop check"},
    "parent_ref": {"label": "Belongs to", "placeholder": "e.g. Acme Towers, or ST-0002",
                   "help": "The record it hangs off - by name, number or #id"},
    "record_fields": {"label": "Fields", "type": "textarea", "placeholder": "survey_date=2026-11-20; floors=12; outcome=Feasible",
                      "help": "field=value; ... - a lookup takes the record's number or name"},
    "record_state": {"label": "State", "placeholder": "e.g. completed", "help": "A state of the record's state model (or the move's name)"},
    "relationship": {"label": "Relationship", "type": "picklist", "source": "@relationships", "help": "Defined in Admin -> Record Types"},
    "related_ref": {"label": "Related record", "placeholder": "e.g. CT-0002 or a customer's name"},
    "unlink": {"label": "Unlink instead", "type": "boolean", "default": False},
    "link_note": {"label": "Note", "placeholder": "e.g. site manager"},
    "record_query": {"label": "Words", "placeholder": "e.g. rooftop"},
    "design_text": {"label": "Describe the record type", "type": "textarea",
                    "placeholder": "Site Survey for a customer: survey date (date, required), floors (number), outcome (Pending, Feasible, "
                                   "Not feasible), site (lookup to Site). States: scheduled -> in progress -> completed, cancelled. "
                                   "Rules: survey date must be in the future when scheduled.",
                    "help": "Name it, say what it belongs to, list its fields, states, relationships and rules - in your own words"},
    "apply_design": {"label": "Build it now", "type": "boolean", "default": True,
                     "help": "Off: only show the draft (Admin -> Record Types shows it too, to adjust before building)"},
}
OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {}
REQUIRED: dict[str, list[str]] = {
    "INT-151": ["record_type", "record_name"], "INT-152": [], "INT-153": ["record_state"], "INT-154": [], "INT-155": ["related_ref"],
    "INT-156": ["record_type"], "INT-157": [], "INT-158": ["design_text"],
}
_NEEDS_RECORD = ("INT-152", "INT-153", "INT-154", "INT-155", "INT-157")


def missing(code: str, entities: dict[str, Any]) -> list[str]:
    out = []
    if code in _NEEDS_RECORD and not (entities.get("record_ref") or (entities.get("entity_type") and entities.get("entity_id"))):
        out.append("record_ref")
    if code == "INT-154" and not (entities.get("to_user") or entities.get("to_position") or entities.get("to_department")):
        out.append("to_user or to_position or to_department")
    return out


NUMBER = r"\b[A-Z][A-Z0-9]{0,9}-\d{3,9}\b"
RULES: list[tuple[str, re.Pattern]] = [
    ("INT-158", re.compile(r"\b(?:design|define|build|set\s+up)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?(?:custom\s+)?(?:record\s+type|object|entity)\b", re.I)),
]


def fields_text(raw: Any) -> dict[str, Any]:
    """'a=1; b = two, c: 3' (or a dict) -> {"a": "1", "b": "two", "c": "3"}."""
    from app.crm_core.custom_record_service import parse_fields

    return parse_fields(raw)


def extract(code: str, text: str, fields: dict[str, Any], loose: dict[str, Any]) -> dict[str, Any]:
    e: dict[str, Any] = {k: fields[k] for k in SPECS[code][5] if fields.get(k) not in (None, "")}
    if code == "INT-158" and "design_text" not in e:
        body = re.sub(r"^\s*(?:please\s+)?(?:design|define|build|set\s+up)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?(?:custom\s+)?"
                      r"(?:record\s+type|object|entity)\s*[:\-,]?\s*", "", text, flags=re.I)
        e["design_text"] = body.strip() or text.strip()
    return e


NEXT: dict[str, list[tuple[str, str]]] = {
    "INT-151": [("INT-153", "Move it on"), ("INT-154", "Assign it"), ("INT-155", "Link a related record")],
    "INT-152": [("INT-153", "Move it on")],
    "INT-153": [("INT-154", "Assign it")],
    "INT-154": [("INT-153", "Move it on")],
    "INT-155": [("INT-156", "Find related records")],
    "INT-156": [("INT-151", "Create another")],
    "INT-158": [("INT-151", "Create the first record")],
}
TRAINING: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = [
    ("INT-158", [("Design a record type: Site Survey for a customer with survey date and floors", {}),
                 ("define a new record type called Asset Inspection", {})]),
]
FLOW_FIELDS: dict[str, list[str]] = {code: list(spec[5]) for code, spec in SPECS.items()}
RESULTS: dict[str, dict[str, list[str]]] = {
    "INT-151": {"entity_type": [], "entity_id": [], "number": [], "name": [], "state": []},
    "INT-152": {"entity_type": [], "entity_id": [], "number": [], "updated_fields": []},
    "INT-153": {"entity_type": [], "entity_id": [], "number": [], "from_state": [], "to_state": []},
    "INT-154": {"entity_type": [], "entity_id": [], "number": [], "owner_user_id": [], "owner_position_id": []},
    "INT-155": {"entity_type": [], "entity_id": [], "number": [], "relationship": [], "related_id": [], "linked": ["true", "false"]},
    "INT-156": {"entity_type": [], "total": [], "records": []},
    "INT-157": {"entity_type": [], "entity_id": [], "number": []},
    "INT-158": {"entity_type": [], "record_type": [], "fields": [], "states": [], "applied": ["true", "false"]},
}
