"""Training data for the platform's own telecom CRM model.

Fine-tuning a small open model (Qwen / Llama) on exactly the jobs this
CRM gives it makes it match much bigger hosted models on them. The jobs
are the prompts the app really sends, so every example uses the *same*
system prompt as production:

1. Intent understanding (classifier.system_prompt, shortlisted per request
   exactly as in production) - from
   a) the tenant's own history: every free-text request that ran
      successfully (IntentExecutionLog), with the intent and fields that
      worked - the best data there is, and it grows with usage;
   b) generated telecom examples for every built-in intent (templates x
      realistic companies, products, ids, typos), so rare intents are
      covered from day one.
2. Configure 360 instructions -> layout operations (c360 agent prompt),
   labelled by the deterministic rule engine.

Output: chat-format JSONL ({"messages": [system, user, assistant]}) that
Unsloth / TRL / Axolotl / OpenAI fine-tuning all read as-is.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterator
from typing import Any

from sqlalchemy.orm import Session

_COMPANIES = ["Acme Telecom", "Globex", "Tata Logistics", "Burger King", "Infosys BPM", "Reliance Retail", "Apollo Clinics",
              "Mahindra Finance", "Zomato", "HDFC Branch Mumbai", "Sunrise Hotels", "Bharat Steel", "Nova Schools", "Orbit Media"]
_PRODUCTS = ["Fiber 800", "Fiber 500", "Business Broadband 1G", "SD-WAN Pro", "MPLS 100", "Enterprise Voice", "IoT Fleet SIM", "5G Fixed Wireless"]
_COMPONENTS = ["Fiber500", "VOIP", "Static IP", "Managed Router", "SIM", "Security Suite"]
_SEGMENTS = ["B2C", "B2B", "B2B-SMB", "B2B-Enterprise"]
_SOURCES = ["referral", "website", "event", "partner", "cold_call", "campaign"]
_REGIONS = ["North", "South", "East", "West", "Mumbai", "Delhi NCR"]
_PEOPLE = ["Ravi Kumar", "Anita Desai", "Sanket Patil", "Priya Nair", "Arjun Mehta"]
_ADDRESSES = ["12 MG Road, Bengaluru", "Plot 7, BKC, Mumbai", "45 Park Street, Kolkata", "Tower B, Cyber City, Gurugram"]
_PROBLEMS = ["internet has been down since this morning", "speed is very slow at the head office", "VOIP calls keep dropping",
             "the router shows a red light", "we were billed twice this month", "the leased line is flapping every hour"]
_REASONS = ["strategic account", "competitive deal", "customer is a long-term partner", "within margin guidelines"]


def _fill(rnd: random.Random) -> dict[str, Any]:
    company = rnd.choice(_COMPANIES)
    p1, p2 = rnd.sample(_PRODUCTS, 2)
    c1, c2 = rnd.sample(_COMPONENTS, 2)
    return {
        "company": company, "email": f"ops@{company.split()[0].lower()}.com", "product": p1, "product2": p2, "c1": c1, "c2": c2,
        "segment": rnd.choice(_SEGMENTS), "source": rnd.choice(_SOURCES), "region": rnd.choice(_REGIONS), "person": rnd.choice(_PEOPLE),
        "address": rnd.choice(_ADDRESSES), "problem": rnd.choice(_PROBLEMS), "reason": rnd.choice(_REASONS),
        "id": rnd.randint(1, 400), "id2": rnd.randint(1, 400), "price": rnd.choice([799, 999, 1200, 1499, 2500, 4999, 12000]),
        "pct": rnd.choice([3, 5, 8, 10, 12, 15]), "n": rnd.randint(2, 20), "idnum": str(rnd.randint(10**9, 10**10 - 1)),
        "doc": f"ACC-{rnd.randint(1000, 9999)}",
    }


# (intent, [(text template, entities template)]) - {slots} filled from _fill()
_T: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = [
    ("INT-01", [("Launch {product} with {c1} and {c2} for {segment} at {price}", {"name": "{product}", "composition": ["{c1}", "{c2}"], "segment": "{segment}", "list_price": "{price}"}),
                ("new bundle {product}: {c1} + {c2}, price {price} a month", {"name": "{product}", "composition": ["{c1}", "{c2}"], "list_price": "{price}"})]),
    ("INT-02", [("Change {product} price to {price}", {"offering_name": "{product}", "new_list_price": "{price}"}),
                ("make {product} available for {segment} customers", {"offering_name": "{product}", "new_segment": "{segment}"})]),
    ("INT-03", [("Retire {product} and move its customers to {product2}", {"offering_name": "{product}", "replacement_offering_name": "{product2}"}),
                ("stop selling {product}, replacement is {product2}", {"offering_name": "{product}", "replacement_offering_name": "{product2}"})]),
    ("INT-04", [("New lead {company} from a {source} in the {region}, interested in {product}",
                 {"company_name": "{company}", "source": "{source}", "region": "{region}", "offering_name": "{product}"}),
                ("qualify {company} - came via {source}, wants {n} x {product}",
                 {"company_name": "{company}", "source": "{source}", "offering_name": "{product}", "quantity": "{n}"})]),
    ("INT-05", [("Quote {product} for opportunity {id} with {pct}% off", {"opportunity_id": "{id}", "offering_name": "{product}", "requested_discount_pct": "{pct}"}),
                ("price opp {id} on {product}", {"opportunity_id": "{id}", "offering_name": "{product}"})]),
    ("INT-06", [("Approve quote {id} - {reason}", {"quote_id": "{id}", "decision": "approve", "reasoning": "{reason}"}),
                ("reject quote {id}, discount too high", {"quote_id": "{id}", "decision": "reject", "reasoning": "discount too high"})]),
    ("INT-07", [("Activate the order for quote {id}", {"quote_id": "{id}"}), ("go live with quote {id} end to end", {"quote_id": "{id}"})]),
    ("INT-08", [("Upgrade order {id} to {price} a month", {"order_id": "{id}", "new_monthly_price": "{price}"}),
                ("downgrade service on order {id}, new price {price}", {"order_id": "{id}", "new_monthly_price": "{price}"})]),
    ("INT-09", [("Suspend order {id}", {"order_id": "{id}", "action": "suspend"}), ("customer wants to cancel order {id}", {"order_id": "{id}", "action": "cancel"}),
                ("terminate order {id} immediately", {"order_id": "{id}", "action": "terminate"})]),
    ("INT-10", [("{company}'s {problem}", {"description": "{problem}", "company_name": "{company}"}),
                ("complaint from {company}: {problem}", {"description": "{problem}", "company_name": "{company}"})]),
    ("INT-11", [("Diagnose ticket {id}", {"ticket_id": "{id}"}), ("what's the root cause of ticket {id}?", {"ticket_id": "{id}"})]),
    ("INT-12", [("Send a fiber technician for ticket {id} tomorrow 9-12", {"ticket_id": "{id}", "required_skill": "fiber_install", "scheduled_window": "tomorrow 9-12"}),
                ("dispatch an engineer to order {id}", {"order_id": "{id}"})]),
    ("INT-13", [("Verify ID {idnum} for customer {id}", {"party_id": "{id}", "id_number": "{idnum}"}),
                ("kyc check passport {idnum} for party {id}", {"party_id": "{id}", "id_number": "{idnum}", "id_type": "passport"})]),
    ("INT-14", [("Provision the A-End of order {id}", {"order_id": "{id}", "site_label": "A-End"}), ("bring Z-End live on order {id}", {"order_id": "{id}", "site_label": "Z-End"})]),
    ("INT-15", [("Add prospect {company}, {email}", {"name": "{company}", "email": "{email}"}), ("new prospect {company}", {"name": "{company}"})]),
    ("INT-16", [("Lead {id} wants {product}", {"lead_id": "{id}", "offering_name": "{product}"}), ("add {product} to lead {id}", {"lead_id": "{id}", "offering_name": "{product}"})]),
    ("INT-17", [("Approve lead {id}", {"lead_id": "{id}", "decision": "approve"}), ("reject lead {id}, no budget", {"lead_id": "{id}", "decision": "reject", "reasoning": "no budget"})]),
    ("INT-18", [("Check feasibility for opportunity {id} at {address}", {"opportunity_id": "{id}", "address": "{address}"}),
                ("can we serve opp {id}? site is {address}", {"opportunity_id": "{id}", "address": "{address}"})]),
    ("INT-19", [("Revise quote {id} to {pct}% discount", {"quote_id": "{id}", "requested_discount_pct": "{pct}"}),
                ("change quote {id} quantity to {n}", {"quote_id": "{id}", "quantity": "{n}"})]),
    ("INT-20", [("Cancel quote {id}, customer went with a competitor", {"quote_id": "{id}", "reasoning": "customer went with a competitor"})]),
    ("INT-21", [("Change order {id} to {n} lines", {"order_id": "{id}", "new_quantity": "{n}"}), ("revise order {id} price to {price}", {"order_id": "{id}", "new_monthly_price": "{price}"})]),
    ("INT-22", [("Send the proposal for quote {id}", {"quote_id": "{id}"}), ("generate proposal quote {id}", {"quote_id": "{id}"})]),
    ("INT-23", [("The customer accepted proposal {id}", {"proposal_id": "{id}", "decision": "approve"}),
                ("customer declined proposal {id}", {"proposal_id": "{id}", "decision": "reject"})]),
    ("INT-24", [("Create the order for quote {id}", {"quote_id": "{id}"}), ("raise the order from quote {id}", {"quote_id": "{id}"})]),
    ("INT-25", [("Approve order {id}", {"order_id": "{id}", "decision": "approve"}), ("order control: reject order {id}", {"order_id": "{id}", "decision": "reject"})]),
    ("INT-26", [("Installation done for work order {id}", {"work_order_id": "{id}"})]),
    ("INT-27", [("Connectivity test passed for work order {id}, signed by {person}", {"work_order_id": "{id}", "signature": "{person}"})]),
    ("INT-28", [("Acceptance letter received for work order {id}, ref {doc}", {"work_order_id": "{id}", "document_ref": "{doc}"})]),
    ("INT-29", [("Update opportunity {id} name to {company} expansion", {"entity_type": "opportunity", "entity_id": "{id}", "fields": {"name": "{company} expansion"}}),
                ("change the email of customer {id} to {email}", {"entity_type": "party", "entity_id": "{id}", "fields": {"email": "{email}"}})]),
    ("INT-30", [("Make a new revision of quote {id}", {"quote_id": "{id}"})]),
    ("INT-31", [("Close opportunity {id} as won", {"entity_type": "opportunity", "entity_id": "{id}", "to_state": "won"}),
                ("mark opp {id} lost", {"entity_type": "opportunity", "entity_id": "{id}", "to_state": "lost"})]),
    ("INT-32", [("Assign lead {id} to {email}", {"entity_type": "lead", "entity_id": "{id}", "to_user": "{email}"}),
                ("hand opportunity {id} over to the Deal Desk", {"entity_type": "opportunity", "entity_id": "{id}", "to_position": "Deal Desk"})]),
    # Sales-to-Order / B2C steps (INT-35..41). Kept last: the generator is
    # seeded, so every example above stays exactly what it always was.
    ("INT-35", [("Convert prospect {id} into an account on the Enterprise pricelist", {"party_id": "{id}", "price_list": "Enterprise"}),
                ("make customer {id} an active account", {"party_id": "{id}"})]),
    ("INT-36", [("Approve opportunity {id} - {reason}", {"entity_type": "opportunity", "entity_id": "{id}", "decision": "approve", "reasoning": "{reason}"}),
                ("reject account {id}, credit check failed", {"entity_type": "party", "entity_id": "{id}", "decision": "reject", "reasoning": "credit check failed"})]),
    ("INT-37", [("Configure the product on opportunity {id}: {product}, bandwidth 100 Mbps, 36 months",
                 {"opportunity_id": "{id}", "offering_name": "{product}", "pricing_attributes": {"Bandwidth": "100 Mbps", "Contract term": "36 months"}}),
                ("set the pricing attributes for opp {id}, SLA 99.9%", {"opportunity_id": "{id}", "pricing_attributes": {"SLA": "99.9%"}})]),
    ("INT-38", [("Send opportunity {id} to presales, site {address}", {"opportunity_id": "{id}", "address": "{address}"}),
                ("hand opp {id} to presales for the feasibility check", {"opportunity_id": "{id}"})]),
    ("INT-39", [("Attach PO-{idnum} to quote {id} for {price}", {"quote_id": "{id}", "po_number": "PO-{idnum}", "po_amount": "{price}"}),
                ("received the customer's purchase order {doc} for quote {id}", {"quote_id": "{id}", "po_number": "{doc}"})]),
    ("INT-40", [("Match all for quote {id}", {"quote_id": "{id}"}), ("match the PO on quote {id}", {"quote_id": "{id}"})]),
    ("INT-41", [("Add a consumer {person}, {email}", {"name": "{person}", "email": "{email}"}), ("register new consumer {person}", {"name": "{person}"})]),
    # Product configuration (INT-42..45) - also last, for the same reason.
    ("INT-42", [("Design {product}: Bandwidth 100 Mbps +0, 1 Gbps +{price}; technical: VLAN ID required number",
                 {"offering_name": "{product}", "instruction": "Bandwidth 100 Mbps +0, 1 Gbps +{price}; technical: VLAN ID required number"}),
                ("define attributes for {product}: Static IPs: 50 each; CPE type: Cisco, Huawei",
                 {"offering_name": "{product}", "instruction": "Static IPs: 50 each; CPE type: Cisco, Huawei"})]),
    ("INT-43", [("Put {product} on the Enterprise pricelist at {price}", {"offering_name": "{product}", "price_list": "Enterprise", "price": "{price}"}),
                ("add {product} to the Government pricelist for {price}", {"offering_name": "{product}", "price_list": "Government", "price": "{price}"})]),
    ("INT-44", [("Use the Strategic pricelist for quote {id}", {"entity_type": "quote", "entity_id": "{id}", "price_list": "Strategic"}),
                ("apply the Enterprise pricelist to customer {id}", {"entity_type": "party", "entity_id": "{id}", "price_list": "Enterprise"})]),
    ("INT-45", [("Order {id}: VLAN ID {n}, CPE type Cisco", {"order_id": "{id}", "technical_attributes": "VLAN ID {n}, CPE type Cisco"}),
                ("set technical attributes on order {id}: IP block /29", {"order_id": "{id}", "technical_attributes": "IP block /29"})]),
    # New Connect (INT-46) - last, for the same reason.
    ("INT-46", [("Generate a lead for prospect {id} interested in {product}", {"party_id": "{id}", "products": ["{product}"]}),
                ("raise a lead for customer {id}, they want {product} and {product2}", {"party_id": "{id}", "products": ["{product}", "{product2}"]})]),
    # Configurator (INT-47..50) - last, for the same reason.
    ("INT-47", [("Configure with AI for opportunity {id}: fibre office line with a router, budget {price}",
                 {"opportunity_id": "{id}", "need": "fibre office line with a router, budget {price}"})]),
    ("INT-48", [("Release the product model of {product}", {"offering_name": "{product}"})]),
    ("INT-49", [("What can customer {id} buy?", {"party_id": "{id}"}), ("check eligible products for prospect {id}", {"party_id": "{id}"})]),
    ("INT-50", [("Reconfigure order {id}: Bandwidth 4 Mbps", {"order_id": "{id}", "pricing_attributes": "Bandwidth 4 Mbps"})]),
]

# Order Management (INT-51..71) - appended last, so every earlier example stays exactly as it was.
from app.intent_engine.om_intents import TRAINING as _OM_TRAINING  # noqa: E402

_T = _T + _OM_TRAINING

# Communications (INT-72..102) - appended after those, for the same reason.
from app.intent_engine.comm_intents import TRAINING as _CM_TRAINING  # noqa: E402

_T = _T + _CM_TRAINING

# Configuration (INT-103) - appended after those, for the same reason.
from app.intent_engine.config_intents import TRAINING as _CF_TRAINING  # noqa: E402

_T = _T + _CF_TRAINING

# B2C direct order (INT-104..105) - appended after those, for the same reason.
from app.intent_engine.b2c_intents import TRAINING as _B2C_TRAINING  # noqa: E402

_T = _T + _B2C_TRAINING

# Sales-to-Order documents (INT-109) - appended last, for the same reason.
from app.intent_engine.sto_intents import TRAINING as _STO_TRAINING  # noqa: E402

_T = _T + _STO_TRAINING

# Siebel asset-based ordering (INT-110..115) - appended last, for the same reason.
from app.intent_engine.abo_intents import TRAINING as _ABO_TRAINING  # noqa: E402

_T = _T + _ABO_TRAINING

# Quote price override (INT-116) - appended last, for the same reason.
from app.intent_engine.quote_intents import TRAINING as _QN_TRAINING  # noqa: E402

_T = _T + _QN_TRAINING

# Fulfillment (INT-117..118) - appended last, for the same reason.
from app.intent_engine.fulfillment_intents import TRAINING as _FULFILLMENT_TRAINING  # noqa: E402

_T = _T + _FULFILLMENT_TRAINING

# Inventory (INT-120..128) - appended last, for the same reason.
from app.intent_engine.inventory_intents import TRAINING as _INVENTORY_TRAINING  # noqa: E402

_T = _T + _INVENTORY_TRAINING

_INT_SLOTS = {"id", "id2", "price", "pct", "n"}


def _render(value: Any, slots: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {k: _render(v, slots) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, slots) for v in value]
    if isinstance(value, str) and value.startswith("{") and value.endswith("}") and value[1:-1] in _INT_SLOTS:
        return slots[value[1:-1]]
    return value.format(**slots) if isinstance(value, str) else value


def _typo(text: str, rnd: random.Random) -> str:
    """A little real-world messiness: lower case, a dropped letter."""
    if rnd.random() < 0.3:
        text = text.lower()
    if rnd.random() < 0.15 and len(text) > 12:
        i = rnd.randrange(1, len(text) - 1)
        if text[i].isalpha():
            text = text[:i] + text[i + 1:]
    return text


def _answer(intent: str, entities: dict[str, Any], why: str) -> str:
    return json.dumps({"intent_type": intent, "entities": entities, "confidence": 0.95, "rationale": why, "alternatives": []}, ensure_ascii=False)


def _example(system: str, user: str, assistant: str) -> dict[str, Any]:
    return {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}, {"role": "assistant", "content": assistant}]}


def synthetic_intents(per_template: int = 12, seed: int = 7) -> Iterator[tuple[str, str]]:
    """(user text, assistant JSON) pairs for every built-in intent."""
    from app.intent_engine import intent_registry

    rnd = random.Random(seed)
    for intent, templates in _T:
        label = intent_registry.BUILTIN[intent].label
        for text_t, ent_t in templates:
            seen: set[str] = set()
            for _ in range(per_template):
                slots = _fill(rnd)
                text = _typo(text_t.format(**slots), rnd)
                if text in seen:
                    continue
                seen.add(text)
                yield text, _answer(intent, _render(ent_t, slots), f"The user wants to {label.lower()}.")
    for text in C360_INSTRUCTIONS:
        yield f"Configure 360: {text}", _answer("INT-33", {"instruction": text}, "A change to the Customer 360 page.")


def history(db: Session, tenant_id: int | None) -> Iterator[tuple[str, str]]:
    """Free-text requests that really ran - what users actually say."""
    from app.intent_engine import intent_registry
    from app.models.intent_log import IntentExecutionLog

    q = db.query(IntentExecutionLog).filter(IntentExecutionLog.status == "executed")
    if tenant_id is not None:
        q = q.filter(IntentExecutionLog.tenant_id == tenant_id)
    for row in q.order_by(IntentExecutionLog.id).yield_per(500):
        text = (row.raw_text or "").strip()
        label = intent_registry.label_of(db, row.tenant_id, row.intent_type) or ""
        # Form submissions carry the intent code or a generated summary, not words a user typed.
        if len(text) < 6 or text == row.intent_type or text.lower() == label.lower() or text.startswith("INT-"):
            continue
        try:
            entities = {k: v for k, v in json.loads(row.entities_json or "{}").items() if not str(k).startswith("_")}
        except json.JSONDecodeError:
            continue
        yield text, _answer(row.intent_type, entities, f"Same request as a past successful '{label or row.intent_type}'.")


C360_INSTRUCTIONS = [
    "add sales, service, network and billing panes on a single page",
    "connect our billing system and make it compact with Vi red",
    "show tickets by priority as a bar chart",
    "remove the inventory pane",
    "move billing to the top and make it full width",
    "rename the network pane to Network & NOC",
    "add usage trend to the billing pane",
    "switch to a light theme with outline cards",
    "connect all the respective systems",
    "make the sales pane purple",
    "use the customer care layout",
    "put latency in the header kpis",
    "three columns",
    "add a donut of invoice status",
    "put AI insights at the top",
    "add network availability and utilization",
    "show bookings trend as a line chart",
    "connect our network system at https://oss.example.com/api",
    "make it dark and spacious",
    "go back to the previous version",
]


def c360_examples() -> Iterator[dict[str, Any]]:
    from app.c360 import agent, catalog

    layout = catalog.default_layout()
    for text in C360_INSTRUCTIONS:
        ops = [o for o in agent._rules(text, layout, "#e60000") if o.get("op") != "_note"]
        if not ops:
            continue
        context = {"TEMPLATES": list(catalog.TEMPLATES), "THEMES": list(catalog.THEMES),
                   "recipes": [f"{r.id}: {r.title} ({r.type}, {r.domain})" for r in catalog.RECIPES.values()],
                   "current_layout": catalog.describe(layout), "connected_systems": [], "tenant_brand_color": "#e60000"}
        result = agent.apply_ops(layout, ops)
        reply = "Done: " + "; ".join(result["changes"]) + "." if result["changes"] else "Done."
        yield _example(agent._LLM_PROMPT, f"{json.dumps(context)}\n\nRequest: {text}", json.dumps({"ops": ops, "reply": reply}, ensure_ascii=False))


STYLES = ("reasoned", "plain")


def build(db: Session, tenant_id: int | None, per_template: int = 12, include_synthetic: bool = True,
          style: str = "reasoned") -> Iterator[dict[str, Any]]:
    """style "reasoned" (default): answers think first - a specific rationale,
    look-alikes ruled out, vague requests with alternatives, out-of-scope ones
    as UNKNOWN, plus multi-step planning (crm_core/training_reasoning.py).
    "plain": the original examples, exactly as before."""
    from app.intent_engine import classifier

    custom = classifier.custom_intent_specs(db, tenant_id) if tenant_id is not None else []
    entries = classifier.prompt_entries(db, tenant_id)

    def system(text: str, answer: str) -> str:
        """The prompt production sends for this text - its shortlisted intents, the answer's among them."""
        code = json.loads(answer).get("intent_type")
        return classifier.system_prompt(custom, text, entries, None, (code,) if code else ())

    if style == "plain":
        for text, answer in history(db, tenant_id):
            yield _example(system(text, answer), classifier.user_prompt(text), answer)
        if include_synthetic:
            for text, answer in synthetic_intents(per_template):
                yield _example(system(text, answer), classifier.user_prompt(text), answer)
            yield from c360_examples()
        return
    from app.crm_core import training_reasoning

    for i, (text, answer) in enumerate(history(db, tenant_id)):
        old = json.loads(answer)
        answer = training_reasoning.reasoned_answer(old["intent_type"], old["entities"], i)
        yield _example(system(text, answer), classifier.user_prompt(text), answer)
    if include_synthetic:
        for text, answer in training_reasoning.intents(per_template):
            yield _example(system(text, answer), classifier.user_prompt(text), answer)
        yield from c360_examples()
        yield from training_reasoning.plans(max(2, per_template))


def to_jsonl(examples: Iterator[dict[str, Any]]) -> Iterator[str]:
    for ex in examples:
        yield json.dumps(ex, ensure_ascii=False) + "\n"
