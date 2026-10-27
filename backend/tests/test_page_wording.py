"""A page change said the way people say it - "make the Email field mandatory
for any B2C Prospect" - is a Page Designer change on the page that record and
segment name, never a question nobody can answer or an "engineering" request."""

from app.crm_core import page_service
from app.orchestration import process_builder

SAID = "make the Email field mandatory for any B2C Prospect"


def _design(client, text):
    db = client.TestSessionLocal()
    try:
        return process_builder.design(db, 1, text, None, None, None, None, None)
    finally:
        db.close()


def _mandatory(client, entity, segment, key):
    db = client.TestSessionLocal()
    try:
        layout = page_service.page(db, 1, entity, segment)["layout"]
        return any(f.get("key") == key and f.get("mandatory") for s in layout["sections"] for f in s["fields"])
    finally:
        db.close()


def test_the_record_and_segment_named_pick_the_page(client):
    bp = _design(client, SAID)
    assert not bp["questions"] and not bp["engineering"]
    page = bp["components"][0]
    assert page["kind"] == "page" and page["spec"]["page_key"] == "party.B2C" and page["lines"] == ["Make Email mandatory"]
    b2b = _design(client, "make phone mandatory for business prospects")["components"][0]
    assert b2b["spec"]["page_key"] == "party.B2B" and b2b["lines"] == ["Make Phone mandatory"]
    quote = _design(client, "make the discount justification field mandatory on every quote")
    assert [c["spec"]["page_key"] for c in quote["components"]] == ["quote"]


def test_a_record_with_two_pages_and_no_segment_asks_between_just_those(client):
    asked = _design(client, "make email mandatory for prospects")["questions"]
    assert [q["id"] for q in asked] == ["c1.page"]
    assert [o["value"] for o in asked[0]["options"]] == ["party.B2B", "party.B2C"]


def test_said_in_ask_anything_and_built_on_consent(client):
    out = client.post("/api/assistant/understand", json={"text": SAID}).json()
    assert out["route"] == "process" and not out["blueprint"]["engineering"] and not out["blueprint"]["questions"]
    assert not _mandatory(client, "party", "B2C", "email")
    built = client.post("/api/process-builder/build", json={"request": SAID, "steps": out["blueprint"]["steps"], "answers": {}, "graphs": {},
                                                             "mode": "execute"})
    assert built.status_code == 200, built.text
    assert _mandatory(client, "party", "B2C", "email") and not _mandatory(client, "party", "B2B", "email")  # only the B2C page
