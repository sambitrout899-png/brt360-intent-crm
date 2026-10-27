"""A request to design a process - "I need to create a process which will create an order automatically
from the prospect ..." - is never read as information to capture (a field "To create a process") nor run
as the single action it mentions (Create the order)."""

from app.orchestration import process_fit

ASKED = ("I need to create a process which will create an order automatically from the prospect for a prospect with Company Type "
         "for a B2B scenario for any product")


def test_a_process_request_is_not_a_field():
    assert process_fit.read_data(ASKED) is None
    assert process_fit._split_fields("to create a process, GST number") == ["GST number"]


def test_a_process_request_is_not_run_as_one_action(client):
    out = client.post("/api/assistant/understand", json={"text": ASKED}).json()
    assert out["route"] != "action", out
    fields = [f["label"] for i in (out.get("fit") or {}).get("items", []) for f in i.get("fields") or []]
    assert not any(f.lower().startswith("to ") for f in fields), fields


def test_capturing_information_still_reads_as_fields():
    said = process_fit.read_data("We need to capture the GST number and PAN for every B2B prospect")
    assert said and said["fields"] == ["GST number", "PAN"] and said["entity_type"] == "party"
