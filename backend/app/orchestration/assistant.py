"""Ask anything - one box for every request, routed to the platform's own pillars.

    text ──► understand() ──► "process" + fit   a business process described:     (process_fit: the closest vanilla
                         │                 only what vanilla lacks is built        process; data -> custom fields, rules
                         │                                                          -> Approvals / Flow Studio, new steps
                         │                                                          -> built from scratch - all on consent)
                         ├──► "action"   one intent, its form prefilled          (the intent pipeline)
                         ├──► "chain"    things to DO, in order                   (agent: plan → rehearse → run)
                         ├──► "process"  things to SET UP: approvals, flows,      (process builder: Flow Studio,
                         │               pages, custom fields, journeys            Page Designer, custom fields,
                         │                                                          approvals, assignment rules)
                         └──► "unknown"  said plainly, with what it can do instead

Order of the checks:
  1. memory - a verified process recipe or a chain that ran to the end, for
     this exact request (process_memory / chain_memory);
  2. configuration language - a journey, approval rules, "when ... then ...",
     page and field changes (the process builder's own rule readers);
  3. the agent's planner - one runnable action, or a chain of them;
  4. the process builder's AI rewrite for what is left.
Design requests (a page, a product's attributes, Customer 360) are actions of
their own (INT-103, INT-42, INT-33) and land in 3.

Every request is recorded in the journal (the audit store, channel
"assistant"): what was asked, how it was understood, where it went; the
outcome is appended as its own row when the user's run or build finishes -
log rows are never edited after completion.
"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.auth import User, UserRole
from app.models.intent_log import IntentExecutionLog

JOURNAL = "ASSISTANT"
OUTCOME = "ASSISTANT-OUTCOME"
ROUTES = ("answer", "action", "chain", "process", "unknown")
LABELS = {"answer": "A question", "action": "One action", "chain": "A chain of actions", "process": "A process to set up", "unknown": "Not understood yet"}

_QUESTION = re.compile(r"^\s*(?:how|what|where|which|why|who|is\s+there|are\s+there|can\s+(?:i|we|you)|could\s+(?:i|we|you)|do\s+(?:i|we|you)|does|"
                       r"help(?:\s+me)?|tell\s+me|show\s+me\s+how|explain)\b", re.I)
# the question's wrapper around the thing asked about
_WRAPPER = re.compile(r"^\s*(?:how\s+(?:can|do|should|would|could|to)\s*(?:i|we|you|one)?\s*|(?:can|could)\s+(?:i|we|you)\s+|is\s+(?:it|there\s+a\s+way)\s+"
                      r"(?:possible\s+)?to\s+|where\s+(?:can|do)\s+(?:i|we)\s+|what(?:'s|\s+is)\s+the\s+way\s+to\s+|help\s+(?:me\s+)?(?:to\s+)?|"
                      r"tell\s+me\s+how\s+(?:to|i\s+can)\s+|show\s+me\s+how\s+to\s+|explain\s+how\s+to\s+|(?:i\s+)?want\s+to\s+know\s+how\s+to\s+)", re.I)
_PLACE = re.compile(r"\s*\b(?:here|in\s+(?:this|the)\s+(?:app|application|system|crm|tool|screen)|in\s+brt\s*360|please)\b\s*", re.I)
# "I need to create a process which will create an order ..." - a process to set up, never the action it mentions
_DESIGN_ASK = re.compile(r"\b(?:create|build|set\s*up|design|define|make|need|want)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?(?:generic\s+)?"
                         r"(?:process|workflow|automation)\b", re.I)
_CAPABILITIES = re.compile(r"^\s*(?:what\s+(?:can|do)\s+you\s+do|what\s+are\s+you|help|how\s+does\s+this\s+work|what\s+can\s+i\s+(?:do|ask))\b", re.I)


def _is_admin(user: User) -> bool:
    return getattr(user, "role", None) == UserRole.admin


def understand(db: Session, tenant_id: int, user: User, text: str, context: dict | None = None, autonomous: bool = False) -> dict[str, Any]:
    from app.orchestration import agent

    with agent.reuse_plans():  # the agent and the process builder may plan the same goal - one model call
        return _understand(db, tenant_id, user, text, context, autonomous)


def _understand(db: Session, tenant_id: int, user: User, text: str, context: dict | None, autonomous: bool) -> dict[str, Any]:
    from app.orchestration import agent, chain_memory, process_builder, process_memory

    text = " ".join((text or "").split())
    out: dict[str, Any] = {"request": text}

    # 0. a question ("How can I add a B2C customer?") is answered - with the actions that do it
    if _QUESTION.match(text) or text.endswith("?"):
        answer = _answer(db, tenant_id, user, text)
        if answer is not None:
            out.update(answer)
            out["label"] = LABELS[out["route"]]
            out["journal_id"] = _record(db, tenant_id, user, text, out)
            return out

    # 1. memory
    recipe = process_memory.recall(db, tenant_id, text)
    if recipe is not None:
        out.update(route="process", why="You built this exact process before - reusing its verified recipe.", memory="process")
    elif chain_memory.recall(db, tenant_id, text) is not None and not context:
        out.update(route="chain", why="This exact request ran to the end before - reusing the plan that worked.", memory="chain")

    # 1b. a business process described in plain English: read against the vanilla processes - only the gaps get built
    from app.orchestration import process_fit

    fit = process_fit.read(db, tenant_id, user, text) if "route" not in out else None
    if fit is not None and not fit["journey"]:
        out.update(route="process", fit=fit, why=process_fit.summary(fit))

    # 1c. an automation along the sales road, reasoned out: what exists is recommended, the rest planned as flows
    from app.orchestration import reasoner

    goal = reasoner.understand(db, tenant_id, text) if "route" not in out else None
    if goal is not None:
        out.update(route="process", why=reasoner.summary(goal), goal=goal.as_dict())

    # 2. configuration language
    if "route" not in out:
        steps = process_builder.split(text)
        configured = [s for s in steps if s.get("kind") == "journey"
                      or (process_builder._recognised(s["text"]) and not process_builder._INTRO.match(s["text"]))]
        if configured:
            out.update(route="process", why="It sets something up - " + _what(configured))

    # 3. the agent's planner (one action, or a chain)
    plan = None
    if ("route" not in out or out["route"] == "chain") and not out.get("fit"):
        plan = agent.plan(db, tenant_id, user, text, context, prefer_ai=autonomous)
        runnable = [s for s in plan["steps"] if s.get("intent_type")]
        if out.get("route") == "chain":
            out["plan"] = plan
        elif len(runnable) >= 2 and len(runnable) >= len(plan["steps"]) - (1 if len(plan["steps"]) >= 4 else 0):
            out.update(route="chain", plan=plan, why=f"{len(runnable)} things to do, in order - each passes on what it creates.")
        elif len(runnable) == 1 and len(plan["steps"]) == 1 and not _DESIGN_ASK.search(text) and (
            _says(db, tenant_id, runnable[0]["intent_type"], text) or autonomous and plan.get("planner") == "ai"
        ):
            step = runnable[0]
            out.update(route="action", why=f"One action: {step['label']}.",
                       action={"intent_type": step["intent_type"], "entities": step["entities"], "text": step["text"] or text,
                               "label": step["label"], "icon": step.get("icon")})

    # 4. the process builder (its AI rewrite, its questions) for what is left
    if out.get("route") == "process" or "route" not in out:
        if not _is_admin(user):
            if out.get("route") == "process":
                out.update(needs_admin=True, why=out["why"] + " Setting up a process is for administrators - ask yours to build it.")
            else:
                out.update(route="unknown", needs_admin=True,
                           why="I couldn't match it to an action you can run. Try naming the action, e.g. “Create a quote for opportunity 12” - "
                               "or, if it's something new, ask an administrator: they can have me build it from scratch.")
        elif out.get("goal"):
            blueprint = process_builder.design(db, tenant_id, text, None, [{"kind": "goal", "text": text}], None, None, user)
            out["blueprint"] = blueprint
            if blueprint["recommendations"] and not blueprint["components"] and not blueprint["questions"]:
                out["why"] = "This already runs - nothing to build. " + " ".join(blueprint["recommendations"])
        elif out.get("fit"):
            # only what the vanilla process lacks goes to the builders (new steps: built from scratch, each on consent)
            if out["fit"]["build_steps"]:
                out["blueprint"] = process_builder.design(db, tenant_id, text, None, out["fit"]["build_steps"], None, None, user)
        else:
            blueprint = process_builder.design(db, tenant_id, text, None, None, None, None, user)
            if blueprint.get("mode") == "actions":
                out.update(route="chain", plan=blueprint["actions"], why="Things to do, in order - the agent carries them out.")
            elif blueprint["components"] or blueprint["engineering"] or any(not q["id"].endswith(".unclear") for q in blueprint["questions"]):
                out.setdefault("route", "process")
                out.setdefault("why", "It sets something up across the engines.")
                out["blueprint"] = blueprint
            else:
                out.update(route="unknown", blueprint=blueprint,
                           why="Nothing in your set-up does this yet - no action, chain, flow, rule or field covers it. I can build it from scratch "
                               "as a new action and switch it on, once you agree to what I'd make. Rephrase it if you meant something else: "
                               "“When <something happens>, <do this>”, "
                               "“<record> needs approval from <position>”, or a chain like “Add prospect X, then generate a lead for Fiber 500”.")
                # the new action is drafted when the panel asks (/scratch/draft) - drafting a flow is the
                # longest model call there is, and the answer shouldn't wait for it
                out["can_build"] = True

    if fit is not None and fit["journey"] and out.get("route") == "process":
        out["fit"] = fit  # for information: which vanilla process the journey's stages already run in
    out["label"] = LABELS[out["route"]]
    out["journal_id"] = _record(db, tenant_id, user, text, out)
    return out


# ---------------------------------------------------------------- questions


_SYNONYMS = {"b2c": "consu", "consumer": "consu", "retail": "consu", "individual": "consu", "b2b": "busin", "business": "busin",
             "deal": "oppor", "deals": "oppor", "client": "custo", "account": "custo", "bill": "invoi", "invoice": "invoi",
             "complaint": "ticke", "issue": "ticke", "fault": "ticke", "price": "quote", "proposal": "propo"}


def _concepts(text: str) -> set[str]:
    """Words as 5-letter stems, with a few telecom synonyms folded together (B2C customer = consumer)."""
    out = set()
    for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower()):
        if w in _STOP:
            continue
        out.add(w[:5])
        if w in _SYNONYMS:
            out.add(_SYNONYMS[w])
    return out


def _answer(db: Session, tenant_id: int, user: User, text: str) -> dict[str, Any] | None:
    """A question answered from the user's own action catalog: the actions that do what it asks about."""
    from app.intent_engine import classifier, intent_registry

    catalog = {e["code"]: e for e in intent_registry.intents_for_user(db, tenant_id, user)}
    if _CAPABILITIES.match(text):
        categories = sorted({e.get("category") for e in catalog.values() if e.get("category")})
        return {"route": "answer", "why": (
            f"You can ask me in plain English to do any of your {len(catalog)} actions ({', '.join(c for c in categories if c)}), "
            "to carry out several in a row (“Add prospect Globex, then generate a lead for Fiber 500, then approve the lead”), "
            "or - as an administrator - to set up a process: approvals, automations, message boxes, page fields and journeys "
            "(“Orders need CPMO approval before they become activating”). I record every request, and remember what worked."),
            "answers": []}
    core = _PLACE.sub(" ", _WRAPPER.sub("", text.rstrip("?! ."))).strip(" ,.?")
    if len(core) < 3:
        return None
    wanted = _concepts(core)
    scored: dict[str, float] = {}
    guess = classifier.classify(core, db, tenant_id, None)
    if guess.intent_type in catalog:
        scored[guess.intent_type] = 2.0
    for code, e in catalog.items():
        label, rest = _concepts(e.get("label", "")), _concepts(" ".join([e.get("description", ""), *(e.get("phrases") or [])]))
        score = 1.5 * len(wanted & label) + 0.5 * len(wanted & rest)
        if score:
            scored[code] = scored.get(code, 0) + score
    best = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)[:3]
    if not best or best[0][1] < 1.5:
        return None
    top = best[0][1]
    picks = [catalog[c] for c, s in best if s >= top * 0.6]
    first = picks[0]
    why = f"Use “{first['label']}” - {first.get('description', '').rstrip('.')}."
    if len(picks) > 1:
        why += " Also related: " + ", ".join(f"“{p['label']}”" for p in picks[1:]) + "."
    why += " I can open it for you now, with what you said filled in."
    entities = guess.entities if guess.intent_type == first["code"] else {}
    return {"route": "answer", "why": why, "answers": [
        {"intent_type": p["code"], "label": p["label"], "description": p.get("description", ""), "icon": p.get("icon"),
         "entities": {k: v for k, v in (entities if p is first else {}).items() if v not in (None, "", [], {})}, "text": core}
        for p in picks]}


_STOP = {"the", "and", "for", "with", "this", "that", "into", "from", "your", "their", "our", "new", "all", "any", "one", "make", "get"}


def _words(text: str) -> set[str]:
    import re

    return {w[:5] for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower()) if w not in _STOP}


def _says(db: Session, tenant_id: int, code: str, text: str) -> bool:
    """A request is one action only when it says that action - its name's words, or one of its phrases -
    not when a vague sentence was merely matched to the nearest action."""
    from app.intent_engine import intent_registry

    entry = intent_registry.entry(db, tenant_id, code) or {}
    if code.startswith("FLOW-"):  # a tenant flow: its phrase, or most of its name - one shared word ("prospect") is not enough
        from app.intent_engine import classifier

        return classifier.custom_fits(text, {"label": entry.get("label", ""), "phrases": [p.lower() for p in entry.get("phrases") or []]})
    said = _words(text)
    if _words(entry.get("label", "")) & said:
        return True
    lowered = " ".join(text.lower().split())
    return any(p and len(p) > 3 and p.lower() in lowered for p in entry.get("phrases") or [])


def _what(steps: list[dict[str, Any]]) -> str:
    kinds = []
    for s in steps:
        t = s["text"]
        if s.get("kind") == "journey":
            kinds.append("a journey of stages")
        elif process_builder_approval(t):
            kinds.append("an approval rule")
        elif "page" in t.lower() or "field" in t.lower():
            kinds.append("a page / field change")
        else:
            kinds.append("an automation")
    return ", ".join(dict.fromkeys(kinds)) + "."


def process_builder_approval(text: str) -> bool:
    from app.orchestration import process_builder

    return process_builder._APPROVAL_RULE(text)


# ---------------------------------------------------------------- the journal


def _record(db: Session, tenant_id: int, user: User, text: str, understood: dict[str, Any]) -> int:
    from app.data_intelligence import audit

    log = audit.start_execution(db, tenant_id, text[:4000], channel="assistant")
    log.intent_type = JOURNAL
    log.user_id = user.id
    summary = {"route": understood["route"], "why": understood.get("why"), "memory": understood.get("memory"),
               "action": (understood.get("action") or {}).get("intent_type"),
               "steps": [s.get("intent_type") for s in (understood.get("plan") or {}).get("steps", [])],
               "components": [c["kind"] for c in (understood.get("blueprint") or {}).get("components", [])],
               **({"vanilla": understood["fit"]["process"]["key"], "gaps": understood["fit"]["gaps"]} if understood.get("fit") else {})}
    audit.record_context(log, summary)
    audit.complete(log, "understood" if understood["route"] != "unknown" else "needs_review")
    db.commit()
    return log.id


def record_outcome(db: Session, tenant_id: int, user: User, journal_id: int, status: str, summary: str) -> None:
    """The outcome, appended as its own row (journal rows are never edited)."""
    from app.data_intelligence import audit

    entry = db.get(IntentExecutionLog, journal_id)
    if entry is None or entry.tenant_id != tenant_id or entry.intent_type != JOURNAL or entry.user_id != user.id:
        raise ValueError("Request not found in your journal")
    log = audit.start_execution(db, tenant_id, entry.raw_text, channel="assistant")
    log.intent_type = OUTCOME
    log.user_id = user.id
    audit.record_context(log, {"journal_id": journal_id, "status": status[:30], "summary": (summary or "")[:500]})
    audit.complete(log, "executed" if status in ("done", "built", "executed") else status[:20])
    db.commit()


def journal(db: Session, tenant_id: int, user: User, everyone: bool = False, limit: int = 50) -> list[dict[str, Any]]:
    query = db.query(IntentExecutionLog).filter(IntentExecutionLog.tenant_id == tenant_id, IntentExecutionLog.intent_type == JOURNAL)
    if not (everyone and _is_admin(user)):
        query = query.filter(IntentExecutionLog.user_id == user.id)
    rows = query.order_by(IntentExecutionLog.id.desc()).limit(limit).all()
    outcomes: dict[int, dict[str, Any]] = {}
    if rows:
        for o in (db.query(IntentExecutionLog).filter(IntentExecutionLog.tenant_id == tenant_id, IntentExecutionLog.intent_type == OUTCOME,
                                                       IntentExecutionLog.id > rows[-1].id).order_by(IntentExecutionLog.id)):
            try:
                data = json.loads(o.context_json or "{}")
            except ValueError:
                continue
            outcomes[int(data.get("journal_id") or 0)] = {"status": data.get("status"), "summary": data.get("summary"),
                                                         "at": o.created_at.isoformat(timespec="seconds")}
    names = {u.id: u.name for u in db.query(User).filter(User.id.in_({r.user_id for r in rows if r.user_id}))} if everyone else {}
    out = []
    for r in rows:
        try:
            data = json.loads(r.context_json or "{}")
        except ValueError:
            data = {}
        out.append({"id": r.id, "request": r.raw_text, "route": data.get("route"), "label": LABELS.get(data.get("route"), ""),
                    "why": data.get("why"), "memory": data.get("memory"), "at": r.created_at.isoformat(timespec="seconds"),
                    "outcome": outcomes.get(r.id), **({"by": names.get(r.user_id)} if everyone else {})})
    return out
