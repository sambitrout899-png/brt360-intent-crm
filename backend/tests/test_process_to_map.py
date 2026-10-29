"""A process designed in the command bar goes live through the Functional Map:
staged into a draft version of the map in use (a new process, or added to
one), simulated with nothing kept, and built only when that version is
activated; retiring the version switches it off again. A BRD - on the
template or free text, Word or plain text - becomes the builder's request."""

import base64
import io
import zipfile

from test_process_builder import _settled

MAP = "/api/admin/function-map"
DESK = ("Quotes with discount above 20% need approval from the Regional Sales Head. "
        "When a quote is approved, notify the account manager that the quote is cleared.")


def _payload(client, request=DESK):
    steps, answers, bp = _settled(client, request)
    graphs = {c["id"]: c["spec"]["graph"] for c in bp["components"] if c["kind"] == "flow"}
    return {"request": request, "steps": steps, "answers": answers, "graphs": graphs, "name": "Deal desk"}


def _counts(client):
    return len(client.get("/api/admin/approval-rules").json()), len(client.get("/api/admin/flows").json())


def test_a_process_waits_in_a_draft_version_and_goes_live_only_when_it_is_activated(client):
    payload = _payload(client)
    before = _counts(client)
    staged = client.post("/api/process-builder/stage", json={**payload, "placement": {"mode": "new", "label": "Deal desk"}})
    assert staged.status_code == 200, staged.text
    staged = staged.json()
    vid = staged["version"]["id"]
    assert staged["version"]["status"] == "draft" and staged["version"]["based_on"] == "active map"
    assert [(b["name"], b["status"]) for b in staged["builds"]] == [("Deal desk", "pending")]
    assert any(p["label"] == "Deal desk" and p.get("tenant") for p in staged["processes"])
    assert _counts(client) == before  # nothing built yet
    assert not any(p["label"] == "Deal desk" for p in client.get(MAP).json()["processes"])

    sim = client.post(f"{MAP}/versions/{vid}/simulate").json()
    assert sim["builds"][0]["checks"] and sim["builds"][0]["ok"], sim
    assert _counts(client) == before  # the simulation kept nothing

    planned = client.post(f"{MAP}/versions/{vid}/plan").json()
    build = next(c for c in planned["changes"] if c["kind"] == "build")
    assert "Deal desk" in build["text"] and len(build["pieces"]) == 2

    out = client.post(f"{MAP}/versions/{vid}/activate").json()
    assert out["active_id"] == vid and out["built"][0]["name"] == "Deal desk"
    assert all(c["ok"] for c in out["built"][0]["checks"]), out["built"]
    rules, flows = _counts(client)
    assert (rules, flows) == (before[0] + 1, before[1] + 1)
    assert any(p["label"] == "Deal desk" for p in client.get(MAP).json()["processes"])
    made = {m["kind"]: m["id"] for m in out["built"][0]["made"]}

    # retired: its flow and approval rule switch off - kept, not deleted
    client.post(f"{MAP}/original/activate")
    assert not next(f for f in client.get("/api/admin/flows").json() if f["id"] == made["flow"])["is_active"]
    assert not next(r for r in client.get("/api/admin/approval-rules").json() if r["id"] == made["approval"])["is_active"]
    # activated again: back on, nothing built twice
    again = client.post(f"{MAP}/versions/{vid}/activate").json()
    assert again["built"] == [] and _counts(client) == (rules, flows)
    assert next(f for f in client.get("/api/admin/flows").json() if f["id"] == made["flow"])["is_active"]

    # a copy of the active version carries the process it built - activating the copy keeps it running
    copy = client.post(f"{MAP}/versions", json={"source": "active"}).json()["version"]["id"]
    client.post(f"{MAP}/versions/{copy}/activate")
    assert next(f for f in client.get("/api/admin/flows").json() if f["id"] == made["flow"])["is_active"]


def test_a_process_can_be_added_to_an_existing_process_of_the_map(client):
    l2q = next(p for p in client.get(MAP).json()["processes"] if p["key"] == "l2q")
    staged = client.post("/api/process-builder/stage", json={**_payload(client), "placement": {"mode": "append", "process_key": "l2q"}}).json()
    mine = next(p for p in staged["processes"] if p["key"] == "l2q")
    assert mine["label"] == l2q["label"] and mine["steps"][:len(l2q["steps"])] == l2q["steps"]
    assert staged["builds"][0]["placement"]["process_key"] == "l2q"
    # a second process goes into the same draft
    second = client.post("/api/process-builder/stage", json={**_payload(client), "version_id": staged["version"]["id"]}).json()
    assert len(second["builds"]) == 2
    assert client.post("/api/process-builder/stage", json={**_payload(client), "placement": {"mode": "append", "process_key": "nope"}}).status_code == 409


def test_a_process_with_open_questions_cannot_be_staged(client):
    out = client.post("/api/process-builder/stage", json={"request": "Leads need approval from the Chief Happiness Officer before they are qualified"})
    assert out.status_code == 409 and "open question" in out.json()["detail"]


def test_building_from_the_builder_goes_through_a_new_active_version(client):
    payload = _payload(client)
    sim = client.post("/api/process-builder/build", json={**payload, "mode": "simulate"}).json()
    assert sim["status"] == "simulated" and sim["checks"] and sim["impact"]
    before = _counts(client)
    assert _counts(client) == before
    done = client.post("/api/process-builder/build", json={**payload, "mode": "execute", "placement": {"mode": "new", "label": "Deal desk"}}).json()
    assert done["status"] == "built" and all(c["ok"] for c in done["checks"]), done
    listed = client.get(f"{MAP}/versions").json()
    assert listed["active_id"] == done["version"]["id"]
    assert _counts(client) == (before[0] + 1, before[1] + 1)


def _docx(paragraphs):
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>')
    return out.getvalue()


def _upload(client, filename, data):
    return client.post("/api/process-builder/brd", json={"filename": filename, "content_base64": base64.b64encode(data).decode()})


def test_the_brd_template_reads_back_into_the_builders_request(client):
    template = client.get("/api/process-builder/brd-template")
    assert template.status_code == 200 and template.content[:2] == b"PK"
    read = _upload(client, "BRT360-process-BRD-template.docx", template.content).json()
    assert read["template"] and read["name"] == "Enterprise deal desk"
    assert read["placement"] == {"mode": "append", "process_key": "b2b_sto"}  # "B2B Sales to Order" on the map
    assert "Enterprise deal desk: Qualified -> Commercial review -> Quote approved -> Proposal sent" in read["request"]
    assert "Quotes with discount above 15% need approval" in read["request"] and "Guidance" not in read["request"]

    own = _upload(client, "brd.docx", _docx(["Process name", "Field visit check", "Business function", "New process",
                                            "Approvals", "Orders need CPMO approval before they become activating"])).json()
    assert own["placement"] == {"mode": "new", "label": "Field visit check"}
    assert own["request"] == "Orders need CPMO approval before they become activating."


def test_any_other_document_goes_over_as_text_and_bad_files_are_refused(client):
    text = "We want large quotes approved by the finance controller and the account manager told once it is done."
    free = _upload(client, "notes.txt", text.encode()).json()
    assert not free["template"] and free["request"] == text
    assert _upload(client, "brd.docx", _docx(["Some intro", text])).json()["request"] == f"Some intro\n{text}"
    assert _upload(client, "brd.pdf", b"%PDF-1.7").status_code == 422
    assert _upload(client, "broken.docx", b"not a zip").status_code == 422
    assert client.post("/api/process-builder/brd", json={"filename": "a.txt", "content_base64": "@@@@"}).status_code == 422
