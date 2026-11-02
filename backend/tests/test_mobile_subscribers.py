"""Mobile subscribers (INT-129..148, crm_core/comm_mobile.py): prepaid and postpaid lines for
operators, MVNOs and MVNEs - activation, recharge and vouchers, packs and online charging,
emergency credit and transfers, postpaid limits and deposits, migration, SIM swap, number
change, barring, roaming, shared plans, termination and quarantine, wholesale partners and
settlements - each refusing what the line's state or the policy doesn't allow."""

import datetime as dt

from app.crm_core import comm_billing as cb, comm_mobile as mb
from app.models.mobile import MbLine


def run(client, code, **entities):
    return client.post("/api/intents", json={"text": "", "structured": {"intent_type": code, "entities": entities}}).json()


def ok(body):
    assert body["status"] == "executed", {k: body.get(k) for k in ("intent_type", "status", "error", "result", "missing_fields", "rationale")}
    return body["result"]


def refused(body, words):
    assert body["status"] != "executed", body
    assert words.lower() in str(body).lower(), body
    return body


def customer(client, name):
    return ok(run(client, "INT-41", name=name))["party_id"]


def line(client, msisdn):
    db = client.TestSessionLocal()
    try:
        return mb.line_out(db, db.query(MbLine).filter(MbLine.msisdn == msisdn).one(), full=True)
    finally:
        db.close()


def age(client, msisdn, days):
    db = client.TestSessionLocal()
    try:
        row = db.query(MbLine).filter(MbLine.msisdn == msisdn).one()
        row.activated_at -= dt.timedelta(days=days)
        db.commit()
    finally:
        db.close()


def owed(client, party_id):
    db = client.TestSessionLocal()
    try:
        return cb.balance(db, 1, party_id)
    finally:
        db.close()


def test_prepaid_lifecycle(client):
    party = customer(client, "Priya Prepaid")
    refused(run(client, "INT-136", party_id=party, plan="PRE_SMART", initial_recharge=100), "at least")
    made = ok(run(client, "INT-136", party_id=party, plan="PRE_SMART", initial_recharge=400, sim_type="esim"))
    msisdn = made["msisdn"]
    assert made["payment_type"] == "prepaid" and made["esim_activation_codes"][msisdn].startswith("LPA:1$")
    view = line(client, msisdn)
    assert view["balance"] == 101 and view["remaining"] == {"voice": 1000, "data": 30720, "sms": 300} and view["valid_until"]

    # usage inside the plan's allowance costs nothing; international never comes out of a pack
    call = ok(run(client, "INT-144", msisdn=msisdn, event_type="voice", quantity=10))
    assert call["granted"] and call["charged"] == 0 and call["from_packs"] == {"PRE_SMART": 10}
    intl = ok(run(client, "INT-144", msisdn=msisdn, event_type="voice", quantity=5, international=True))
    assert intl["charged"] == 10.0 and intl["balance"] == 91  # 5 min x 0.5 x 4
    roam = ok(run(client, "INT-144", msisdn=msisdn, event_type="data", quantity=10, roaming=True))
    assert roam["granted"] is False and "roaming is off" in roam["reason"]

    # recharge by amount, limits checked; then a pack from the wallet, and removed again
    refused(run(client, "INT-129", msisdn=msisdn, amount=5), "a recharge is")
    top = ok(run(client, "INT-129", msisdn=msisdn, amount=500, payment_method="upi"))
    assert top["recharged"] and top["balance"] == 591 and top["method"] == "upi"
    pack = ok(run(client, "INT-130", msisdn=msisdn, pack="DATA10"))
    assert pack["paid_from"] == "balance" and pack["balance"] == 392
    assert ok(run(client, "INT-130", msisdn=msisdn, pack="DATA10", action="remove"))["removed"] == 1

    # emergency credit: only on a low balance and after the minimum time on the network
    second = ok(run(client, "INT-136", party_id=party, plan="PRE_BASIC", sim_type="esim"))["msisdn"]
    refused(run(client, "INT-130", msisdn=second, pack="DATA1"), "recharge first")
    refused(run(client, "INT-133", msisdn=second), "on the network")
    age(client, second, 40)
    credit = ok(run(client, "INT-133", msisdn=second))
    assert credit["credited"] == 10 and credit["owed"] == 12
    refused(run(client, "INT-133", msisdn=second), "still owes")
    refused(run(client, "INT-132", msisdn=second, to_msisdn=msisdn, amount=5), "emergency credit")
    back = ok(run(client, "INT-129", msisdn=second, amount=20, payment_method="cash"))
    assert back["recovered"] == 12 and back["balance"] == 18

    # balance transfer, with the fee
    sent = ok(run(client, "INT-132", msisdn=msisdn, to_msisdn=second, amount=50))
    assert sent["fee"] == 1 and sent["to_balance"] == 68
    refused(run(client, "INT-132", msisdn=msisdn, to_msisdn=second, amount=5000), "at most")

    # out of money: refused with nothing taken; spent to zero, it is barred for no balance until recharged
    refuse = ok(run(client, "INT-144", msisdn=second, event_type="voice", quantity=60, international=True))
    assert refuse["granted"] is False and "insufficient balance" in refuse["reason"] and refuse["balance"] == 68
    spent = ok(run(client, "INT-144", msisdn=second, event_type="voice", quantity=136))
    assert spent["granted"] and spent["charged"] == 68 and spent["balance"] == 0
    assert "no_balance" in line(client, second)["barrings"]
    # (what a pack covers still goes through - only the money is missing)
    assert "insufficient balance" in ok(run(client, "INT-144", msisdn=second, event_type="sms", quantity=1))["reason"]
    ok(run(client, "INT-129", msisdn=second, amount=10, payment_method="cash"))
    assert "no_balance" not in line(client, second)["barrings"]

    check = ok(run(client, "INT-131", msisdn=msisdn))
    assert check["msisdn"] == msisdn and "prepaid" in check["summary"]


def test_vouchers_once_and_wrong_pins(client):
    party = customer(client, "Vikram Voucher")
    msisdn = ok(run(client, "INT-136", party_id=party, plan="PRE_BASIC", sim_type="esim"))["msisdn"]
    made = ok(run(client, "INT-145", count=3, face_value=100, validity_days=45))
    assert made["count"] == 3 and "pending_pins" not in made
    csv = client.get(made["download"])
    assert csv.status_code == 200 and csv.text.startswith("serial,pin"), csv.text
    pins = [row.split(",")[1] for row in csv.text.strip().splitlines()[1:]]
    assert len(pins) == 3 and client.get(made["download"]).status_code == 400  # handed out once

    wrong = ok(run(client, "INT-129", msisdn=msisdn, voucher_pin="99999999999999"))
    assert wrong["recharged"] is False and "not a voucher" in wrong["reason"]
    good = ok(run(client, "INT-129", msisdn=msisdn, voucher_pin=pins[0]))
    assert good["recharged"] and good["amount"] == 100 and good["method"] == "voucher"
    again = ok(run(client, "INT-129", msisdn=msisdn, voucher_pin=pins[0]))
    assert again["recharged"] is False and "already used" in again["reason"]
    for _ in range(4):
        run(client, "INT-129", msisdn=msisdn, voucher_pin="11111111111111")
    refused(run(client, "INT-129", msisdn=msisdn, voucher_pin=pins[1]), "too many wrong voucher pins")
    batches = client.get("/api/mobile/vouchers/batches").json()
    assert batches[0]["redeemed"] == 1 and batches[0]["available"] == 2 and not batches[0]["pins_pending"]


def test_postpaid_limits_deposits_and_shared_plans(client):
    party = customer(client, "Acme SME")
    made = ok(run(client, "INT-136", party_id=party, plan="BIZ_SHARED", quantity=1, credit_limit=1600, deposit=1000, sim_type="esim"))
    head = made["msisdn"]
    assert owed(client, party) == 1499  # the first month is on the bill
    members = ok(run(client, "INT-136", party_id=party, plan="POST_399", quantity=3, user_name="Asha, Ben, Chen", sim_type="esim"))
    assert members["count"] == 3 and [l["user_name"] for l in members["lines"]] == ["Asha", "Ben", "Chen"]
    m1 = members["lines"][0]["msisdn"]
    refused(run(client, "INT-141", msisdn=m1, member_msisdn=head), "not a shared plan")
    assert ok(run(client, "INT-141", msisdn=head, member_msisdn=m1))["members"] == [m1]
    other = customer(client, "Somebody Else")
    stranger = ok(run(client, "INT-136", party_id=other, plan="POST_399", sim_type="esim"))["msisdn"]
    refused(run(client, "INT-141", msisdn=head, member_msisdn=stranger), "same customer")

    # the member draws on the plan owner's pool once its own allowance is gone
    used = ok(run(client, "INT-144", msisdn=m1, event_type="data", quantity=40960 + 100))
    assert used["from_packs"] == {"POST_399": 40960, "BIZ_SHARED": 100} and used["charged"] == 0

    # spend alert, then the credit limit bars outgoing use; a new cycle lifts it
    ok(run(client, "INT-135", msisdn=head, credit_limit=1600, spend_alert_pct=95))
    pack = ok(run(client, "INT-130", msisdn=head, pack="VOICE100"))
    assert pack["paid_from"] == "bill" and pack["cycle_spend"] == 1548  # the plan's 1499 + 49
    assert any(h["action"] == "spend_alert" for h in line(client, head)["history"])
    big = ok(run(client, "INT-144", msisdn=head, event_type="voice", quantity=100, international=True))
    assert big["granted"] is False and "credit limit" in big["reason"]
    assert "credit_limit" in line(client, head)["barrings"]
    refused(run(client, "INT-139", msisdn=head, action="unbar", barring="credit_limit"), "lifts by itself")
    raised = ok(run(client, "INT-135", msisdn=head, credit_limit=5000))
    assert raised["barred"] is False

    # deposit: refund refused while the account owes
    assert ok(run(client, "INT-148", msisdn=head, action="take", amount=500))["deposit"] == 1500
    refused(run(client, "INT-148", msisdn=head, action="refund"), "owes")
    refused(run(client, "INT-134", msisdn=head, to_payment_type="prepaid", plan="PRE_BASIC"), "settle the bill")

    # a prepaid line moves to postpaid with its balance carried to the bill
    pre = ok(run(client, "INT-136", party_id=party, plan="PRE_BASIC", initial_recharge=300, sim_type="esim"))["msisdn"]
    before = owed(client, party)
    moved = ok(run(client, "INT-134", msisdn=pre, to_payment_type="postpaid", plan="POST_399"))
    assert moved["carried_over"] == 300 and owed(client, party) == before - 300 + 399

    # a month on: the cycle starts again (spend back to 0) and the plans renew onto the bill
    db = client.TestSessionLocal()
    try:
        before = cb.balance(db, 1, party)
        done = mb.housekeeping(db, now=dt.datetime.utcnow() + dt.timedelta(days=32))
        db.commit()
        row = db.query(MbLine).filter(MbLine.msisdn == head).one()
        assert done["cycles"] >= 1 and done["renewed"] >= 1 and row.cycle_spend == 1499  # the renewed plan is the new cycle's first spend
        assert cb.balance(db, 1, party) > before
    finally:
        db.close()
    assert set(client.post("/api/mobile/housekeeping").json()) >= {"renewed", "cycles", "numbers_released"}


def test_sim_number_barring_roaming_and_termination(client):
    party = customer(client, "Sam Swap")
    msisdn = ok(run(client, "INT-136", party_id=party, plan="PRE_BASIC", initial_recharge=200,
                    iccid="8991000000000000001"))["msisdn"]
    refused(run(client, "INT-137", msisdn=msisdn, reason="lost"), "verify the customer's identity")
    swapped = ok(run(client, "INT-137", msisdn=msisdn, reason="lost", identity_verified=True, sim_type="esim"))
    assert swapped["old_iccid"] == "8991000000000000001" and swapped["esim_activation_code"] and swapped["fee"] == 25
    cooled = ok(run(client, "INT-144", msisdn=msisdn, event_type="voice", quantity=1, international=True))
    assert cooled["granted"] is False and "just swapped" in cooled["reason"]
    refused(run(client, "INT-140", msisdn=msisdn), "just swapped")

    # barring: lost / stolen stops everything; lifting it needs the identity checked
    ok(run(client, "INT-139", msisdn=msisdn, action="bar", barring="lost_stolen", reason="phone stolen"))
    stopped = ok(run(client, "INT-144", msisdn=msisdn, event_type="sms", quantity=1))
    assert stopped["granted"] is False and "lost_stolen" in stopped["reason"]
    refused(run(client, "INT-129", msisdn=msisdn, amount=50), "barred")
    refused(run(client, "INT-139", msisdn=msisdn, action="unbar", barring="lost_stolen"), "verify")
    ok(run(client, "INT-139", msisdn=msisdn, action="unbar", barring="lost_stolen", identity_verified=True))
    ok(run(client, "INT-139", msisdn=msisdn, action="bar", barring="data"))
    assert ok(run(client, "INT-144", msisdn=msisdn, event_type="data", quantity=5))["granted"] is False

    # number change: the old number is quarantined
    changed = ok(run(client, "INT-138", msisdn=msisdn, new_msisdn="9100055555"))
    assert changed["msisdn"] == "9100055555" and changed["old_msisdn"] == msisdn
    other = customer(client, "Next Person")
    refused(run(client, "INT-136", party_id=other, plan="PRE_BASIC", msisdn=msisdn, sim_type="esim"), "quarantined")
    assert line(client, "9100055555")["iccid"] == swapped["iccid"]

    # roaming: prepaid needs a balance; usage abroad then charges at the roaming rate
    db = client.TestSessionLocal()
    try:
        row = db.query(MbLine).filter(MbLine.msisdn == "9100055555").one()
        row.swap_cooldown_until = None
        db.commit()
    finally:
        db.close()
    on = ok(run(client, "INT-140", msisdn="9100055555", action="on"))
    assert on["roaming"] is True
    abroad = ok(run(client, "INT-144", msisdn="9100055555", event_type="voice", quantity=2, roaming=True))
    assert abroad["granted"] and abroad["charged"] == 5.0  # 2 x 0.5 x 5
    assert ok(run(client, "INT-140", msisdn="9100055555", action="off"))["roaming"] is False

    # suspend / resume / terminate - the prepaid balance refunded, the number quarantined
    ok(run(client, "INT-143", msisdn="9100055555", action="suspend", reason="travelling"))
    assert ok(run(client, "INT-144", msisdn="9100055555", event_type="sms", quantity=1))["granted"] is False
    ok(run(client, "INT-143", msisdn="9100055555", action="resume"))
    refused(run(client, "INT-143", msisdn="9100055555", action="terminate"), "reason")
    gone = ok(run(client, "INT-143", msisdn="9100055555", action="terminate", reason="moving abroad", refund_balance=True))
    assert gone["status"] == "terminated" and gone["refund"] > 0 and gone["quarantined_until"]
    refused(run(client, "INT-129", msisdn="9100055555", amount=50), "terminated")
    refused(run(client, "INT-136", party_id=other, plan="PRE_BASIC", msisdn="9100055555", sim_type="esim"), "quarantined")


def test_wholesale_partner_lines_and_settlement(client):
    partner = ok(run(client, "INT-146", wholesale_partner="Zoom Mobile", partner_kind="mvno", wholesale_rates="voice 0.2, sms 0.05, data 0.01",
                     number_range="9180000000-9180000009", revenue_share_pct=0))
    assert partner["created"] and partner["wholesale_rates"] == {"voice": 0.2, "sms": 0.05, "data": 0.01}
    refused(run(client, "INT-146", wholesale_partner="Overlap Telecom", partner_kind="mvno", wholesale_rates="voice 0.1",
                number_range="9180000005-9180000020"), "overlaps")
    dealer = ok(run(client, "INT-146", wholesale_partner="CityMobile", partner_kind="dealer", revenue_share_pct=5))
    assert dealer["kind"] == "dealer"

    party = customer(client, "Zoom Customer")
    made = ok(run(client, "INT-136", party_id=party, plan="PRE_BASIC", quantity=2, wholesale_partner="Zoom Mobile", initial_recharge=100, sim_type="esim"))
    assert [l["msisdn"] for l in made["lines"]] == ["9180000000", "9180000001"]
    a = made["lines"][0]["msisdn"]
    ok(run(client, "INT-144", msisdn=a, event_type="voice", quantity=30))
    ok(run(client, "INT-144", msisdn=a, event_type="data", quantity=200))
    ok(run(client, "INT-129", msisdn=a, amount=1000, payment_method="cash", dealer="CityMobile"))

    today = dt.date.today()
    zoom = ok(run(client, "INT-147", wholesale_partner="Zoom Mobile", period_start=today.replace(day=1).isoformat(), period_end=today.isoformat()))
    assert zoom["wholesale_due"] == 8.0  # 30 x 0.2 + 200 x 0.01
    refused(run(client, "INT-147", wholesale_partner="Zoom Mobile", period_start=today.replace(day=1).isoformat(), period_end=today.isoformat()),
            "settled")
    city = ok(run(client, "INT-147", wholesale_partner="CityMobile", period_start=today.replace(day=1).isoformat(), period_end=today.isoformat()))
    assert city["commission_due"] == 50.0 and city["net"] == -50.0

    ok(run(client, "INT-146", wholesale_partner="Zoom Mobile", partner_status="suspended"))
    refused(run(client, "INT-136", party_id=party, plan="PRE_BASIC", wholesale_partner="Zoom Mobile", sim_type="esim"), "suspended")
    assert {p["name"] for p in client.get("/api/mobile/partners").json()["partners"]} == {"Zoom Mobile", "CityMobile"}


def test_plain_english_and_the_mobile_pages(client):
    party = customer(client, "Nita Natural")
    said = client.post("/api/intents", json={"text": f"Activate 2 postpaid lines on BIZ_599 for customer {party} as eSIMs"}).json()
    assert said["intent_type"] == "INT-136" and said["status"] == "executed", said
    first = said["result"]["lines"][0]["msisdn"]
    pack = client.post("/api/intents", json={"text": f"Add the DATA10 pack to {first}"}).json()
    assert pack["intent_type"] == "INT-130" and pack["status"] == "executed", pack
    balance = client.post("/api/intents", json={"text": f"What's the balance on {first}?"}).json()
    assert balance["intent_type"] == "INT-131" and balance["status"] == "executed", balance
    billing = client.post("/api/intents", json={"text": f"switch customer {party} to prepaid with auto top-up of 200"}).json()
    assert billing["intent_type"] == "INT-74", billing  # an account's billing profile, not a line

    overview = client.get("/api/mobile/overview").json()
    assert overview["postpaid"] >= 2 and overview["lines"] >= 2
    found = client.get("/api/mobile/lines", params={"q": "Nita"}).json()
    assert len(found) == 2 and client.get(f"/api/mobile/lines/{found[0]['id']}").json()["packs"]
    catalog = client.get("/api/mobile/catalog").json()
    assert {p["code"] for p in catalog["plans"]} >= {"PRE_SMART", "POST_399", "BIZ_SHARED"}
    assert client.put("/api/mobile/catalog", json={"packs": [{"code": "X", "name": "Bad", "price": -1, "validity_days": 1}]}).status_code == 400
    saved = client.put("/api/mobile/catalog", json={"transfer_max": 250, "packs": catalog["packs"] + [
        {"code": "NIGHT", "name": "Night data 5 GB", "kind": "data", "price": 29, "validity_days": 7, "allowance": {"data": 5120}}]})
    assert saved.status_code == 200 and saved.json()["transfer_max"] == 250
    assert any(p["code"] == "NIGHT" for p in client.get("/api/mobile/catalog").json()["packs"])
    catalog_intents = {i["code"] for i in client.get("/api/intents/catalog").json()["intents"]}
    assert {f"INT-{n}" for n in range(129, 149)} <= catalog_intents
