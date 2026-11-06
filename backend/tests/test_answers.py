"""Ask anything answers questions: the permutations a telecom operator's users ask - every record type x phrasing x
period, every KPI said several ways, predictions, records by number and by name, rankings, telecom terms, my work -
answered from the tenant's data with what the user may see; instructions, how-to questions and automations are
left to the action catalog and the builders. Without an AI model (the rules read everything here)."""

import datetime as dt
import itertools
import re

import pytest

from app.analytics import answers
from app.models.assurance import TroubleTicket
from app.models.auth import User, UserRole
from app.models.crm import Party, PartyProfile
from app.models.orders import Order
from tests.test_analytics import _seed

NOW = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


@pytest.fixture()
def world(client):
    _seed(client)
    with client.TestSessionLocal() as db:
        tid = db.query(Party).first().tenant_id
        # prospects: three today, two last week; and one paying account
        for i in range(3):
            p = Party(tenant_id=tid, name=f"Today Prospect {i}", created_at=NOW - dt.timedelta(minutes=5 + i))
            db.add(p)
            db.flush()
            db.add(PartyProfile(party_id=p.id, segment="B2C", account_status="prospect"))
        for i in range(2):
            p = Party(tenant_id=tid, name=f"Older Prospect {i}", created_at=NOW - dt.timedelta(days=9 + i))
            db.add(p)
            db.flush()
            db.add(PartyProfile(party_id=p.id, segment="B2B", account_status="prospect"))
        acme = Party(tenant_id=tid, name="Acme Fibre Holdings", created_at=NOW - dt.timedelta(days=400))
        db.add(acme)
        db.flush()
        db.add(PartyProfile(party_id=acme.id, segment="B2B", account_status="account"))
        db.add(TroubleTicket(tenant_id=tid, party_id=acme.id, category="Connectivity", severity="critical", status="open", created_at=NOW - dt.timedelta(hours=2)))
        db.commit()
        user = db.query(User).filter(User.tenant_id == tid, User.role == UserRole.admin).first()
        ticket = db.query(TroubleTicket).filter(TroubleTicket.party_id == acme.id).one()
        order = db.query(Order).filter(Order.tenant_id == tid).first()
        return {"tid": tid, "user_id": user.id, "acme": acme.id, "ticket": ticket.id, "order": order.id}


def _ask(client, w, text):
    with client.TestSessionLocal() as db:
        user = db.get(User, w["user_id"])
        return answers.answer(db, w["tid"], user, text)


def _ok(out, text, kinds=None):
    assert out is not None, f"not answered: {text!r}"
    ins = out["insight"]
    assert out["route"] == "answer" and ins["answer"] and len(ins["answer"]) > 8, (text, ins)
    assert not re.search(r"\bNone\b|\bnan\b|\{|\}", ins["answer"]), (text, ins["answer"])
    if kinds:
        assert ins["kind"] in kinds, (text, ins["kind"], ins["answer"])
    return ins


# ---------------------------------------------------------------- the permutations

ENTITIES = ["prospects", "customers", "leads", "opportunities", "deals", "quotes", "orders", "tickets", "complaints", "invoices", "payments",
            "work orders", "open tickets", "critical tickets", "won deals", "overdue invoices", "live orders", "B2B customers", "B2C orders"]
PERIODS = ["today", "yesterday", "this week", "last week", "this month", "last month", "this quarter", "last quarter", "this year",
           "last year", "in the last 30 days", "in the last 7 days", ""]
TEMPLATES = ["How many {e} were created {p}?", "how many {e} {p}", "number of {e} {p}", "count of {e} {p}", "show me {e} {p}",
             "list {e} {p}", "which {e} were created {p}?", "{e} by month {p}", "{e} by status {p}", "total {e} {p}"]


def _all(client, w, questions, kinds):
    """Every question asked; the failures listed together."""
    failed = []
    for text in questions:
        try:
            _ok(_ask(client, w, text), text, kinds)
        except AssertionError as exc:
            failed.append(f"{text!r}: {str(exc).splitlines()[0][:200]}")
    assert not failed, f"{len(failed)} of {len(questions)} failed:\n" + "\n".join(failed[:60])


def test_every_record_type_phrasing_and_period_is_answered_from_the_data(client, world):
    questions = [" ".join(t.format(e=e, p=p).split()) for e, p, t in itertools.product(ENTITIES, PERIODS, TEMPLATES)]
    assert len(questions) == 2470
    _all(client, world, questions, {"data", "kpi"})


def test_the_numbers_are_right(client, world):
    today = _ok(_ask(client, world, "How many prospects are created today?"), "today", {"data"})
    assert today["value"]["display"] == "3" and today["answer"].startswith("3 prospects were created today")
    assert _ask(client, world, "how many prospects were created this month")["insight"]["value"]["display"] in ("3", "5")  # month may hold last week's
    assert _ask(client, world, "how many prospects do we have")["insight"]["value"]["display"] == "5"
    listed = _ask(client, world, "which prospects were created today?")["insight"]
    assert {r["name"] for r in listed["table"]["rows"]} == {"Today Prospect 0", "Today Prospect 1", "Today Prospect 2"}
    with client.TestSessionLocal() as db:
        open_tickets = db.query(TroubleTicket).filter(TroubleTicket.tenant_id == world["tid"], TroubleTicket.status.notin_(["resolved", "closed", "cancelled"])).count()
    assert _ask(client, world, "how many open tickets are there?")["insight"]["value"]["display"] == str(open_tickets)
    # spelling slips are read through
    assert _ask(client, world, "how many prospcts creted todya")["insight"]["value"]["display"] == "3"


KPIS = ["MRR", "monthly recurring revenue", "ARR", "ARPU", "average revenue per user", "churn rate", "customer churn", "win rate",
        "pipeline", "average deal size", "sales cycle", "order backlog", "time to activate", "order fallout", "MTTR", "mean time to repair",
        "SLA breaches", "CSAT", "customer satisfaction", "NPS", "DSO", "collection rate", "overdue receivables", "data usage",
        "voice minutes", "net adds", "lead conversion", "open fraud alerts"]
KPI_TEMPLATES = ["What is our {k}?", "what's the {k}", "{k}?", "how is our {k}", "current {k}", "show me our {k}", "tell me our {k} please"]


def test_every_kpi_said_any_way_comes_from_the_scorecard(client, world):
    questions = [t.format(k=k) for k, t in itertools.product(KPIS, KPI_TEMPLATES)]
    _all(client, world, questions, {"kpi", "knowledge"})
    no_value = [t for t in questions if not (_ask(client, world, t)["insight"].get("value") or {}).get("display")]
    assert not no_value, no_value


PREDICTIONS = [
    ("Which customers are at risk of churning?", "Customers at risk"), ("who is likely to churn", "Customers at risk"),
    ("show me the churn risk list", "Customers at risk"), ("which B2B customers might leave us", "Customers at risk"),
    ("top 5 customers at risk", "Customers at risk"), ("Which deals are likely to win?", "Deals most likely"),
    ("what is our weighted pipeline", "Deals most likely"), ("which opportunities should I focus on", "Deals most likely"),
    ("what should we upsell", "Next best"), ("next best offer for my customers", "Next best"), ("cross-sell opportunities", "Next best"),
    ("any unusual usage this week?", "Unusual usage"), ("bill shock alerts", "Unusual usage"), ("customer segmentation", "Customer segments"),
    ("Forecast revenue for next quarter", "Forecast"), ("predict tickets next month", "Forecast"), ("what will churn look like next quarter", "Forecast"),
]


def test_predictions(client, world):
    for text, title in PREDICTIONS:
        ins = _ok(_ask(client, world, text), text, {"prediction"})
        assert ins["title"].startswith(title), (text, ins["title"])
    risk = _ask(client, world, "Which customers are at risk of churning?")["insight"]
    assert risk["table"] and risk["records"] and risk["value"]["display"].startswith("₹")


def test_records_by_number_and_by_name(client, world):
    for text in (f"Tell me about order {world['order']}", f"status of order #{world['order']}", f"order no. {world['order']}",
                 f"what's happening with ticket {world['ticket']}?", f"ticket #{world['ticket']}"):
        ins = _ok(_ask(client, world, text), text, {"record"})
        assert ins["records"] and ins["records"][0]["id"] in (world["order"], world["ticket"])
    assert "can't find" in _ask(client, world, "order 99999999")["insight"]["answer"]
    acme = _ok(_ask(client, world, "Tell me about Acme Fibre Holdings"), "acme", {"record"})
    assert "Acme Fibre Holdings is a B2B account" in acme["answer"] and "1 open ticket" in acme["answer"]
    tickets = _ok(_ask(client, world, "open tickets of Acme Fibre Holdings"), "acme tickets", {"record", "data"})
    assert tickets["table"] and tickets["table"]["rows"]
    _ok(_ask(client, world, f"tell me about customer {world['acme']}"), "customer by id", {"record"})


RANKINGS = ["Top 10 customers by monthly revenue", "top 5 customers by revenue", "which customers have the most tickets",
            "which customers owe the most", "top 5 customers by outstanding amount", "bottom 5 customers by revenue",
            "top 3 products by sales", "which products sell the most", "top sources by leads", "which owners have the most leads",
            "top 10 customers by data usage", "best 5 customers by won deals", "top categories by tickets", "top 5 B2B customers by revenue"]


def test_rankings(client, world):
    for text in RANKINGS:
        ins = _ok(_ask(client, world, text), text, {"data"})
        assert ins["table"] and ins["table"]["rows"], text
        assert ins["table"]["columns"][0]["key"] == "rank", text
    top = _ask(client, world, "Top 10 customers by monthly revenue")["insight"]["table"]["rows"]
    values = [r[next(k for k in r if k.startswith("sum("))] for r in top]
    assert values == sorted(values, reverse=True)


TERMS = ["ARPU", "MRR", "churn", "NPS", "CSAT", "MTTR", "SLA", "DSO", "FTTH", "GPON", "MPLS", "SD-WAN", "SIP trunk", "VoLTE", "5G", "eSIM",
         "MVNO", "number porting", "KYC", "BSS", "eTOM", "CPE", "jitter", "CDR", "dunning", "postpaid", "roaming", "MSISDN", "APN",
         "leased line", "dark fibre", "CLV", "CAC", "TCV", "order fallout", "lead to cash"]


def test_telecom_knowledge(client, world):
    questions = [t.format(t=term) for term, t in itertools.product(TERMS, ["What is {t}?", "explain {t}", "define {t}", "what does {t} mean",
                                                                             "meaning of {t}"])]
    _all(client, world, questions, {"knowledge"})
    arpu = _ask(client, world, "explain ARPU")["insight"]
    assert "Yours is ₹" in arpu["answer"]  # the definition, and the value here


def test_my_work_and_greetings(client, world):
    for text in ("What is waiting for my approval?", "my pending approvals", "what's on my plate today", "what should I work on today?",
                 "show my tasks"):
        _ok(_ask(client, world, text), text, {"mywork"})
    for text in ("hi", "Hello!", "thanks"):
        _ok(_ask(client, world, text), text, {"hello"})


NOT_QUESTIONS = [
    # instructions - one action, or a chain
    "Add a prospect called Acme", "Create a quote for opportunity 12", "Raise a ticket for customer 5", "cancel order 59", "approve quote 3",
    "Add prospect Globex, then generate a lead for Fiber 500", "Generate a lead for Fiber 500", "Assign account 12 to Priya",
    # how-to - the action catalog answers with the actions that do it
    "How do I add a B2C customer?", "how can I raise a ticket", "How to create a quote?", "what are the steps to cancel an order",
    "Can I change an installed asset?",
    # configuration - the process builder
    "When an order is created, email the customer", "Quotes with discount above 15% need approval from the CFO",
    "Orders must be approved by CPMO", "Whenever a ticket is critical, notify the NOC manager",
    "Build a B2C process - customer -> identity verification -> raise order -> installation -> service activation",
    "On the quote page add a mandatory field Discount reason",
    "Orders need CPMO approval before they become activating. When an order becomes active, notify the account manager",
    "I want to add a new B2C process - customer->identify verification->raise order ->installation->service activation",
    "We need to send a welcome SMS to every new customer", "I'd like to see a new field on the lead page",
]


def test_instructions_how_tos_and_automations_are_left_to_the_builders(client, world):
    for text in NOT_QUESTIONS:
        assert _ask(client, world, text) is None, text


def test_ask_anything_answers_with_the_insight_and_the_actions_on_it(client, world):
    out = client.post("/api/assistant/understand", json={"text": "How many prospects are created today?"}).json()
    assert out["route"] == "answer" and out["label"] == "A question"
    assert out["insight"]["kind"] == "data" and out["insight"]["value"]["display"] == "3"
    assert out["why"].startswith("3 prospects were created today")
    assert any("prospect" in a["label"].lower() for a in out["answers"])  # "Add a prospect", beside the count
    assert out["insight"]["follow_ups"]
    # the how-to still gets the action catalog's answer, an instruction still opens the action
    how = client.post("/api/assistant/understand", json={"text": "How can I add a B2C customer?"}).json()
    assert how["route"] == "answer" and not how.get("insight") and how["answers"]
    act = client.post("/api/assistant/understand", json={"text": "Add prospect Globex Ltd"}).json()
    assert act["route"] in ("action", "chain") and not act.get("insight")
    journal = client.get("/api/assistant/journal").json()
    assert journal[0]["request"] in ("Add prospect Globex Ltd", "How can I add a B2C customer?")


def test_what_the_readers_cant_place_goes_to_the_ai_model_and_its_query_is_run_here(client, world, monkeypatch):
    """With an AI model: an open question it turns into a query of the data (checked, run with the user's access, then phrased),
    or answers from expertise; a query that doesn't hold is dropped, never run."""
    from app.crm_core import llm

    question = "Is our consumer base growing faster than business?"
    assert _ask(client, world, question) is None  # no model: honestly not answered here (the assistant falls back)
    calls = []

    def fake(target, system, user, **kw):
        calls.append(system[:30])
        if "1-3 plain" in system:  # the phrasing step sees the computed result
            assert "B2C" in user or "B2B" in user
            return {"answer": "Consumer customers grew faster than business ones this year.", "follow_ups": ["Customers by segment by month"]}
        return {"kind": "query", "query": {"entity": "party", "dimensions": [{"field": "profile.segment"}], "measures": [{"agg": "count"}],
                                           "filters": [{"field": "created_at", "op": "this_year"}]}}

    monkeypatch.setattr(llm, "resolve", lambda *a, **k: llm.local_target("test"))
    monkeypatch.setattr(llm, "chat_json", fake)
    ins = _ok(_ask(client, world, question), question, {"data"})
    assert ins["answer"].startswith("Consumer customers grew") and ins["table"]["rows"] and ins["source"] != "Built-in analyst"
    assert len(calls) == 2
    # expertise, no data needed
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"kind": "answer", "answer": "Bundle fibre with mobile and offer a loyalty discount at renewal."})
    advice = _ok(_ask(client, world, "How do telecoms usually reduce churn in the consumer segment?"), "advice", {"ai"})
    assert "Bundle" in advice["answer"]
    # a query of fields that don't exist is not run
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"kind": "query", "query": {"entity": "party", "dimensions": [{"field": "no_such"}]}})
    assert _ask(client, world, question) is None


def test_answers_respect_what_the_user_may_see(client, world):
    """A user who sees only their own records counts only those."""
    from app.crm_core import access_service

    with client.TestSessionLocal() as db:
        user = db.get(User, world["user_id"])
        real = access_service.resolve(db, user)
        narrow = access_service.Access(user=user, tenant_id=world["tid"], pages=real.pages, roles=real.roles, positions=real.positions,
                                       active_position=None, max_scope="own", scope="own", visibility=access_service.Visibility(user_ids={-1}))
        ctx = answers._Ctx(db, world["tid"], user, "how many orders", "how many orders", narrow)
        ins = answers._data(ctx)
        assert ins["value"]["display"] == "0"
