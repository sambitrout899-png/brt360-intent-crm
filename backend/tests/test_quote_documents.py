"""The B2B Sales-to-Order documents, as seeded in the vanilla process:
discount approved -> Generate proposal (a PDF on the quote, INT-109) -> send it
(INT-22) -> the customer's PO, its PDF uploaded on the quote (INT-39, which
accepts the proposal) -> Sales Ops Head: Match all (INT-40) -> the order and
the agreement PDF on the quote. Every PDF is listed on the quote itself, and
the proposal / agreement wording is the tenant's to change or switch off."""

import base64

from test_sales_to_order import _approved_quote, _feasible_opportunity, _launch, run, say

PDF = base64.b64encode(b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF").decode()


def _buttons(client, quote_id):
    return [(a["intent"], a["label"]) for a in client.get(f"/api/records/quote/{quote_id}").json()["actions"]]


def test_proposal_po_match_all_and_agreement_on_the_quote(client):
    _launch(client)
    quote_id = _approved_quote(client, "Docs Co")

    # approved: the proposal comes first, then sending it, then the PO
    assert [i for i, _ in _buttons(client, quote_id) if i in ("INT-109", "INT-22", "INT-39")] == ["INT-109", "INT-22", "INT-39"]
    # the form shows the proposal as the template writes it for this quote - nothing saved yet
    preview = client.get(f"/api/quotes/{quote_id}/documents/preview/proposal")
    assert preview.status_code == 200 and preview.content.startswith(b"%PDF") and b"Docs Co" in preview.content
    assert client.get(f"/api/quotes/{quote_id}/documents").json() == []
    made = run(client, "INT-109", quote_id=quote_id, notes="first cut")
    assert made["status"] == "executed", made["rationale"]
    doc_id = made["result"]["document_id"]
    pdf = client.get(f"/api/quotes/{quote_id}/documents/{doc_id}/file")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and pdf.headers["content-type"] == "application/pdf"
    assert b"Docs Co" in pdf.content and b"Commercial proposal" in pdf.content
    assert ("INT-22", "Send proposal to customer") in _buttons(client, quote_id)

    sent = run(client, "INT-22", quote_id=quote_id)
    assert sent["result"]["document_id"] == doc_id  # the generated PDF is the one sent - not a second one
    assert {i for i, _ in _buttons(client, quote_id)} >= {"INT-39", "INT-23"}

    # the customer's PO PDF, uploaded on the quote, then INT-39 links it and accepts the proposal
    bad = client.post(f"/api/quotes/{quote_id}/documents", json={"kind": "purchase_order", "file_name": "po.txt",
                                                                  "content_base64": base64.b64encode(b"hello").decode()})
    assert bad.status_code == 400 and "PDF" in bad.text
    up = client.post(f"/api/quotes/{quote_id}/documents", json={"kind": "purchase_order", "file_name": "PO-77.pdf", "content_base64": PDF})
    assert up.status_code == 200, up.text
    total = client.get(f"/api/quotes/{quote_id}").json()["total_price"]
    po = run(client, "INT-39", quote_id=quote_id, po_number="PO-77", po_amount=total)
    assert po["result"]["proposal_accepted"] is True and po["result"]["po_document_id"] == up.json()["id"]
    quote = client.get(f"/api/quotes/{quote_id}").json()
    assert quote["purchase_order"]["document_ref"] == up.json()["ref"] and quote["proposals"][0]["status"] == "customer_approved"

    matched = run(client, "INT-40", quote_id=quote_id)
    assert matched["status"] == "executed", matched["rationale"]
    assert matched["result"]["order_id"] and matched["result"]["agreement_document_id"]
    quote = client.get(f"/api/quotes/{quote_id}").json()
    assert quote["order_id"] == matched["result"]["order_id"]
    kinds = sorted(d["kind"] for d in quote["documents"])
    assert kinds == ["agreement", "proposal", "purchase_order"]
    agreement = next(d for d in quote["documents"] if d["kind"] == "agreement")
    body = client.get(f"/api/quotes/{quote_id}/documents/{agreement['id']}/file?download=true")
    assert b"Service agreement" in body.content and b"PO-77" in body.content and "attachment" in body.headers["content-disposition"]


def test_the_tenant_writes_or_switches_off_the_documents(client):
    _launch(client)
    templates = client.get("/api/admin/quote-document-templates").json()
    assert {t["kind"] for t in templates["templates"]} == {"proposal", "agreement"} and "customer" in templates["placeholders"]
    saved = client.put("/api/admin/quote-document-templates/proposal",
                       json={"title": "Offer for {customer}", "terms": "Payment within 15 days."}).json()
    assert saved["customized"] and saved["title"] == "Offer for {customer}"
    quote_id = _approved_quote(client, "Wording Ltd")
    preview = client.get(f"/api/admin/quote-document-templates/proposal/preview?quote_id={quote_id}")
    assert preview.status_code == 200 and b"Offer for Wording Ltd" in preview.content and b"Payment within 15 days." in preview.content

    # proposal switched off: approval goes straight to the PO (the original buttons)
    client.put("/api/admin/quote-document-templates/proposal", json={"enabled": False})
    assert "INT-109" not in {i for i, _ in _buttons(client, quote_id)}
    # agreement switched off: Match all orders without one
    client.put("/api/admin/quote-document-templates/agreement", json={"enabled": False})
    total = client.get(f"/api/quotes/{quote_id}").json()["total_price"]
    run(client, "INT-39", quote_id=quote_id, po_number="PO-9", po_amount=total)
    matched = run(client, "INT-40", quote_id=quote_id)
    assert matched["status"] == "executed" and "agreement_document_id" not in matched["result"]
    reset = client.put("/api/admin/quote-document-templates/proposal", json={"reset": True}).json()
    assert reset["enabled"] and reset["title"] == "Commercial proposal"


def test_proposal_is_recognised_and_offered_after_approval(client):
    _launch(client)
    quote_id = _approved_quote(client, "Words Co")
    understood = say(client, f"Prepare the proposal for quote {quote_id}")
    assert understood["intent_type"] == "INT-109" and understood["status"] == "executed", understood
    assert say(client, f"Generate proposal for this quote. quote_id: {quote_id}")["intent_type"] == "INT-22"  # unchanged

    draft = run(client, "INT-05", opportunity_id=_feasible_opportunity(client, "Next Co"), offering_name="Fiber 900")["result"]["quote_id"]
    assert run(client, "INT-109", quote_id=draft)["status"] != "executed"  # not before the discount is approved
    approved = run(client, "INT-06", quote_id=draft, decision="approve")
    assert approved["next_actions"][0]["code"] == "INT-109", approved["next_actions"]
