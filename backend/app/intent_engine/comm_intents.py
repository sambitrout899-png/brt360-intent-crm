"""Communications intents (INT-72..102) - the Siebel Communications Guide's
account, premises, billing, credit, fraud, service and agreement processes.
Like the Order Management intents (om_intents.py) they are recognised by
phrase rules before any model runs, so the trained model's prompt stays
unchanged; their training examples are appended last.

  accounts     INT-72 account hierarchy      INT-73 add a contact
               INT-74 billing profile        INT-75 account profile
               INT-77 validate an address    INT-78 premises hookup
               INT-79 friends & family list
  assets       INT-80 asset transaction      INT-81 change request
  sales        INT-82 related site / partner INT-83 profile an opportunity
               INT-100 revise an agreement   INT-101 agreement document
  billing      INT-84 run a bill             INT-85 take a bill payment
               INT-86 bill adjustment        INT-87 payment arrangement
               INT-88 customer's decision    INT-89 duplicate bill
               INT-90 explain a bill
  credit       INT-91 run collections        INT-92 work a credit alert
  fraud        INT-76 fraud thresholds       INT-93 check for fraud
               INT-94 work a fraud alert
  service      INT-95 link tickets to parent INT-96 resolve / reopen a ticket
               INT-97 satisfaction survey    INT-98 order fallout
               INT-99 find a solution
  orders       INT-102 allocate an order to providers
"""

from __future__ import annotations

import re
from typing import Any

CODES = tuple(f"INT-{n}" for n in range(72, 103))

# code -> (label, description, category, icon, target, fields, examples)
SPECS: dict[str, tuple[str, str, str, str, str, list[str], list[str]]] = {
    "INT-72": ("Set up the account hierarchy", "Add a child account (service, billing or an aggregator) under a parent, move an account - "
               "its whole tree moves with it - classify it, mark it a partner or block it.", "customers", "🌳", "party",
               ["party_id", "company_name", "account_name", "parent_id", "parent", "account_class", "account_type", "site", "partner", "good_standing", "reason"],
               ["Add York Service Account under Acme Holdings as a service account", "move customer 12 under customer 4"]),
    "INT-73": ("Add a contact", "Add a person to an account (or link an existing contact to another account), with their role.",
               "customers", "👤", "party", ["party_id", "company_name", "contact_name", "first_name", "last_name", "title", "email", "phone", "role",
                                           "primary"],
               ["Add contact Sara Ali, sara@acme.com, to Acme as the billing contact"]),
    "INT-74": ("Set up a billing profile", "How an account is billed: frequency, postpaid or prepaid (with top-up), payment method "
               "(last 4 digits only), bill media and email.", "billing", "🧾", "party",
               ["party_id", "company_name", "profile_name", "frequency", "payment_type", "payment_method", "card_last4", "card_brand",
                "bank_last4", "bill_media", "email", "bill_day", "auto_topup", "topup_amount", "topup_threshold", "primary"],
               ["Bills for Acme by email to ap@acme.com, monthly, by card ending 4242", "switch customer 7 to prepaid with auto top-up of 200"]),
    "INT-75": ("Update an account profile", "Exemption (tax exempt, lifeline), financial (with a credit check), site, statement or customer "
               "profile - or refresh the loyalty scores.", "customers", "🗂️", "party",
               ["party_id", "company_name", "profile_kind", "values", "credit_check"],
               ["Acme is tax exempt - certificate TX-778", "update the financial profile of customer 4 and run a credit check"]),
    "INT-76": ("Change fraud thresholds", "An account's fraud limits - daily, international and domestic value, call length and count, "
               "sensitive numbers; raising them can clear an open alert.", "billing", "🛡️", "party",
               ["party_id", "company_name", "daily_threshold", "international_threshold", "max_call_minutes", "max_calls",
                "sensitive_threshold", "block_international", "values"],
               ["Raise the international fraud threshold for Acme to 2000", "block international calls for customer 9"]),
    "INT-77": ("Validate an address", "Check an address against the street guide, store its standard form and (optionally) make it a premises.",
               "customers", "📍", "party", ["address", "party_id", "company_name", "address_kind", "make_premise", "access_instruction"],
               ["Validate the address 12 King Fahd Road, Riyadh 12271 for Acme and make it a premises"]),
    "INT-78": ("Register a premises hookup", "A premises and its service point (jack, port, meter) - with the service and occupant.",
               "service", "🏢", "party", ["address", "premise_id", "service_point", "product", "service_type", "location", "party_id",
                                           "company_name", "asset_id", "floors", "read_route", "bill_group"],
               ["Register a hookup at 12 King Fahd Road, Riyadh: service point JACK-1 for Fiber 100"]),
    "INT-79": ("Manage a friends & family list", "Numbers that get special rates - up to the list's maximum; locked while an order for the "
               "service is in flight.", "customers", "📇", "party",
               ["party_id", "company_name", "list_name", "add_numbers", "remove_numbers", "product", "max_items"],
               ["Add 0551234567 and 0559876543 to Acme's friends and family list"]),
    "INT-80": ("Record an asset transaction", "Install, replace or remove a component of an asset, move it, or relate another asset "
               "(backup / option).", "service", "🔩", "party",
               ["asset_id", "transaction", "component", "serial_number", "related_asset_id", "relation", "address", "description"],
               ["Replace the Router CPE on asset 14", "asset 14 has asset 22 as its backup"]),
    "INT-81": ("Raise a change request", "A customer's suggestion or a needed change on an asset or a service.", "service", "💡", "party",
               ["asset_id", "party_id", "company_name", "description", "cr_type", "area", "priority", "owner"],
               ["Change request on asset 14: customer wants a static IP on the backup line"]),
    "INT-82": ("Add a related site or partner", "Another account on an opportunity - a branch site of the customer, or a partner.",
               "sales", "🤝", "opportunity", ["opportunity_id", "related_party_id", "related_account", "link_kind", "note"],
               ["Add Acme York as a related site on opportunity 5", "add partner NetBuild to opportunity 5"]),
    "INT-83": ("Profile an opportunity", "Answer the qualification script (budget, authority, need, timeline, switching) - it scores the "
               "opportunity and marks the target market.", "sales", "📋", "opportunity", ["opportunity_id", "answers"],
               ["Profile opportunity 5: budget yes, authority yes, need yes, timeline no"]),
    "INT-84": ("Run a bill", "The bill for an account's period: recurring charges, one-time charges and credits, usage above the "
               "allowances and tax - prepaid accounts draw down their balance.", "billing", "🧮", "party",
               ["party_id", "company_name", "period_end"], ["Run the bill for Acme", "generate the invoice for customer 7 up to 2026-10-31"]),
    "INT-85": ("Take a bill payment", "A payment on the account (oldest bill first), on one or more bills or for a credit alert - card or "
               "bank account by last 4 digits only.", "billing", "💵", "party",
               ["party_id", "company_name", "invoice", "amount", "payment_method", "card_last4", "card_brand", "bank_last4", "reference",
                "location", "credit_alert"],
               ["Take a payment of 1500 for Acme's bill INV-000012 by card ending 4242", "customer 7 paid their balance in cash"]),
    "INT-86": ("Request a bill adjustment", "Credit (or debit) a whole bill, one bill line, unbilled usage or the account - small credits "
               "post at once, bigger ones wait for a supervisor.", "billing", "↩️", "party",
               ["invoice", "line", "party_id", "company_name", "amount", "percent", "adjustment_type", "reason", "comments", "usage_id",
                "adjustment", "decision"],
               ["Credit 120 on the city tax line of INV-000012 - customer is tax exempt", "approve adjustment ADJ-000004"]),
    "INT-87": ("Request a payment arrangement", "Spread what the customer owes over instalments, or defer it - approved, countered or "
               "rejected by policy.", "billing", "📆", "party",
               ["party_id", "company_name", "invoice", "credit_alert", "installments", "plan_type", "start_date", "amount", "comments",
                "arrangement", "decision"],
               ["Payment arrangement for Acme: 3 instalments", "defer bill INV-000012 to 2026-12-15"]),
    "INT-88": ("Record the customer's decision", "The customer accepts or rejects the outcome of an adjustment or a payment arrangement.",
               "billing", "✅", "party", ["adjustment", "arrangement", "customer_decision", "comments"],
               ["The customer accepted arrangement PA-000003", "customer rejected adjustment ADJ-000004"]),
    "INT-89": ("Send a duplicate bill", "A copy of a bill by email, paper or the portal, to the address on file or another.", "billing", "📨",
               "party", ["invoice", "party_id", "company_name", "bill_media", "address", "comments"],
               ["Send a duplicate of INV-000012 by email", "resend Acme's last bill to finance@acme.com"]),
    "INT-90": ("Explain a bill", "A bill line by line against the previous one - what changed, top destinations, usage and what is "
               "not billed yet.", "billing", "🔍", "party", ["invoice", "party_id", "company_name"],
               ["Explain bill INV-000012", "why is Acme's bill so high?"]),
    "INT-91": ("Run collections", "Overdue bills open credit alerts and step through the collections ladder - reminders, letters, "
               "late fees, suspension, an outside agency; paid-up accounts exit.", "billing", "📉", "party",
               ["party_id", "company_name", "as_of"], ["Run collections", "run collections for Acme"]),
    "INT-92": ("Work a credit alert", "Raise a credit alert or move one on: a collection action, a note, the owner or its status.",
               "billing", "🚨", "party",
               ["credit_alert", "party_id", "company_name", "action_type", "status", "owner", "comments", "amount", "priority"],
               ["Credit alert CA-000003: courtesy phone call done, customer will pay Friday", "raise a credit alert for Acme"]),
    "INT-93": ("Check for fraud", "Scan usage against each account's fraud thresholds (and calls from two places at once) - or report "
               "a stolen phone or other fraud.", "billing", "🕵️", "party",
               ["party_id", "company_name", "scan", "day", "fraud_type", "service", "comments"],
               ["Run a fraud check", "report a stolen phone for Acme, number 0551234567"]),
    "INT-94": ("Work a fraud alert", "Contact the customer about a fraud alert: confirmed, not fraud, no answer (repeatedly - disconnect), "
               "investigate or close.", "billing", "🧯", "party", ["fraud_alert", "fraud_outcome", "comments", "owner"],
               ["Fraud alert FA-000002: customer confirms the calls were not theirs", "FA-000002 no answer"]),
    "INT-95": ("Link tickets to a parent", "Hang customer tickets off one network ticket - by number, or every open ticket on the same "
               "service item (exchange, circuit).", "service", "🔗", "ticket", ["parent_ticket_id", "ticket_ids", "service_item"],
               ["Link tickets 12, 15 and 19 to parent ticket 10", "parent ticket 10 is the outage on exchange RYD-04 - link its tickets"]),
    "INT-96": ("Resolve a trouble ticket", "Close a ticket with its resolution (or a solution) and tell the customer - child tickets wait "
               "for verification or close too; or reopen it.", "service", "🏁", "ticket",
               ["ticket_id", "ticket_action", "resolution", "solution", "save_solution", "notify", "close_children"],
               ["Resolve ticket 10 - cable repaired and tested", "reopen ticket 12"]),
    "INT-97": ("Record a satisfaction survey", "The customer's ratings after a ticket - overall, timeliness, professionalism, resolved, "
               "would recommend.", "service", "⭐", "ticket",
               ["ticket_id", "overall", "timeliness", "professionalism", "resolved", "would_recommend", "comments", "survey_channel"],
               ["Survey for ticket 10: overall 4, timeliness 5, would recommend 9"]),
    "INT-98": ("Log an order fallout", "An order failed in fulfilment: a trouble ticket carries the failure, the order is held, and the "
               "recovery step is suggested.", "orders", "🧨", "order", ["order_id", "error_code", "system_of_failure", "line", "description"],
               ["Order 7 failed in provisioning - error PORT_UNAVAILABLE"]),
    "INT-99": ("Find a solution", "Search the knowledge base and earlier resolved tickets for how a problem was fixed.", "service", "📚",
               "ticket", ["ticket_id", "query"], ["Find a solution for ticket 12", "search solutions for no dial tone"]),
    "INT-100": ("Revise an agreement", "A new revision: add or remove terms, change the end date, term, value, response time, cover "
                "orders or accounts - or add a sub-agreement.", "sales", "📝", "party", ["agreement_id", "changes", "reason", "sub_agreement"],
                ["Revise agreement AG-000004: add term Early exit: 3 months' fees; term 24 months"]),
    "INT-101": ("Generate the agreement document", "Assemble the agreement document from the template and the agreement's terms - "
                "mark it signed when it comes back.", "sales", "🖨️", "party", ["agreement_id", "signed"],
                ["Generate the agreement document for AG-000004"]),
    "INT-102": ("Allocate an order to providers", "Split an order into one work order per provider (mobile operator, ISP, local carrier) "
                "by product, with due dates and terms.", "orders", "🏗️", "order", ["order_id", "reallocate", "return_to"],
                ["Allocate order 7 to providers"]),
}

LABELS: dict[str, str] = {code: spec[0] for code, spec in SPECS.items()}

LIB: dict[str, dict[str, Any]] = {
    "account_name": {"label": "New account name", "placeholder": "e.g. Acme York Service", "help": "Blank = an existing account (pick it above)"},
    "parent_id": {"label": "Parent account", "type": "record", "record_type": "party"},
    "parent": {"label": "Or the parent's name"},
    "account_class": {"label": "Account class", "type": "picklist",
                      "values": ["customer", "service_aggregator", "service", "billing_aggregator", "billing"]},
    "account_type": {"label": "Account type", "type": "picklist", "values": ["business", "residential"]},
    "site": {"label": "Site", "placeholder": "e.g. Headquarters, York office"},
    "partner": {"label": "Partner account", "type": "boolean"},
    "good_standing": {"label": "Good standing", "type": "boolean", "help": "Off = the account is blocked"},
    "contact_name": {"label": "Name", "placeholder": "e.g. Sara Ali"},
    "first_name": {"label": "First name", "help": "Or give the whole name above"},
    "last_name": {"label": "Last name"},
    "title": {"label": "Job title"},
    "role": {"label": "Role", "type": "picklist", "values": ["decision_maker", "billing", "technical", "user", "other"]},
    "primary": {"label": "Primary", "type": "boolean"},
    "profile_name": {"label": "Billing profile", "placeholder": "Blank = the account's profile (or a new one)"},
    "frequency": {"label": "Billing frequency", "type": "picklist", "values": ["monthly", "quarterly", "annual"]},
    "payment_type": {"label": "Payment type", "type": "picklist", "values": ["postpaid", "prepaid"]},
    "bank_last4": {"label": "Bank account - last 4 digits", "placeholder": "e.g. 1234"},
    "bill_media": {"label": "Bill media", "type": "picklist", "values": ["email", "paper", "portal", "sms"]},
    "bill_day": {"label": "Bill day", "type": "number", "help": "Day of the month the bill is cut (1-28)"},
    "auto_topup": {"label": "Auto top-up", "type": "boolean"},
    "topup_amount": {"label": "Top-up amount", "type": "money"},
    "topup_threshold": {"label": "Top up when below", "type": "money"},
    "profile_kind": {"label": "Profile", "type": "picklist", "values": ["exemption", "financial", "site", "statement", "customer", "loyalty"]},
    "values": {"label": "Values", "type": "textarea", "placeholder": "e.g. tax exempt: yes, tax exemption number: TX-778"},
    "credit_check": {"label": "Run a credit check", "type": "boolean"},
    "daily_threshold": {"label": "Daily limit", "type": "money"},
    "international_threshold": {"label": "International limit (a day)", "type": "money"},
    "max_call_minutes": {"label": "Longest call (minutes)", "type": "number"},
    "max_calls": {"label": "Calls a day", "type": "number"},
    "sensitive_threshold": {"label": "Calls to premium numbers a day", "type": "number"},
    "block_international": {"label": "Block international calls", "type": "boolean"},
    "address_kind": {"label": "Address type", "type": "picklist", "values": ["service", "billing", "mailing"]},
    "make_premise": {"label": "Make it a premises", "type": "boolean"},
    "access_instruction": {"label": "Access instructions"},
    "premise_id": {"label": "Premises #", "type": "number"},
    "service_point": {"label": "Service point", "placeholder": "e.g. JACK-1, PORT-3/12, METER-778"},
    "service_type": {"label": "Service type", "type": "picklist", "values": ["voice", "data", "video", "mobile"]},
    "floors": {"label": "Floors", "type": "number"},
    "read_route": {"label": "Meter read route"},
    "bill_group": {"label": "Bill group"},
    "asset_id": {"label": "Asset #", "type": "number"},
    "list_name": {"label": "List", "placeholder": "e.g. Friends & family"},
    "add_numbers": {"label": "Add numbers", "placeholder": "e.g. 0551234567, 0559876543"},
    "remove_numbers": {"label": "Remove numbers"},
    "max_items": {"label": "Most numbers on the list", "type": "number"},
    "transaction": {"label": "Transaction", "type": "picklist", "values": ["install", "replace", "remove", "upgrade", "move", "relate"]},
    "component": {"label": "Component", "placeholder": "e.g. Router CPE, SFP module"},
    "serial_number": {"label": "Serial number"},
    "related_asset_id": {"label": "Related asset #", "type": "number"},
    "relation": {"label": "Relation", "type": "picklist", "values": ["backup", "option", "component", "redundant"]},
    "cr_type": {"label": "Type", "type": "picklist", "values": ["enhancement", "defect", "configuration"]},
    "area": {"label": "Product area"},
    "priority": {"label": "Priority", "type": "picklist", "values": ["low", "medium", "high", "critical"]},
    "owner": {"label": "Owner"},
    "related_party_id": {"label": "Account to add", "type": "record", "record_type": "party"},
    "related_account": {"label": "Or its name"},
    "link_kind": {"label": "Add as", "type": "picklist", "values": ["related_site", "partner"], "default": "related_site"},
    "note": {"label": "Note"},
    "answers": {"label": "Answers", "type": "textarea", "placeholder": "budget: yes, authority: no, need: yes, timeline: yes, switching: no"},
    "period_end": {"label": "Bill up to", "placeholder": "YYYY-MM-DD - blank = the end of the period (or today)"},
    "invoice": {"label": "Bill", "placeholder": "e.g. INV-000012 - blank = the latest"},
    "reference": {"label": "Reference #", "help": "For a payment made at a bank or post office"},
    "credit_alert": {"label": "Credit alert", "placeholder": "e.g. CA-000003"},
    "percent": {"label": "Percent", "type": "percent"},
    "adjustment_type": {"label": "Credit or debit", "type": "picklist", "values": ["credit", "debit"], "default": "credit"},
    "comments": {"label": "Comments", "type": "textarea"},
    "usage_id": {"label": "Usage event #", "type": "number", "help": "To adjust one unbilled call or session"},
    "adjustment": {"label": "Adjustment request", "placeholder": "e.g. ADJ-000004"},
    "installments": {"label": "Instalments", "type": "number"},
    "plan_type": {"label": "Plan", "type": "picklist", "values": ["installment", "deferral"]},
    "start_date": {"label": "First payment", "placeholder": "YYYY-MM-DD"},
    "arrangement": {"label": "Payment arrangement", "placeholder": "e.g. PA-000003"},
    "customer_decision": {"label": "Customer decision", "type": "picklist", "values": ["accepted", "rejected"]},
    "as_of": {"label": "As of", "placeholder": "YYYY-MM-DD - blank = today"},
    "action_type": {"label": "Collection action", "type": "picklist",
                    "values": ["courtesy_email_sms", "courtesy_phone_call", "courtesy_dunning_letter", "demanding_phone_call", "harsh_dunning_letter",
                               "no_action_impose_late_fee", "suspend_services", "terminate_services", "refer_to_outside_agency",
                               "no_action_write_off", "payment_arrangement_agreed", "no_action"]},
    "status": {"label": "Status", "type": "picklist", "values": ["open", "pending", "closed", "rejected", "cancelled"]},
    "scan": {"label": "Scan usage", "type": "boolean"},
    "day": {"label": "Day", "placeholder": "YYYY-MM-DD - blank = the latest usage"},
    "fraud_type": {"label": "Fraud", "type": "picklist", "values": ["stolen_handset", "invalid_calls", "subscription", "returned_mail"]},
    "service": {"label": "Service / number"},
    "fraud_alert": {"label": "Fraud alert", "placeholder": "e.g. FA-000002"},
    "fraud_outcome": {"label": "Outcome", "type": "picklist", "values": ["confirm", "false_positive", "no_answer", "investigate", "disconnect", "close"]},
    "parent_ticket_id": {"label": "Parent ticket", "type": "record", "record_type": "ticket"},
    "ticket_ids": {"label": "Child tickets", "placeholder": "e.g. 12, 15, 19 - blank = every open ticket on the service item"},
    "service_item": {"label": "Service item", "placeholder": "e.g. exchange RYD-04, circuit C-778"},
    "ticket_action": {"label": "Action", "type": "picklist", "values": ["resolve", "reopen"], "default": "resolve"},
    "resolution": {"label": "Resolution", "type": "textarea"},
    "solution": {"label": "Solution", "placeholder": "Its # or title"},
    "save_solution": {"label": "Save as a solution", "type": "boolean"},
    "notify": {"label": "Tell the customer", "type": "boolean", "default": True},
    "close_children": {"label": "Close the child tickets too", "type": "boolean"},
    "overall": {"label": "Overall (1-5)", "type": "number"},
    "timeliness": {"label": "Timeliness (1-5)", "type": "number"},
    "professionalism": {"label": "Professionalism (1-5)", "type": "number"},
    "resolved": {"label": "Problem solved", "type": "boolean", "default": True},
    "would_recommend": {"label": "Would recommend (0-10)", "type": "number"},
    "survey_channel": {"label": "Survey by", "type": "picklist", "values": ["phone", "email", "letter", "in_person"]},
    "error_code": {"label": "Error code"},
    "system_of_failure": {"label": "Failed in", "placeholder": "e.g. provisioning, billing, inventory"},
    "query": {"label": "Problem", "type": "textarea"},
    "agreement_id": {"label": "Agreement", "placeholder": "e.g. AG-000004 or its #"},
    "sub_agreement": {"label": "New sub-agreement", "placeholder": "Name - instead of a revision"},
    "signed": {"label": "Signed by the customer", "type": "boolean"},
    "reallocate": {"label": "Re-allocate", "type": "boolean"},
    "return_to": {"label": "Return items to"},
}

OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    ("INT-74", "payment_method"): {"values": ["bill_me", "credit_card", "auto_debit"], "default": None},
    ("INT-85", "payment_method"): {"values": ["credit_card", "eft", "bank_transfer", "cash", "cheque", "reference"], "default": None},
    ("INT-85", "amount"): {"help": "Blank = the bills chosen, else the whole balance"},
    ("INT-86", "amount"): {"help": "Blank = the whole bill / line (or use a percent)"},
    ("INT-86", "line"): {"label": "Bill line", "placeholder": "Line number or words from it, e.g. city tax"},
    ("INT-86", "reason"): {"type": "picklist", "values": ["billing_error", "tax_exemption", "service_outage", "goodwill", "special_credit",
                                                          "partner_code", "write_off", "other"]},
    ("INT-86", "decision"): {"label": "Supervisor decision (for a request waiting)", "values": ["approve", "reject"], "default": None},
    ("INT-87", "decision"): {"label": "Supervisor decision (for a plan waiting)", "values": ["approve", "reject"], "default": None},
    ("INT-87", "amount"): {"help": "Blank = the bill / alert / balance owed"},
    ("INT-89", "address"): {"label": "Send to", "help": "Blank = the billing profile's email / address"},
    ("INT-78", "address"): {"label": "Premises address"}, ("INT-78", "product"): {"label": "Service on it"},
    ("INT-80", "address"): {"label": "New address (move)"},
    ("INT-81", "description"): {"label": "What should change?"},
    ("INT-98", "description"): {"label": "What happened", "placeholder": "Blank = built from the error"},
    ("INT-98", "line"): {"label": "Failed line"},
    ("INT-92", "amount"): {"label": "Amount owed (new alert)"},
    ("INT-93", "comments"): {"label": "What happened"},
    ("INT-100", "changes"): {"placeholder": "add term Early exit: 3 months' fees; end date 2027-12-31; term 24 months; monthly value 5000; "
                                            "response 4 hours; payment terms Net 45; add order 7; add account Acme York"},
    ("INT-72", "reason"): {"label": "Block reason"},
    ("INT-79", "product"): {"label": "Special rating product", "placeholder": "e.g. Friends & Family"},
}

_PARTY = ("party_id", "company_name")
REQUIRED: dict[str, list[str]] = {
    "INT-72": [], "INT-73": [], "INT-74": [], "INT-75": ["profile_kind"], "INT-76": [], "INT-77": ["address"], "INT-78": [],
    "INT-79": [], "INT-80": ["asset_id", "transaction"], "INT-81": ["description"], "INT-82": ["opportunity_id"], "INT-83": ["opportunity_id"],
    "INT-84": [], "INT-85": ["payment_method"], "INT-86": [], "INT-87": [], "INT-88": ["customer_decision"], "INT-89": [], "INT-90": [],
    "INT-91": [], "INT-92": [], "INT-93": [], "INT-94": ["fraud_alert"], "INT-95": ["parent_ticket_id"], "INT-96": ["ticket_id"],
    "INT-97": ["ticket_id", "overall"], "INT-98": ["order_id"], "INT-99": [], "INT-100": ["agreement_id"], "INT-101": ["agreement_id"],
    "INT-102": ["order_id"],
}

# One of each group must be given.
EITHER: dict[str, list[tuple[str, ...]]] = {
    "INT-72": [("party_id", "company_name", "account_name")], "INT-73": [_PARTY, ("contact_name", "last_name")], "INT-74": [_PARTY], "INT-75": [_PARTY], "INT-76": [_PARTY],
    "INT-78": [("premise_id", "address")], "INT-79": [_PARTY], "INT-81": [("asset_id", "party_id", "company_name")],
    "INT-82": [("related_party_id", "related_account")], "INT-84": [_PARTY],
    "INT-85": [("party_id", "company_name", "invoice", "credit_alert")], "INT-86": [("party_id", "company_name", "invoice", "usage_id", "adjustment")],
    "INT-87": [("party_id", "company_name", "invoice", "credit_alert", "arrangement")], "INT-88": [("adjustment", "arrangement")],
    "INT-89": [("invoice", "party_id", "company_name")], "INT-90": [("invoice", "party_id", "company_name")],
    "INT-92": [("credit_alert", "party_id", "company_name")], "INT-99": [("ticket_id", "query")], "INT-100": [("changes", "sub_agreement")],
}


def missing(code: str, entities: dict[str, Any]) -> list[str]:
    return [" or ".join(g) for g in EITHER.get(code, []) if not any(entities.get(k) not in (None, "", []) for k in g)]


FLOW_FIELDS: dict[str, list[str]] = {code: list(spec[5]) for code, spec in SPECS.items()}

_NAME = r"([A-Z][\w&.'-]*(?:\s+(?:[A-Z][\w&.'-]*|&|of))*)"
RULES: list[tuple[str, re.Pattern]] = [
    ("INT-88", re.compile(r"\b(?:customer|they|he|she|client)\s+(?:has\s+)?(?:accept|reject|decline|agree)\w*\b[^.]{0,50}\b(?:adjustment|arrangement|plan|ADJ-\d|PA-\d)"
                          r"|\b(?:record|log)\b[^.]{0,30}\bdecision\b|\b(?:accept|reject)\w*\s+(?:the\s+)?(?:outcome|terms)\b", re.I)),
    ("INT-87", re.compile(r"\bpayment\s+(?:arrangement|plan)\b|\binstal+ments?\b|\bdefer\w*\s+(?:the\s+|their\s+|a\s+|his\s+|her\s+)?(?:payment|bill|invoice|INV-)"
                          r"|\b(?:approve|reject)\w*\s+(?:the\s+)?(?:arrangement|PA-\d)", re.I)),
    ("INT-86", re.compile(r"\b(?:approve|reject)\w*\s+(?:the\s+)?(?:adjustment|ADJ-\d)|\bbill(?:ing)?\s+adjustment\b|\bwrite\s+off\b"
                          r"|\b(?:adjust\w*|credit|refund)\b(?!\s+(?:alert|check|limit|rating|card|profile))[^.]{0,60}\b(?:bill|invoice|INV-\d+|unbilled|usage\s+event)\b", re.I)),
    ("INT-89", re.compile(r"\b(?:duplicate|reprint|resend|re-send)\b[^.]{0,30}\b(?:bill|invoice|statement|INV-)|\bcopy\s+of\s+(?:the\s+|their\s+|his\s+|her\s+)?"
                          r"(?:last\s+)?(?:bill|invoice|statement|INV-)", re.I)),
    ("INT-90", re.compile(r"\bexplain\b[^.]{0,30}\b(?:bill|invoice|charges?|INV-)|\bwhy\s+is\b[^.]{0,40}\bbill\b|\bbill\s+shock\b"
                          r"|\bunbilled\s+(?:charges|usage)\b|\busage\s+(?:details|history)\b", re.I)),
    ("INT-85", re.compile(r"\b(?:take|record|post|enter|apply|make)\s+(?:a\s+|the\s+)?(?:bill\s+)?payment\b(?![^.]*\b(?:order|SO-\d))"
                          r"|\bpaid\s+(?:off\s+)?(?:the\s+|their\s+|his\s+|her\s+|its\s+)?(?:bill|invoice|balance|outstanding|INV-)"
                          r"|\bpay(?:s|ing)?\s+(?:off\s+)?(?:the\s+|their\s+|his\s+|her\s+|its\s+)?(?:bill|invoice|balance|outstanding|INV-)", re.I)),
    ("INT-84", re.compile(r"\b(?:run|generate|create|produce|issue|cut)\s+(?:a\s+|the\s+)?(?:bill|invoice|statement)(?!\s+(?:adjustment|payment|profile))\b"
                          r"|\bbill(?:ing)?\s+run\b", re.I)),
    ("INT-91", re.compile(r"\brun\s+(?:the\s+)?collections?\b|\bcollections?\s+run\b|\bdunning\s+run\b|\bcheck\s+(?:for\s+)?overdue\s+(?:bills|invoices|accounts)\b", re.I)),
    ("INT-92", re.compile(r"\bcredit\s+alert\b|\bCA-\d+|\bcollection\s+action\b|\b(?:send|issue)\s+(?:a\s+)?(?:harsh\s+|courtesy\s+)?dunning\s+letter\b"
                          r"|\brefer\b[^.]{0,30}\b(?:collection|outside)\s+agency\b", re.I)),
    ("INT-76", re.compile(r"\bfraud\s+(?:thresholds?|limits?|profile)\b|\b(?:raise|lower|change|set|increase|decrease)\b[^.]{0,30}"
                          r"\b(?:daily|international|domestic|call)\s+(?:threshold|limit)\b|\bblock\s+international\b", re.I)),
    ("INT-93", re.compile(r"\b(?:check|scan|run)\b[^.]{0,20}\bfraud\b|\bfraud\s+(?:check|scan)\b|\b(?:raise|open|log|report)\b[^.]{0,20}\bfraud\b"
                          r"|\b(?:stolen|lost|cloned)\s+(?:phone|handset|sim|mobile)\b", re.I)),
    ("INT-94", re.compile(r"\bfraud\s+alert\b|\bFA-\d+", re.I)),
    ("INT-74", re.compile(r"\bbilling\s+profile\b|\bbills?\s+(?:\w+\s+){0,4}(?:by|via)\s+(?:e-?mail|paper|post|sms)\b|\bauto[\s-]?top[\s-]?up\b"
                          r"|\bswitch\b[^.]{0,30}\b(?:to\s+)?(?:prepaid|postpaid)\b|\bpaperless\s+billing\b", re.I)),
    ("INT-75", re.compile(r"\b(?:exemption|financial|site|statement|customer|loyalty)\s+profile\b|\btax[\s-]exempt(?:ion)?\b|\blifeline\b"
                          r"|\bchurn\s+(?:risk|score)\b", re.I)),
    ("INT-82", re.compile(r"\b(?:related\s+site|partner)\b[^.]{0,40}\bopportunit\w*|\bopportunit\w*\b[^.]{0,40}\b(?:related\s+site|partner)\b", re.I)),
    ("INT-83", re.compile(r"\b(?:profile|qualification\s+script)\b[^.]{0,30}\bopportunit\w*\b|\bopportunity\s+profile\b|\bBANT\b", re.I)),
    ("INT-72", re.compile(r"\baccount\s+hierarchy\b|\b(?:billing|service)\s+(?:aggregator\s+)?account\b[^.]{0,60}\bunder\b"
                          r"|\bunder\b[^.]{0,60}\bas\s+(?:an?\s+|its\s+|their\s+)?(?:billing|service|child|customer)\b|\b(?:child|sub-?)\s*accounts?\b"
                          r"|\b(?:move|re-?parent)\b[^.]{0,50}\bunder\s+(?:customer|account|the\s+parent|[A-Z])|\bparent\s+account\b"
                          r"|\bmark\b[^.]{0,30}\bas\s+(?:a\s+)?partner\b|\b(?:block|unblock)\s+(?:the\s+)?account\b", re.I)),
    ("INT-73", re.compile(r"\b(?:add|create|new)\s+(?:a\s+)?contact\b|\bcontact\b[^.]{0,30}\b(?:to|for|on)\s+(?:the\s+)?account\b", re.I)),
    ("INT-77", re.compile(r"\b(?:validate|verify|standardi[sz]e|check)\s+(?:the\s+|this\s+|an?\s+)?(?:service\s+|billing\s+|mailing\s+)?address\b|\bMSAG\b"
                          r"|\brevalidate\b", re.I)),
    ("INT-79", re.compile(r"\bfriends?\s*(?:&|and)\s*family\b|\bspecial\s+rating\b|\bfavou?rite\s+(?:numbers?|call\s+list)\b|\bF&F\b", re.I)),
    ("INT-78", re.compile(r"\bpremises?\b|\bservice\s+point\b|\bhook[\s-]?up\b", re.I)),
    ("INT-81", re.compile(r"\bchange\s+request\b|\bCR-\d+", re.I)),
    ("INT-80", re.compile(r"\basset\s*#?\s*\d+\b[^.]{0,60}\b(?:install|replace|swap|remove|upgrade|backup|relate|component|move)\w*"
                          r"|\b(?:install|replace|swap|remove)\w*\b[^.]{0,40}\b(?:on|in|from)\s+asset\b|\basset\s+transaction\b|\brelate\w*\s+asset", re.I)),
    ("INT-95", re.compile(r"\b(?:link|associate|attach|group)\b[^.]{0,40}\btickets?\b[^.]{0,40}\b(?:parent|outage|network)\b|\bparent\s+ticket\b"
                          r"|\bchild\s+tickets?\b", re.I)),
    ("INT-97", re.compile(r"\b(?:satisfaction|CSAT)\s+survey\b|\bsurvey\b[^.]{0,30}\bticket\b", re.I)),
    ("INT-96", re.compile(r"\b(?:resolve|close|reopen|re-open)\s+(?:the\s+|this\s+)?(?:trouble\s+)?ticket\b", re.I)),
    ("INT-98", re.compile(r"\b(?:fallout|order\s+failure|failed\s+order)\b|\border\s*#?\s*(?:SO-0*)?\d+\s+(?:has\s+)?failed\b", re.I)),
    ("INT-99", re.compile(r"\b(?:find|search|look\s+up|suggest)\b[^.]{0,20}\bsolutions?\b|\bknowledge\s+base\b|\bhow\s+(?:was|were)\b[^.]{0,30}\bresolved\b", re.I)),
    ("INT-101", re.compile(r"\b(?:generate|create|print|draft|produce)\s+(?:the\s+|an?\s+)?agreement\s+document\b|\bauto\s*-?document\b"
                           r"|\bagreement\b[^.]{0,20}\bdocument\b", re.I)),
    ("INT-100", re.compile(r"\b(?:revise|amend|update|change)\s+(?:the\s+|this\s+)?agreement\b|\bsub-?agreement\b"
                           r"|\bAGR?-\d+\b[^.]{0,40}\b(?:add\s+term|extend|revise)", re.I)),
    ("INT-102", re.compile(r"\b(?:allocate|split|send)\b[^.]{0,40}\bproviders?\b|\bprovider\s+work\s+orders?\b", re.I)),
]


def _num(pattern: str, text: str) -> int | None:
    m = re.search(pattern, text, re.I)
    return int(m.group(1)) if m else None


def _money(text: str) -> float | None:
    m = re.search(r"(?:₹|rs\.?|sar|usd|\$|amount\s+(?:of\s+)?|payment\s+of\s+|paid\s+|pays?\s+|credit\s+(?:of\s+)?|debit\s+(?:of\s+)?|to\s+)\s*([\d,]+(?:\.\d+)?)\b(?!\s*%)",
                  text, re.I)
    return float(m.group(1).replace(",", "")) if m else None


def _after_dash(text: str) -> str | None:
    m = re.search(r"\s[-–—]\s+(.+)$|\bbecause\b\s+(.+)$", text, re.I)
    return next((g.strip(" .") for g in m.groups() if g), None) if m else None


def _ref(prefix: str, text: str) -> str | None:
    m = re.search(rf"\b({prefix}-\d+)\b", text, re.I)
    return m.group(1).upper() if m else None


def _company(text: str) -> str | None:
    for pattern in (r"\b(?:for|of|to)\s+" + _NAME + r"(?:'s)?\b", r"^" + _NAME + r"(?:'s)\s", r"\b" + _NAME + r"'s\b",
                    r"^" + _NAME + r"\s+(?:is|has|wants|needs|asked)\b"):
        for m in re.finditer(pattern, text):
            name = re.sub(r"'s$", "", m.group(1).strip())
            if name.split()[0].lower() not in {"the", "a", "an", "this", "customer", "account", "order", "ticket", "bill", "invoice", "asset",
                                               "opportunity", "agreement", "parent", "service", "billing", "inv", "pa", "adj", "ca", "fa", "agr"} \
                    and not re.match(r"^(?:INV|ADJ|PA|CA|FA|AGR|SO|CR)\b", name):
                return name
    return None


def _bool_word(text: str, yes: str, no: str) -> bool | None:
    if re.search(no, text, re.I):
        return False
    if re.search(yes, text, re.I):
        return True
    return None


def extract(code: str, text: str, fields: dict[str, Any], loose: dict[str, Any]) -> dict[str, Any]:
    """Pull the fields of one of these intents out of free text."""
    e: dict[str, Any] = {}
    keys = SPECS[code][5]
    for k in keys:
        v = fields.get(k)
        if v not in (None, ""):
            e[k] = v
    if "party_id" in keys and "party_id" not in e and (n := _num(r"\b(?:customer|account|party)\s*#?\s*(\d+)\b", text)):
        e["party_id"] = n
    if "company_name" in keys and "party_id" not in e and "company_name" not in e and (c := _company(text)):
        e["company_name"] = c
    for key, pattern in (("order_id", r"\b(?:sales\s+)?order\s*#?\s*(?:SO-0*)?(\d+)"), ("ticket_id", r"\bticket\s*#?\s*(?:TT-0*)?(\d+)"),
                         ("asset_id", r"\basset\s*#?\s*(\d+)"), ("opportunity_id", r"\bopportunit(?:y|ies)\s*#?\s*(\d+)"),
                         ("premise_id", r"\bpremises?\s*#\s*(\d+)"), ("usage_id", r"\busage\s+(?:event\s*)?#?\s*(\d+)")):
        if key in keys and key not in e and (n := _num(pattern, text)):
            e[key] = n
    for key, prefix in (("invoice", "INV"), ("credit_alert", "CA"), ("fraud_alert", "FA"), ("adjustment", "ADJ"), ("arrangement", "PA"),
                        ("agreement_id", "AGR?")):
        if key in keys and key not in e and (r := _ref(prefix, text)):
            e[key] = r
    if "invoice" in keys and "invoice" not in e and (n := _num(r"\b(?:bill|invoice)\s*#\s*(\d+)", text)):
        e["invoice"] = n
    if "agreement_id" in keys and "agreement_id" not in e and (n := _num(r"\bagreement\s*#?\s*(\d+)", text)):
        e["agreement_id"] = n

    if code == "INT-72":
        if m := re.search(r"\b(?i:add|create|new)\s+(?i:an?\s+)?(?i:(?:billing|service)\s+(?:aggregator\s+)?account\s+)?" + _NAME + r"\s+(?i:under)\b",
                          text):
            e.setdefault("account_name", m.group(1).strip())
            e.pop("company_name", None)
        if m := re.search(r"\bunder\s+(?:customer|account)\s*#?\s*(\d+)", text, re.I):
            e.setdefault("parent_id", int(m.group(1)))
        elif m := re.search(r"\bunder\s+(?:the\s+)?" + _NAME, text):
            e.setdefault("parent", m.group(1).strip())
        if m := re.search(r"\b(?:move|re-?parent)\s+(?:customer|account)\s*#?\s*(\d+)", text, re.I):
            e["party_id"] = int(m.group(1))
        elif (m := re.search(r"\b(?i:move|re-?parent)\s+" + _NAME + r"\s+(?i:under)\b", text)) and "party_id" not in e:
            e["company_name"] = m.group(1).strip()
        for cls in ("billing_aggregator", "service_aggregator", "billing", "service", "customer"):
            if re.search(r"\bas\s+(?:an?\s+|its\s+|their\s+)?" + cls.replace("_", r"\s+") + r"(?:\s+account)?\b", text, re.I) or \
                    re.search(r"\b" + cls.replace("_", r"\s+") + r"\s+account\b", text, re.I):
                e.setdefault("account_class", cls)
                break
        if re.search(r"\bas\s+(?:a\s+)?partner\b", text, re.I):
            e["partner"] = True
        if re.search(r"\bunblock\b", text, re.I):
            e["good_standing"] = True
        elif re.search(r"\bblock\b", text, re.I):
            e["good_standing"] = False
            if r := _after_dash(text):
                e.setdefault("reason", r)
    elif code == "INT-73":
        if m := re.search(r"\b(?i:contact)\s+([A-Z][a-z'-]+(?:\s+[A-Z][\w'-]+)*)", text):
            e.setdefault("contact_name", m.group(1))
        if m := re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text):
            e.setdefault("email", m.group(0))
        if m := re.search(r"\+?\d[\d\s-]{6,}\d", text):
            e.setdefault("phone", m.group(0).strip())
        if m := re.search(r"\bas\s+(?:the\s+|a\s+|an\s+)?(billing|technical|decision[\s-]maker|user)\b", text, re.I):
            e.setdefault("role", m.group(1).lower().replace(" ", "_").replace("-", "_"))
        if m := re.search(r"\bto\s+" + _NAME, text):
            e["company_name"] = m.group(1).strip()
        if re.search(r"\bprimary\b", text, re.I):
            e["primary"] = True
    elif code == "INT-74":
        if m := re.search(r"\b(monthly|quarterly|annual(?:ly)?|yearly)\b", text, re.I):
            e.setdefault("frequency", {"annually": "annual", "yearly": "annual"}.get(m.group(1).lower(), m.group(1).lower()))
        if m := re.search(r"\b(prepaid|postpaid|pre-paid|post-paid)\b", text, re.I):
            e.setdefault("payment_type", m.group(1).lower().replace("-", ""))
        if re.search(r"\bcard\b", text, re.I):
            e.setdefault("payment_method", "credit_card")
            if m := re.search(r"\bending(?:\s+in)?\s+(\d{4})\b", text, re.I):
                e.setdefault("card_last4", m.group(1))
            if m := re.search(r"\b(visa|mastercard|amex|mada)\b", text, re.I):
                e.setdefault("card_brand", m.group(1).lower())
        elif re.search(r"\b(?:direct|auto(?:matic)?)\s+debit\b", text, re.I):
            e.setdefault("payment_method", "auto_debit")
            if m := re.search(r"\b(?:account\s+)?ending(?:\s+in)?\s+(\d{4})\b", text, re.I):
                e.setdefault("bank_last4", m.group(1))
        elif re.search(r"\bbill\s+me\b|\bon\s+invoice\b", text, re.I):
            e.setdefault("payment_method", "bill_me")
        if m := re.search(r"\b(?:by|via)\s+(e-?mail|paper|post|sms|portal)\b", text, re.I):
            e.setdefault("bill_media", {"e-mail": "email", "post": "paper"}.get(m.group(1).lower(), m.group(1).lower()))
        elif re.search(r"\bpaperless\b", text, re.I):
            e.setdefault("bill_media", "email")
        if m := re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text):
            e.setdefault("email", m.group(0))
        if re.search(r"\bauto[\s-]?top[\s-]?up\b", text, re.I):
            e["auto_topup"] = True
            if m := re.search(r"\btop[\s-]?up\s+(?:of\s+)?([\d,]+(?:\.\d+)?)", text, re.I):
                e.setdefault("topup_amount", float(m.group(1).replace(",", "")))
            if m := re.search(r"\b(?:below|under|at)\s+([\d,]+(?:\.\d+)?)", text, re.I):
                e.setdefault("topup_threshold", float(m.group(1).replace(",", "")))
        if m := re.search(r"\bon\s+the\s+(\d{1,2})(?:st|nd|rd|th)\b", text, re.I):
            e.setdefault("bill_day", int(m.group(1)))
    elif code == "INT-75":
        if m := re.search(r"\b(exemption|financial|site|statement|customer|loyalty)\s+profile\b", text, re.I):
            e.setdefault("profile_kind", m.group(1).lower())
        elif re.search(r"\btax[\s-]exempt|\blifeline\b", text, re.I):
            e.setdefault("profile_kind", "exemption")
        elif re.search(r"\bchurn\b", text, re.I):
            e.setdefault("profile_kind", "loyalty")
        if re.search(r"\b(?:not|no\s+longer)\s+tax[\s-]exempt\b", text, re.I):
            e.setdefault("values", "tax exempt: no")
        elif re.search(r"\btax[\s-]exempt\b", text, re.I):
            vals = ["tax exempt: yes"]
            if m := re.search(r"\bcertificate\s*(?:#|no\.?|number)?\s*([\w-]*\d[\w-]*)", text, re.I):
                vals.append(f"tax exemption number: {m.group(1)}")
            e.setdefault("values", ", ".join(vals))
        elif m := re.search(r":\s*(.+)$", text):
            e.setdefault("values", m.group(1).strip())
        if re.search(r"\bcredit[\s-]check\b", text, re.I):
            e["credit_check"] = True
            e.setdefault("profile_kind", "financial")
    elif code == "INT-76":
        value = re.search(r"\b(?:to|at|of)\s+([\d,]+(?:\.\d+)?)\b|[:=]\s*([\d,]+(?:\.\d+)?)\b", text, re.I)
        for key, pat in (("international_threshold", r"\binternational\s+(?:fraud\s+)?(?:threshold|limit)"),
                         ("daily_threshold", r"\bdaily\s+(?:fraud\s+)?(?:threshold|limit)"),
                         ("max_call_minutes", r"\b(?:call\s+length|longest\s+call|call\s+duration)\b"), ("max_calls", r"\bcalls\s+(?:a|per)\s+day\b"),
                         ("sensitive_threshold", r"\b(?:sensitive|premium)\s+(?:numbers?|calls?)\b")):
            if key not in e and value and re.search(pat, text, re.I):
                e[key] = float((value.group(1) or value.group(2)).replace(",", ""))
                break
        if re.search(r"\bunblock\s+international\b", text, re.I):
            e["block_international"] = False
        elif re.search(r"\bblock\s+international\b", text, re.I):
            e["block_international"] = True
    elif code == "INT-77":
        if m := re.search(r"\baddress\s*:?\s+(.+?)(?:\s+for\s+(?:customer\s+\d+|[A-Z][\w&.' -]*?)(?:\s+and\b|$)|\s+and\s+make\b|$)", text, re.I):
            e.setdefault("address", m.group(1).strip(" ,."))
        if re.search(r"\b(?:make|mark|flag)\b[^.]{0,20}\bpremises?\b|\bas\s+a\s+premises?\b", text, re.I):
            e["make_premise"] = True
        if m := re.search(r"\bfor\s+" + _NAME + r"\s*(?:and\b|$)", text):
            e["company_name"] = m.group(1).strip()
    elif code == "INT-78":
        if m := re.search(r"\bat\s+(.+?)(?::|\s+-\s+|\s+for\s+|\s+with\s+service\s+point|$)", text, re.I):
            e.setdefault("address", m.group(1).strip(" ,."))
        if m := re.search(r"\b(?i:service\s+point)\s+([A-Z][\w/-]*\d[\w/-]*|\d[\w/-]*)", text):
            e.setdefault("service_point", m.group(1))
        if m := re.search(r"\bfor\s+([A-Z][\w +-]*\d*[\w+-]*)\s*$", text):
            e.setdefault("product", m.group(1).strip())
            if str(e.get("company_name") or "") and e["product"].startswith(str(e.get("company_name"))):
                e.pop("company_name")  # "for Fiber 900" is the service, not a customer
    elif code == "INT-79":
        nums = re.findall(r"\+?\d[\d -]{6,}\d", text)
        if re.search(r"\bremove\b|\bdelete\b|\btake\s+off\b", text, re.I):
            e.setdefault("remove_numbers", ", ".join(n.strip() for n in nums))
        elif nums:
            e.setdefault("add_numbers", ", ".join(n.strip() for n in nums))
        if m := re.search(r"\bmax(?:imum)?\s+(?:of\s+)?(\d+)", text, re.I):
            e.setdefault("max_items", int(m.group(1)))
        if (c := _company(text)) and "party_id" not in e:
            e["company_name"] = c
    elif code == "INT-80":
        for kind in ("install", "replace", "remove", "upgrade", "move"):
            if re.search(rf"\b{kind}\w*\b", text, re.I) or (kind == "replace" and re.search(r"\bswap\w*\b", text, re.I)):
                e.setdefault("transaction", kind)
                break
        if m := re.search(r"\b(backup|option|redundant)\b", text, re.I):
            e.setdefault("transaction", "relate")
            e.setdefault("relation", m.group(1).lower())
            if n := _num(r"\basset\s*#?\s*\d+\b[^.]*?\basset\s*#?\s*(\d+)", text):
                e.setdefault("related_asset_id", n)
        if m := re.search(r"\b(?:install|replace|swap|remove|upgrade)\w*\s+(?:the\s+|a\s+|an\s+)?(.+?)\s+(?:on|in|from|of)\s+asset\b", text, re.I):
            e.setdefault("component", m.group(1).strip())
        if m := re.search(r"\bserial\s*(?:#|no\.?|number)?\s*([\w-]+)", text, re.I):
            e.setdefault("serial_number", m.group(1))
    elif code == "INT-81":
        if m := re.search(r"\bchange\s+request\b[^:]*:\s*(.+)$", text, re.I):
            e.setdefault("description", m.group(1).strip())
        elif r := _after_dash(text):
            e.setdefault("description", r)
        if m := re.search(r"\b(enhancement|defect|configuration)\b", text, re.I):
            e.setdefault("cr_type", m.group(1).lower())
    elif code == "INT-82":
        e.setdefault("link_kind", "partner" if re.search(r"\bpartner\b", text, re.I) else "related_site")
        if m := re.search(r"\b(?i:add)\s+(?i:partner\s+)?(?:(?i:account|customer)\s*#?\s*(\d+)|" + _NAME + r")\s+(?i:as\s+|to\b)", text):
            if m.group(1):
                e.setdefault("related_party_id", int(m.group(1)))
            else:
                e.setdefault("related_account", m.group(2).strip())
    elif code == "INT-83":
        if m := re.search(r":\s*(.+)$", text):
            e.setdefault("answers", m.group(1).strip())
    elif code == "INT-84":
        if m := re.search(r"\b(?:up\s+to|until|through|to)\s+(\d{4}-\d{2}-\d{2})\b", text, re.I):
            e.setdefault("period_end", m.group(1))
    elif code == "INT-85":
        if (p := _money(text)) is not None:
            e.setdefault("amount", p)
        if re.search(r"\bcard\b", text, re.I):
            e.setdefault("payment_method", "credit_card")
            if m := re.search(r"\bending(?:\s+in)?\s+(\d{4})\b", text, re.I):
                e.setdefault("card_last4", m.group(1))
            if m := re.search(r"\b(visa|mastercard|amex|mada)\b", text, re.I):
                e.setdefault("card_brand", m.group(1).lower())
        elif re.search(r"\b(?:eft|direct\s+debit|bank\s+account)\b", text, re.I):
            e.setdefault("payment_method", "eft")
            if m := re.search(r"\bending(?:\s+in)?\s+(\d{4})\b", text, re.I):
                e.setdefault("bank_last4", m.group(1))
        elif m := re.search(r"\b(bank\s+transfer|transfer|cash|cheque|check)\b", text, re.I):
            e.setdefault("payment_method", {"transfer": "bank_transfer", "check": "cheque"}.get(m.group(1).lower(), m.group(1).lower().replace(" ", "_")))
        elif re.search(r"\b(?:post\s+office|at\s+the\s+bank|bank\s+counter)\b", text, re.I):
            e.setdefault("payment_method", "reference")
        if m := re.search(r"\b(?:ref(?:erence)?|receipt)\s*(?:#|no\.?|number)?\s*([\w-]*\d[\w-]*)", text, re.I):
            e.setdefault("reference", m.group(1))
        refs = re.findall(r"\bINV-\d+\b", text, re.I)
        if len(refs) > 1:
            e["invoice"] = ", ".join(r.upper() for r in refs)
    elif code == "INT-86":
        if re.search(r"\b(?:approve|reject)\w*\s+(?:the\s+)?(?:adjustment|ADJ-\d)", text, re.I):
            e["decision"] = "reject" if re.search(r"\breject", text, re.I) else "approve"
        if m := re.search(r"(\d+(?:\.\d+)?)\s*%", text):
            e.setdefault("percent", float(m.group(1)))
        elif (p := _money(text)) is not None:
            e.setdefault("amount", p)
        elif m := re.search(r"\b(?:credit|adjust\w*|refund|debit)\s+(?:of\s+)?([\d,]+(?:\.\d+)?)\b", text, re.I):
            e.setdefault("amount", float(m.group(1).replace(",", "")))
        if m := re.search(r"\bon\s+(?:the\s+)?(.+?)\s+line\b", text, re.I):
            e.setdefault("line", m.group(1).strip())
        elif m := re.search(r"\bline\s+(\d+)\b", text, re.I):
            e.setdefault("line", m.group(1))
        if re.search(r"\bdebit\b", text, re.I):
            e.setdefault("adjustment_type", "debit")
        if re.search(r"\bwrite\s+off\b", text, re.I):
            e.setdefault("reason", "write_off")
        elif re.search(r"\btax[\s-]exempt", text, re.I):
            e.setdefault("reason", "tax_exemption")
        elif re.search(r"\boutage\b", text, re.I):
            e.setdefault("reason", "service_outage")
        elif re.search(r"\bgoodwill\b", text, re.I):
            e.setdefault("reason", "goodwill")
        elif re.search(r"\b(?:wrong|error|overcharg|incorrect)\w*\b", text, re.I):
            e.setdefault("reason", "billing_error")
        if r := _after_dash(text):
            e.setdefault("comments", r)
    elif code == "INT-87":
        if re.search(r"\b(?:approve|reject)\w*\s+(?:the\s+)?(?:arrangement|PA-\d)", text, re.I):
            e["decision"] = "reject" if re.search(r"\breject", text, re.I) else "approve"
        if m := re.search(r"\b(\d+)\s*(?:monthly\s+)?instal+ments?\b|\binstal+ments?\s*:?\s*(\d+)", text, re.I):
            e.setdefault("installments", int(m.group(1) or m.group(2)))
        if re.search(r"\bdefer", text, re.I):
            e.setdefault("plan_type", "deferral")
        if m := re.search(r"\b(?:to|until|from|starting)\s+(\d{4}-\d{2}-\d{2})\b", text, re.I):
            e.setdefault("start_date", m.group(1))
        if (p := _money(text)) is not None and not re.search(r"\b(?:to|until)\s+\d{4}-", text):
            e.setdefault("amount", p)
    elif code == "INT-88":
        e.setdefault("customer_decision", "rejected" if re.search(r"\b(?:reject|decline|refus)\w*", text, re.I) else "accepted")
    elif code == "INT-89":
        if m := re.search(r"\b(?:by|via)\s+(e-?mail|paper|post|portal|sms)\b", text, re.I):
            e.setdefault("bill_media", {"e-mail": "email", "post": "paper"}.get(m.group(1).lower(), m.group(1).lower()))
        if m := re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text):
            e.setdefault("address", m.group(0))
            e.setdefault("bill_media", "email")
    elif code == "INT-91":
        if m := re.search(r"\bas\s+of\s+(\d{4}-\d{2}-\d{2})\b", text, re.I):
            e.setdefault("as_of", m.group(1))
    elif code == "INT-92":
        low = text.lower()
        for key, words in (("courtesy_dunning_letter", r"courtesy\s+(?:dunning\s+)?letter"), ("harsh_dunning_letter", r"harsh\s+(?:dunning\s+)?letter"),
                           ("demanding_phone_call", r"demanding\s+(?:phone\s+)?call"), ("courtesy_phone_call", r"courtesy\s+(?:phone\s+)?call"),
                           ("courtesy_email_sms", r"(?:email|sms|text)\s+reminder|reminder\s+(?:email|sms)"),
                           ("no_action_impose_late_fee", r"late\s+fee"), ("no_action_write_off", r"write[\s-]?off"),
                           ("suspend_services", r"suspend"), ("terminate_services", r"terminat"), ("refer_to_outside_agency", r"agency"),
                           ("payment_arrangement_agreed", r"arrangement\s+agreed")):
            if re.search(r"\b" + words, low):
                e.setdefault("action_type", key)
                break
        if m := re.search(r"\b(?:close|closed|cancel|cancelled|reject|rejected|reopen|pending)\b", low):
            e.setdefault("status", {"close": "closed", "cancel": "cancelled", "reject": "rejected", "reopen": "open"}.get(m.group(0), m.group(0)))
        if m := re.search(r":\s*(.+)$", text):
            e.setdefault("comments", m.group(1).strip())
        elif r := _after_dash(text):
            e.setdefault("comments", r)
        if m := re.search(r"\bassign\w*\s+(?:it\s+)?to\s+([A-Z][\w .'-]+)", text):
            e.setdefault("owner", m.group(1).strip())
    elif code == "INT-93":
        if m := re.search(r"\b(stolen|lost|cloned|clone|subscription|returned\s+mail)\b", text, re.I):
            e.setdefault("fraud_type", {"stolen": "stolen_handset", "lost": "stolen_handset", "cloned": "invalid_calls", "clone": "invalid_calls",
                                        "subscription": "subscription"}.get(m.group(1).lower(), "returned_mail"))
            if m2 := re.search(r"\+?\d[\d -]{6,}\d", text):
                e.setdefault("service", m2.group(0).strip())
            e.setdefault("comments", text.strip())
        else:
            e.setdefault("scan", True)
        if m := re.search(r"\b(?:on|for)\s+(\d{4}-\d{2}-\d{2})\b", text):
            e.setdefault("day", m.group(1))
    elif code == "INT-94":
        low = text.lower()
        for key, words in (("false_positive", r"not\s+fraud|false\s+(?:alarm|positive)|were\s+(?:theirs|genuine|his|hers)|made\s+the\s+calls"),
                           ("confirm", r"confirm|not\s+(?:theirs|his|hers)|did\s*n.?t\s+make"), ("no_answer", r"no\s+answer|could\s*n.?t\s+reach|unreachable"),
                           ("disconnect", r"disconnect|cut\s+off"), ("investigate", r"investigat"), ("close", r"\bclose")):
            if re.search(words, low):
                e.setdefault("fraud_outcome", key)
                break
        if m := re.search(r":\s*(.+)$", text):
            e.setdefault("comments", m.group(1).strip())
    elif code == "INT-95":
        if m := re.search(r"\bparent\s+(?:ticket\s*)?#?\s*(?:TT-0*)?(\d+)", text, re.I):
            e["parent_ticket_id"] = int(m.group(1))
        elif m := re.search(r"\bto\s+ticket\s*#?\s*(\d+)", text, re.I):
            e["parent_ticket_id"] = int(m.group(1))
        if m := re.search(r"\btickets?\s+((?:#?\d+\s*(?:,|and|&)?\s*)+)\s+(?:to|under|with)\b", text, re.I):
            ids = [x for x in re.findall(r"\d+", m.group(1)) if int(x) != e.get("parent_ticket_id")]
            if ids:
                e.setdefault("ticket_ids", ", ".join(ids))
        if m := re.search(r"\b(?:on|for)\s+((?:exchange|circuit|cable|node|cell|tower|number)\s+[\w/-]+)", text, re.I):
            e.setdefault("service_item", m.group(1))
        e.pop("ticket_id", None)
    elif code == "INT-96":
        e.setdefault("ticket_action", "reopen" if re.search(r"\bre-?open\b", text, re.I) else "resolve")
        if (r := _after_dash(text)) and "resolution" not in e:
            e["resolution"] = r
        elif m := re.search(r":\s*(.+)$", text):
            e.setdefault("resolution", m.group(1).strip())
        if m := re.search(r"\bsolution\s*#?\s*(\d+)", text, re.I):
            e.setdefault("solution", m.group(1))
        if re.search(r"\b(?:and|with)\s+(?:its|the|all)?\s*(?:child|children)\b", text, re.I):
            e["close_children"] = True
        if re.search(r"\bsave\b[^.]{0,20}\bsolution\b", text, re.I):
            e["save_solution"] = True
    elif code == "INT-97":
        for key in ("overall", "timeliness", "professionalism"):
            if m := re.search(rf"\b{key}\s*(?:rating\s*)?(?:of\s+|:|=)?\s*(\d)\b", text, re.I):
                e.setdefault(key, int(m.group(1)))
        if "overall" not in e and (m := re.search(r"\brated\b[^.\d]{0,20}(\d)\s*(?:/|out\s+of)\s*5", text, re.I)):
            e["overall"] = int(m.group(1))
        if m := re.search(r"\b(?:would\s+)?recommend\w*\s*(?:of\s+|:|=)?\s*(\d{1,2})\b", text, re.I):
            e.setdefault("would_recommend", int(m.group(1)))
        if v := _bool_word(text, r"\b(?:fixed|resolved|solved)\b", r"\bnot\s+(?:fixed|resolved|solved)\b|\bstill\s+(?:broken|not\s+working)\b"):
            e.setdefault("resolved", v)
        elif v is False:
            e.setdefault("resolved", False)
    elif code == "INT-98":
        if m := re.search(r"\b(?:error|code)\s*:?\s*([A-Z0-9_.-]{3,})", text):
            e.setdefault("error_code", m.group(1))
        if m := re.search(r"\bfailed\s+(?:in|at)\s+(?:the\s+)?([a-z]+)", text, re.I):
            e.setdefault("system_of_failure", m.group(1).lower())
    elif code == "INT-99":
        if "ticket_id" not in e and (m := re.search(r"\b(?:for|about|to)\s+(.+)$", text, re.I)):
            e.setdefault("query", m.group(1).strip(" .?"))
    elif code == "INT-100":
        if m := re.search(r"\bsub-?agreement\s+(?:called\s+|named\s+)?['\"]?([^'\":]+?)['\"]?(?:\s+(?:under|to|for)\b|$)", text, re.I):
            e.setdefault("sub_agreement", m.group(1).strip())
        elif m := re.search(r":\s*(.+)$", text):
            e.setdefault("changes", m.group(1).strip())
    elif code == "INT-101":
        if re.search(r"\bsigned\b", text, re.I):
            e["signed"] = True
    elif code == "INT-102":
        if re.search(r"\bre-?allocate\b|\bagain\b", text, re.I):
            e["reallocate"] = True
    return e


NEXT: dict[str, list[tuple[str, str]]] = {
    "INT-72": [("INT-73", "Add a contact"), ("INT-74", "Set up its billing profile")],
    "INT-73": [("INT-74", "Set up a billing profile")],
    "INT-74": [("INT-84", "Run a bill")],
    "INT-77": [("INT-78", "Register a premises hookup")],
    "INT-84": [("INT-90", "Explain the bill"), ("INT-85", "Take a payment"), ("INT-89", "Send a copy")],
    "INT-90": [("INT-86", "Adjust the bill"), ("INT-87", "Offer a payment arrangement")],
    "INT-86": [("INT-88", "Record the customer's decision")],
    "INT-87": [("INT-88", "Record the customer's decision")],
    "INT-91": [("INT-92", "Work a credit alert")],
    "INT-92": [("INT-85", "Take a payment"), ("INT-87", "Agree a payment arrangement"), ("INT-09", "Suspend the service")],
    "INT-93": [("INT-94", "Work the fraud alert"), ("INT-76", "Change the thresholds")],
    "INT-94": [("INT-09", "Disconnect the service"), ("INT-76", "Change the thresholds")],
    "INT-95": [("INT-96", "Resolve the parent ticket")],
    "INT-96": [("INT-97", "Record a satisfaction survey")],
    "INT-98": [("INT-96", "Resolve the fallout ticket"), ("INT-71", "Track the order")],
    "INT-99": [("INT-96", "Resolve the ticket with it")],
    "INT-100": [("INT-101", "Generate the agreement document")],
    "INT-102": [("INT-71", "Track the order")],
    "INT-82": [("INT-83", "Profile the opportunity")],
}

# Generated training templates (appended at the END of training_data._T).
TRAINING: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = [
    ("INT-72", [("Add {company} York under {company} as a service account", {"account_name": "{company} York", "parent": "{company}", "account_class": "service"}),
                ("move customer {id} under customer {id2}", {"party_id": "{id}", "parent_id": "{id2}"}),
                ("set up the account hierarchy for {company}", {"company_name": "{company}"})]),
    ("INT-73", [("Add contact {person} to {company} as the billing contact", {"company_name": "{company}", "contact_name": "{person}", "role": "billing"}),
                ("new contact {person}, {email}, for customer {id}", {"party_id": "{id}", "contact_name": "{person}", "email": "{email}"})]),
    ("INT-74", [("Bills for {company} by email to {email}, monthly", {"company_name": "{company}", "bill_media": "email", "email": "{email}", "frequency": "monthly"}),
                ("switch customer {id} to prepaid with auto top-up of {price}", {"party_id": "{id}", "payment_type": "prepaid", "auto_topup": True, "topup_amount": "{price}"}),
                ("billing profile for customer {id}: card ending 4242", {"party_id": "{id}", "payment_method": "credit_card", "card_last4": "4242"})]),
    ("INT-75", [("{company} is tax exempt - certificate TX-{n}", {"company_name": "{company}", "profile_kind": "exemption", "values": "tax exempt: yes, tax exemption number: TX-{n}"}),
                ("update the financial profile of customer {id} and run a credit check", {"party_id": "{id}", "profile_kind": "financial", "credit_check": True}),
                ("show the churn risk of customer {id}", {"party_id": "{id}", "profile_kind": "loyalty"})]),
    ("INT-76", [("Raise the international fraud threshold for customer {id} to {price}", {"party_id": "{id}", "international_threshold": "{price}"}),
                ("block international calls for customer {id}", {"party_id": "{id}", "block_international": True})]),
    ("INT-77", [("Validate the address {address} for customer {id}", {"address": "{address}", "party_id": "{id}"}),
                ("check the service address {address} and make it a premises", {"address": "{address}", "make_premise": True})]),
    ("INT-78", [("Register a hookup at {address}: service point JACK-{n}", {"address": "{address}", "service_point": "JACK-{n}"}),
                ("add a service point to premises #{id}", {"premise_id": "{id}"})]),
    ("INT-79", [("Add 05512{n}4567 to customer {id}'s friends and family list", {"party_id": "{id}", "add_numbers": "05512{n}4567"}),
                ("remove 05598{n}6543 from the friends & family list of customer {id}", {"party_id": "{id}", "remove_numbers": "05598{n}6543"})]),
    ("INT-80", [("Replace the {c1} on asset {id}", {"asset_id": "{id}", "transaction": "replace", "component": "{c1}"}),
                ("install {c2} on asset {id}", {"asset_id": "{id}", "transaction": "install", "component": "{c2}"}),
                ("asset {id} has asset {id2} as its backup", {"asset_id": "{id}", "transaction": "relate", "relation": "backup", "related_asset_id": "{id2}"})]),
    ("INT-81", [("Change request on asset {id}: {reason}", {"asset_id": "{id}", "description": "{reason}"})]),
    ("INT-82", [("Add customer {id2} as a related site on opportunity {id}", {"opportunity_id": "{id}", "related_party_id": "{id2}", "link_kind": "related_site"}),
                ("add partner {company} to opportunity {id}", {"opportunity_id": "{id}", "related_account": "{company}", "link_kind": "partner"})]),
    ("INT-83", [("Profile opportunity {id}: budget yes, authority yes, need yes, timeline no",
                 {"opportunity_id": "{id}", "answers": "budget yes, authority yes, need yes, timeline no"})]),
    ("INT-84", [("Run the bill for customer {id}", {"party_id": "{id}"}), ("generate the invoice for {company}", {"company_name": "{company}"})]),
    ("INT-85", [("Take a payment of {price} for bill INV-000{id} by card ending 4242",
                 {"invoice": "INV-000{id}", "amount": "{price}", "payment_method": "credit_card", "card_last4": "4242"}),
                ("customer {id} paid their balance in cash", {"party_id": "{id}", "payment_method": "cash"})]),
    ("INT-86", [("Credit {price} on the city tax line of INV-000{id} - customer is tax exempt",
                 {"invoice": "INV-000{id}", "amount": "{price}", "line": "city tax", "reason": "tax_exemption", "comments": "customer is tax exempt"}),
                ("adjust bill INV-000{id} by {pct}% - billing error", {"invoice": "INV-000{id}", "percent": "{pct}", "reason": "billing_error", "comments": "billing error"}),
                ("approve adjustment ADJ-000{id}", {"adjustment": "ADJ-000{id}", "decision": "approve"})]),
    ("INT-87", [("Payment arrangement for customer {id}: {n} instalments", {"party_id": "{id}", "installments": "{n}"}),
                ("defer bill INV-000{id} to 2026-12-15", {"invoice": "INV-000{id}", "plan_type": "deferral", "start_date": "2026-12-15"})]),
    ("INT-88", [("The customer accepted arrangement PA-000{id}", {"arrangement": "PA-000{id}", "customer_decision": "accepted"}),
                ("customer rejected adjustment ADJ-000{id}", {"adjustment": "ADJ-000{id}", "customer_decision": "rejected"})]),
    ("INT-89", [("Send a duplicate of INV-000{id} by email", {"invoice": "INV-000{id}", "bill_media": "email"}),
                ("resend the last bill of customer {id} to {email}", {"party_id": "{id}", "address": "{email}", "bill_media": "email"})]),
    ("INT-90", [("Explain bill INV-000{id}", {"invoice": "INV-000{id}"}), ("why is customer {id}'s bill so high", {"party_id": "{id}"})]),
    ("INT-91", [("Run collections", {}), ("run collections for customer {id}", {"party_id": "{id}"})]),
    ("INT-92", [("Credit alert CA-000{id}: courtesy phone call done", {"credit_alert": "CA-000{id}", "action_type": "courtesy_phone_call", "comments": "courtesy phone call done"}),
                ("raise a credit alert for customer {id}", {"party_id": "{id}"})]),
    ("INT-93", [("Run a fraud check", {"scan": True}), ("report a stolen phone for customer {id}", {"party_id": "{id}", "fraud_type": "stolen_handset"})]),
    ("INT-94", [("Fraud alert FA-000{id}: customer confirms the calls were not theirs", {"fraud_alert": "FA-000{id}", "fraud_outcome": "confirm"}),
                ("FA-000{id} no answer", {"fraud_alert": "FA-000{id}", "fraud_outcome": "no_answer"})]),
    ("INT-95", [("Link tickets {id2} and {n} to parent ticket {id}", {"parent_ticket_id": "{id}", "ticket_ids": "{id2}, {n}"})]),
    ("INT-96", [("Resolve ticket {id} - cable repaired and tested", {"ticket_id": "{id}", "ticket_action": "resolve", "resolution": "cable repaired and tested"}),
                ("reopen ticket {id}", {"ticket_id": "{id}", "ticket_action": "reopen"})]),
    ("INT-97", [("Satisfaction survey for ticket {id}: overall 4, would recommend 9", {"ticket_id": "{id}", "overall": 4, "would_recommend": 9})]),
    ("INT-98", [("Order {id} failed in provisioning - error PORT_UNAVAILABLE", {"order_id": "{id}", "system_of_failure": "provisioning", "error_code": "PORT_UNAVAILABLE"})]),
    ("INT-99", [("Find a solution for ticket {id}", {"ticket_id": "{id}"}), ("search solutions for {problem}", {"query": "{problem}"})]),
    ("INT-100", [("Revise agreement AG-000{id}: term 24 months", {"agreement_id": "AG-000{id}", "changes": "term 24 months"})]),
    ("INT-101", [("Generate the agreement document for AG-000{id}", {"agreement_id": "AG-000{id}"})]),
    ("INT-102", [("Allocate order {id} to providers", {"order_id": "{id}"})]),
]
