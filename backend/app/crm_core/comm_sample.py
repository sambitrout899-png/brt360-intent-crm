"""Sample communications set-up: providers, solutions, and - for a few
customers with orders - a billing profile, a contact, an account hierarchy,
allowances, usage (with one suspicious day), a past-due bill and a premises.
Safe to run twice: what already exists is left alone."""

from __future__ import annotations

import datetime as dt
import random
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import comm_billing as cb, comm_service as cs
from app.models.comms import CmAllowance, CmBillingProfile, CmInvoice, CmProvider, CmSolution, CmUsage
from app.models.crm import Party
from app.models.orders import Order
from app.timeutils import utcnow

_PROVIDERS = [("Mobile Wholesale Partner", "mobile,sim,handset,pager,5g,4g", 4), ("Metro Fiber ISP", "internet,broadband,ip,dia,fiber", 6),
              ("Local Exchange Carrier", "voice,voip,line,pbx,voice mail", 3)]
_SOLUTIONS = [
    ("No dial tone on a fixed line", "Customer has no dial tone on the office phone", "Check the exchange for an outage first; if none, reset the "
     "line card port and test from the MDF. Dispatch only if the line tests faulty.", "voip", "dial tone, no dial tone, phone dead, line"),
    ("Static or noise on the line", "Crackling, static or noise on calls", "Run a line test; static usually means a wet joint or a damaged drop "
     "cable. Replace the drop or reterminate the jack.", "voip", "static, noise, crackling, line"),
    ("Slow internet speeds", "Broadband slower than the plan speed", "Check the line sync rate and the CPE; reboot the router, move it off "
     "2.4 GHz, update the firmware. If sync is low, check the port profile.", "connectivity", "slow, speed, internet, broadband, latency"),
    ("Router keeps rebooting", "CPE restarts every few minutes", "Usually a failing power adapter or overheating - swap the adapter, then the "
     "router (asset transaction: replace).", "connectivity", "router, reboot, restart, cpe"),
    ("International calls on the bill the customer did not make", "Unexpected international charges", "Check the usage detail by destination; "
     "if the calls are not theirs, raise a fraud alert and block international dialling, then credit the bill.", "billing",
     "international, overcharged, bill, calls, fraud"),
]


def sample_setup(db: Session, tenant_id: int) -> dict[str, Any]:
    created: list[str] = []
    if not db.query(CmProvider).filter(CmProvider.tenant_id == tenant_id).count():
        for name, keywords, days in _PROVIDERS:
            db.add(CmProvider(tenant_id=tenant_id, name=name, keywords=keywords, lead_days=days))
        created.append(f"{len(_PROVIDERS)} providers")
    if not db.query(CmSolution).filter(CmSolution.tenant_id == tenant_id).count():
        for title, problem, resolution, line, keywords in _SOLUTIONS:
            db.add(CmSolution(tenant_id=tenant_id, title=title, problem=problem, resolution=resolution, product_line=line, keywords=keywords))
        created.append(f"{len(_SOLUTIONS)} solutions")
    db.flush()
    rnd = random.Random(tenant_id)
    party_ids = [pid for (pid,) in db.query(Order.party_id).filter(Order.tenant_id == tenant_id).distinct().limit(3).all()]
    for n, pid in enumerate(party_ids):
        party = db.get(Party, pid)
        if party is None or db.query(CmBillingProfile).filter(CmBillingProfile.party_id == pid).count():
            continue
        email = party.email or f"billing@{cs._key(party.name)[:12] or 'customer'}.example"
        cs.save_billing_profile(db, tenant_id, {"party_id": pid, "frequency": "monthly", "bill_media": "email", "email": email})
        cs.add_contact(db, tenant_id, {"party_id": pid, "first_name": "Accounts", "last_name": "Payable", "email": email, "role": "billing"})
        start = (utcnow() - dt.timedelta(days=70)).date().replace(day=1)
        for name, etype, granted, unit in (("Included minutes", "voice", 300, "minutes"), ("Included messages", "sms", 1000, "messages"),
                                           ("Included data", "data", 20000, "MB")):
            db.add(CmAllowance(tenant_id=tenant_id, party_id=pid, name=name, event_type=etype, granted=granted, unit=unit, valid_from=start))
        # usage inside the period the sample bill covers (from the first service day, at most 60 days back)
        today = utcnow().date()
        first = cb.current_period(db, tenant_id, pid)[0]
        start_day = max(first, today - dt.timedelta(days=60))
        bill_end = max(first, today - dt.timedelta(days=2))
        events = []
        for k in range((bill_end - start_day).days + 1):
            at = dt.datetime.combine(start_day + dt.timedelta(days=k), dt.time(9, 0))
            for c in range(rnd.randint(3, 8)):
                when = at + dt.timedelta(minutes=rnd.randint(0, 600))
                intl = (k == 0 and c == 0) or rnd.random() < 0.06  # at least one chargeable international call
                events.append({"event_type": "voice", "quantity": rnd.randint(1, 18), "at": when, "service": f"05{50 + n}000{n}{n}{n}",
                               "destination": f"+4969{rnd.randint(1000000, 9999999)}" if intl else f"05{rnd.randint(10000000, 99999999)}",
                               "international": intl, "destination_location": "Frankfurt" if intl else "Riyadh", "origin_location": "Riyadh"})
            events.append({"event_type": "data", "quantity": rnd.randint(50, 600), "at": at, "service": f"05{50 + n}000{n}{n}{n}"})
            events.append({"event_type": "sms", "quantity": rnd.randint(1, 20), "at": at, "service": f"05{50 + n}000{n}{n}{n}"})
        if n == 0:  # today (not billed yet): the same number calling from two cities minutes apart, and a run of premium calls
            y = dt.datetime.combine(today, dt.time(0, 5))
            events += [{"event_type": "voice", "quantity": 7, "at": y, "service": "0550000000", "destination": "0551112222", "origin_location": "Riyadh"},
                       {"event_type": "voice", "quantity": 4, "at": y + dt.timedelta(minutes=6), "service": "0550000000", "destination": "0553334444",
                        "origin_location": "Jeddah"}]
            events += [{"event_type": "voice", "quantity": 25, "at": y + dt.timedelta(minutes=30 + i), "service": "0550000000", "premium": True,
                        "destination": f"0900{i:04d}", "origin_location": "Jeddah"} for i in range(5)]
        cb.record_usage(db, tenant_id, pid, events)
        # a bill for last period, issued long enough ago to be overdue
        try:
            out = cb.bill_run(db, tenant_id, pid, bill_end)
            inv = db.get(CmInvoice, out["invoice_id"])
            inv.issue_date = utcnow().date() - dt.timedelta(days=30)
            inv.due_date = inv.issue_date + dt.timedelta(days=14)  # 16 days overdue
            cb.refresh_status(inv)
            created.append(f"bill {inv.invoice_number} for {party.name}")
        except ValueError:
            pass
        if n == 0:
            child = cs.set_hierarchy(db, tenant_id, {"account_name": f"{party.name} - Billing", "parent_id": pid, "account_class": "billing"})
            cs.set_hierarchy(db, tenant_id, {"account_name": f"{party.name} - Branch", "parent_id": pid, "account_class": "service", "site": "Branch office"})
            cs.validate_address(db, tenant_id, {"party_id": pid, "address": "12 King Fahd Road, Riyadh 12271", "make_premise": True})
            created.append(f"hierarchy and premises for {party.name} (+ account {child['party_id']})")
        created.append(f"usage and allowances for {party.name}")
    db.flush()
    collections = cb.run_collections(db, tenant_id)
    fraud = cb.fraud_check(db, tenant_id, day=utcnow().date())
    return {"created": created, "collections": {"opened": collections["opened"], "escalated": len(collections["escalated"])},
            "fraud_alerts": [a["alert_number"] for a in fraud["raised"]],
            "usage_events": db.query(CmUsage).filter(CmUsage.tenant_id == tenant_id).count()}
