"""Training data that teaches the platform's own model to *think* before it
answers - v2 of the generated examples (training_data.build(style="reasoned")).

What the plain examples never taught, and these do:

1. Reason first. The answer JSON starts with "rationale", so the model works
   out what is asked, what is given and what is missing *before* it commits to
   an intent - and the rationale is specific (the record, the values, the rival
   intent it ruled out and why), not "The user wants to X".
2. Tell look-alikes apart. Pairs that are easily confused (send vs prepare a
   proposal, a quote's approval vs any approval, revise vs new revision ...)
   carry the distinction in the rationale.
3. Know what it doesn't know. Vague requests get a best guess, a lower
   confidence and the plausible alternatives; requests outside the CRM get
   "UNKNOWN" instead of being forced into an action.
4. Plan. Multi-step goals are broken into ordered actions, in the planning
   agent's own prompt (orchestration/agent.py), leaving out what an earlier
   step creates.
5. Every action. The intents that had no examples (inventory sync, offering
   orders, asset changes, fulfilment confirmation) get some.

Labels stay exact: every example's answer is known from its template, never
guessed by a model.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterator
from typing import Any

# intent -> [(rival intent, why it is this one and not the rival)]
CONTRAST: dict[str, list[tuple[str, str]]] = {
    "INT-22": [("INT-109", "it sends the proposal to the customer - preparing the PDF alone is the other one")],
    "INT-109": [("INT-22", "it asks for the proposal document itself, not to send it yet")],
    "INT-06": [("INT-36", "it is a quote's discount approval, which has its own action")],
    "INT-36": [("INT-06", "the record waiting is not a quote, so the general approval decides it")],
    "INT-17": [("INT-36", "a lead at the qualification gate has its own approval")],
    "INT-25": [("INT-36", "order control before fulfilment has its own approval")],
    "INT-19": [("INT-30", "the quote is still editable, so it is changed in place")],
    "INT-30": [("INT-19", "a locked quote gets a new revision instead of an in-place edit")],
    "INT-04": [("INT-46", "a new company is qualified; generating a lead is for an existing prospect")],
    "INT-46": [("INT-04", "the prospect already exists (its id is given), so a lead is generated for it")],
    "INT-15": [("INT-41", "a business prospect, not a consumer")],
    "INT-41": [("INT-15", "a consumer (B2C) customer, not a business prospect")],
    "INT-08": [("INT-50", "a new monthly price, not a change of the configured options")],
    "INT-50": [("INT-08", "the configured options change, not just the price")],
    "INT-21": [("INT-08", "the order is not live yet, so it is revised before fulfilment")],
    "INT-24": [("INT-40", "an accepted proposal makes the order; matching a PO is Sales Ops' step")],
    "INT-40": [("INT-24", "Sales Ops matches the PO first and the order follows from the match")],
    "INT-07": [("INT-24", "activation end to end, not only creating the order")],
    "INT-39": [("INT-23", "a purchase order arrived, not just a yes or no on the proposal")],
    "INT-23": [("INT-39", "the customer's answer to the proposal, no PO is mentioned")],
    "INT-37": [("INT-47", "exact attribute values are given; the AI configurator is for a described need")],
    "INT-47": [("INT-37", "a need is described for the AI to work out, not exact values")],
    "INT-18": [("INT-38", "the feasibility check itself, not handing it to Presales")],
    "INT-38": [("INT-18", "it hands the opportunity to Presales rather than recording the check")],
    "INT-09": [("INT-20", "a live service or order, not a quote")],
    "INT-20": [("INT-09", "a quote is withdrawn, not a live service")],
    "INT-11": [("INT-12", "only the cause is asked for, nobody is sent on site")],
    "INT-12": [("INT-11", "someone is sent on site, not just a diagnosis")],
    "INT-45": [("INT-37", "technical attributes on an order, not pricing on an opportunity")],
    "INT-31": [("INT-29", "the stage changes, not field values")],
    "INT-29": [("INT-31", "field values change, not the stage")],
    "INT-16": [("INT-46", "the lead exists already - a product is added to it")],
    "INT-02": [("INT-08", "a catalog product's price changes, not one customer's service")],
}

# Vague requests: (text, best guess, entities, alternatives, confidence, why)
AMBIGUOUS: list[tuple[str, str, dict[str, Any], list[str], float, str]] = [
    ("approve it", "INT-36", {"decision": "approve"}, ["INT-06", "INT-17", "INT-25"], 0.45,
     "Approve something, but no record is named - the general approval fits any record; it could be a quote, a lead or an order."),
    ("send it to the customer", "INT-22", {}, ["INT-109"], 0.55,
     "Sending to the customer is most likely the proposal; no quote is named."),
    ("change the price", "INT-19", {}, ["INT-02", "INT-08", "INT-21"], 0.4,
     "A price change, but not whose - a quote, a catalog product, a live service or an order are all possible."),
    ("cancel it", "INT-20", {}, ["INT-09"], 0.4, "Cancel something unnamed - a quote or a customer's service."),
    ("the customer said yes", "INT-23", {"decision": "approve"}, ["INT-36"], 0.6,
     "A customer's yes is usually their answer to a proposal; no proposal is named."),
    ("move it forward", "INT-31", {}, ["INT-36"], 0.35, "Move a record to another stage - which record and stage is not said."),
    ("add the product", "INT-16", {}, ["INT-37", "INT-46"], 0.5, "A product for a lead most likely; neither the lead nor the product is named."),
    ("configure it", "INT-37", {}, ["INT-47", "INT-33"], 0.5, "Configure the product on an opportunity most likely; nothing is named."),
    ("check it", "INT-18", {}, ["INT-11", "INT-49"], 0.35, "Check what - feasibility, a ticket or eligibility; no record is named."),
    ("send someone", "INT-12", {}, [], 0.6, "Someone on site means a technician; the ticket or order is not named."),
    ("it's down again", "INT-10", {"description": "it's down again"}, ["INT-11"], 0.7,
     "A service problem to log as a complaint; the customer is not named."),
    ("quote it", "INT-05", {}, ["INT-19"], 0.6, "Price an opportunity; which one and the product are not said."),
    ("they sent the PO", "INT-39", {}, [], 0.7, "A purchase order arrived for a quote; the quote and the PO number are missing."),
    ("approve the discount", "INT-06", {"decision": "approve"}, ["INT-36"], 0.7, "A discount is approved on a quote; the quote is not named."),
    ("upgrade them", "INT-08", {}, ["INT-50"], 0.5, "An upgrade of a live service; the order and the new price are missing."),
    ("make it live", "INT-07", {}, ["INT-25", "INT-14"], 0.5, "Go live - activate an order end to end; no quote or order is named."),
    ("stop the service", "INT-09", {"action": "suspend"}, ["INT-20"], 0.5,
     "Stopping a service is a suspension unless they say cancel; the order is not named."),
    ("close it", "INT-31", {}, ["INT-20", "INT-09"], 0.4, "Close - a deal won or lost, or something cancelled; nothing is named."),
    ("give it to Presales", "INT-38", {}, ["INT-32"], 0.7, "Presales gets an opportunity for feasibility; which one is not said."),
    ("what's next for this customer", "INT-49", {}, [], 0.5, "Closest is what this customer can buy next; no customer is named."),
]

# Not CRM actions at all
UNKNOWN: list[tuple[str, str]] = [
    ("hi", "A greeting, not an action."), ("thanks!", "Thanks, not an action."), ("ok", "An acknowledgement, not an action."),
    ("how are you", "Small talk, not an action."), ("test", "A test message, not an action."), ("asdfgh", "No request in this text."),
    ("what's the weather in Mumbai", "Weather is outside the CRM."), ("tell me a joke", "A joke is outside the CRM."),
    ("who won the cricket match yesterday", "Sports news is outside the CRM."), ("write a poem about fibre", "Writing poems is outside the CRM."),
    ("what is 245 times 17", "Arithmetic is not a CRM action."), ("book me a flight to Delhi", "Travel booking is outside the CRM."),
    ("translate hello into Hindi", "Translation is outside the CRM."), ("how do I reset my laptop password", "IT support for a laptop is outside the CRM."),
    ("order a pizza for the team", "Food orders are not telecom orders."), ("what's the capital of France", "General knowledge is outside the CRM."),
    ("can you write a python script", "Writing code is outside the CRM."), ("summarise today's news", "News is outside the CRM."),
    ("remind me to call mom", "Personal reminders are outside the CRM."), ("play some music", "Music is outside the CRM."),
    ("what is your name", "A question about the assistant, not an action."), ("set an alarm for 7", "Alarms are outside the CRM."),
    ("what's the share price of Reliance", "Share prices are outside the CRM."), ("draft a leave application", "HR letters are outside the CRM."),
    ("recommend a movie", "Movies are outside the CRM."),
]

# The intents that had no examples
EXTRA_TEMPLATES: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = [
    ("INT-34", [("Sync inventory check types", {}), ("load the feasibility check types from our inventory system", {})]),
    ("INT-106", [("Order an offering for customer {id}: {product}", {"party_id": "{id}", "offering_name": "{product}", "order_mode": "interactive"}),
                 ("order an offering in silent mode for customer {id}: {product}", {"party_id": "{id}", "offering_name": "{product}", "order_mode": "silent"})]),
    ("INT-107", [("Modify installed asset {id}", {"asset_id": "{id}", "asset_operation": "modify"}),
                 ("disconnect asset {id}", {"asset_id": "{id}", "asset_operation": "disconnect"}),
                 ("relocate asset {id} to {address}", {"asset_id": "{id}", "asset_operation": "relocate", "target_site": "{address}"})]),
    ("INT-108", [("Confirm fulfillment of order {id}", {"order_id": "{id}"}), ("order {id} is fulfilled - confirm it", {"order_id": "{id}"})]),
]

# Multi-step goals: (parts, [(intent, entities)]) - one step per part, in order
GOALS: list[tuple[list[str], list[tuple[str, dict[str, Any]]]]] = [
    (["Add prospect {company}, {email}", "generate a lead for them interested in {product}", "approve the lead"],
     [("INT-15", {"name": "{company}", "email": "{email}"}), ("INT-46", {"products": ["{product}"]}), ("INT-17", {"decision": "approve"})]),
    (["qualify {company} from a {source} for {product}", "quote {product} with {pct}% off", "approve the quote"],
     [("INT-04", {"company_name": "{company}", "source": "{source}", "offering_name": "{product}"}),
      ("INT-05", {"offering_name": "{product}", "requested_discount_pct": "{pct}"}), ("INT-06", {"decision": "approve"})]),
    (["approve quote {id}", "prepare the proposal", "send it to the customer"],
     [("INT-06", {"quote_id": "{id}", "decision": "approve"}), ("INT-109", {"quote_id": "{id}"}), ("INT-22", {"quote_id": "{id}"})]),
    (["attach PO-{idnum} to quote {id} for {price}", "match all"],
     [("INT-39", {"quote_id": "{id}", "po_number": "PO-{idnum}", "po_amount": "{price}"}), ("INT-40", {"quote_id": "{id}"})]),
    (["configure opportunity {id} with {product}, bandwidth 100 Mbps", "send it to presales, site {address}"],
     [("INT-37", {"opportunity_id": "{id}", "offering_name": "{product}", "pricing_attributes": {"Bandwidth": "100 Mbps"}}),
      ("INT-38", {"opportunity_id": "{id}", "address": "{address}"})]),
    (["log a complaint for {company}: {problem}", "diagnose it", "send a technician"],
     [("INT-10", {"company_name": "{company}", "description": "{problem}"}), ("INT-11", {}), ("INT-12", {})]),
    (["launch {product} with {c1} and {c2} for {segment} at {price}", "put it on the Enterprise pricelist at {price}"],
     [("INT-01", {"name": "{product}", "composition": ["{c1}", "{c2}"], "segment": "{segment}", "list_price": "{price}"}),
      ("INT-43", {"offering_name": "{product}", "price_list": "Enterprise", "price": "{price}"})]),
    (["add consumer {person}, {email}", "verify ID {idnum}"],
     [("INT-41", {"name": "{person}", "email": "{email}"}), ("INT-13", {"id_number": "{idnum}"})]),
    (["convert prospect {id} into an account on the Strategic pricelist", "generate a lead for them interested in {product}"],
     [("INT-35", {"party_id": "{id}", "price_list": "Strategic"}), ("INT-46", {"party_id": "{id}", "products": ["{product}"]})]),
    (["approve order {id}", "installation done for work order {id2}", "connectivity test passed for work order {id2}, signed by {person}"],
     [("INT-25", {"order_id": "{id}", "decision": "approve"}), ("INT-26", {"work_order_id": "{id2}"}),
      ("INT-27", {"work_order_id": "{id2}", "signature": "{person}"})]),
]
_JOINERS = [", then ", " and then ", "; ", ". After that, ", ", next "]
_POLITE = [("", ""), ("please ", ""), ("can you ", "?"), ("pls ", ""), ("", " please"), ("i need to ", ""), ("quickly ", "")]

_FIELD_WORDS = {"quote_id": "quote", "order_id": "order", "opportunity_id": "opportunity", "lead_id": "lead", "party_id": "customer",
                "ticket_id": "ticket", "work_order_id": "work order", "proposal_id": "proposal", "asset_id": "asset", "entity_id": "record",
                "offering_name": "product", "po_number": "PO", "requested_discount_pct": "discount %", "new_list_price": "new price",
                "new_monthly_price": "new price", "company_name": "company", "name": "name", "decision": "decision", "action": "action"}


def _say(key: str) -> str:
    return _FIELD_WORDS.get(key, key.replace("_", " "))


def _short(value: Any) -> str | None:
    if isinstance(value, (dict, list)) or value in (None, ""):
        return None
    text = str(value)
    return text if len(text) <= 40 else None


def required_of(code: str) -> list[str]:
    from app.intent_engine import reasoning

    return list(reasoning._REQUIRED_FIELDS.get(code, []))


def reasoned_answer(code: str, entities: dict[str, Any], seed: int = 0) -> str:
    """The answer, rationale first: what is asked, what is given, the look-alike
    ruled out, what is still missing."""
    from app.intent_engine.intent_registry import BUILTIN

    meta = BUILTIN.get(code)
    label = meta.label if meta else code
    parts = [f"Asks to {label[:1].lower()}{label[1:]}."]
    given = [f"{_say(k)} {shown}" for k in entities if (shown := _short(entities[k])) is not None][:4]
    if given:
        parts.append("Given: " + ", ".join(given) + ".")
    rivals = [(r, why) for r, why in CONTRAST.get(code, []) if r in BUILTIN]
    if rivals:
        rival, why = rivals[seed % len(rivals)]
        parts.append(f"Not {BUILTIN[rival].label} ({rival}): {why}.")
    missing = [k for k in required_of(code) if entities.get(k) in (None, "", [], {})]
    if missing:
        parts.append(f"Still needs the {', '.join(_say(k) for k in missing)} - ask for it.")
    return json.dumps({"rationale": " ".join(parts), "intent_type": code, "entities": entities,
                       "confidence": 0.7 if missing else 0.95, "alternatives": []}, ensure_ascii=False)


def _polite(text: str, rnd: random.Random) -> str:
    before, after = rnd.choice(_POLITE)
    return f"{before}{text}{after}" if rnd.random() < 0.35 else text


def intents(per_template: int, seed: int = 11) -> Iterator[tuple[str, str]]:
    """(user text, reasoned answer) for every built-in intent, plus the vague and the out-of-scope ones."""
    from app.crm_core import training_data as td

    rnd = random.Random(seed)
    for code, templates in td._T + EXTRA_TEMPLATES:
        for n, (text_t, ent_t) in enumerate(templates):
            seen: set[str] = set()
            for _ in range(per_template):
                slots = td._fill(rnd)
                text = _polite(td._typo(text_t.format(**slots), rnd), rnd)
                if text in seen:
                    continue
                seen.add(text)
                yield text, reasoned_answer(code, td._render(ent_t, slots), seed=n + len(seen))
    for text in td.C360_INSTRUCTIONS:
        yield f"Configure 360: {text}", json.dumps({"rationale": "A change to the Customer 360 page - the whole text is the instruction.",
                                                    "intent_type": "INT-33", "entities": {"instruction": text}, "confidence": 0.95,
                                                    "alternatives": []}, ensure_ascii=False)
    # rare in the templates, so each comes in several wordings
    for text, code, entities, alternatives, confidence, why in AMBIGUOUS:
        for variant in dict.fromkeys([text, text.capitalize()] + [f"{b}{text}{a}" for b, a in _POLITE[1:6]]):
            yield variant, json.dumps({"rationale": why, "intent_type": code, "entities": entities, "confidence": confidence,
                                       "alternatives": alternatives}, ensure_ascii=False)
    for text, why in UNKNOWN:
        for variant in dict.fromkeys([text, text.capitalize(), f"{text}?", f"hey, {text}"]):
            yield variant, json.dumps({"rationale": f"{why} No action in the catalog fits.", "intent_type": "UNKNOWN", "entities": {},
                                       "confidence": 0.9, "alternatives": []}, ensure_ascii=False)


def planning_catalog() -> dict[str, dict[str, Any]]:
    """Every built-in action as the planning agent lists it (code -> label, description, fields)."""
    from app.intent_engine.intent_registry import BUILTIN

    return {code: {"label": m.label, "description": m.description,
                   "fields": [{"key": k, "required": k in required_of(code)} for k in m.fields]}
            for code, m in BUILTIN.items()}


def plans(per_goal: int, seed: int = 13) -> Iterator[dict[str, Any]]:
    """Goal -> ordered steps, in the planning agent's own prompt."""
    from app.crm_core import training_data as td
    from app.intent_engine.intent_registry import BUILTIN
    from app.orchestration import agent

    catalog = planning_catalog()
    rnd = random.Random(seed)
    for parts, steps in GOALS:
        seen: set[str] = set()
        for _ in range(per_goal):
            slots = td._fill(rnd)
            texts = [p.format(**slots) for p in parts]
            joiner = rnd.choice(_JOINERS)
            goal = _polite(td._typo(joiner.join(texts), rnd), rnd)
            if goal in seen:
                continue
            seen.add(goal)
            out = []
            for i, (text, (code, ent_t)) in enumerate(zip(texts, steps)):
                entities = td._render(ent_t, slots)
                why = f"{BUILTIN[code].label}" + (" - the record comes from an earlier step" if i and not any(
                    k.endswith("_id") for k in entities) and any(k.endswith("_id") for k in required_of(code)) else "") + "."
                out.append({"text": text, "intent_type": code, "entities": entities, "why": why})
            yield td._example(agent._PLAN_PROMPT, agent.plan_prompt(catalog, goal, None, tuple(s["intent_type"] for s in out)),
                              json.dumps({"steps": out}, ensure_ascii=False))
