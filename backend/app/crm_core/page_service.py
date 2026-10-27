"""Page Designer - what a record's page shows, per entity and segment, and
what it takes for a page to be saved (models/pages.py).

Fields on a page come from one catalog per entity:
- core fields - columns of the record (and, for parties, the B2B/B2C
  profile: segment, industry);
- field definitions (custom_field_definitions): the standard telecom
  B2B/B2C fields BRT 360 ships are seeded there as system definitions, and
  a tenant's own custom fields sit beside them.

A page (PageLayout) arranges them in sections and marks each mandatory,
optional, hidden or read-only - per segment, so GSTIN can be mandatory on
the B2B prospect page while a B2C prospect never sees it. Without a saved
page a sensible default is computed, so nothing has to be set up first.

Saving a page runs, in order: mandatory fields, each field's type check,
the simple validation rules (compiled from plain English by the rule
compiler into a closed JSON form - never code), then complex rules
delegated to Flow Studio flows (trigger "validation").
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import custom_field_service
from app.models.custom_fields import CUSTOM_FIELD_DATA_TYPES, CustomFieldDefinition
from app.models.pages import PageLayout, ValidationRule

ENTITIES: dict[str, str] = {
    "training_program": "Training program",
    "party": "Prospect / Customer",
    "lead": "Lead",
    "opportunity": "Opportunity",
    "quote": "Quote",
    "order": "Order",
}
SEGMENTS = ("B2B", "B2C")

_INDUSTRIES = ("Banking & Finance,Government,Healthcare,Education,Retail,Manufacturing,IT & Services,Media,Hospitality,"
               "Logistics,Energy & Utilities,Telecom,Other")


def _f(key: str, label: str, data_type: str = "text", section: str = "", segment: str | None = None, values: str = "",
       help: str = "", core: bool = False, labels: dict[str, str] | None = None) -> dict[str, Any]:
    return {"key": key, "label": label, "data_type": data_type, "allowed_values": values or None, "section": section,
            "segment": segment, "help_text": help or None, "core": core, "labels": labels or {}}


# Columns of the record (or the party's B2B/B2C profile) a page can show.
CORE_FIELDS: dict[str, list[dict[str, Any]]] = {
    "training_program": [],
    "party": [
        _f("segment", "Segment", "picklist", "Basics", values="B2B,B2C", core=True, help="Business (B2B) or consumer (B2C)"),
        _f("name", "Name", "text", "Basics", core=True, labels={"B2B": "Company name", "B2C": "Full name"}),
        _f("email", "Email", "email", "Basics", core=True),
        _f("phone", "Phone", "phone", "Basics", core=True, labels={"B2C": "Mobile number"}),
        _f("industry", "Industry", "picklist", "Company", "B2B", values=_INDUSTRIES, core=True),
    ],
    "lead": [
        _f("source", "Lead source", "picklist", "Lead", values="referral,website,event,partner,cold_call,campaign,walk_in,inbound_call", core=True),
        _f("region", "Region", "text", "Lead", core=True),
    ],
    "opportunity": [
        _f("name", "Opportunity name", "text", "Opportunity", core=True),
        _f("offering_name", "Product", "text", "Opportunity", core=True),
        _f("quantity", "Quantity", "number", "Opportunity", core=True),
    ],
    "quote": [_f("discount_justification", "Why this discount?", "textarea", "Quote", core=True)],
    "order": [_f("contract_months", "Contract months", "number", "Order", core=True)],
}
# Core fields kept on the party's profile rather than the party row.
_PROFILE_FIELDS = {"segment", "industry"}

# The standard fields a telecom B2B/B2C implementation needs - seeded per tenant as system field definitions.
STANDARD_FIELDS: dict[str, list[dict[str, Any]]] = {
    "party": [
        # B2B - the company
        _f("legal_name", "Registered legal name", section="Company", segment="B2B"),
        _f("trading_name", "Trading / brand name", section="Company", segment="B2B"),
        _f("company_type", "Company type", "picklist", "Company", "B2B",
           "Private Limited,Public Limited,LLP,Partnership,Proprietorship,Government,PSU,NGO / Trust,MNC Subsidiary"),
        _f("registration_number", "Company registration no. (CIN)", section="Company", segment="B2B"),
        _f("tax_id", "GSTIN / VAT number", section="Company", segment="B2B", help="15-character GSTIN in India"),
        _f("pan", "PAN / tax reference", section="Company", segment="B2B"),
        _f("website", "Website", section="Company", segment="B2B"),
        _f("employee_count", "Employees", "picklist", "Company", "B2B", "1-10,11-50,51-200,201-1000,1001-5000,5000+"),
        _f("annual_revenue", "Annual revenue", "picklist", "Company", "B2B",
           "Under ₹1 Cr,₹1-10 Cr,₹10-100 Cr,₹100-500 Cr,₹500 Cr+"),
        _f("account_tier", "Account tier", "picklist", "Company", "B2B", "Strategic,Enterprise,Mid-market,SMB"),
        _f("number_of_sites", "Number of sites", "number", "Company", "B2B"),
        # B2B - who we deal with
        _f("contact_name", "Primary contact", section="Primary contact", segment="B2B"),
        _f("contact_designation", "Designation", section="Primary contact", segment="B2B"),
        _f("contact_email", "Contact email", "email", "Primary contact", "B2B"),
        _f("contact_phone", "Contact phone", "phone", "Primary contact", "B2B"),
        _f("is_decision_maker", "Decision maker?", "boolean", "Primary contact", "B2B"),
        # B2C - the person
        _f("first_name", "First name", section="Personal", segment="B2C"),
        _f("last_name", "Last name", section="Personal", segment="B2C"),
        _f("date_of_birth", "Date of birth", "date", "Personal", "B2C"),
        _f("gender", "Gender", "picklist", "Personal", "B2C", "Female,Male,Other,Prefer not to say"),
        _f("alternate_phone", "Alternate phone", "phone", "Personal", "B2C"),
        _f("preferred_language", "Preferred language", "picklist", "Personal", "B2C",
           "English,Hindi,Odia,Bengali,Tamil,Telugu,Kannada,Malayalam,Marathi,Gujarati,Punjabi,Other"),
        _f("id_type", "ID proof type", "picklist", "KYC", "B2C", "Aadhaar,Passport,Driving licence,Voter ID,PAN"),
        _f("id_number", "ID proof number", section="KYC", segment="B2C"),
        _f("kyc_status", "KYC status", "picklist", "KYC", "B2C", "Not started,Pending,Verified,Rejected"),
        _f("marketing_consent", "Agrees to marketing messages", "boolean", "KYC", "B2C"),
        _f("dwelling_type", "Dwelling type", "picklist", "Address", "B2C", "Apartment,Independent house,Villa,PG / Hostel,Office"),
        _f("occupancy", "Owner or tenant", "picklist", "Address", "B2C", "Owner,Tenant"),
        # both - where they are and what they have today
        _f("address_line1", "Address line 1", section="Address"),
        _f("address_line2", "Address line 2", section="Address"),
        _f("city", "City", section="Address"),
        _f("state", "State", section="Address"),
        _f("postal_code", "PIN code", section="Address"),
        _f("country", "Country", section="Address"),
        _f("current_provider", "Current service provider", section="Current services"),
        _f("current_services", "Services they use today", "textarea", "Current services", "B2B",
           help="e.g. 2 x ILL 100 Mbps, MPLS across 12 branches, 300 mobile lines"),
        _f("monthly_telecom_spend", "Monthly telecom spend (₹)", "number", "Current services", "B2B"),
        _f("contract_end_date", "Current contract ends", "date", "Current services", "B2B"),
        _f("credit_rating", "Credit rating", "picklist", "Current services", "B2B", "A,B,C,Not rated"),
    ],
    "lead": [
        _f("requirement_summary", "What they need", "textarea", "Requirement", help="In the customer's words"),
        _f("budget", "Budget (₹ / month)", "number", "Requirement", "B2B"),
        _f("decision_timeline", "Decision timeline", "picklist", "Requirement", "B2B", "Immediate,1-3 months,3-6 months,6+ months"),
        _f("expected_close_date", "Expected close date", "date", "Requirement", "B2B"),
        _f("competitors", "Competitors in the deal", section="Requirement", segment="B2B"),
        _f("preferred_install_date", "Preferred installation date", "date", "Requirement", "B2C"),
        _f("referral_code", "Referral code", section="Requirement", segment="B2C"),
        _f("campaign", "Campaign", section="Requirement"),
    ],
}

# Mandatory on a page until the admin says otherwise.
DEFAULT_MANDATORY: dict[str, set[str]] = {"party": {"name"}, "opportunity": {"name"}}
_SECTION_ORDER = ["Basics", "Company", "Personal", "Primary contact", "Address", "KYC", "Current services", "Lead", "Requirement"]


def entity_type_of(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    value = {"prospect": "party", "customer": "party", "account": "party", "parties": "party"}.get(value, value)
    if value not in ENTITIES:
        raise ValueError(f"Pages exist for: {', '.join(ENTITIES)}")
    return value


def segment_of(raw: Any) -> str | None:
    value = str(raw or "").strip().upper()
    if value.startswith("B2B"):
        return "B2B"
    if value.startswith("B2C") or value in ("CONSUMER", "RESIDENTIAL", "INDIVIDUAL"):
        return "B2C"
    return None


# ---------------------------------------------------------------- the field catalog


def seed_defaults(db: Session, tenant_id: int) -> int:
    """The standard fields, where the tenant has no field of that key yet -
    a tenant's own field of the same key is kept and used instead."""
    created = 0
    for entity_type, fields in STANDARD_FIELDS.items():
        existing = {
            k for (k,) in db.query(CustomFieldDefinition.key).filter(
                CustomFieldDefinition.tenant_id == tenant_id, CustomFieldDefinition.entity_type == entity_type
            )
        }
        for position, spec in enumerate(fields):
            if spec["key"] in existing:
                continue
            db.add(CustomFieldDefinition(
                tenant_id=tenant_id, entity_type=entity_type, key=spec["key"], label=spec["label"], data_type=spec["data_type"],
                allowed_values=spec["allowed_values"], required=False, is_system=True, segment=spec["segment"],
                section=spec["section"], position=position, help_text=spec["help_text"],
            ))
            created += 1
    if created:
        db.flush()
    return created


def _def_out(d: CustomFieldDefinition) -> dict[str, Any]:
    return {
        "key": d.key, "label": d.label, "data_type": d.data_type, "values": custom_field_service.allowed_values(d),
        "section": d.section or "More details", "segment": d.segment, "help": d.help_text or "", "core": False,
        "system": bool(d.is_system), "id": d.id, "is_active": d.is_active, "position": d.position or 0,
        "required_everywhere": bool(d.required),
    }


def catalog(db: Session, tenant_id: int, entity_type: str, segment: str | None = None, include_inactive: bool = False) -> list[dict[str, Any]]:
    """Every field a page of this entity (and segment) can show."""
    out = []
    for spec in CORE_FIELDS.get(entity_type, []):
        if segment and spec["segment"] and spec["segment"] != segment:
            continue
        out.append({
            "key": spec["key"], "label": spec["labels"].get(segment or "", spec["label"]), "data_type": spec["data_type"],
            "values": [v for v in (spec["allowed_values"] or "").split(",") if v], "section": spec["section"],
            "segment": spec["segment"], "help": spec["help_text"] or "", "core": True, "system": True, "id": None,
            "is_active": True, "position": 0, "required_everywhere": False,
        })
    defs = custom_field_service.list_definitions(db, tenant_id, entity_type, active_only=not include_inactive)
    for d in sorted(defs, key=lambda d: (0 if d.is_system else 1, d.position or 0, d.id)):
        if segment and d.segment and d.segment != segment:
            continue
        out.append(_def_out(d))
    return out


def _by_key(fields: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {f["key"]: f for f in fields}


# ---------------------------------------------------------------- pages


def page_key(entity_type: str, segment: str | None) -> str:
    return f"{entity_type}.{segment}" if segment else entity_type


def default_layout(db: Session, tenant_id: int, entity_type: str, segment: str | None) -> dict[str, Any]:
    fields = catalog(db, tenant_id, entity_type, segment)
    sections: dict[str, list[dict[str, Any]]] = {}
    for f in fields:
        sections.setdefault(f["section"], []).append({
            "key": f["key"], "mandatory": f["key"] in DEFAULT_MANDATORY.get(entity_type, set()) or f["required_everywhere"],
            "hidden": False, "readonly": False,
        })
    if entity_type == "party" and segment is None:
        # One page for both segments: the segment picker decides which fields apply.
        pass
    order = sorted(sections, key=lambda s: (_SECTION_ORDER.index(s) if s in _SECTION_ORDER else 99, s))
    return {"sections": [{"title": title, "columns": 2, "fields": sections[title]} for title in order]}


def _layout_of(row: PageLayout) -> dict[str, Any]:
    try:
        layout = json.loads(row.layout_json or "{}")
    except json.JSONDecodeError:
        layout = {}
    layout.setdefault("sections", [])
    return layout


def _page_out(entity_type: str, segment: str | None, key: str, name: str, layout: dict[str, Any], row: PageLayout | None) -> dict[str, Any]:
    return {
        "page_key": key, "entity_type": entity_type, "segment": segment, "name": name, "layout": layout,
        "description": row.description if row else "", "is_default": row.is_default if row else True,
        "customized": row is not None, "is_active": row.is_active if row else True, "id": row.id if row else None,
        "brt_id": row.brt_id if row else None, "role_ids": layout.get("role_ids") or [], "events": layout.get("events") or {},
    }


def _for_roles(row: PageLayout, role_ids: set[int] | None) -> bool:
    """A page given to some roles is only for them; role_ids None = no user to check (design, checks)."""
    wanted = _layout_of(row).get("role_ids") or []
    return not wanted or role_ids is None or bool(set(wanted) & role_ids)


def _default_name(entity_type: str, segment: str | None) -> str:
    noun = {"party": "Prospect"}.get(entity_type, ENTITIES[entity_type])
    return f"{segment} {noun.lower()} page" if segment else f"{noun} page"


def page(db: Session, tenant_id: int, entity_type: str, segment: str | None = None, key: str | None = None,
         role_ids: set[int] | None = None) -> dict[str, Any]:
    """The page to show: a named page (key), else an active page of the
    tenant's own given to one of the user's roles, else the segment's page,
    else the entity's general page - saved or computed."""
    keys = [key] if key else []
    keys += [page_key(entity_type, segment)] if segment else []
    keys.append(entity_type)
    rows = {
        r.page_key: r for r in db.query(PageLayout).filter(
            PageLayout.tenant_id == tenant_id, PageLayout.page_key.in_(keys), PageLayout.is_active.is_(True)
        )
    }

    def out(row: PageLayout) -> dict[str, Any]:
        return _page_out(row.entity_type, row.segment, row.page_key, row.name, reconcile(db, tenant_id, row.entity_type, row.segment, _layout_of(row)), row)

    rows = {k: r for k, r in rows.items() if r.entity_type == entity_type and (r.segment is None or r.segment == segment)}
    if key and key in rows and _for_roles(rows[key], role_ids):
        return out(rows[key])
    if role_ids:
        assigned = [
            r for r in db.query(PageLayout).filter(
                PageLayout.tenant_id == tenant_id, PageLayout.entity_type == entity_type, PageLayout.is_active.is_(True),
                PageLayout.is_default.is_(False))
            if (r.segment is None or r.segment == segment) and _layout_of(r).get("role_ids") and _for_roles(r, role_ids)
        ]
        if assigned:
            return out(max(assigned, key=lambda r: (r.segment is not None, r.updated_at or r.created_at, r.id)))
    for k in keys:
        if k in rows and _for_roles(rows[k], role_ids):
            return out(rows[k])
    return _page_out(entity_type, segment, page_key(entity_type, segment), _default_name(entity_type, segment),
                     default_layout(db, tenant_id, entity_type, segment), None)


def page_by_key(db: Session, tenant_id: int, key: str) -> dict[str, Any] | None:
    """The page with this key, switched on or not (designing a page that is off)."""
    return next((p for p in pages(db, tenant_id) if p["page_key"] == key), None)


def reconcile(db: Session, tenant_id: int, entity_type: str, segment: str | None, layout: dict[str, Any]) -> dict[str, Any]:
    """A saved page keeps working as fields come and go: fields no longer in
    the catalog drop out, and fields added since land (optional) in their
    section - so a new custom field shows up without re-designing pages."""
    fields = _by_key(catalog(db, tenant_id, entity_type, segment))
    placed: set[str] = set()
    sections = []
    for section in layout.get("sections") or []:
        journey = any(str(f.get("key", "")).startswith("journey_") for f in section.get("fields") or [])
        match = re.search(r"\b(B2B|B2C)\b", section.get("title", ""), re.I) if journey else None
        section_scope = section.get("segment") or (match.group(1).upper() if match else None)
        kept = [dict(f) for f in section.get("fields") or []
                if f.get("kind") in ("button", "text") or (f.get("key") in fields and f["key"] not in placed)]
        placed |= {f["key"] for f in kept if f.get("key")}
        sections.append({"title": section.get("title") or "Details", "columns": section.get("columns") or 2, "fields": kept,
                         **({"segment": section_scope} if section_scope else {})})
    excluded = set(layout.get("excluded") or [])
    for key, f in fields.items():
        if key in placed or key in excluded:
            continue
        target = next((s for s in sections if s["title"] == f["section"]), None)
        if target is None:
            target = {"title": f["section"], "columns": 2, "fields": []}
            sections.append(target)
        target["fields"].append({"key": key, "mandatory": f["required_everywhere"], "hidden": False, "readonly": False})
    return {**layout, "sections": sections}


def pages(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    """Every page: each entity's general and per-segment pages (saved or
    default), and the tenant's own pages."""
    from app.crm_core import process_service

    served = sorted(process_service.segments(db, tenant_id)) or list(SEGMENTS)
    saved = {r.page_key: r for r in db.query(PageLayout).filter(PageLayout.tenant_id == tenant_id).order_by(PageLayout.id)}
    out = []
    for entity_type in ENTITIES:
        for segment in ([None] if entity_type not in ("party", "lead") else [s for s in SEGMENTS if s in served]):
            key = page_key(entity_type, segment)
            row = saved.pop(key, None)
            if row is not None:
                out.append(_page_out(entity_type, segment, key, row.name, reconcile(db, tenant_id, entity_type, segment, _layout_of(row)), row))
            else:
                out.append(_page_out(entity_type, segment, key, _default_name(entity_type, segment), default_layout(db, tenant_id, entity_type, segment), None))
    for row in saved.values():
        out.append(_page_out(row.entity_type, row.segment, row.page_key, row.name, reconcile(db, tenant_id, row.entity_type, row.segment, _layout_of(row)), row))
    from app.licensing import entity_available
    return [p for p in out if entity_available(db, tenant_id, p["entity_type"])]


# ---------------------------------------------------------------- screen elements beside fields
#
# A section holds fields, and also:
#   {"kind": "button", "id", "label", "intent", "style"} - runs an intent (a built-in one, or a
#       Flow Studio flow by its FLOW-<id> code) for the record on the page;
#   {"kind": "text", "id", "text"} - a note, heading or instruction.
# A page also carries "role_ids" (only these roles get it; none = everyone) and
# "events" {"on_open"|"after_save": FLOW-<id>} - a Flow Studio flow kicked off then.

PAGE_EVENTS = ("on_open", "after_save")
BUTTON_STYLES = ("primary", "default", "danger")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")[:40] or "item"


def _intent_code(db: Session, tenant_id: int, ref: Any) -> str:
    """An intent code, or a Flow Studio flow given by name - checked to exist for this tenant."""
    from app.intent_engine import intent_registry
    from app.models.flows import FlowDefinition
    from app.orchestration import flow_engine

    text = str(ref or "").strip()
    if not text:
        raise ValueError("Say what the button runs - an intent or a Flow Studio flow")
    if intent_registry.entry(db, tenant_id, text.upper()) is not None:
        return text.upper()
    name = re.sub(r"^(?:the\s+)?(?:flow\s+)?['\"]?|['\"]?(?:\s+flow)?$", "", text, flags=re.I).strip()
    flow = db.query(FlowDefinition).filter(FlowDefinition.tenant_id == tenant_id, FlowDefinition.name.ilike(name)).first()
    if flow is not None:
        if flow.trigger_type != "custom_intent":
            raise ValueError(f"The flow '{flow.name}' isn't started by people - make it a custom intent flow in the Flow Studio first")
        return flow_engine.custom_intent_code(flow)
    match = next((e for e in intent_registry.all_intents(db, tenant_id) if str(e.get("label", "")).lower() == text.lower()), None)
    if match is not None:
        return match["code"]
    raise ValueError(f"No intent or Flow Studio flow called '{text}'")


def _clean_element(db: Session, tenant_id: int, f: dict[str, Any]) -> dict[str, Any]:
    if f.get("kind") == "button":
        label = str(f.get("label") or "").strip()[:60]
        if not label:
            raise ValueError("A button needs a label")
        style = f.get("style") if f.get("style") in BUTTON_STYLES else "default"
        element_id = _slug(f.get("id") or label)
        return {"kind": "button", "key": f"button:{element_id}", "id": element_id, "label": label,
                "intent": _intent_code(db, tenant_id, f.get("intent")), "style": style}
    text = str(f.get("text") or "").strip()[:1000]
    if not text:
        raise ValueError("A text block needs its text")
    element_id = _slug(f.get("id") or text[:30])
    return {"kind": "text", "key": f"text:{element_id}", "id": element_id, "text": text}


def _clean_roles(db: Session, tenant_id: int, raw: Any) -> list[int]:
    from app.models.org import AppRole

    ids = []
    for ref in raw or []:
        role = db.get(AppRole, int(ref)) if str(ref).isdigit() else db.query(AppRole).filter(
            AppRole.tenant_id == tenant_id, AppRole.name.ilike(str(ref).strip())).first()
        if role is None or role.tenant_id != tenant_id:
            raise ValueError(f"No role '{ref}'")
        if role.id not in ids:
            ids.append(role.id)
    return ids


def _clean_events(db: Session, tenant_id: int, raw: Any) -> dict[str, str]:
    out = {}
    for event, ref in (raw or {}).items():
        if event not in PAGE_EVENTS:
            raise ValueError(f"Page events: {', '.join(PAGE_EVENTS)}")
        if not ref:
            continue
        code = _intent_code(db, tenant_id, ref)
        if not code.startswith("FLOW-"):
            raise ValueError("A page event starts a Flow Studio flow - pick one (buttons can run any intent)")
        out[event] = code
    return out


def _clean_layout(db: Session, tenant_id: int, entity_type: str, segment: str | None, layout: dict[str, Any]) -> dict[str, Any]:
    fields = _by_key(catalog(db, tenant_id, entity_type, segment))
    seen: set[str] = set()
    sections = []
    for section in layout.get("sections") or []:
        title = str(section.get("title") or "").strip()[:60] or "Details"
        kept = []
        for f in section.get("fields") or []:
            if f.get("kind") in ("button", "text"):
                kept.append(_clean_element(db, tenant_id, f))
                continue
            key = f.get("key")
            if key not in fields:
                raise ValueError(f"'{key}' is not a field of {ENTITIES[entity_type].lower()} - add it as a field first")
            if key in seen:
                continue
            seen.add(key)
            kept.append({
                "key": key, "mandatory": bool(f.get("mandatory")) and not f.get("hidden"), "hidden": bool(f.get("hidden")),
                "readonly": bool(f.get("readonly")), **({"label": str(f["label"])[:150]} if f.get("label") else {}),
                **({"help": str(f["help"])[:300]} if f.get("help") else {}),
                **({"span": "full"} if f.get("span") == "full" else {}),
            })
        sections.append({"title": title, "columns": 1 if section.get("columns") == 1 else 2, "fields": kept})
    if entity_type == "party" and "name" in seen and not any(f["key"] == "name" and not f["hidden"] for s in sections for f in s["fields"]):
        raise ValueError("A prospect page has to show the name")
    excluded = sorted(set(fields) - seen)
    out: dict[str, Any] = {"sections": sections, "excluded": excluded}
    roles = _clean_roles(db, tenant_id, layout.get("role_ids"))
    if roles:
        out["role_ids"] = roles
    events = _clean_events(db, tenant_id, layout.get("events"))
    if events:
        out["events"] = events
    return out


def save_page(db: Session, tenant_id: int, entity_type: str, segment: str | None, layout: dict[str, Any], key: str | None = None,
              name: str | None = None, description: str | None = None) -> dict[str, Any]:
    key = key or page_key(entity_type, segment)
    clean = _clean_layout(db, tenant_id, entity_type, segment, layout)
    row = db.query(PageLayout).filter(PageLayout.tenant_id == tenant_id, PageLayout.page_key == key).first()
    if row is None:
        row = PageLayout(tenant_id=tenant_id, page_key=key, entity_type=entity_type, segment=segment,
                         name=name or _default_name(entity_type, segment), is_default=key == page_key(entity_type, segment))
        db.add(row)
    if name:
        row.name = name[:150]
    if description is not None:
        row.description = description
    row.layout_json = json.dumps(clean)
    db.flush()
    return page_by_key(db, tenant_id, key) or page(db, tenant_id, entity_type, segment, key)


def new_page(db: Session, tenant_id: int, entity_type: str, segment: str | None, name: str, description: str = "",
             layout: dict[str, Any] | None = None) -> dict[str, Any]:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40] or "page"
    key = f"{entity_type}.{slug}"
    n = 2
    while db.query(PageLayout.id).filter(PageLayout.tenant_id == tenant_id, PageLayout.page_key == key).first():
        key = f"{entity_type}.{slug}_{n}"
        n += 1
    row = PageLayout(tenant_id=tenant_id, page_key=key, entity_type=entity_type, segment=segment, name=name[:150],
                     description=description, is_default=False, layout_json=json.dumps(layout or default_layout(db, tenant_id, entity_type, segment)))
    db.add(row)
    db.flush()
    return save_page(db, tenant_id, entity_type, segment, _layout_of(row), key)


def reset_page(db: Session, tenant_id: int, key: str) -> None:
    db.query(PageLayout).filter(PageLayout.tenant_id == tenant_id, PageLayout.page_key == key).delete()
    db.flush()


# ---------------------------------------------------------------- forms and values


def _party_profile(db: Session, party, create: bool):
    from app.crm_core import process_service

    return process_service.party_profile(party, create=create)


def record_values(db: Session, tenant_id: int, entity_type: str, obj) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for spec in CORE_FIELDS.get(entity_type, []):
        if entity_type == "party" and spec["key"] in _PROFILE_FIELDS:
            profile = obj.profile
            values[spec["key"]] = getattr(profile, spec["key"], None) if profile is not None else None
        else:
            raw = getattr(obj, spec["key"], None)
            values[spec["key"]] = getattr(raw, "value", raw)
    values.update(custom_field_service.get_values(db, tenant_id, entity_type, obj.id))
    return values


def form(db: Session, tenant_id: int, entity_type: str, segment: str | None = None, key: str | None = None,
         values: dict[str, Any] | None = None, role_ids: set[int] | None = None) -> dict[str, Any]:
    """The page ready to render: sections of fields with their type, choices,
    mandatory flag and current value, its buttons and text, plus the simple
    rules the browser checks as you type (the server checks them all again
    on save) and the flows its events start."""
    shown = page(db, tenant_id, entity_type, segment, key, role_ids)
    fields = _by_key(catalog(db, tenant_id, entity_type, segment))
    values = values or {}
    sections = []
    for section in shown["layout"]["sections"]:
        # Older generated journeys could land on a general page. Their
        # journey field and explicitly named process identify the scope.
        journey_fields = [f for f in section["fields"] if str(f.get("key", "")).startswith("journey_")]
        scope = re.search(r"\b(B2B|B2C)\b", section.get("title", ""), re.I) if journey_fields else None
        section_scope = section.get("segment") or (scope.group(1).upper() if scope else None)
        if section_scope and section_scope != segment:
            continue
        out = []
        for placed in section["fields"]:
            if placed.get("kind") == "button":
                from app.intent_engine import intent_registry
                button_intent = intent_registry.entry(db, tenant_id, placed["intent"]) or {}
                if button_intent.get("segment") and button_intent["segment"] != segment:
                    continue
                out.append({"kind": "button", "key": f"button:{placed['id']}", "label": placed["label"], "type": "button",
                            "intent": placed["intent"], "style": placed.get("style", "default"), "values": [], "mandatory": False,
                            "readonly": True, "help": "", "core": False, "value": None, "span": None})
                continue
            if placed.get("kind") == "text":
                out.append({"kind": "text", "key": f"text:{placed['id']}", "label": "", "type": "text_block", "text": placed["text"],
                            "values": [], "mandatory": False, "readonly": True, "help": "", "core": False, "value": None, "span": "full"})
                continue
            if placed.get("hidden") or placed.get("key") not in fields:
                continue
            f = fields[placed["key"]]
            out.append({
                "key": f["key"], "label": placed.get("label") or f["label"], "type": f["data_type"], "values": f["values"],
                "mandatory": bool(placed.get("mandatory")), "readonly": bool(placed.get("readonly")),
                "help": placed.get("help") or f["help"], "core": f["core"], "value": values.get(f["key"]),
                "span": placed.get("span") or ("full" if f["data_type"] == "textarea" else None),
            })
        if out:
            sections.append({"title": section["title"], "columns": section.get("columns", 2), "fields": out})
    return {
        "page_key": shown["page_key"], "name": shown["name"], "entity_type": entity_type, "segment": segment,
        "sections": sections, "rules": [compiled_rule(r) for r in rules_for(db, tenant_id, entity_type, segment) if r.kind == "simple"],
        "events": shown.get("events") or {}, "is_default": shown["is_default"],
    }


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == []


def validate(db: Session, tenant_id: int, entity_type: str, segment: str | None, values: dict[str, Any], key: str | None = None,
             execution_id: int | None = None, partial: bool = False) -> list[str]:
    """Everything wrong with these values for this page - empty = can save.
    partial=True (an update of a few fields) only checks what is given.
    values["_page_key"]: the page the values were entered on (a page given
    to the user's role), checked instead of the default one."""
    key = key or (values.get("_page_key") if isinstance(values.get("_page_key"), str) else None)
    shown = page(db, tenant_id, entity_type, segment, key)
    fields = _by_key(catalog(db, tenant_id, entity_type, segment))
    labels = {f["key"]: f["label"] for f in fields.values()}
    errors: list[str] = []
    if not partial:
        for section in shown["layout"]["sections"]:
            for placed in section["fields"]:
                if placed.get("mandatory") and not placed.get("hidden") and _empty(values.get(placed["key"])):
                    errors.append(f"{placed.get('label') or labels.get(placed['key'], placed['key'])} is mandatory")
    defs = {d.key: d for d in custom_field_service.list_definitions(db, tenant_id, entity_type)}
    for k, v in values.items():
        if _empty(v):
            continue
        if k in defs:
            try:
                coerce(defs[k], v)
            except ValueError as exc:
                errors.append(str(exc).replace(f"Custom field '{k}'", labels.get(k, k)))
        elif k in fields and fields[k]["data_type"] in ("email", "phone"):
            problem = _format_problem(fields[k]["data_type"], v)
            if problem:
                errors.append(f"{labels[k]} {problem}")
    for rule in rules_for(db, tenant_id, entity_type, segment):
        if rule.kind == "simple":
            errors += evaluate(compiled_rule(rule)["compiled"], values, labels, partial=partial)
        elif rule.kind == "flow" and rule.flow_id and execution_id is not None:
            errors += _run_flow_rule(db, tenant_id, execution_id, rule, values)
    return list(dict.fromkeys(errors))


def save_values(db: Session, tenant_id: int, entity_type: str, obj, values: dict[str, Any]) -> dict[str, Any]:
    """Writes page values to the record - core columns, the party profile and
    field definitions alike. Blank values for fields not given are left."""
    from app.crm_core import record_service

    applied: dict[str, Any] = {}
    core_keys = {f["key"] for f in CORE_FIELDS.get(entity_type, [])}
    defs = {d.key: d for d in custom_field_service.list_definitions(db, tenant_id, entity_type)}
    core: dict[str, Any] = {}
    for k, v in values.items():
        if entity_type == "party" and k in _PROFILE_FIELDS:
            if k == "segment":
                v = segment_of(v) or (None if _empty(v) else str(v))
            profile = _party_profile(db, obj, create=not _empty(v))
            if profile is not None:
                setattr(profile, k, None if _empty(v) else v)
                applied[k] = v
        elif k in core_keys:
            core[k] = v
        elif k in defs:
            if _empty(v) and not defs[k].required:
                applied[k] = custom_field_service.set_value(db, tenant_id, entity_type, obj.id, defs[k], None)
            elif not _empty(v):
                applied[k] = custom_field_service.set_value(db, tenant_id, entity_type, obj.id, defs[k], v)
    core = {k: v for k, v in core.items() if not (_empty(v) and k == "name")}
    if core:
        applied.update(record_service.update_fields(db, tenant_id, entity_type, obj, core, enforce_editable=False))
    db.flush()
    return applied


# ---------------------------------------------------------------- types

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE = re.compile(r"^\+?[0-9][0-9 ()-]{5,18}[0-9]$")


def _format_problem(data_type: str, value: Any) -> str | None:
    text = str(value).strip()
    if data_type == "email" and not _EMAIL.match(text):
        return "must be an email address"
    if data_type == "phone" and not _PHONE.match(text):
        return "must be a phone number"
    return None


def coerce(defn: CustomFieldDefinition, raw: Any) -> str | None:
    if defn.data_type in ("email", "phone") and not _empty(raw):
        problem = _format_problem(defn.data_type, raw)
        if problem:
            raise ValueError(f"Custom field '{defn.key}' {problem}")
    return custom_field_service.coerce(defn, raw)


# ---------------------------------------------------------------- validation rules

OPS = ("eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "empty", "not_empty", "matches", "length", "before_today", "after_today")
FORMATS: dict[str, tuple[str, str]] = {
    "gstin": (r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$", "a valid 15-character GSTIN"),
    "pan": (r"^[A-Z]{5}[0-9]{4}[A-Z]$", "a valid PAN (e.g. ABCDE1234F)"),
    "cin": (r"^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$", "a valid 21-character CIN"),
    "pincode": (r"^[1-9][0-9]{5}$", "a valid 6-digit PIN code"),
    "mobile": (r"^(\+91[ -]?)?[6-9][0-9]{9}$", "a valid 10-digit mobile number"),
    "aadhaar": (r"^[2-9][0-9]{3} ?[0-9]{4} ?[0-9]{4}$", "a valid 12-digit Aadhaar number"),
    "email": (r"^[^@\s]+@[^@\s]+\.[^@\s]+$", "a valid email address"),
    "website": (r"^(https?://)?([a-z0-9-]+\.)+[a-z]{2,}(/.*)?$", "a valid website address"),
    "ifsc": (r"^[A-Z]{4}0[A-Z0-9]{6}$", "a valid IFSC code"),
}


def rules_for(db: Session, tenant_id: int, entity_type: str, segment: str | None) -> list[ValidationRule]:
    rows = db.query(ValidationRule).filter(
        ValidationRule.tenant_id == tenant_id, ValidationRule.entity_type == entity_type, ValidationRule.is_active.is_(True)
    ).order_by(ValidationRule.id).all()
    return [r for r in rows if r.segment is None or segment is None or r.segment == segment]


def compiled_rule(rule: ValidationRule) -> dict[str, Any]:
    try:
        compiled = json.loads(rule.compiled_json or "{}")
    except json.JSONDecodeError:
        compiled = {}
    return {"id": rule.id, "name": rule.name, "segment": rule.segment, "compiled": compiled, "message": rule.message}


def _number(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def test(cond: dict[str, Any], values: dict[str, Any]) -> bool | None:
    """True/False, or None when the field is blank (a check on a blank field
    is the mandatory rule's business, not this one's)."""
    actual = values.get(cond.get("field"))
    op, expected = cond.get("op", "eq"), cond.get("value")
    if op == "empty":
        return _empty(actual)
    if op == "not_empty":
        return not _empty(actual)
    if _empty(actual):
        return None
    text = str(actual).strip()
    if op in ("gt", "gte", "lt", "lte"):
        a, b = _number(actual), _number(expected)
        if a is None or b is None:
            da, db_ = _date(actual), _date(expected)
            if da is None or db_ is None:
                return False
            a, b = da.toordinal(), db_.toordinal()
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
    if op in ("in", "not_in"):
        options = [str(o).strip().lower() for o in (expected if isinstance(expected, list) else str(expected).split(","))]
        inside = text.lower() in options
        return inside if op == "in" else not inside
    if op == "matches":
        pattern = FORMATS.get(str(expected).lower(), (str(expected), ""))[0]
        try:
            return re.fullmatch(pattern, text, re.I if str(expected).lower() in ("email", "website") else 0) is not None
        except re.error:
            return False
    if op == "length":
        bounds = expected if isinstance(expected, list) else [expected, expected]
        lo, hi = _number(bounds[0]), _number(bounds[-1])
        n = len(re.sub(r"\s", "", text))
        return (lo is None or n >= lo) and (hi is None or n <= hi)
    if op in ("before_today", "after_today"):
        d = _date(actual)
        if d is None:
            return False
        return d < dt.date.today() if op == "before_today" else d > dt.date.today()
    a, b = _number(actual), _number(expected)
    equal = a == b if a is not None and b is not None else text.lower() == str(expected).strip().lower()
    return equal if op == "eq" else not equal


def evaluate(compiled: dict[str, Any], values: dict[str, Any], labels: dict[str, str] | None = None, partial: bool = False) -> list[str]:
    labels = labels or {}
    when = compiled.get("when") or []
    if partial and any(c.get("field") not in values for c in when):
        return []
    if not all(test(c, values) for c in when):
        return []
    errors = []
    message = compiled.get("message")
    for key in compiled.get("require") or []:
        if partial and key not in values:
            continue
        if _empty(values.get(key)):
            errors.append(message or f"{labels.get(key, key)} is mandatory here")
    for cond in compiled.get("check") or []:
        if partial and cond.get("field") not in values:
            continue
        if test(cond, values) is False:
            errors.append(message or describe_condition(cond, labels, failing=True))
    return list(dict.fromkeys(errors))


def _show(value: Any) -> str:
    return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)


_OP_WORDS = {"eq": "is", "ne": "is not", "gt": "is more than", "gte": "is at least", "lt": "is less than", "lte": "is at most",
             "in": "is one of", "not_in": "is not one of", "empty": "is empty", "not_empty": "is filled in"}


def describe_condition(cond: dict[str, Any], labels: dict[str, str], failing: bool = False) -> str:
    label = labels.get(cond.get("field"), cond.get("field"))
    op, value = cond.get("op"), cond.get("value")
    if op == "matches":
        if m := re.fullmatch(r"\^\[0-9\]\{(\d+)\}\$", str(value)):
            return f"{label} must be {m.group(1)} digits"
        what = FORMATS.get(str(value).lower(), ("", f"in the format {value}"))[1]
        return f"{label} must be {what}"
    if op == "length":
        bounds = value if isinstance(value, list) else [value, value]
        span = f"{bounds[0]}" if bounds[0] == bounds[-1] else f"{bounds[0]} to {bounds[-1]}"
        return f"{label} must be {span} characters"
    if op == "before_today":
        return f"{label} must be in the past"
    if op == "after_today":
        return f"{label} must be in the future"
    shown = ", ".join(map(_show, value)) if isinstance(value, list) else _show(value)
    if failing:
        return f"{label} must {'be' if op not in ('empty', 'not_empty') else ''} {_OP_WORDS.get(op, op).removeprefix('is ')} {shown if op not in ('empty', 'not_empty') else ''}".replace("  ", " ").strip()
    return f"{label} {_OP_WORDS.get(op, op)}{'' if op in ('empty', 'not_empty') else f' {shown}'}"


def describe(compiled: dict[str, Any], labels: dict[str, str]) -> str:
    parts = []
    if compiled.get("require"):
        parts.append(", ".join(labels.get(k, k) for k in compiled["require"]) + " " + ("are" if len(compiled["require"]) > 1 else "is") + " mandatory")
    parts += [describe_condition(c, labels, failing=True) for c in compiled.get("check") or []]
    text = "; ".join(parts) or "(no effect)"
    if compiled.get("when"):
        lowered = text[0].lower() + text[1:] if len(text) > 1 and not text[1].isupper() else text
        text = "When " + " and ".join(describe_condition(c, labels) for c in compiled["when"]) + ": " + lowered
    return text


def _check_compiled(compiled: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    """Only the closed rule shape gets through - whatever an AI returned."""
    def cond(c: Any) -> dict[str, Any]:
        if not isinstance(c, dict) or c.get("field") not in keys or c.get("op") not in OPS:
            raise ValueError(f"not a valid condition: {c}")
        out = {"field": c["field"], "op": c["op"]}
        if c["op"] not in ("empty", "not_empty", "before_today", "after_today"):
            out["value"] = c.get("value")
        return out

    clean = {
        "when": [cond(c) for c in compiled.get("when") or []],
        "require": [k for k in compiled.get("require") or [] if k in keys],
        "check": [cond(c) for c in compiled.get("check") or []],
    }
    if compiled.get("message"):
        clean["message"] = str(compiled["message"])[:300]
    if not clean["require"] and not clean["check"]:
        raise ValueError("the rule neither requires a field nor checks one")
    return clean


_RULE_PROMPT = """You compile one plain-English validation rule for a CRM page into JSON. Answer strictly as
{"when": [cond...], "require": [field...], "check": [cond...], "message": "short message for the user, or empty"}
where cond = {"field": <field key>, "op": one of eq|ne|gt|gte|lt|lte|in|not_in|empty|not_empty|matches|length|before_today|after_today, "value": ...}.
- "when": conditions that must ALL hold for the rule to apply (empty = always).
- "require": field keys that must then be filled in.
- "check": conditions the values must then satisfy.
- matches: value is a named format (gstin, pan, cin, pincode, mobile, aadhaar, email, website, ifsc) or a regular expression.
- length: value is [min, max] characters.  in/not_in: value is a list.
Use only the field keys listed. If the rule cannot be expressed this way answer {"error": "why"}."""


def _field_aliases(fields: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(phrase, key), longest phrases first - labels, keys and common names."""
    extra = {"gstin": "tax_id", "gst": "tax_id", "gst number": "tax_id", "vat": "tax_id", "cin": "registration_number",
             "pin": "postal_code", "pincode": "postal_code", "pin code": "postal_code", "zip": "postal_code", "postcode": "postal_code",
             "mobile": "phone", "mobile number": "phone", "dob": "date_of_birth", "birth date": "date_of_birth",
             "id number": "id_number", "id proof": "id_number", "employees": "employee_count", "revenue": "annual_revenue",
             "company name": "name", "customer name": "name", "segment": "segment"}
    keys = {f["key"] for f in fields}
    pairs = [(f["label"].lower(), f["key"]) for f in fields] + [(f["key"].replace("_", " "), f["key"]) for f in fields]
    pairs += [(a, k) for a, k in extra.items() if k in keys]
    pairs = [(re.sub(r"\s*\(.*?\)", "", p).strip(), k) for p, k in pairs] + pairs
    seen, out = set(), []
    for p, k in sorted(pairs, key=lambda x: -len(x[0])):
        if p and p not in seen:
            seen.add(p)
            out.append((p, k))
    return out


def _find_field(text: str, aliases: list[tuple[str, str]]) -> tuple[str, str] | None:
    """(key, rest-of-text) for the field named at the start of text."""
    t = text.strip().lower()
    t = re.sub(r"^(the|a|an)\s+", "", t)
    for phrase, key in aliases:
        if t == phrase or t.startswith(phrase + " ") or t.startswith(phrase + "'s "):
            return key, text.strip()[len(text.strip()) - len(t) + len(phrase):].strip()
    return None


def _parse_value(raw: str) -> Any:
    raw = raw.strip().strip("'\"").strip()
    number = _number(raw)
    return number if number is not None and re.fullmatch(r"[₹\d,.\s]+", raw) else raw


def _parse_cond(text: str, aliases: list[tuple[str, str]]) -> dict[str, Any] | None:
    found = _find_field(text, aliases)
    if not found:
        return None
    key, rest = found
    rest = rest.strip(" ,.")
    patterns: list[tuple[str, str]] = [
        (r"^(?:is\s+)?(?:empty|blank|not (?:given|filled(?: in)?))$", "empty"),
        (r"^(?:is\s+)?(?:given|filled(?: in)?|not empty|present|provided)$", "not_empty"),
        (r"^(?:is\s+)?(?:above|over|more than|greater than|>)\s*(.+)$", "gt"),
        (r"^(?:is\s+)?(?:at least|>=|minimum)\s*(.+)$", "gte"),
        (r"^(?:is\s+)?(?:below|under|less than|<)\s*(.+)$", "lt"),
        (r"^(?:is\s+)?(?:at most|<=|maximum)\s*(.+)$", "lte"),
        (r"^(?:is\s+)?not one of\s+(.+)$", "not_in"),
        (r"^(?:is\s+)?(?:one of|any of|in)\s+(.+)$", "in"),
        (r"^(?:is not|isn't|!=|is other than)\s+(.+)$", "ne"),
        (r"^(?:is|=|==|equals)\s+(.+)$", "eq"),
    ]
    for pattern, op in patterns:
        m = re.match(pattern, rest, re.I)
        if m:
            if op in ("empty", "not_empty"):
                return {"field": key, "op": op}
            value = m.group(1)
            if op in ("in", "not_in"):
                return {"field": key, "op": op, "value": [v.strip() for v in re.split(r",|\bor\b", value) if v.strip()]}
            return {"field": key, "op": op, "value": _parse_value(value)}
    return None


def _parse_check(key: str, rest: str) -> dict[str, Any] | None:
    original = re.sub(r"^(?:must|should|has to|needs to|shall)\s+(?:be\s+|have\s+)?", "", rest.strip(" ,."), flags=re.I)
    rest = original.lower()
    if m := re.match(r"^(?:a\s+)?(?:valid\s+)?(gstin|gst|pan|cin|pin ?code|pincode|mobile(?: number)?|aadhaar|email(?: address)?|website|ifsc)(?: number| format| code)?$", rest):
        name = m.group(1).replace(" ", "")
        name = {"gst": "gstin", "pincode": "pincode", "mobilenumber": "mobile", "emailaddress": "email"}.get(name, name)
        return {"field": key, "op": "matches", "value": name}
    if m := re.match(r"^(?:exactly\s+)?(\d+)\s+(digits|characters|chars)(?:\s+long)?$", rest):
        n = int(m.group(1))
        if m.group(2) == "digits":
            return {"field": key, "op": "matches", "value": rf"^[0-9]{{{n}}}$"}
        return {"field": key, "op": "length", "value": [n, n]}
    if m := re.match(r"^between\s+(\d+)\s+and\s+(\d+)\s+(characters|chars|digits)(?:\s+long)?$", rest):
        return {"field": key, "op": "length", "value": [int(m.group(1)), int(m.group(2))]}
    if re.match(r"^(?:in the future|after today|a future date|later than today)$", rest):
        return {"field": key, "op": "after_today"}
    if re.match(r"^(?:in the past|before today|a past date|earlier than today)$", rest):
        return {"field": key, "op": "before_today"}
    if m := re.match(r"^(?:more than|greater than|above|over)\s+(.+)$", rest):
        return {"field": key, "op": "gt", "value": _parse_value(m.group(1))}
    if m := re.match(r"^(?:at least|minimum|no less than)\s+(.+)$", rest):
        return {"field": key, "op": "gte", "value": _parse_value(m.group(1))}
    if m := re.match(r"^(?:less than|below|under)\s+(.+)$", rest):
        return {"field": key, "op": "lt", "value": _parse_value(m.group(1))}
    if m := re.match(r"^(?:at most|maximum|no more than)\s+(.+)$", rest):
        return {"field": key, "op": "lte", "value": _parse_value(m.group(1))}
    if m := re.match(r"^(?:one of|any of)\s+(.+)$", original, re.I):
        return {"field": key, "op": "in", "value": [v.strip() for v in re.split(r",|\bor\b", m.group(1)) if v.strip()]}
    if m := re.match(r"^(?:match(?:es)?|in the format|like)\s+/?(.+?)/?$", original, re.I):
        return {"field": key, "op": "matches", "value": m.group(1)}
    return None


def _rules_compile(text: str, fields: list[dict[str, Any]]) -> dict[str, Any] | None:
    aliases = _field_aliases(fields)
    t = text.strip().rstrip(".")
    splits: list[tuple[str, str]] = []
    if m := re.match(r"^(?:if|when|for)\s+(.+)$", t, re.I):
        rest = m.group(1)
        if " then " in rest.lower():
            i = rest.lower().index(" then ")
            splits.append((rest[:i], rest[i + 6:]))
        # the condition may itself hold commas ("one of A, B") - try the last comma first
        splits += [(rest[:i], rest[i + 1:]) for i in reversed([j for j, ch in enumerate(rest) if ch == ","])]
    elif m := re.match(r"^(.+?)\s+(?:if|when)\s+(.+)$", t, re.I):
        splits.append((m.group(2), m.group(1)))
    for cond_text, body in splits:
        conds = [_parse_cond(c, aliases) for c in re.split(r"\s+and\s+", cond_text.strip())]
        if all(conds):
            parsed = _rules_body(body.strip(), aliases)
            if parsed is not None:
                return {"when": conds, **parsed}
    if splits:
        return None
    parsed = _rules_body(t, aliases)
    return {"when": [], **parsed} if parsed is not None else None


def _rules_body(body: str, aliases: list[tuple[str, str]]) -> dict[str, Any] | None:
    require: list[str] = []
    check: list[dict[str, Any]] = []
    for clause in re.split(r"\s+and\s+(?=(?:the\s+)?[a-z])", body, flags=re.I):
        clause = clause.strip(" ,.")
        if mm := re.match(r"^(.+?)\s+(?:is|are)\s+(?:mandatory|required|compulsory|needed)$", clause, re.I) \
                or re.match(r"^(?:require|make)\s+(.+?)(?:\s+mandatory|\s+required)?$", clause, re.I):
            names = [n.strip() for n in re.split(r",|\s+and\s+", mm.group(1)) if n.strip()]
            keys = [(_find_field(n, aliases) or (None, ""))[0] for n in names]
            if not all(keys):
                return None
            require += keys  # type: ignore[arg-type]
            continue
        found = _find_field(clause, aliases)
        if not found:
            return None
        parsed = _parse_check(*found)
        if parsed is None:
            return None
        check.append(parsed)
    return {"require": require, "check": check}


def compile_rule(db: Session, tenant_id: int, entity_type: str, text: str, segment: str | None = None) -> dict[str, Any]:
    """Plain English -> the closed rule form, by the tenant's AI model when one
    is set up (checked against the field list), else the built-in reader."""
    from app.crm_core import llm

    fields = catalog(db, tenant_id, entity_type, segment)
    keys = {f["key"] for f in fields}
    labels = {f["key"]: f["label"] for f in fields}
    compiled, engine, problem = None, "rules", ""
    target = llm.resolve(db, tenant_id)
    if target is not None:
        try:
            listing = "\n".join(f"- {f['key']}: {f['label']} ({f['data_type']}{', values: ' + ', '.join(f['values']) if f['values'] else ''})" for f in fields)
            reply = llm.chat_json(target, _RULE_PROMPT, f"Fields:\n{listing}\n\nRule: {text}")
            if reply.get("error"):
                problem = str(reply["error"])
            else:
                compiled, engine = _check_compiled(reply, keys), "ai"
        except Exception as exc:  # noqa: BLE001 - any AI failure falls back to the built-in reader
            problem = f"AI unavailable ({type(exc).__name__})"
    if compiled is None:
        parsed = _rules_compile(text, fields)
        if parsed is not None:
            try:
                compiled, engine = _check_compiled(parsed, keys), "rules"
            except ValueError as exc:
                problem = str(exc)
    if compiled is None:
        raise ValueError(
            "I couldn't turn that into a rule" + (f" ({problem})" if problem else "")
            + ". Try e.g. \"GSTIN must be a valid GSTIN\", \"if Employees is 1001-5000 then PAN is mandatory\", "
              "\"PIN code must be 6 digits\", \"Date of birth must be in the past\" - or use a Flow Studio flow for complex checks."
        )
    return {"compiled": compiled, "engine": engine, "explanation": describe(compiled, labels)}


def _run_flow_rule(db: Session, tenant_id: int, execution_id: int, rule: ValidationRule, values: dict[str, Any]) -> list[str]:
    from app.models.flows import FlowDefinition
    from app.orchestration import flow_engine

    flow = db.get(FlowDefinition, rule.flow_id)
    if flow is None or flow.tenant_id != tenant_id or not flow.is_active:
        return []
    try:
        flow_engine.run_flow(db, tenant_id, execution_id, flow, {"entities": dict(values), "record": dict(values)})
    except flow_engine.FlowRejected as exc:
        return [rule.message or str(exc).split(": ", 1)[-1]]
    return []


def save_rule(db: Session, tenant_id: int, entity_type: str, text: str, segment: str | None = None, name: str | None = None,
              message: str = "", flow_id: int | None = None, rule_id: int | None = None) -> ValidationRule:
    if flow_id:
        from app.models.flows import FlowDefinition

        flow = db.get(FlowDefinition, flow_id)
        if flow is None or flow.tenant_id != tenant_id:
            raise ValueError("That flow doesn't exist")
        values = {"kind": "flow", "flow_id": flow.id, "compiled_json": "{}", "name": (name or f"Flow: {flow.name}")[:150]}
    else:
        result = compile_rule(db, tenant_id, entity_type, text, segment)
        values = {"kind": "simple", "flow_id": None, "compiled_json": json.dumps(result["compiled"]),
                  "name": (name or result["explanation"])[:150]}
    row = db.get(ValidationRule, rule_id) if rule_id else None
    if row is None or row.tenant_id != tenant_id:
        row = ValidationRule(tenant_id=tenant_id, entity_type=entity_type)
        db.add(row)
    row.segment, row.message, row.source_text = segment, message[:300], text
    for k, v in values.items():
        setattr(row, k, v)
    db.flush()
    return row


def rule_out(db: Session, tenant_id: int, rule: ValidationRule) -> dict[str, Any]:
    labels = {f["key"]: f["label"] for f in catalog(db, tenant_id, rule.entity_type, rule.segment)}
    out = compiled_rule(rule)
    flow_name = None
    if rule.kind == "flow" and rule.flow_id:
        from app.models.flows import FlowDefinition

        flow = db.get(FlowDefinition, rule.flow_id)
        flow_name = flow.name if flow else None
    return {**out, "entity_type": rule.entity_type, "kind": rule.kind, "source_text": rule.source_text, "flow_id": rule.flow_id,
            "flow_name": flow_name, "is_active": rule.is_active, "brt_id": rule.brt_id,
            "explanation": describe(out["compiled"], labels) if rule.kind == "simple" else f"Runs the flow '{flow_name}' - its Reject blocks the save"}


# ---------------------------------------------------------------- AI page design

_DESIGN_PROMPT = """You change a CRM page layout from an admin's plain-English instruction. Answer strictly as JSON:
{"ops": [op...], "reply": "one short sentence"} where op is one of
{"op": "add_field", "key": snake_case, "label": str, "data_type": text|number|boolean|date|picklist|email|phone|textarea, "values": [str] (picklist only), "section": str, "mandatory": bool}
{"op": "show", "key": str, "section": str|null}   {"op": "hide", "key": str}
{"op": "mandatory", "key": str}   {"op": "optional", "key": str}   {"op": "readonly", "key": str, "value": bool}
{"op": "rename", "key": str, "label": str}   {"op": "move", "key": str, "section": str}
{"op": "add_section", "title": str}   {"op": "rename_section", "title": str, "to": str}
{"op": "add_button", "label": str, "intent": intent code or Flow Studio flow name, "style": primary|default|danger, "section": str|null}
{"op": "remove_button", "label": str}   {"op": "add_text", "text": str, "section": str|null}   {"op": "remove_text", "text": str}
{"op": "on_event", "event": on_open|after_save, "flow": Flow Studio flow name}   {"op": "clear_event", "event": on_open|after_save}
{"op": "roles", "roles": [role name...], "mode": set|add|remove}  (empty set = every role)
{"op": "activate"}   {"op": "deactivate"}
Use existing field keys where a field exists; add_field only for genuinely new fields. Buttons run the intents and flows listed."""


_Q = r"['\"“”‘’]"


def _element_rule(clause: str) -> list[dict[str, Any]] | None:
    """Buttons, text, page events, roles and switching a page on/off, in plain English."""
    c = clause.strip()
    # add a button "Send welcome kit" that runs the flow Welcome kit / runs INT-49
    if m := re.match(rf"^(?:add|put|place|create)\s+(?:a\s+|an\s+)?(?:(primary|danger|red)\s+)?button\s+(?:called\s+|named\s+|labell?ed\s+)?{_Q}?(.+?){_Q}?"
                     r"\s+(?:that|which|to|for)?\s*(?:runs?|starts?|triggers?|kicks?\s+off|calls?|opens?|launch(?:es)?)\s+(?:the\s+)?(.+?)"
                     r"(?:\s+(?:in|on|to|under)\s+(?:the\s+)?([A-Za-z][\w &/-]*?)\s+section)?$", c, re.I):
        style = {"red": "danger"}.get((m.group(1) or "").lower(), (m.group(1) or "default").lower())
        target = re.sub(r"^(?:flow|intent)\s+", "", m.group(3).strip(), flags=re.I)
        return [{"op": "add_button", "label": m.group(2).strip(), "intent": target.strip("'\"“”"), "style": style,
                 "section": (m.group(4) or "").strip().title() or None}]
    if m := re.match(rf"^(?:remove|delete|drop)\s+(?:the\s+)?button\s+{_Q}?(.+?){_Q}?$", c, re.I):
        return [{"op": "remove_button", "label": m.group(1).strip()}]
    if m := re.match(rf"^(?:add|put|show)\s+(?:a\s+|an\s+)?(?:note|text|heading|instruction|message)\s*(?:saying|that says|:)\s*{_Q}?(.+?){_Q}?$", c, re.I) \
            or re.match(rf"^(?:add|put|show)\s+(?:a\s+|an\s+)?(?:note|text|heading|instruction|message)\s+{_Q}(.+?){_Q}$", c, re.I):
        return [{"op": "add_text", "text": m.group(1).strip()}]
    # when the page is saved, run (the) flow X / on open start flow X
    if m := re.match(r"^(?:when|after|on|once)\s+(?:the\s+)?(?:page\s+|record\s+|form\s+|it\s+)?(?:is\s+)?(saved|save|opened|open|loaded|load)\b[\s,]*"
                     r"(?:then\s+)?(?:run|start|trigger|kick\s+off|call)\s+(?:the\s+)?(?:flow\s+)?(.+)$", c, re.I):
        event = "after_save" if m.group(1).lower().startswith("sav") else "on_open"
        return [{"op": "on_event", "event": event, "flow": m.group(2).strip().strip("'\"“”")}]
    if m := re.match(r"^(?:stop|don't|do not)\s+(?:running|starting)\s+(?:a\s+|the\s+)?flow\s+(?:when|on|after)\s+(?:it\s+is\s+)?(saved|save|opened|open)", c, re.I):
        return [{"op": "clear_event", "event": "after_save" if m.group(1).lower().startswith("sav") else "on_open"}]
    # show this page to the Sales and Billing roles / only for role X / take it away from role Y / for everyone
    if re.match(r"^(?:show|give|assign)\s+(?:this|the|it)?\s*(?:page\s+)?to\s+everyone$|^(?:for\s+)?all\s+roles$", c, re.I):
        return [{"op": "roles", "roles": [], "mode": "set"}]
    if m := re.match(r"^(?:show|give|assign)\s+(?:this\s+|the\s+|it\s+)?(?:page\s+)?(?:only\s+)?to\s+(?:the\s+)?(?:roles?\s+)?(.+?)(?:\s+roles?)?(?:\s+only)?$"
                     r"|^(?:only\s+)?for\s+(?:the\s+)?roles?\s+(.+)$|^(?:roles?)\s*:\s*(.+)$", c, re.I):
        names = next(g for g in m.groups() if g)
        return [{"op": "roles", "roles": [n.strip(" '\"") for n in re.split(r",|\s+and\s+", names) if n.strip(" '\"")], "mode": "set"}]
    if m := re.match(r"^(?:also\s+)?(?:give|show)\s+(?:it|this\s+page)\s+(?:also\s+)?to\s+(?:the\s+)?(?:roles?\s+)?(.+?)(?:\s+roles?)?\s+too$", c, re.I):
        return [{"op": "roles", "roles": [n.strip() for n in re.split(r",|\s+and\s+", m.group(1)) if n.strip()], "mode": "add"}]
    if re.match(r"^(?:activate|switch on|turn on|enable|publish|go live with)(?:\s+(?:this|the|it))?(?:\s+page)?$", c, re.I):
        return [{"op": "activate"}]
    if re.match(r"^(?:deactivate|switch off|turn off|disable|unpublish)(?:\s+(?:this|the|it))?(?:\s+page)?$", c, re.I):
        return [{"op": "deactivate"}]
    return None


_TARGET_TAIL = re.compile(r"\s+(?:for|on|in|of|to)\s+(?:any|all|every|each|the|a|an|new|our)?\s*(?:b2b|b2c|consumer|business|enterprise|retail)?\s*"
                          r"(?:prospects?|customers?|consumers?|accounts?|parties|party|leads?|opportunit(?:y|ies)|deals?|quot(?:e|es|ation|ations)|orders?)"
                          r"(?:\s+(?:page|form|screen|records?))?\s*$", re.I)


def _design_rules(instruction: str, fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aliases = _field_aliases(fields)
    ops: list[dict[str, Any]] = []
    types = {"date": "date", "number": "number", "numeric": "number", "amount": "number", "yes/no": "boolean", "checkbox": "boolean",
             "boolean": "boolean", "picklist": "picklist", "dropdown": "picklist", "list": "picklist", "email": "email",
             "phone": "phone", "mobile": "phone", "long text": "textarea", "notes": "textarea", "text": "text"}

    def keys_in(text: str) -> list[str]:
        out = []
        for part in re.split(r",|\s+and\s+", text):
            part = re.sub(r"\s+(?:fields?|box(?:es)?|column)\s*$", "", part.strip(), flags=re.I)  # "the Email field" = Email
            found = _find_field(part, aliases)
            if found and not found[1]:
                out.append(found[0])
        return out

    for raw in re.split(r"[;\n]+|\.\s+", instruction):
        # "... for any B2C prospect" / "... on every quote" says which page - the page is already chosen
        clause = _TARGET_TAIL.sub("", raw.strip(" .")).strip(" .,")
        if not clause:
            continue
        low = clause.lower()
        element = _element_rule(clause)
        if element is not None:
            ops.extend(element)
            continue
        if m := re.match(r"^(?:make|mark|set)\s+(.+?)\s+(mandatory|required|compulsory|optional|read[- ]?only|editable)$", low):
            what = m.group(2)
            for key in keys_in(m.group(1)):
                if what in ("mandatory", "required", "compulsory"):
                    ops.append({"op": "mandatory", "key": key})
                elif what == "optional":
                    ops.append({"op": "optional", "key": key})
                else:
                    ops.append({"op": "readonly", "key": key, "value": what != "editable"})
            continue
        if (m := re.match(r"^(hide|remove|drop|show|unhide)\s+(.+?)(?:\s+(?:from|on|in)\s+(?:the\s+)?(?:page|form))?$", low)) and keys_in(m.group(2)):
            for key in keys_in(m.group(2)):
                ops.append({"op": "hide" if m.group(1) in ("hide", "remove", "drop") else "show", "key": key, "section": None})
            continue
        if m := re.match(r"^rename\s+(.+?)\s+to\s+(.+)$", clause, re.I):
            found = _find_field(m.group(1), aliases)
            if found and not found[1]:
                ops.append({"op": "rename", "key": found[0], "label": m.group(2).strip()})
                continue
        if m := re.match(r"^move\s+(.+?)\s+to\s+(?:the\s+)?(.+?)(?:\s+section)?$", clause, re.I):
            for key in keys_in(m.group(1)):
                ops.append({"op": "move", "key": key, "section": m.group(2).strip().title()})
            continue
        if m := re.match(r"^add\s+(?:a\s+)?(?:new\s+)?section\s+(?:called\s+)?(.+)$", clause, re.I):
            ops.append({"op": "add_section", "title": m.group(1).strip().strip("'\"")})
            continue
        if m := re.match(r"^add\s+(?:a\s+|an\s+)?(?:new\s+)?(?:(mandatory|required|optional)\s+)?(?:field\s+)?(.+)$", clause, re.I):
            rest = m.group(2)
            mandatory = bool(m.group(1)) and m.group(1).lower() != "optional"
            existing = keys_in(re.split(r"\s+(?:as|to|in|under|with)\s+", rest)[0])
            section = None
            if re.search(r"\b(mandatory|required)\b", rest, re.I):
                mandatory = True
                rest = re.sub(r",?\s*\b(?:as\s+)?(mandatory|required)\b", "", rest, flags=re.I)
            ms = re.search(r",?\s+(?:to|in|under)\s+(?:the\s+|a\s+)?(?:new\s+)?([A-Za-z][\w &/-]*?)\s+section\b", rest, re.I) \
                or re.search(r",?\s+(?:to|in|under)\s+(?:the\s+)?([A-Za-z][\w &/-]*?)(?=\s+(?:as|with)\b|\s*$)", rest, re.I)
            if ms:
                section = ms.group(1).strip().title()
                rest = (rest[: ms.start()] + rest[ms.end():]).strip(" ,")
            if existing and not re.search(r"\b(as|with values|options)\b", rest, re.I):
                for key in existing:
                    ops.append({"op": "show", "key": key, "section": section})
                    if mandatory:
                        ops.append({"op": "mandatory", "key": key})
                continue
            values: list[str] = []
            if mv := re.search(r"(?:with\s+)?(?:values|options|choices)\s*:?\s*(.+)$", rest, re.I) or re.search(r":\s*(.+)$", rest):
                values = [v.strip() for v in re.split(r",|\s+or\s+|/", mv.group(1)) if v.strip()]
                rest = rest[: mv.start()]
            data_type = "picklist" if values else "text"
            if mt := re.search(r"\s+(?:as\s+)?(?:a\s+|an\s+)?(" + "|".join(map(re.escape, types)) + r")(?:\s+field)?\s*$", rest, re.I):
                data_type = "picklist" if values else types[mt.group(1).lower()]
                rest = rest[: mt.start()]
            label = rest.strip(" ,:'\"").strip()
            if not label:
                continue
            label = label[0].upper() + label[1:]
            ops.append({"op": "add_field", "key": re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:60], "label": label,
                        "data_type": data_type, "values": values, "section": section, "mandatory": mandatory})
    return ops


def _runnable_listing(db: Session, tenant_id: int) -> str:
    from app.intent_engine import intent_registry
    from app.models.org import AppRole

    intents = ", ".join(f"{e['code']} {e['label']}" for e in intent_registry.all_intents(db, tenant_id) if e.get("is_active", True))
    roles = ", ".join(r.name for r in db.query(AppRole).filter(AppRole.tenant_id == tenant_id))
    return f"Intents and flows a button can run: {intents}\nRoles: {roles or '(none yet)'}"


def plan_page(db: Session, tenant_id: int, entity_type: str, segment: str | None, instruction: str, key: str | None = None) -> dict[str, Any]:
    from app.crm_core import llm

    fields = catalog(db, tenant_id, entity_type, segment)
    current = (page_by_key(db, tenant_id, key) if key else None) or page(db, tenant_id, entity_type, segment, key)
    ops: list[dict[str, Any]] = []
    engine, reply = "rules", ""
    target = llm.resolve(db, tenant_id)
    if target is not None:
        try:
            listing = "\n".join(f"- {f['key']}: {f['label']} ({f['data_type']})" for f in fields)
            context = json.dumps({"page": current["name"], "sections": [
                {"title": s["title"], "items": [f.get("key") or f"{f.get('kind')}: {f.get('label') or f.get('text')}" for f in s["fields"]]}
                for s in current["layout"]["sections"]], "role_ids": current.get("role_ids"), "events": current.get("events")})
            answer = llm.chat_json(target, _DESIGN_PROMPT, f"Fields:\n{listing}\n{_runnable_listing(db, tenant_id)}\nCurrent page: {context}\n\n"
                                                           f"Instruction: {instruction}")
            ops = [o for o in answer.get("ops") or [] if isinstance(o, dict) and o.get("op")]
            engine, reply = "ai", str(answer.get("reply") or "")
        except Exception:  # noqa: BLE001 - fall back to the built-in reader
            ops = []
    if not ops:
        ops, engine = _design_rules(instruction, fields), "rules"
    return {"ops": ops, "engine": engine, "reply": reply, "preview": describe_page_ops(ops, fields)}


def describe_page_ops(ops: list[dict[str, Any]], fields: list[dict[str, Any]]) -> list[str]:
    labels = {f["key"]: f["label"] for f in fields}
    out = []
    for o in ops:
        name = labels.get(o.get("key"), o.get("label") or o.get("key"))
        kind = o.get("op")
        if kind == "add_field":
            extra = f" ({o.get('data_type', 'text')}{': ' + ', '.join(o['values']) if o.get('values') else ''})"
            out.append(f"Add new field {o.get('label')}{extra}{' - mandatory' if o.get('mandatory') else ''}{' in ' + o['section'] if o.get('section') else ''}")
        elif kind == "show":
            out.append(f"Show {name}{' in ' + o['section'] if o.get('section') else ''}")
        elif kind == "hide":
            out.append(f"Hide {name}")
        elif kind == "mandatory":
            out.append(f"Make {name} mandatory")
        elif kind == "optional":
            out.append(f"Make {name} optional")
        elif kind == "readonly":
            out.append(f"Make {name} {'read-only' if o.get('value', True) else 'editable'}")
        elif kind == "rename":
            out.append(f"Rename {name} to {o.get('label')}")
        elif kind == "move":
            out.append(f"Move {name} to {o.get('section')}")
        elif kind == "add_section":
            out.append(f"Add section {o.get('title')}")
        elif kind == "rename_section":
            out.append(f"Rename section {o.get('title')} to {o.get('to')}")
        elif kind == "add_button":
            out.append(f"Add a button '{o.get('label')}' that runs {o.get('intent') or o.get('flow')}"
                       f"{' in ' + o['section'] if o.get('section') else ''}")
        elif kind == "remove_button":
            out.append(f"Remove the button '{o.get('label')}'")
        elif kind == "add_text":
            out.append(f"Add the text \"{o.get('text')}\"")
        elif kind == "remove_text":
            out.append(f"Remove the text \"{o.get('text')}\"")
        elif kind == "on_event":
            out.append(f"{'After the page is saved' if o.get('event') == 'after_save' else 'When the page is opened'}, start the flow "
                       f"{o.get('flow') or o.get('intent')}")
        elif kind == "clear_event":
            out.append(f"Stop the flow on {str(o.get('event')).replace('_', ' ')}")
        elif kind == "roles":
            names = ", ".join(map(str, o.get("roles") or []))
            out.append({"add": f"Also give the page to {names}", "remove": f"Take the page away from {names}"}.get(
                o.get("mode", "set"), f"Give the page only to {names}" if names else "Give the page to every role"))
        elif kind == "activate":
            out.append("Switch the page on")
        elif kind == "deactivate":
            out.append("Switch the page off")
    return out


def apply_page_ops(db: Session, tenant_id: int, entity_type: str, segment: str | None, ops: list[dict[str, Any]],
                   key: str | None = None) -> dict[str, Any]:
    """Creates the new fields the ops add, then saves the changed page."""
    from app.crm_core import lifecycle_service

    shown = (page_by_key(db, tenant_id, key) if key else None) or page(db, tenant_id, entity_type, segment, key)
    layout = json.loads(json.dumps(shown["layout"]))
    sections = layout["sections"]

    def find(field_key: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        for s in sections:
            for f in s["fields"]:
                if f.get("key") == field_key:
                    return s, f
        return None

    def find_element(kind: str, ref: Any) -> tuple[dict[str, Any], dict[str, Any]] | None:
        wanted = str(ref or "").strip().lower()
        for s in sections:
            for f in s["fields"]:
                if f.get("kind") == kind and wanted in (str(f.get("id", "")).lower(), str(f.get("label", "")).lower(), _slug(wanted)):
                    return s, f
        return None

    activate: bool | None = None

    def section(title: str | None) -> dict[str, Any]:
        title = (title or "More details").strip()[:60]
        found = next((s for s in sections if s["title"].lower() == title.lower()), None)
        if found is None:
            found = {"title": title, "columns": 2, "fields": []}
            sections.append(found)
        return found

    existing_keys = {d.key for d in db.query(CustomFieldDefinition).filter(
        CustomFieldDefinition.tenant_id == tenant_id, CustomFieldDefinition.entity_type == entity_type)}
    core_names = set(lifecycle_service.UPDATABLE_CORE_FIELDS.get(entity_type, {})) | {f["key"] for f in CORE_FIELDS.get(entity_type, [])}
    for o in ops:
        kind = o.get("op")
        if kind == "add_field":
            field_key = re.sub(r"[^a-z0-9_]", "_", str(o.get("key") or "").lower()).strip("_")[:60]
            if not field_key or not re.match(r"^[a-z]", field_key):
                raise ValueError(f"'{o.get('label')}' needs a name that starts with a letter")
            data_type = o.get("data_type") if o.get("data_type") in CUSTOM_FIELD_DATA_TYPES else "text"
            values = [str(v).strip() for v in o.get("values") or [] if str(v).strip()]
            if data_type == "picklist" and not values:
                data_type = "text"
            if field_key in core_names:
                raise ValueError(f"'{field_key}' is a built-in field - show it instead of adding it")
            if field_key not in existing_keys:
                db.add(CustomFieldDefinition(
                    tenant_id=tenant_id, entity_type=entity_type, key=field_key, label=str(o.get("label") or field_key)[:150],
                    data_type=data_type, allowed_values=",".join(values) if data_type == "picklist" else None, required=False,
                    is_system=False, segment=segment, section=(o.get("section") or "More details")[:60],
                ))
                db.flush()
                existing_keys.add(field_key)
            elif segment:
                # the same field added on another segment's page (B2B and B2C prospects): it is shared from now on
                shared = db.query(CustomFieldDefinition).filter(CustomFieldDefinition.tenant_id == tenant_id, CustomFieldDefinition.entity_type == entity_type,
                                                                CustomFieldDefinition.key == field_key).first()
                if shared is not None and shared.segment and shared.segment != segment:
                    shared.segment = None
                    db.flush()
            placed = find(field_key)
            if placed is None:
                section(o.get("section"))["fields"].append({"key": field_key, "mandatory": bool(o.get("mandatory")), "hidden": False, "readonly": False})
            else:
                placed[1].update({"hidden": False, "mandatory": bool(o.get("mandatory")) or placed[1].get("mandatory", False)})
            layout["excluded"] = [k for k in layout.get("excluded") or [] if k != field_key]
        elif kind == "add_button":
            label = str(o.get("label") or "").strip()
            existing = find_element("button", label)
            button = {"kind": "button", "id": _slug(o.get("id") or label), "label": label,
                      "intent": _intent_code(db, tenant_id, o.get("intent") or o.get("flow")), "style": o.get("style") or "default"}
            if existing is not None:
                existing[1].update(button)
            else:
                section(o.get("section") or "Actions")["fields"].append(button)
        elif kind in ("remove_button", "remove_text"):
            found = find_element("button" if kind == "remove_button" else "text", o.get("label") or o.get("id") or o.get("text"))
            if found is not None:
                found[0]["fields"].remove(found[1])
        elif kind == "add_text":
            text = str(o.get("text") or "").strip()
            if text:
                section(o.get("section"))["fields"].insert(0, {"kind": "text", "id": _slug(o.get("id") or text[:30]), "text": text})
        elif kind == "on_event":
            event = o.get("event") if o.get("event") in PAGE_EVENTS else "after_save"
            layout.setdefault("events", {})[event] = _intent_code(db, tenant_id, o.get("intent") or o.get("flow"))
        elif kind == "clear_event":
            (layout.get("events") or {}).pop(o.get("event"), None)
        elif kind == "roles":
            current = set(layout.get("role_ids") or [])
            ids = set(_clean_roles(db, tenant_id, o.get("roles") or []))
            layout["role_ids"] = sorted(ids if o.get("mode", "set") == "set" else current | ids if o.get("mode") == "add" else current - ids)
        elif kind in ("activate", "deactivate"):
            activate = kind == "activate"
        elif kind == "add_section":
            section(o.get("title"))
        elif kind == "rename_section":
            s = next((s for s in sections if s["title"].lower() == str(o.get("title", "")).lower()), None)
            if s is not None and o.get("to"):
                s["title"] = str(o["to"])[:60]
        else:
            field_key = o.get("key")
            placed = find(field_key)
            if placed is None and kind in ("show", "mandatory", "move") and field_key:
                # a field the page doesn't have yet ("make Discount reason mandatory"): create it as a custom
                # field rather than fail the whole change
                clean = re.sub(r"[^a-z0-9_]", "_", str(field_key).lower()).strip("_")[:60]
                known = clean in existing_keys or clean in core_names or clean in {f["key"] for f in catalog(db, tenant_id, entity_type, segment)}
                if not known and re.match(r"^[a-z]", clean or ""):
                    label = str(o.get("label") or clean.replace("_", " ").capitalize())[:150]
                    db.add(CustomFieldDefinition(tenant_id=tenant_id, entity_type=entity_type, key=clean, label=label, data_type="text",
                                                 required=False, is_system=False, segment=segment, section=(o.get("section") or "More details")[:60]))
                    db.flush()
                    existing_keys.add(clean)
                field_key = clean or field_key
                section(o.get("section"))["fields"].append({"key": field_key, "mandatory": False, "hidden": False, "readonly": False})
                layout["excluded"] = [k for k in layout.get("excluded") or [] if k != field_key]
                placed = find(field_key)
            if placed is None:
                continue
            s, f = placed
            if kind == "show":
                f["hidden"] = False
                if o.get("section") and o["section"].lower() != s["title"].lower():
                    s["fields"].remove(f)
                    section(o["section"])["fields"].append(f)
            elif kind == "hide":
                f.update({"hidden": True, "mandatory": False})
            elif kind == "mandatory":
                f.update({"mandatory": True, "hidden": False})
            elif kind == "optional":
                f["mandatory"] = False
            elif kind == "readonly":
                f["readonly"] = bool(o.get("value", True))
            elif kind == "rename":
                f["label"] = str(o.get("label") or "")[:150] or None
            elif kind == "move":
                s["fields"].remove(f)
                section(o.get("section"))["fields"].append(f)
    layout["sections"] = [s for s in sections if s["fields"] or s["title"]]
    out = save_page(db, tenant_id, entity_type, segment, layout, shown["page_key"])
    if activate is not None:
        out = set_active(db, tenant_id, shown["page_key"], activate)
    return out


# ---------------------------------------------------------------- copy, switch on/off, events


def _row(db: Session, tenant_id: int, key: str) -> PageLayout | None:
    return db.query(PageLayout).filter(PageLayout.tenant_id == tenant_id, PageLayout.page_key == key).first()


def find_page(db: Session, tenant_id: int, ref: str) -> dict[str, Any]:
    """A page by its key or name ("B2B prospect page", "Enterprise onboarding")."""
    text = str(ref or "").strip().lower()
    listing = pages(db, tenant_id)
    found = next((p for p in listing if p["page_key"].lower() == text or p["name"].lower() == text), None) \
        or next((p for p in listing if text and (text in p["name"].lower() or p["name"].lower().removesuffix(" page") in text)), None)
    if found is None:
        raise ValueError(f"No page '{ref}' - pages: {', '.join(p['name'] for p in listing)}")
    return found


def copy_page(db: Session, tenant_id: int, key: str, name: str) -> dict[str, Any]:
    """A new page of the tenant's own, starting as a copy of this one -
    switched off until it is activated, so it changes nothing yet."""
    source = next((p for p in pages(db, tenant_id) if p["page_key"] == key), None)
    if source is None:
        raise ValueError("Page not found")
    layout = json.loads(json.dumps(source["layout"]))
    layout.pop("role_ids", None)
    out = new_page(db, tenant_id, source["entity_type"], source["segment"], name, f"Copy of {source['name']}", layout)
    return set_active(db, tenant_id, out["page_key"], False)


def set_active(db: Session, tenant_id: int, key: str, active: bool) -> dict[str, Any]:
    row = _row(db, tenant_id, key)
    if row is None:
        source = next((p for p in pages(db, tenant_id) if p["page_key"] == key), None)
        if source is None:
            raise ValueError("Page not found")
        save_page(db, tenant_id, source["entity_type"], source["segment"], source["layout"], key)
        row = _row(db, tenant_id, key)
    row.is_active = bool(active)
    db.flush()
    return next(p for p in pages(db, tenant_id) if p["page_key"] == key)


def fire_event(db: Session, tenant_id: int, execution_id: int, entity_type: str, obj, event: str, page_key: str | None = None,
               segment: str | None = None, user_id: int | None = None) -> list[dict[str, Any]]:
    """Starts the Flow Studio flow a page's event is bound to, for this record
    (the page the user saw: page_key, else the one their roles get)."""
    from app.crm_core import access_service
    from app.models.auth import User
    from app.orchestration import flow_engine

    user = db.get(User, user_id) if user_id else None
    role_ids = {r.id for r in access_service.user_roles(db, user)} if user is not None else None
    shown = page(db, tenant_id, entity_type, segment, page_key if isinstance(page_key, str) else None, role_ids)
    code = (shown.get("events") or {}).get(event)
    if not code:
        return []
    flow = flow_engine.flow_from_intent_code(db, tenant_id, code)
    if flow is None:
        return []
    ctx = {"entity_type": entity_type, "entity_id": obj.id, "entities": {f"{entity_type}_id": obj.id, "entity_type": entity_type,
                                                                         "entity_id": obj.id}, "result": {}}
    flow_engine.run_flow(db, tenant_id, execution_id, flow, ctx)
    return [{"event": event, "page": shown["page_key"], "flow": flow.name}]


# ---------------------------------------------------------------- products for a segment


def offerings_for_segment(db: Session, tenant_id: int, segment: str | None) -> list[str]:
    """Active products sold to this segment (B2B covers B2B-SMB/-Enterprise);
    a product without a segment is sold to both."""
    from app.models.catalog import OfferingStatus, ProductOffering

    rows =db.query(ProductOffering).filter(ProductOffering.tenant_id == tenant_id, ProductOffering.status == OfferingStatus.active)
    out: list[str] = []
    for o in rows.order_by(ProductOffering.name).all():
        offered = segment_of(o.segment)
        if (segment is None or offered is None or offered == segment) and o.name not in out:
            out.append(o.name)
    return out
