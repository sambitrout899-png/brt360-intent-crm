"""The answer engine - Ask anything's questions answered from the tenant's own data, models and knowledge.

    question ──► read() ──► "kpi"         a telecom KPI (MRR, ARPU, churn, NPS, MTTR, DSO ...) from the scorecard
                         ├► "prediction"  customers at risk, deals likely to win, next best offers, usage anomalies, forecasts
                         ├► "mywork"      my approvals and the work assigned to me
                         ├► "record"      one customer / order / ticket ... by number or by name, and what it holds
                         ├► "data"        how many / how much / average / list / which / top N / trend / breakdown - of any record type
                         ├► "knowledge"   what a telecom term means (with its value here, when it is a KPI)
                         └► "ai"          anything else - the tenant's AI model, shown the data catalog, its answer run and checked

Every answer is computed with what the asking user may see (crm_core.access_service), from the analytics engine
(analytics.engine / semantic / telecom / predictive); nothing is changed. An answer carries the sentence, the
figure, a table or chart and follow-up questions; Ask anything (orchestration/assistant.py, which reaches this through
app.capabilities.service("answers.answer")) adds the actions that act on it. A how-to question ("How do I add a
prospect?") is not answered here - it goes to the action catalog.
"""
from __future__ import annotations

import datetime as dt
import difflib
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.auth import User

# ---------------------------------------------------------------- reading the question

# an instruction to do something - an action, not a question (unless it is asked as one)
_DO = re.compile(r"^\s*(?:please\s+)?(?:(?:i|we)(?:\s+(?:want|need|would\s+like|wish|have)|'d\s+like)\s+to\b|(?:i|we)\s+(?:want|need)\s+(?:a|an|the)\b|"
                 r"let'?s\b|create|add|raise|log|book|schedule|cancel|approve|reject|update|change|assign|send|generate|suspend|"
                 r"resume|terminate|activate|convert|qualify|place|submit|issue|apply|resolve|escalate|move|transfer|port|upgrade|downgrade|"
                 r"renew|register|onboard|make|set\s*up|build|design|configure|enable|disable|delete|remove|edit|modify|run|start|"
                 r"verify|check\s+what|order|quote|bill|refund|credit|charge|dispatch|install|provision)\b", re.I)
# a how-to question - for the action catalog
_HOWTO = re.compile(r"^\s*(?:how\s+(?:do|can|should|would|could)\s+(?:i|we|you|one)\b|how\s+to\b|what(?:'s|\s+is|\s+are)\s+the\s+"
                    r"(?:steps|way|process|procedure)\b|steps\s+to\b|(?:can|could)\s+(?:i|we|you)\s+(?!see|get\s+(?:a\s+)?(?:list|count|number|report))|"
                    r"where\s+(?:can|do)\s+i\b|is\s+(?:it|there\s+a\s+way)\s+(?:possible\s+)?to\b|help\s+me\s+(?:to\s+)?(?!understand|find|see))", re.I)
_QUESTIONISH = re.compile(r"\?\s*$|^\s*(?:how|what|where|which|why|who|whom|whose|when|is|are|was|were|do|does|did|can|could|should|will|would|"
                          r"have|has|show|list|give|tell|display|find|get|count|compare|explain|define|describe|summari[sz]e|any|top|bottom|"
                          r"total|average|number|my|our|today'?s?|this|last)\b", re.I)
_DATA = re.compile(r"\b(?:how\s+many|how\s+much|number\s+of|no\.?\s+of|count|total|sum|average|avg|mean|median|top\s+\d+|bottom\s+\d+|top|"
                   r"highest|lowest|largest|smallest|biggest|most|least|max(?:imum)?|min(?:imum)?|list|show|give\s+me|display|which|who\s+are|"
                   r"what\s+are\s+the|trend|over\s+time|by\s+\w+|per\s+\w+|breakdown|break\s*down|split|distribution|compare|vs\.?|versus|"
                   r"percentage|share|ratio|report|chart|graph|created|opened|raised|logged|closed|resolved|won|lost|placed|booked|"
                   r"activated|cancel+ed|terminated|issued|paid|received|today|yesterday|this\s+(?:week|month|quarter|year)|"
                   r"last\s+\d+|last\s+(?:week|month|quarter|year)|since|so\s+far|new|recent|latest|oldest|any|open|closed|pending|"
                   r"overdue|unpaid|outstanding|active|live|critical|won|qualified|rejected|cancelled)\b", re.I)
_LISTING = re.compile(r"^\s*(?:please\s+)?(?:list|show(?:\s+me)?|give\s+me|display|find|get|fetch|pull\s+up)\b(?!.*\b(?:count|number|total|how\s+many|"
                      r"by\s+\w+|per\s+\w+|chart|graph|trend|breakdown)\b)|^\s*(?:which|who)\b(?!.*\b(?:by\s+\w+|per\s+\w+)\b)|"
                      r"^\s*what\s+are\s+the\b(?!.*\b(?:count|number|total)\b)", re.I)
_TOPN = re.compile(r"\b(?:top|best|biggest|largest|highest|bottom|worst|smallest|lowest)\s+(\d+)\b", re.I)
_MINE = re.compile(r"\b(?:my|i\s+have|assigned\s+to\s+me|for\s+me|on\s+my\s+plate|i\s+need\s+to|should\s+i\s+(?:do|work\s+on|focus))\b", re.I)
_MYWORK = re.compile(r"\b(?:approvals?|pending|tasks?|to-?dos?|work(?!\s+orders?)|queue|inbox|plate|assigned|waiting|focus|priorit\w+|do\s+today|work\s+on)\b", re.I)
# advice or an explanation ("How do telecoms reduce churn?", "Why are tickets up?", "Any tips to improve NPS?")
_ADVICE = re.compile(r"^\s*(?:how\s+(?:do|does|can|could|should|would|might)\b|why\b|what\s+(?:should|can|could)\s+(?:i|we)\s+do\b|"
                     r"what\s+(?:are|is)\s+(?:the\s+)?(?:best|good|right|ideal)\b|what\s+are\s+(?:some\s+)?(?:ways|tips|reasons|causes|drivers)\b|"
                     r"(?:any|give\s+me|share)\s+(?:some\s+)?(?:tips|advice|ideas|suggestions)\b|suggest\b|advise\b|is\s+it\s+(?:better|worth|wise)\b)", re.I)
_COUNTING = re.compile(r"\b(?:how\s+many|how\s+much|count|number\s+of|total|list|show|top\s+\d+)\b", re.I)
_QUESTION_START = re.compile(r"^\s*(?:how|what|what's|where|which|why|who|whom|whose|is|are|was|were|do|does|did|can|could|should|will|would|"
                             r"has|have|show|list|give\s+me|tell\s+me|display|compare|explain|define)\b", re.I)
# configuration language - a rule or automation to set up, not a question
_SETUP = re.compile(r"(?:^|[.;!]\s*)(?:when|whenever|if|every\s+time|each\s+time|once|after|before)\b|\b(?:needs?|requires?)\s+(?:\w+\s+){0,3}?"
                    r"(?:approval|sign[\s-]?off)\b|\bmust\b|\bautomatically\b|\bshould\s+(?:be|go|get|require|need|always)\b|,\s*then\b|"
                    r"\bthen\s+(?:send|create|notify|email|assign)|->|→|\b(?:process|workflow|journey|automation)\b", re.I)
_GREETING = re.compile(r"^\s*(?:hi|hello|hey|good\s+(?:morning|afternoon|evening)|thanks?|thank\s+you|cheers|ok(?:ay)?|cool|great)\b[\s!.]*$", re.I)

# a record by its number: "order 59", "ticket #195", "quote no. 12"
_RECORD_NO = re.compile(r"\b(order|ticket|fault|incident|complaint|quote|quotation|proposal|lead|opportunity|opp|deal|invoice|bill|work\s+order|"
                        r"customer|account|party|prospect)\s*(?:#|no\.?|number|id)?\s*(\d{1,9})\b", re.I)
_RECORD_ONLY = re.compile(r"^\s*(?:order|ticket|quote|invoice|bill|lead|opportunity|deal|customer|account)\s*(?:#|no\.?|number|id)?\s*\d+\s*[?.!]?\s*$", re.I)
_RECORD_ENTITY = {"order": "order", "ticket": "ticket", "fault": "ticket", "incident": "ticket", "complaint": "ticket", "quote": "quote",
                  "quotation": "quote", "proposal": "proposal", "lead": "lead", "opportunity": "opportunity", "opp": "opportunity", "deal": "opportunity",
                  "invoice": "invoice", "bill": "invoice", "work order": "work_order", "customer": "party", "account": "party", "party": "party",
                  "prospect": "party"}

# ---------------------------------------------------------------- KPIs (analytics.telecom scorecard keys)

KPI_WORDS: list[tuple[str, str]] = [
    ("arpu", r"\barpu\b|average\s+revenue\s+per\s+(?:user|customer|account|subscriber)"),
    ("arr", r"\barr\b|annual(?:ised|ized)?\s+recurring\s+revenue"),
    ("mrr", r"\bmrr\b|monthly\s+recurring\s+revenue|recurring\s+revenue|monthly\s+revenue"),
    ("customer_churn", r"customer\s+churn|logo\s+churn|customers?\s+(?:lost|churned)"),
    ("service_churn", r"churn\s+rate|service\s+churn|\bchurn\b(?!\s+(?:risk|probabilit|predict|score))"),
    ("net_adds", r"net\s+adds|net\s+additions|net\s+growth"),
    ("lead_conversion", r"lead\s+conversion|lead\s+to\s+opportunity|conversion\s+rate"),
    ("win_rate", r"win\s+rate|win\s+ratio|close\s+rate|hit\s+rate"),
    ("deal_size", r"(?:average\s+)?deal\s+size|average\s+deal"),
    ("sales_cycle", r"sales\s+cycle|time\s+to\s+close|days\s+to\s+close"),
    ("pipeline", r"(?:open\s+|sales\s+)?pipeline(?:\s+value)?\b(?!\s+by)"),
    ("backlog", r"order\s+backlog|\bbacklog\b"),
    ("activation_days", r"time\s+to\s+activat\w*|activation\s+time|provisioning\s+time|order\s+to\s+activat\w*|lead\s+time"),
    ("fallout", r"\bfallout\b|order\s+failures?|failed\s+orders?"),
    ("mttr", r"\bmttr\b|mean\s+time\s+to\s+(?:repair|resolve|restore)|(?:average\s+)?resolution\s+time|time\s+to\s+(?:resolve|fix)"),
    ("sla_breach", r"sla\s+breach\w*|breach(?:es|ed)?\s+(?:of\s+)?sla|sla\s+(?:compliance|performance)"),
    ("repeat", r"repeat\s+(?:faults?|tickets?|calls?)"),
    ("csat", r"\bcsat\b|customer\s+satisfaction|satisfaction\s+score"),
    ("nps", r"\bnps\b|net\s+promoter"),
    ("collection_rate", r"collection\s+rate|collection\s+efficiency"),
    ("dso", r"\bdso\b|days\s+sales\s+outstanding|debtor\s+days"),
    ("overdue", r"overdue\s+(?:receivables?|amount|balance|debt)|receivables?|(?:total\s+)?outstanding\s+(?:amount|balance|debt|dues)|bad\s+debt"),
    ("billed_30", r"(?:amount\s+)?billed|billing\s+(?:amount|value)|invoiced\s+amount"),
    ("data_gb", r"data\s+(?:usage|consumption|traffic|volume|used)|\bgb\s+used"),
    ("voice_min", r"voice\s+(?:minutes|usage|traffic)|minutes\s+of\s+use|\bmou\b"),
    ("sms", r"\bsms\s+(?:usage|volume|count|sent)"),
    ("intl_share", r"international\s+(?:share|usage|traffic)|roaming\s+share"),
    ("usage_revenue", r"usage\s+revenue|rated\s+usage"),
    ("fraud_open", r"open\s+fraud|fraud\s+alerts?|fraud\s+cases?"),
    ("fraud_confirmed", r"confirmed\s+fraud|fraud\s+rate"),
]

# ---------------------------------------------------------------- telecom knowledge

GLOSSARY: dict[str, tuple[str, str]] = {
    # key: (pattern, definition) - a KPI key in KPI_WORDS adds its value here
    "arpu": (r"\barpu\b", "ARPU - Average Revenue Per User: the recurring revenue per paying customer (MRR ÷ paying customers). It shows how much each customer is worth each month; it rises with upsell and premium plans and falls with discounts."),
    "mrr": (r"\bmrr\b", "MRR - Monthly Recurring Revenue: the monthly charges of every live service. The heartbeat of a telecom business; new sales add to it, churn and downgrades take from it."),
    "arr": (r"\barr\b", "ARR - Annual Recurring Revenue: MRR × 12, the yearly run-rate of the recurring business."),
    "churn": (r"\bchurn\b", "Churn: customers (customer churn) or services (service churn) that leave in a period, as a share of those at its start. Telecoms usually track it monthly; the main drivers are service quality, price and competition."),
    "nps": (r"\bnps\b|net\s+promoter", "NPS - Net Promoter Score: % promoters (9-10 on 'would you recommend us?') minus % detractors (0-6). Ranges -100 to +100; above 0 is positive, above 30 is good for a telecom."),
    "csat": (r"\bcsat\b", "CSAT - Customer Satisfaction: the average rating customers give after an interaction, usually on a 1-5 scale, shown here as a percentage."),
    "mttr": (r"\bmttr\b", "MTTR - Mean Time To Repair/Resolve: the average time from a fault being raised to it being fixed. Lower is better; SLAs usually commit to it by severity."),
    "sla": (r"\bsla\b", "SLA - Service Level Agreement: the commitments made to a customer - availability (e.g. 99.9%), response and resolution times by severity - with service credits when they are breached."),
    "dso": (r"\bdso\b", "DSO - Days Sales Outstanding: how many days of billing are still unpaid on average (receivables ÷ daily billing). Lower means faster collection."),
    "ftth": (r"\bftth\b|fib(?:re|er)\s+to\s+the\s+home", "FTTH - Fibre To The Home: optical fibre all the way to the customer's premises (an ONT in the home), giving gigabit speeds. Variants: FTTB (building), FTTC (cabinet), together FTTx."),
    "gpon": (r"\bx?gs?pon\b", "GPON - Gigabit Passive Optical Network: the shared fibre technology behind most FTTH - an OLT in the exchange feeds up to 64/128 homes through passive splitters. XGS-PON is its 10 Gbps successor."),
    "ont": (r"\bont\b|\bolt\b", "ONT / OLT: the Optical Network Terminal at the customer's premises and the Optical Line Terminal in the exchange - the two ends of a fibre (PON) connection."),
    "mpls": (r"\bmpls\b", "MPLS - Multiprotocol Label Switching: a carrier-grade private network for businesses, with guaranteed performance and classes of service between their sites (MPLS VPN)."),
    "sd-wan": (r"\bsd[\s-]?wan\b", "SD-WAN - Software-Defined WAN: connects business sites over any mix of links (fibre, broadband, 4G/5G) with central policy, application-aware routing and failover - often sold as a managed service."),
    "sip": (r"\bsip(?:\s+trunk\w*)?\b", "SIP trunk: voice lines delivered over IP to a business's phone system (PBX), replacing ISDN/PRI lines - priced per channel and number range."),
    "voip": (r"\bvoip\b", "VoIP - Voice over IP: phone calls carried over a data network instead of the circuit-switched phone network."),
    "volte": (r"\bvolte\b", "VoLTE - Voice over LTE: HD voice calls over the 4G network instead of falling back to 2G/3G."),
    "5g": (r"\b5g\b", "5G: the fifth mobile generation - much higher speed and capacity, very low latency, and network slicing for dedicated business networks; 4G (LTE) is its predecessor."),
    "lte": (r"\blte\b|\b4g\b", "LTE / 4G: the fourth mobile generation - mobile broadband in the tens to hundreds of Mbps; voice over it is VoLTE."),
    "esim": (r"\be-?sim\b", "eSIM: a SIM built into the device and downloaded over the air instead of a plastic card - activated by QR code, useful for multi-device and IoT plans."),
    "mvno": (r"\bmvno\b", "MVNO - Mobile Virtual Network Operator: a mobile brand that sells under its own name on another operator's radio network (the host MNO), wholesale."),
    "mnp": (r"\bmnp\b|number\s+port\w*|port[\s-]?in|port[\s-]?out", "MNP - Mobile Number Portability: a customer keeps their number when changing operator; port-in brings it here, port-out takes it away (usually with a porting code / UPC)."),
    "kyc": (r"\bkyc\b", "KYC - Know Your Customer: verifying a customer's identity and documents before activating a service, as regulators require (especially for SIMs)."),
    "bss": (r"\bbss\b|\boss\b", "BSS / OSS: Business Support Systems (customers, products, orders, billing - this CRM) and Operations Support Systems (network inventory, provisioning, assurance)."),
    "etom": (r"\betom\b|\btm\s*forum\b|\bsid\b|\boda\b", "TM Forum frameworks: eTOM (the business process map - e.g. Lead-to-Cash, Order-to-Activate, Trouble-to-Resolve), SID (the information model) and ODA (the open digital architecture); this platform follows them."),
    "cpe": (r"\bcpe\b", "CPE - Customer Premises Equipment: the devices at the customer's site - router, ONT, set-top box, SD-WAN appliance, desk phones."),
    "qos": (r"\bqos\b|\bjitter\b|\blatency\b|packet\s+loss", "QoS - Quality of Service: how a network treats traffic - latency (delay), jitter (variation in delay), packet loss and bandwidth; voice and video need low values of the first three."),
    "cdr": (r"\bcdrs?\b|\bxdrs?\b", "CDR - Call Detail Record: one usage event (a call, an SMS, a data session) with who, when, how long/how much; mediation collects them and rating prices them for the bill."),
    "dunning": (r"\bdunning\b", "Dunning: the collection steps for unpaid bills - reminders, then barring outgoing service, then suspension, then debt collection - each after a set number of days overdue."),
    "prepaid": (r"\bprepaid\b|\bpostpaid\b|pre-paid|post-paid", "Prepaid / postpaid: prepaid customers buy credit or bundles in advance; postpaid customers use the service and are billed monthly against a contract and credit limit."),
    "roaming": (r"\broaming\b", "Roaming: using a mobile service on a partner operator's network abroad; usage is charged through roaming agreements (TAP files) and often sold as daily passes or bundles."),
    "imsi": (r"\bimsi\b|\bmsisdn\b|\biccid\b|\bimei\b", "Mobile identifiers: MSISDN is the phone number, IMSI identifies the subscription on the SIM, ICCID is the SIM card's serial number and IMEI identifies the handset."),
    "apn": (r"\bapn\b", "APN - Access Point Name: the gateway a mobile device uses for data; businesses can have a private APN to reach their own network securely."),
    "leased line": (r"leased\s+line|\bdia\b|dedicated\s+internet", "Leased line / DIA - Dedicated Internet Access: an uncontended, symmetric business connection with an SLA, unlike shared broadband."),
    "dark fibre": (r"dark\s+fib(?:re|er)", "Dark fibre: unlit fibre leased to a customer who connects their own equipment to it - full control and capacity, usually for large enterprises and carriers."),
    "backhaul": (r"\bbackhaul\b|last\s+mile", "Backhaul / last mile: backhaul carries traffic from the access network (towers, cabinets) to the core; the last mile is the final connection to the customer."),
    "clv": (r"\bclv\b|\bltv\b|lifetime\s+value", "CLV - Customer Lifetime Value: the revenue a customer is expected to bring over their whole relationship (MRR × expected lifetime); here it comes from the churn model."),
    "cac": (r"\bcac\b|acquisition\s+cost", "CAC - Customer Acquisition Cost: what it costs in sales and marketing to win one new customer; healthy telecoms aim for CLV at least 3× CAC."),
    "tcv": (r"\btcv\b|total\s+contract\s+value", "TCV - Total Contract Value: monthly charges × contract months (plus one-off charges) - the full value of a signed deal."),
    "fallout": (r"\bfallout\b", "Order fallout: orders that drop out of automated fulfilment (rejected, failed provisioning, missing data) and need manual handling - a key Order-to-Activate quality measure."),
    "lead to cash": (r"lead[\s-]to[\s-]cash|order[\s-]to[\s-]activat\w*|trouble[\s-]to[\s-]resolve|concept[\s-]to[\s-]market", "End-to-end telecom journeys: Lead-to-Cash (prospect → lead → opportunity → quote → order → bill), Order-to-Activate (order → provisioning → live service), Trouble-to-Resolve (fault → diagnosis → fix → closure)."),
    "propensity": (r"\bpropensity\b", "Propensity: a lead's predicted likelihood to buy (0-1), from its source, segment and behaviour - used to prioritise which leads sales work first."),
    "b2b": (r"\bb2b\b|\bb2c\b", "B2B / B2C: business customers (organisations with sites, contracts and approvals) versus consumers (individuals buying plans and devices)."),
    "net adds": (r"net\s+adds", "Net adds: new customers or services gained minus those lost in a period - growth after churn."),
}

# for the spelling check: the words this engine reads
_VOCAB = {
    "prospect", "prospects", "customer", "customers", "account", "accounts", "lead", "leads", "opportunity", "opportunities", "quote", "quotes",
    "order", "orders", "ticket", "tickets", "invoice", "invoices", "payment", "payments", "usage", "survey", "surveys", "proposal", "proposals",
    "created", "today", "yesterday", "week", "month", "quarter", "year", "revenue", "churn", "average", "total", "pipeline", "segment",
    "severity", "status", "overdue", "outstanding", "resolved", "critical", "business", "consumer", "subscribers", "services", "assets",
    "products", "product", "offering", "region", "source", "owner", "stage", "approvals", "pending", "assigned", "opened", "closed",
    "activated", "cancelled", "terminated", "complaints", "faults", "collections", "billing", "balance", "satisfaction", "conversion",
}


# everyday words that are never "corrected" into a vocabulary word ("count" is not "account")
_KNOWN = {
    "count", "total", "number", "average", "show", "list", "which", "where", "there", "their", "about", "these", "those", "value", "sales",
    "price", "plans", "sites", "users", "owner", "owners", "region", "state", "stage", "people", "staff", "agent", "agents", "admin", "still",
    "since", "after", "before", "under", "above", "below", "every", "other", "would", "could", "should", "being", "first", "latest",
    "oldest", "newest", "recent", "lowest", "highest", "largest", "biggest", "least", "money", "spend", "amount", "rated", "share", "split",
    "trend", "chart", "graph", "table", "report", "status", "open", "closed", "pending", "today", "weeks", "months", "years", "daily",
    "weekly", "monthly", "yearly", "quarterly", "worth", "ranks", "rank", "top", "bottom", "worst", "best", "whose", "whom", "deals",
    "count", "counts", "group", "grouped", "split", "doing", "going", "happening", "unpaid", "raised", "placed", "booked", "issued",
}
# ... nor the words of the KPIs and terms ("collection rate", "resolution time"), nor the verbs of instructions ("create" a quote)
_KNOWN |= {w for pat in [p for _, p in KPI_WORDS] + [p for p, _ in GLOSSARY.values()] for w in re.findall(r"[a-z]{5,}", pat)}
_KNOWN |= {"create", "cancel", "approve", "reject", "update", "change", "assign", "raise", "schedule", "generate", "suspend", "resume",
           "terminate", "activate", "convert", "qualify", "submit", "apply", "resolve", "escalate", "transfer", "upgrade", "downgrade",
           "renew", "register", "onboard", "design", "configure", "enable", "disable", "delete", "remove", "modify", "start", "verify",
           "refund", "credit", "charge", "dispatch", "install", "provision", "quote", "order", "place", "build", "close", "explain", "define"}
_TYPOS = {"todya": "today", "tody": "today", "toady": "today", "yesterdy": "yesterday", "yestarday": "yesterday", "yesteday": "yesterday",
          "tickts": "tickets", "ticktes": "tickets", "ordres": "orders", "oders": "orders", "custmers": "customers", "cusomers": "customers",
          "costumers": "customers", "invocies": "invoices", "invoces": "invoices", "monht": "month", "mnth": "month", "wek": "week"}


def _fix_spelling(low: str) -> str:
    """"prospcts creted todya" -> "prospects created today": known slips, then each unknown longer word to the nearest word this engine knows."""
    out = []
    for w in re.findall(r"[a-z0-9]+|[^a-z0-9]+", low):
        if w in _TYPOS:
            out.append(_TYPOS[w])
        elif w.isalpha() and len(w) >= 5 and w not in _VOCAB and w not in _KNOWN:
            near = difflib.get_close_matches(w, _VOCAB, n=1, cutoff=0.86)
            out.append(near[0] if near else w)
        else:
            out.append(w)
    return "".join(out)


# ---------------------------------------------------------------- the context of one question


@dataclass
class _Ctx:
    db: Session
    tenant_id: int
    user: User
    text: str  # as asked
    low: str  # lower-case, spelling fixed
    _access: Any = None

    @property
    def access(self):
        if self._access is None:
            from app.crm_core import access_service

            self._access = access_service.resolve(self.db, self.user)
        return self._access


def _fmt(v: Any, fmt: str | None = None) -> str:
    from app.analytics.insights import _fmt as f

    try:
        if fmt == "hours" and v is not None:
            return f"{float(v):,.1f} hours"
        return f(float(v), fmt) if v is not None else "-"
    except (TypeError, ValueError):
        return str(v)


def _plain(v: Any) -> Any:
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()[:16].replace("T", " ")
    if hasattr(v, "value"):
        return v.value
    return v


def _insight(kind: str, title: str, answer: str, **more: Any) -> dict[str, Any]:
    return {"kind": kind, "title": title, "answer": answer, "value": None, "table": None, "chart": None, "records": None,
            "facts": [], "follow_ups": [], "spec": None, "source": "Built-in analyst", **more}


# ---------------------------------------------------------------- the entry point


def answer(db: Session, tenant_id: int, user: User, text: str) -> dict[str, Any] | None:
    """The question answered - {"route": "answer", "why": <the answer>, "insight": {...}} - or None when it is
    not a question this engine answers (an instruction, or a how-to for the action catalog)."""
    text = " ".join((text or "").split())
    raw = text.lower().strip()
    low = _fix_spelling(raw)
    if len(low) < 2:
        return None
    if _GREETING.match(low):
        return _wrap(_insight("hello", "Hello", "Hello! Ask me anything about your business - “How many prospects were created today?”, "
                              "“What is our ARPU?”, “Which customers are at risk of churning?”, “Tell me about order 59” - or tell me what to do."),
                     follow_ups=_STARTERS)
    # what kind of request it is - read from the words as typed
    asked = raw.rstrip().endswith("?") or bool(_QUESTION_START.match(raw))
    if _DO.match(raw) and not asked and not _RECORD_ONLY.match(raw) and not _match_kpi(raw) \
            and not any(re.search(p, raw) for _, p in _PREDICT):  # "order no. 59", "bill shock alerts" are questions; "order fibre for Acme" an action
        return None
    if _HOWTO.match(raw):
        return None
    if _SETUP.search(raw) and not asked:  # "When an order is created, email the customer" - a process to set up
        return None
    if not (_QUESTIONISH.search(low) or _DATA.search(low) or _match_kpi(low) or _match_term(low) or _RECORD_NO.search(low)
            or any(re.search(p, low) for _, p in _PREDICT) or _entity_said(low)):
        return None
    ctx = _Ctx(db, tenant_id, user, text, low)
    readers = (_prediction, _mywork, _record_by_number, _definition, _kpi, _ranking, _data, _named_customer, _term_anywhere)
    if _ADVICE.match(low) and not _COUNTING.search(low):  # advice and explanations: the AI analyst, not a count
        readers = (_prediction, _mywork, _definition)
    for handler in readers:
        try:
            got = handler(ctx)
        except ValueError:  # a question the data model can't hold - the next reader (or the AI) tries
            got = None
        if got is not None:
            return _wrap(got)
    ai = _ai(ctx)
    return _wrap(ai) if ai is not None else None


_STARTERS = ["How many prospects were created today?", "What is our MRR?", "Which customers are at risk of churning?",
             "Top 10 customers by monthly revenue", "Open tickets by severity", "What is waiting for my approval?"]


def _wrap(ins: dict[str, Any], follow_ups: list[str] | None = None) -> dict[str, Any]:
    if follow_ups:
        ins["follow_ups"] = follow_ups
    ins["follow_ups"] = list(dict.fromkeys(ins.get("follow_ups") or []))[:6]
    return {"route": "answer", "why": ins["answer"], "insight": ins}


# ---------------------------------------------------------------- my work


def _mywork(ctx: _Ctx) -> dict[str, Any] | None:
    if not (_MINE.search(ctx.low) and _MYWORK.search(ctx.low)):
        return None
    from app.crm_core import approval_service

    approvals = []
    for inst in approval_service.pending_for_user(ctx.db, ctx.tenant_id, ctx.user.id):
        item = approval_service.describe(ctx.db, inst)
        approvals.append({"type": inst.entity_type, "id": inst.entity_id, "title": item.get("title") or f"{inst.entity_type.title()} #{inst.entity_id}",
                          "subtitle": item.get("step_label") or item.get("gate") or "waiting for your decision"})
    approvals += [{"type": i["entity_type"], "id": i["entity_id"], "title": f"{i['entity_type'].title()} #{i['entity_id']}", "subtitle": "waiting for your decision"}
                  for i in approval_service.classic_waiting_for_user(ctx.db, ctx.tenant_id, ctx.user.id)]
    owned = []
    try:
        from app.experience.routes_records import my_work

        owned = [{"type": w.entity_type, "id": w.entity_id, "title": w.title, "subtitle": getattr(w, "status", None) or "assigned to you"}
                 for w in my_work(ctx.db, ctx.tenant_id, ctx.user)]
    except Exception:  # noqa: BLE001 - the approvals still answer
        owned = []
    approvals_only = re.search(r"\bapprov", ctx.low) is not None
    items = approvals if approvals_only else approvals + owned
    if approvals_only:
        sentence = (f"{len(approvals)} approval{'s are' if len(approvals) != 1 else ' is'} waiting for your decision."
                    if approvals else "Nothing is waiting for your approval right now.")
    else:
        sentence = (f"You have {len(approvals)} approval{'s' if len(approvals) != 1 else ''} to decide and {len(owned)} record"
                    f"{'s' if len(owned) != 1 else ''} assigned to you." if items else "Your queue is clear - nothing waits for you right now.")
        if approvals:
            sentence += " Start with the approvals - others are waiting on them."
    return _insight("mywork", "Your work", sentence, records=items[:25], value={"label": "Waiting for you", "display": str(len(items))},
                    follow_ups=["What is waiting for my approval?", "Open tickets by severity", "Which customers are at risk of churning?"])


# ---------------------------------------------------------------- predictions

_PREDICT = [
    ("forecast", r"\b(?:churn\w*|revenue|mrr|orders?|tickets?|leads?|bill\w*|collections?|payments?|usage|traffic)\b.*\b(?:next|coming|upcoming)\s+"
                 r"(?:month|quarter|year|\d+\s+(?:months|weeks|quarters))"),
    ("churn", r"(?:at[\s-]+risk|likely\s+to\s+(?:churn|leave|cancel|quit|disconnect)|churn\s+(?:risk|probabilit\w*|predict\w*|score|model)|"
              r"(?:will|might|may|going\s+to)\s+(?:churn|leave|cancel)|retention\s+risk|risk\s+of\s+(?:churn\w*|leaving|losing)|who\s+(?:to|should\s+(?:i|we))\s+retain|"
              r"save\s+(?:list|from\s+churn)|unhappy\s+customers)"),
    ("win", r"(?:likely\s+to\s+(?:win|close|convert)|win\s+probabilit\w*|(?:deals?|opportunit\w+)\s+(?:will|should|are\s+likely\s+to)\s+(?:win|close)|"
            r"which\s+(?:deals?|opportunit\w+)\s+(?:should\s+(?:i|we)\s+|to\s+|do\s+(?:i|we)\s+)?(?:focus|prioriti[sz]e|chase|work\s+on|push)|weighted\s+pipeline|expected\s+(?:wins|revenue\s+from\s+(?:the\s+)?pipeline)|"
            r"deal\s+(?:score|risk))"),
    ("offer", r"(?:next\s+best\s+(?:offer|action|product)|up[\s-]?sell\w*|cross[\s-]?sell\w*|what\s+(?:else\s+)?(?:should|can|could)\s+(?:i|we)\s+(?:sell|offer|pitch)|"
              r"recommend\w*\s+(?:a\s+)?(?:product|offer|plan)s?|who\s+(?:to|should\s+(?:i|we))\s+(?:upsell|pitch))"),
    ("usage", r"(?:usage\s+(?:spike|anomal\w*|alert|surge|jump|drop)|unusual\s+(?:usage|spend|traffic)|abnormal\s+(?:usage|spend)|bill\s+shock|"
              r"suspicious\s+(?:usage|traffic|activity))"),
    ("segments", r"(?:customer\s+segments?|segmentation|cluster\w*\s+(?:of\s+)?customers?|types\s+of\s+customers|personas?)"),
    ("forecast", r"(?:forecast\w*|predict\w*|projection|project\w*|expect\w*|outlook)\b.*\b(?:revenue|mrr|orders?|bill\w*|collections?|payments?|churn\w*|"
                 r"tickets?|leads?|usage|traffic)|next\s+(?:month|quarter|year)'?s?\s+(?:revenue|mrr|orders?|bill\w*|tickets?|leads?)"),
]


def _prediction(ctx: _Ctx) -> dict[str, Any] | None:
    from app.analytics import predictive

    kind = next((k for k, pat in _PREDICT if re.search(pat, ctx.low)), None)
    if kind is None:
        return None
    db, tid, acc = ctx.db, ctx.tenant_id, ctx.access
    if kind == "churn":
        out = predictive.churn(db, tid, acc)
        s, rows = out["summary"], [r for r in out["customers"] if r["band"] != "Low"]
        if re.search(r"\bb2b\b|business", ctx.low):
            rows = [r for r in rows if r["segment"] == "B2B"]
        elif re.search(r"\bb2c\b|consumer", ctx.low):
            rows = [r for r in rows if r["segment"] == "B2C"]
        top = rows[: _n(ctx.low, 15)]
        sentence = (f"{s['high']} customers are at high risk of churning and {s['medium']} at medium risk - {_fmt(s['mrr_at_risk'], 'money')} of monthly "
                    f"revenue. The expected loss is {_fmt(s['expected_mrr_loss'], 'money')} a month."
                    + (f" Highest risk: {top[0]['name']} ({round(100 * top[0]['probability'])}% - {', '.join(top[0]['drivers'][:2]).lower()})." if top else ""))
        return _insight("prediction", "Customers at risk of churning", sentence,
                        value={"label": "Monthly revenue at risk", "display": _fmt(s["mrr_at_risk"], "money")},
                        table=_table([("name", "Customer"), ("segment", "Segment"), ("mrr", "MRR", "money"), ("probability", "Churn risk", "pct100"),
                                      ("band", "Band"), ("drivers", "Why")], top),
                        records=[{"type": "party", "id": r["party_id"], "title": r["name"], "subtitle": f"{round(100 * r['probability'])}% risk"} for r in top[:8]],
                        facts=[f"Model: {out['method']}."] + [f"{i['label']} ({i['share']}% of the model)" for i in out["importance"][:3]],
                        follow_ups=["What should we offer customers at risk?", "What is our churn rate?", "Customer churn by segment this year",
                                    "Open tickets of customers at risk"])
    if kind == "win":
        out = predictive.win(db, tid, acc)
        s, top = out["summary"], out["opportunities"][: _n(ctx.low, 15)]
        sentence = (f"{s['open']} open deals worth {_fmt(s['pipeline'], 'money')}; weighted by their chance to win that is {_fmt(s['weighted_pipeline'], 'money')}, "
                    f"about {s['expected_wins']} expected wins."
                    + (f" Best bet: {top[0]['name']}{' for ' + top[0]['customer'] if top[0].get('customer') else ''} ({round(100 * top[0]['probability'])}%)." if top else ""))
        return _insight("prediction", "Deals most likely to win", sentence, value={"label": "Weighted pipeline", "display": _fmt(s["weighted_pipeline"], "money")},
                        table=_table([("name", "Deal"), ("customer", "Customer"), ("stage", "Stage"), ("value", "Value", "money"),
                                      ("probability", "Win chance", "pct100"), ("expected_value", "Expected", "money")], top),
                        records=[{"type": "opportunity", "id": r["opportunity_id"], "title": r["name"], "subtitle": f"{round(100 * r['probability'])}% to win"} for r in top[:8]],
                        facts=[f"Model: {out['method']}."], follow_ups=["What is our win rate?", "Open pipeline by stage", "Average deal size this quarter"])
    if kind == "offer":
        out = predictive.next_offer(db, tid, acc, limit=_n(ctx.low, 20))
        recs = out["recommendations"]
        sentence = (f"{len(recs)} customers have a clear next best offer. Top: {recs[0]['name']} → {recs[0]['offer']} ({recs[0]['reason']})." if recs
                    else "No product pattern is strong enough yet to recommend offers - it needs more customers holding several products.")
        return _insight("prediction", "Next best offers", sentence,
                        table=_table([("name", "Customer"), ("segment", "Segment"), ("offer", "Offer"), ("confidence", "Confidence", "pct100"), ("reason", "Why")], recs),
                        records=[{"type": "party", "id": r["party_id"], "title": r["name"], "subtitle": f"offer {r['offer']}"} for r in recs[:8]],
                        facts=[f"Method: {out['method']}."], follow_ups=["Which customers are at risk of churning?", "Top 10 products by revenue"])
    if kind == "usage":
        out = predictive.usage_alerts(db, tid, acc)
        alerts = out["alerts"][:20]
        sentence = (f"{len(out['alerts'])} customers show unusual usage this week (of {out['customers_checked']} checked)."
                    + (f" Biggest jump: {alerts[0]['name']} - {alerts[0]['risk'].lower()}, {', '.join(alerts[0]['reasons'])}." if alerts else ""))
        return _insight("prediction", "Unusual usage", sentence,
                        table=_table([("name", "Customer"), ("risk", "Risk"), ("increase_pct", "Increase %"), ("recent_daily", "Daily now"),
                                      ("baseline_daily", "Usual")], alerts),
                        records=[{"type": "party", "id": r["party_id"], "title": r["name"], "subtitle": r["risk"]} for r in alerts[:8]],
                        facts=[out["method"]], follow_ups=["Open fraud alerts", "Data usage by day this month"])
    if kind == "segments":
        out = predictive.segments(db, tid, acc)
        segs = out.get("segments") or []
        sentence = f"Your customers fall into {len(segs)} groups: " + "; ".join(f"{g.get('name')} ({g.get('size', g.get('customers', ''))})" for g in segs[:6]) + "."
        return _insight("prediction", "Customer segments", sentence,
                        table=_table([("name", "Segment"), ("size", "Customers"), ("action", "What to do")], segs), facts=[out.get("method", "")],
                        follow_ups=["Which customers are at risk of churning?", "ARPU by segment"])
    # forecast
    preset = ("churned" if re.search(r"churn", ctx.low) else "tickets" if re.search(r"ticket", ctx.low) else "leads" if re.search(r"lead", ctx.low)
              else "orders" if re.search(r"\borders?\b", ctx.low) else "collections" if re.search(r"collect|payment", ctx.low)
              else "billed" if re.search(r"bill", ctx.low) else "data_usage" if re.search(r"usage|traffic", ctx.low) else "new_mrr")
    out = predictive.forecast(db, tid, acc, {"preset": preset})
    fc = out.get("forecast") or []
    if not fc:
        return _insight("prediction", out.get("label", "Forecast"), out.get("note") or "There isn't enough history to forecast this yet.")
    fmt = out.get("fmt")
    sentence = (f"{out['label']}: about {_fmt(fc[0]['value'], fmt)} next period ({fc[0]['period']}), {_fmt(out.get('horizon_total'), fmt)} over the next "
                f"{len(fc)} - the trend is {out['trend']['direction']}. Confidence: {out.get('confidence')}.")
    return _insight("prediction", f"Forecast - {out['label']}", sentence, value={"label": f"Next ({fc[0]['period']})", "display": _fmt(fc[0]["value"], fmt)},
                    chart={"kind": "line", "labels": [h["period"] for h in out["history"][-12:]] + [f["period"] for f in fc],
                           "series": [{"name": "Actual", "values": [h["value"] for h in out["history"][-12:]] + [None] * len(fc)},
                                      {"name": "Forecast", "values": [None] * len(out["history"][-12:]) + [f["value"] for f in fc]}], "fmt": fmt},
                    facts=[f"Method: {out.get('method')}."], follow_ups=["What is our MRR?", "Orders by month this year"])


def _n(low: str, default: int) -> int:
    m = _TOPN.search(low)
    return max(1, min(int(m.group(1)), 100)) if m else default


def _table(cols: list[tuple], rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    columns = [{"key": c[0], "label": c[1], "fmt": c[2] if len(c) > 2 else None} for c in cols]
    out = []
    for r in rows:
        row = {}
        for c in columns:
            v = r.get(c["key"])
            if c["fmt"] == "pct100" and isinstance(v, (int, float)):
                v = round(100 * v, 1)
            row[c["key"]] = ", ".join(map(str, v[:2])) if isinstance(v, list) else _plain(v)
        out.append(row)
    for c in columns:
        if c["fmt"] == "pct100":
            c["fmt"] = "pct"
    return {"columns": columns, "rows": out}


# ---------------------------------------------------------------- one record


_RECORD_COLUMNS = {
    "order": ["id", "status", "order_type", "monthly_price", "contract_months", "created_at", "customer.name"],
    "ticket": ["id", "status", "severity", "category", "created_at", "updated_at", "customer.name"],
    "quote": ["id", "status", "total_price", "requested_discount_pct", "created_at", "customer.name"],
    "lead": ["id", "status", "source", "region", "owner_name", "propensity_score", "created_at", "customer.name"],
    "opportunity": ["id", "name", "stage", "offering_name", "created_at", "customer.name"],
    "invoice": ["id", "status", "total", "outstanding", "issue_date", "due_date", "customer.name"],
    "work_order": ["id", "kind", "status", "created_at"],
    "proposal": ["id", "status", "sent_at"],
}


def _record_by_number(ctx: _Ctx) -> dict[str, Any] | None:
    m = _RECORD_NO.search(ctx.low)
    if m is None:
        return None
    entity = _RECORD_ENTITY[re.sub(r"\s+", " ", m.group(1).lower())]
    rid = int(m.group(2))
    if entity == "party":
        return _customer_summary(ctx, rid)
    from app.analytics import engine, semantic

    model = semantic.model(ctx.db, ctx.tenant_id, entity)
    cols = [c for c in _RECORD_COLUMNS.get(entity, ["id"]) if c in model.fields]
    out = engine.records(ctx.db, ctx.tenant_id, ctx.access, {"entity": entity, "columns": cols, "filters": [{"field": "id", "op": "eq", "value": rid}], "limit": 1})
    label = model.entity.label.rstrip("s").lower() if not model.entity.label.endswith("ies") else model.entity.label[:-3].lower() + "y"
    if not out["rows"]:
        return _insight("record", f"{label.title()} #{rid}", f"I can't find {label} #{rid} - it doesn't exist, or it's outside what you may see.")
    row = out["rows"][0]
    facts = [f"{c['label']}: {_fmt(row[c['key']], c['fmt']) if c['fmt'] else _plain(row[c['key']])}" for c in out["columns"]
             if c["key"] != "id" and row.get(c["key"]) not in (None, "")]
    status = row.get("status") or row.get("stage")
    sentence = f"{label.capitalize()} #{rid}" + (f" is {str(status).replace('_', ' ')}" if status else "") + \
               (f" - {row.get('customer.name')}" if row.get("customer.name") else "") + "."
    rtype = {"ticket": "trouble_ticket"}.get(entity, entity)
    return _insight("record", f"{label.capitalize()} #{rid}", sentence, facts=facts,
                    records=[{"type": rtype, "id": rid, "title": f"{label.capitalize()} #{rid}", "subtitle": str(status or "")}],
                    follow_ups=[f"Tell me about customer {row['customer.name']}" if row.get("customer.name") else f"Open {entity.replace('_', ' ')}s this month"])


def _customer_summary(ctx: _Ctx, party_id: int, asked: str = "") -> dict[str, Any] | None:
    from app.analytics import engine
    from app.crm_core import access_service
    from app.models.crm import Party

    party = ctx.db.get(Party, party_id)
    if party is None or party.tenant_id != ctx.tenant_id or not access_service.can_see(ctx.db, ctx.access, "party", party_id):
        return _insight("record", f"Customer #{party_id}", f"I can't find customer #{party_id} - it doesn't exist, or it's outside what you may see.")
    db, tid, acc = ctx.db, ctx.tenant_id, ctx.access
    profile = party.profile
    by_customer = [{"field": "customer.id", "op": "eq", "value": party_id}]

    def total(entity: str, measure: dict[str, Any], extra: list | None = None) -> float:
        res = engine.query(db, tid, acc, {"entity": entity, "measures": [measure], "filters": by_customer + (extra or [])})
        return res["totals"].get(engine.measure_key(measure)) or 0

    mrr = total("order", {"field": "mrr", "agg": "sum"})
    live = total("order", {"agg": "count"}, [{"field": "is_live", "op": "is_true"}])
    open_t = total("ticket", {"agg": "count"}, [{"field": "status", "op": "not_in", "value": ["resolved", "closed", "cancelled"]}])
    owed = total("invoice", {"field": "outstanding", "agg": "sum"})
    overdue = total("invoice", {"agg": "count"}, [{"field": "status", "op": "eq", "value": "overdue"}])
    status = (profile.account_status if profile else None) or "customer"
    seg = profile.segment if profile else None
    sentence = (f"{party.name} is a {seg + ' ' if seg else ''}{status}"
                + (f" since {party.created_at:%d %b %Y}" if party.created_at else "") + f": {int(live)} live service{'s' if live != 1 else ''}"
                + (f" worth {_fmt(mrr, 'money')} a month" if mrr else "") + f", {int(open_t)} open ticket{'s' if open_t != 1 else ''}"
                + (f", {_fmt(owed, 'money')} unpaid ({int(overdue)} overdue invoice{'s' if overdue != 1 else ''})" if owed else ", nothing unpaid") + ".")
    # what the question asked about, listed
    table, title = None, party.name
    for pat, entity, cols, flt in (
        (r"tickets?|faults?|complaints?|issues?|problems?", "ticket", _RECORD_COLUMNS["ticket"][:-1], []),
        (r"orders?|services?|products?|plans?|subscriptions?|what\s+(?:does|do)\s+\w+\s+(?:have|own|use)", "order", _RECORD_COLUMNS["order"][:-1], []),
        (r"invoices?|bills?|balance|owe|unpaid|outstanding|dues?|payments?", "invoice", _RECORD_COLUMNS["invoice"][:-1], []),
        (r"quotes?|quotations?", "quote", _RECORD_COLUMNS["quote"][:-1], []),
        (r"deals?|opportunit\w+", "opportunity", _RECORD_COLUMNS["opportunity"][:-1], []),
        (r"leads?", "lead", _RECORD_COLUMNS["lead"][:-1], []),
    ):
        if re.search(pat, asked or ctx.low):
            res = engine.records(db, tid, acc, {"entity": entity, "columns": cols, "filters": by_customer + flt, "limit": 25,
                                                "sort": {"key": "id", "dir": "desc"}})
            table = {"columns": [{"key": c["key"], "label": c["label"], "fmt": c["fmt"]} for c in res["columns"]],
                     "rows": [{k: _plain(v) for k, v in r.items() if not k.startswith("__")} for r in res["rows"]]}
            title = f"{party.name} - {entity.replace('_', ' ')}s"
            break
    facts = [f"Segment: {seg}" if seg else "", f"Type: {party.party_type}", f"Email: {party.email}" if party.email else "",
             f"Phone: {party.phone}" if party.phone else "", f"Industry: {profile.industry}" if profile and profile.industry else ""]
    return _insight("record", title, sentence, facts=[f for f in facts if f], table=table,
                    value={"label": "Monthly revenue", "display": _fmt(mrr, "money")},
                    records=[{"type": "party", "id": party_id, "title": party.name, "subtitle": f"{seg or ''} {status}".strip()}],
                    follow_ups=[f"Open tickets of {party.name}", f"Invoices of {party.name}", f"Orders of {party.name}",
                                "Which customers are at risk of churning?"])


_NAME_STOP = {"what", "which", "who", "how", "the", "show", "tell", "give", "list", "about", "does", "have", "has", "for", "of", "is", "are",
              "customer", "customers", "account", "accounts", "prospect", "me", "details", "detail", "status", "balance", "info",
              "information", "summary", "open", "tickets", "orders", "invoices", "services", "and", "with", "their", "his", "her", "its",
              "many", "much", "a", "an", "to", "in", "on", "my", "our", "all", "please", "do", "did", "any", "unpaid", "overdue"}


def _find_customer(ctx: _Ctx) -> Any | None:
    """A customer named in the question ("Tell me about Kaveri Hotels", "open tickets of Aarav Agarwal")."""
    from sqlalchemy import func

    from app.models.crm import Party

    words = [w for w in re.findall(r"[A-Za-z0-9&.'-]+", ctx.text)]
    spans = []
    for size in (4, 3, 2, 1):
        for i in range(0, len(words) - size + 1):
            chunk = words[i: i + size]
            if chunk[0].lower() in _NAME_STOP or chunk[-1].lower() in _NAME_STOP:
                continue
            if size == 1 and (len(chunk[0]) < 4 or not chunk[0][0].isupper()):
                continue
            spans.append(" ".join(chunk))
    for span in spans[:24]:
        q = ctx.db.query(Party).filter(Party.tenant_id == ctx.tenant_id, func.lower(Party.name).like(f"%{span.lower().strip('.')}%"))
        hits = q.limit(6).all()
        if not hits:
            continue
        exact = [p for p in hits if p.name.lower() == span.lower()]
        if exact or len(hits) <= 3:
            return (exact or hits)[0]
    return None


def _named_customer(ctx: _Ctx) -> dict[str, Any] | None:
    party = _find_customer(ctx)
    if party is None:
        return None
    return _customer_summary(ctx, party.id, ctx.low.replace(party.name.lower(), " "))


# ---------------------------------------------------------------- knowledge


def _match_term(low: str) -> str | None:
    return next((k for k, (pat, _) in GLOSSARY.items() if re.search(pat, low)), None)


def _match_kpi(low: str) -> str | None:
    return next((k for k, pat in KPI_WORDS if re.search(pat, low)), None)


_DEFINE = re.compile(r"^\s*(?:what\s+(?:is|are|does)\s+(?:an?\s+|the\s+)?(?:term\s+|meaning\s+of\s+)?(?!our\b|my\b|the\s+current\b|current\b|the\s+total\b|total\b)|"
                     r"what's\s+(?:an?\s+)?(?!our\b|my\b)|define|definition\s+of|meaning\s+of|explain|tell\s+me\s+about|describe|what\s+do\s+you\s+mean\s+by)", re.I)


def _definition(ctx: _Ctx) -> dict[str, Any] | None:
    if not _DEFINE.match(ctx.low) or re.search(r"\b(?:our|my|current|this\s+month|today|now|value|number)\b", ctx.low):
        return None
    term = _match_term(ctx.low)
    if term is None:
        return None
    return _knowledge(ctx, term)


def _term_anywhere(ctx: _Ctx) -> dict[str, Any] | None:
    term = _match_term(ctx.low)
    return _knowledge(ctx, term) if term and len(ctx.low.split()) <= 6 else None


def _knowledge(ctx: _Ctx, term: str) -> dict[str, Any]:
    text = GLOSSARY[term][1]
    kpi = _match_kpi(term) or _match_kpi(ctx.low)
    value = None
    if kpi:
        k = _scorecard_kpi(ctx, kpi)
        if k and k.get("value") is not None:
            value = {"label": f"Yours now: {k['label']}", "display": _fmt(k["value"], k.get("fmt"))}
            text += f" Yours is {_fmt(k['value'], k.get('fmt'))}" + (f" ({k['hint']})" if k.get("hint") else "") + "."
        elif k:
            value = {"label": f"Yours now: {k['label']}", "display": "no data yet"}
            text += f" Your {k['label']} can't be measured yet - there's no data for it."
    return _insight("knowledge", term.upper() if len(term) <= 5 else term.title(), text, value=value,
                    follow_ups=[f"What is our {term.upper() if len(term) <= 5 else term}?" if kpi else "What is our MRR?", "What is our churn rate?",
                                "Which customers are at risk of churning?"])


# ---------------------------------------------------------------- KPIs

_scorecards: dict[tuple[int, int], tuple[float, dict[str, Any]]] = {}


def _scorecard(ctx: _Ctx) -> dict[str, Any]:
    import time

    from app.analytics import telecom

    key = (ctx.tenant_id, ctx.user.id)
    hit = _scorecards.get(key)
    if hit and time.monotonic() - hit[0] < 120:  # several questions in a row reuse it for two minutes
        return hit[1]
    card = telecom.scorecard(ctx.db, ctx.tenant_id, ctx.access, 12)
    _scorecards[key] = (time.monotonic(), card)
    return card


def _scorecard_kpi(ctx: _Ctx, key: str) -> dict[str, Any] | None:
    card = _scorecard(ctx)
    for g in card["groups"]:
        for k in g["kpis"]:
            if k["key"] == key:
                return {**k, "group": g["title"]}
    return None


def _kpi(ctx: _Ctx) -> dict[str, Any] | None:
    key = _match_kpi(ctx.low)
    if key is None:
        return None
    # a breakdown, a list, a named period or status - the data reader answers it exactly
    if re.search(r"\b(?:by|per|each|for\s+each|split|breakdown|break\s+down|trend|over\s+time|list|which|who|top\s+\d+|today|yesterday|"
                 r"this\s+(?:week|month|quarter|year)|last\s+(?:\d+|week|month|quarter|year)|since|b2b|b2c|business|consumer)\b", ctx.low) \
            and key not in _RATES:
        return None
    k = _scorecard_kpi(ctx, key)
    if k is None:
        return None
    card = _scorecard(ctx)
    group = next(g for g in card["groups"] if g["title"] == k["group"])
    shown = _fmt(k["value"], k.get("fmt")) if k.get("value") is not None else "not measurable yet (no data)"
    name = k["label"] if k["label"][:2].isupper() else k["label"][:1].lower() + k["label"][1:]
    sentence = f"Your {name} {'are' if name.endswith('s') and not name.endswith('ss') else 'is'} {shown}" + (f" - {k['hint']}" if k.get("hint") else "") + "."
    note = next((n["text"] for n in card.get("notes") or [] if k["label"].split(" (")[0].lower() in n["text"].lower()), None)
    if note:
        sentence += " " + note
    chart = next((c for c in card.get("charts") or [] if key in c.get("key", "") or k["label"].split(" ")[0].lower() in c.get("title", "").lower()), None)
    return _insight("kpi", k["label"], sentence, value={"label": k["label"], "display": shown},
                    table={"columns": [{"key": "label", "label": k["group"]}, {"key": "value", "label": "Now"}, {"key": "hint", "label": ""}],
                           "rows": [{"label": x["label"], "value": _fmt(x["value"], x.get("fmt")) if x.get("value") is not None else "-", "hint": x.get("hint", "")}
                                    for x in group["kpis"]]},
                    chart=({"kind": "bar" if chart.get("kind") in ("bar", "stacked") else "line", "labels": chart.get("labels") or [],
                            "series": chart.get("series") or [], "fmt": chart.get("fmt")} if chart and chart.get("series") else None),
                    facts=[GLOSSARY[t][1] for t in (key, key.split("_")[0]) if t in GLOSSARY][:1],
                    follow_ups=[*([] if key in _RATES else [f"{k['label'].split(' (')[0]} by month this year"]),
                                *[f"What is our {x['label'].split(' (')[0]}?" for x in group["kpis"] if x["key"] != key][:2],
                                "Which customers are at risk of churning?"])


# rates the scorecard works out - the data reader can't break them down
_RATES = ("service_churn", "customer_churn", "win_rate", "arpu", "lead_conversion", "collection_rate", "dso", "sla_breach", "fallout", "net_adds",
          "arr", "csat", "nps", "intl_share", "fraud_confirmed")


# ---------------------------------------------------------------- data questions


_NOUNS = {"party": "customers and prospects", "lead": "leads", "opportunity": "opportunities", "quote": "quotes", "order": "orders",
          "order_item": "order lines", "ticket": "tickets", "work_order": "work orders", "invoice": "invoices", "payment": "payments",
          "usage": "usage records", "asset": "services", "proposal": "proposals", "survey": "surveys", "fraud_alert": "fraud alerts",
          "credit_alert": "collections cases"}
_VERBS = {"party": "created", "lead": "created", "opportunity": "opened", "quote": "created", "order": "placed", "order_item": "sold",
          "ticket": "raised", "work_order": "created", "invoice": "issued", "payment": "received", "usage": "recorded", "asset": "installed",
          "proposal": "sent", "survey": "answered", "fraud_alert": "raised", "credit_alert": "opened"}
_PERIOD_WORDS = {"today": "today", "yesterday": "yesterday", "this_week": "this week", "last_week": "last week", "this_month": "this month",
                 "last_month": "last month", "this_quarter": "this quarter", "last_quarter": "last quarter", "this_year": "this year", "last_year": "last year"}


def _entity_said(low: str) -> bool:
    from app.analytics import nl

    return any(re.search(rf"\b(?:{pat})", low) for _, pat in nl._ENTITY_WORDS)


def _data(ctx: _Ctx) -> dict[str, Any] | None:
    from app.analytics import engine, nl, semantic

    if not _entity_said(ctx.low):
        return None
    if not (_DATA.search(ctx.low) or ctx.text.rstrip().endswith("?")):
        return None
    read = nl.rules(ctx.db, ctx.tenant_id, ctx.low)
    spec = read["spec"]
    noun = spec.pop("noun", None) or _NOUNS.get(spec["entity"], spec["entity"])
    # "customers" alone: the paying accounts, when this tenant keeps the prospect/account distinction
    if spec["entity"] == "party" and not any(f["field"] == "profile.account_status" for f in spec["filters"]) \
            and re.search(r"\bcustomers?\b|\bsubscribers?\b|\bclients?\b", ctx.low) and not re.search(r"prospect|and\s+prospects|all\s+customers", ctx.low) \
            and _has_accounts(ctx):
        spec["filters"].append({"field": "profile.account_status", "op": "eq", "value": "account"})
        noun = "customers (accounts)"
    # a named customer narrows it: "open tickets of Aarav Agarwal"
    party = _find_customer(ctx) if re.search(r"\b(?:of|for|from|by|does|has)\s+[A-Z]", ctx.text) else None
    m = semantic.model(ctx.db, ctx.tenant_id, spec["entity"])
    if party is not None:
        key = "id" if spec["entity"] == "party" else "customer.id"
        if key in m.fields:
            spec["filters"].append({"field": key, "op": "eq", "value": party.id})
            noun += f" of {party.name}"
    period = next((f["op"] for f in spec["filters"] if f.get("op") in _PERIOD_WORDS), None)
    rel = next((f for f in spec["filters"] if f.get("op") == "last_n_days"), None)
    when = _PERIOD_WORDS.get(period or "", "") or (f"in the last {rel['value']} days" if rel else "")
    related = _related_actions(ctx, spec["entity"], noun)
    follow = _follow_ups(spec["entity"], noun, when)

    # a list: "which prospects were created today", "show me overdue invoices", "open tickets of Acme"
    listing = _LISTING.search(ctx.low) or (party is not None and not re.search(r"how\s+many|how\s+much|\bcount|\bnumber|\btotal|\baverage|\bsum\b", ctx.low))
    if listing and not spec["dimensions"] and spec["measures"][0].get("agg", "count") == "count" and not _TOPN.search(ctx.low):
        cols = [c for c in (_RECORD_COLUMNS.get(spec["entity"]) or engine.default_columns(m)) if c in m.fields] or engine.default_columns(m)
        if spec["entity"] == "party":
            cols = [c for c in ("id", "name", "profile.segment", "profile.account_status", "email", "phone", "created_at") if c in m.fields]
        res = engine.records(ctx.db, ctx.tenant_id, ctx.access, {"entity": spec["entity"], "columns": cols, "filters": spec["filters"],
                                                                "limit": 50, "sort": {"key": m.entity.date if m.entity.date in m.fields else "id", "dir": "desc"}})
        n = res["total"]
        sentence = (f"{n} {noun} {_VERBS.get(spec['entity'], '')} {when}".replace("  ", " ").strip() + ("." if n else " - none so far.")
                    if when else f"There {'is' if n == 1 else 'are'} {n} {noun}.")
        if n > len(res["rows"]):
            sentence += f" Showing the latest {len(res['rows'])}."
        rtype = res.get("record_type")
        return _insight("data", spec.get("title") or noun.capitalize(), _cap(sentence), value={"label": noun.capitalize(), "display": f"{n:,}"},
                        table={"columns": [{"key": c["key"], "label": c["label"], "fmt": c["fmt"]} for c in res["columns"]],
                               "rows": [{k: _plain(v) for k, v in r.items() if not k.startswith("__")} for r in res["rows"]]},
                        records=[{"type": rtype or spec["entity"], "id": r["__id"], "title": str(r.get("name") or r.get("customer.name") or f"#{r['__id']}"),
                                  "subtitle": str(r.get("status") or r.get("stage") or "")} for r in res["rows"][:10]] if rtype else None,
                        spec={**spec, "chart": "table"}, facts=read["understood"], follow_ups=follow, about=related)

    res = engine.query(ctx.db, ctx.tenant_id, ctx.access, spec)
    cols = res["columns"]
    measure = next(c for c in cols if c["role"] == "measure")
    dims = [c for c in cols if c["role"] == "dimension"]
    total = res["totals"].get(measure["key"])
    if not dims:
        if measure["agg"] == "count":
            n = int(total or 0)
            sentence = (f"{n:,} {_one(noun, n)} {'was' if n == 1 else 'were'} {_VERBS.get(spec['entity'], 'recorded')} {when}." if when
                        else f"There {'is' if n == 1 else 'are'} {n:,} {_one(noun, n)}.")
            if n == 0 and when:
                sentence = f"No {noun} {'were' if not noun.endswith('s)') else 'were'} {_VERBS.get(spec['entity'], 'recorded')} {when}."
            display = f"{n:,}"
        else:
            display = _fmt(total, measure.get("fmt"))
            if measure["agg"] == "sum":
                sentence = f"{noun.capitalize()} {_VERBS.get(spec['entity'], '')}{' ' + when if when else ''} total {display}" \
                           f" ({measure['label'].lower().replace('total ', '')}, {res['scanned']:,} records)."
            else:
                sentence = f"The {measure['label'].lower()} of {noun}{' ' + when if when else ''} is {display}."
        sentence += _comparison(ctx, spec, measure, period)
        vlabel = measure["label"] if measure["agg"] not in ("count", "sum") else f"{noun.capitalize()}{' ' + when if when else ''}"
        return _insight("data", spec.get("title") or measure["label"], _cap(sentence), value={"label": vlabel, "display": display},
                        spec=spec, facts=read["understood"] + [f"{res['scanned']:,} records read"], follow_ups=follow, about=related)
    rows = res["rows"]
    d0 = dims[0]
    what = noun.capitalize() if measure["agg"] == "count" else f"{measure['label']} of {noun}"
    by = d0["label"].lower().replace(" (month)", "").replace("created at", "month").replace("issue date", "month")
    if not rows:
        return _insight("data", spec.get("title") or measure["label"], f"No {noun} {_VERBS.get(spec['entity'], 'recorded')}{' ' + when if when else ''} - "
                        "nothing to break down.", spec=spec, facts=read["understood"], follow_ups=follow, about=related)
    def part(r: dict[str, Any]) -> str:
        return f"{_plain(r[d0['key']])}: {_fmt(r.get(measure['key']), measure.get('fmt'))}"

    if d0.get("grain"):  # a trend: the latest periods
        sentence = f"{what} by {by}{' ' + when if when else ''}: " + ", ".join(part(r) for r in rows[-6:]) + \
                   ("" if len(rows) <= 6 else f" (the latest 6 of {len(rows)})") + "."
    else:
        lead = rows[0]
        share = f" ({round(100 * (lead.get(measure['key']) or 0) / total)}%)" if total and measure["agg"] in ("count", "sum") else ""
        sentence = (f"{what} by {by}{' ' + when if when else ''}: " + ", ".join(part(r) for r in rows[:6]) +
                    (f" and {len(rows) - 6} more" if len(rows) > 6 else "") + f". The most: {_plain(lead[d0['key']])}{share}.")
    if total is not None and measure["agg"] in ("count", "sum"):
        sentence += f" Total: {_fmt(total, measure.get('fmt'))}."
    from app.analytics import insights

    return _insight("data", spec.get("title") or measure["label"], _cap(sentence),
                    value=({"label": (f"All {noun}" if measure["agg"] == "count" else f"Total {measure['label'].lower().replace('total ', '')}"
                                      if measure["agg"] == "sum" else f"Overall {measure['label'].lower()}"),
                            "display": _fmt(total, measure.get("fmt"))} if total is not None else None),
                    table={"columns": [{"key": c["key"], "label": c["label"], "fmt": c.get("fmt")} for c in cols],
                           "rows": [{c["key"]: _plain(r.get(c["key"])) for c in cols} for r in rows[:100]]},
                    chart={"kind": "line" if d0.get("grain") and d0["grain"] not in ("weekday", "hour") else "bar",
                           "labels": [str(_plain(r[d0["key"]])) for r in rows[:30]],
                           "series": [{"name": measure["label"], "values": [r.get(measure["key"]) for r in rows[:30]]}], "fmt": measure.get("fmt")},
                    spec=spec, facts=read["understood"] + [n["text"] for n in insights.narrate(res)[1:3]], follow_ups=follow, about=related)


# ---------------------------------------------------------------- rankings: "top 10 customers by revenue", "which customers have the most tickets"

_SUBJECTS = r"customers?|accounts?|clients?|subscribers?|companies|products?|plans?|offerings?|services?|regions?|sources?|owners?|" \
            r"sales\s*(?:people|persons?|reps?)|reps?|agents?|categories|category|industries|industry|segments?|technicians?|cities"
_RANK = re.compile(rf"\b(top|bottom|best|worst|biggest|largest|highest|lowest|leading|smallest)\s+(\d+\s+)?"
                   rf"(?:(?:b2b|b2c|business|consumer|enterprise|retail|paying|active)\s+)?({_SUBJECTS})\b(.*)$", re.I)
_WHICH_MOST = re.compile(rf"\b(?:which|what|who)\s+(?:are\s+(?:the|our)\s+)?({_SUBJECTS})\s+(?:\w+\s+){{0,3}}?(?:the\s+)?(most|highest|biggest|largest|least|lowest|fewest)\b(.*)$", re.I)
# what to rank by: (pattern, entity, measure, label, fmt, filters)
_RANK_BY: list[tuple[str, str, dict[str, Any], str, str | None, list]] = [
    (r"won|wins", "opportunity", {"agg": "count"}, "won deals", None, [{"field": "is_won", "op": "is_true"}]),
    (r"tickets?|faults?|complaints?|issues?|incidents?|problems?|raised", "ticket", {"agg": "count"}, "tickets", None, []),
    (r"outstanding|owe[sd]?|overdue|debt|unpaid|balance|dues", "invoice", {"field": "outstanding", "agg": "sum"}, "outstanding amount", "money", []),
    (r"billed|billing|invoiced|invoices?", "invoice", {"field": "total", "agg": "sum"}, "billed amount", "money", []),
    (r"usage|data|gb|traffic|consum\w*", "usage", {"field": "gb", "agg": "sum"}, "data used (GB)", None, []),
    (r"deals?|opportunit\w+|pipeline", "opportunity", {"agg": "count"}, "deals", None, []),
    (r"leads?", "lead", {"agg": "count"}, "leads", None, []),
    (r"tcv|contract\s+value", "order", {"field": "tcv", "agg": "sum"}, "contract value", "money", []),
    (r"orders?|sold|sales|bought|purchases?", "order", {"agg": "count"}, "orders", None, []),
    (r"revenue|mrr|monthly|recurring|pays?|paying|spend\w*|worth|value|money|arpu|valuable|income", "order", {"field": "mrr", "agg": "sum"}, "MRR", "money", []),
]
# the field that holds each subject, per entity (first present wins)
_SUBJECT_FIELD = [
    (r"customers?|accounts?|clients?|subscribers?|companies", ("customer.name",)),
    (r"products?|plans?|offerings?|services?", ("offering.name", "offering_name", "opportunity.offering_name")),
    (r"regions?|cities", ("region", "lead.region", "technician.region")),
    (r"sources?", ("source", "lead.source")),
    (r"owners?|sales\s*(?:people|persons?|reps?)|reps?|agents?", ("owner_name", "lead.owner_name")),
    (r"categor\w+", ("category",)),
    (r"industr\w+", ("customer_profile.industry", "profile.industry", "industry")),
    (r"segments?", ("customer_profile.segment", "profile.segment", "segment")),
    (r"technicians?", ("technician.name",)),
]


def _ranking(ctx: _Ctx) -> dict[str, Any] | None:
    from app.analytics import engine, nl, semantic

    m1, m2 = _RANK.search(ctx.low), _WHICH_MOST.search(ctx.low)
    if m1:
        word, n, subject, rest = m1.group(1), int(m1.group(2) or 10), m1.group(3), m1.group(4)
        asc = word in ("bottom", "worst", "lowest", "smallest")
    elif m2:
        subject, word, rest = m2.group(1), m2.group(2), m2.group(3)
        n, asc = _n(ctx.low, 10), word in ("least", "lowest", "fewest")
        rest = ctx.low[m2.start():]  # the verb before "most" says what ("have the most tickets", "pay the most")
    else:
        return None
    entity, measure, mlabel, fmt, extra = next(((e, ms, lab, f, x) for pat, e, ms, lab, f, x in _RANK_BY if re.search(rf"\b(?:{pat})", rest)),
                                               ("order", {"field": "mrr", "agg": "sum"}, "MRR", "money", []))
    if re.search(r"products?|plans?|offerings?", subject) and entity == "order":  # products are ranked by their order lines
        entity, measure, mlabel, fmt = "order_item", ({"field": "line_value", "agg": "sum"} if measure.get("field") else {"agg": "count"}), \
            ("monthly value sold" if measure.get("field") else "lines sold"), ("money" if measure.get("field") else None)
    m = semantic.model(ctx.db, ctx.tenant_id, entity)
    keys = next((ks for pat, ks in _SUBJECT_FIELD if re.search(pat, subject)), ("customer.name",))
    dim = next((k for k in keys if k in m.fields), None)
    if dim is None:
        return None
    filters = list(extra)
    for phrase, op in nl._RELATIVE_DATES:
        if re.search(rf"\b{phrase}\b", ctx.low):
            filters.append({"field": m.entity.date, "op": op})
            break
    if re.search(r"\bb2b\b|\bbusiness\b|\benterprise\b", ctx.low) or re.search(r"\bb2c\b|\bconsumer\b", ctx.low):
        seg_key = next((k for k in ("customer_profile.segment", "profile.segment") if k in m.fields), None)
        if seg_key:
            filters.append({"field": seg_key, "op": "contains", "value": "B2B" if re.search(r"\bb2b\b|\bbusiness\b|\benterprise\b", ctx.low) else "B2C"})
    spec = {"entity": entity, "dimensions": [{"field": dim}], "measures": [measure], "filters": filters,
            "top": {"n": n, "dir": "asc" if asc else "desc", "other": False}, "chart": "hbar",
            "title": f"{'Bottom' if asc else 'Top'} {n} {subject} by {mlabel}"}
    res = engine.query(ctx.db, ctx.tenant_id, ctx.access, spec)
    mkey = engine.measure_key(measure)
    rows = sorted(res["rows"], key=lambda r: (r.get(mkey) or 0), reverse=not asc)[:n]
    rows = [r for r in rows if str(r.get(dim)) not in ("(blank)", "None")] or rows
    if not rows:
        return _insight("data", spec["title"], f"There's no {mlabel} to rank {subject} by yet.", spec=spec)
    lead = rows[0]
    parts = ", ".join(f"{_plain(r[dim])} ({_fmt(r.get(mkey), fmt)})" for r in rows[:5])
    sentence = (f"{'Lowest' if asc else 'Top'} {subject} by {mlabel}: {parts}{f' and {len(rows) - 5} more' if len(rows) > 5 else ''}. "
                f"{_plain(lead[dim])} {'has the least' if asc else 'leads'} with {_fmt(lead.get(mkey), fmt)}.")
    return _insight("data", spec["title"], _cap(sentence), value={"label": f"#{1} {_plain(lead[dim])}", "display": _fmt(lead.get(mkey), fmt)},
                    table={"columns": [{"key": "rank", "label": "#"}, {"key": dim, "label": m.fields[dim].label}, {"key": mkey, "label": mlabel if mlabel[:1].isupper() else mlabel.capitalize(), "fmt": fmt}],
                           "rows": [{"rank": i + 1, dim: _plain(r[dim]), mkey: r.get(mkey)} for i, r in enumerate(rows)]},
                    chart={"kind": "bar", "labels": [str(_plain(r[dim])) for r in rows], "series": [{"name": mlabel, "values": [r.get(mkey) for r in rows]}], "fmt": fmt},
                    spec=spec, follow_ups=[f"{'Top' if asc else 'Bottom'} {n} {subject} by {mlabel}", f"Which {subject} have the most tickets?",
                                           "Which customers are at risk of churning?"],
                    about=_related_actions(ctx, entity, subject))


def _cap(s: str) -> str:
    s = " ".join(s.split())
    return s[:1].upper() + s[1:]


def _one(noun: str, n: int) -> str:
    if n != 1:
        return noun
    base, _, rest = noun.partition(" ")
    single = base[:-3] + "y" if base.endswith("ies") else base[:-1] if base.endswith("s") else base
    return (single + (" " + rest if rest else "")).replace(" (accounts)", " (account)")


def _has_accounts(ctx: _Ctx) -> bool:
    from app.models.crm import Party, PartyProfile

    return ctx.db.query(PartyProfile.id).join(Party, Party.id == PartyProfile.party_id).filter(
        Party.tenant_id == ctx.tenant_id, PartyProfile.account_status == "account").first() is not None


_PREVIOUS = {"today": "yesterday", "this_week": "last_week", "this_month": "last_month", "this_year": "last_year"}


def _comparison(ctx: _Ctx, spec: dict[str, Any], measure: dict[str, Any], period: str | None) -> str:
    """"... - up from 2 yesterday": the same count for the period before."""
    from app.analytics import engine

    before = _PREVIOUS.get(period or "")
    if not before:
        return ""
    prev = {**spec, "filters": [{**f, "op": before} if f.get("op") == period else f for f in spec["filters"]]}
    try:
        res = engine.query(ctx.db, ctx.tenant_id, ctx.access, prev)
    except ValueError:
        return ""
    now_v = engine.query(ctx.db, ctx.tenant_id, ctx.access, spec)["totals"].get(measure["key"]) or 0
    then_v = res["totals"].get(measure["key"]) or 0
    word = _PERIOD_WORDS[before]
    if now_v == then_v:
        return f" The same as {word}."
    return f" {'Up' if now_v > then_v else 'Down'} from {_fmt(then_v, measure.get('fmt'))} {word}."


def _follow_ups(entity: str, noun: str, when: str) -> list[str]:
    base = {
        "party": ["Prospects by segment this month", "Which prospects were created today?", "Top 10 customers by monthly revenue", "How many prospects were created this week?"],
        "lead": ["Leads by source this month", "Which leads are awaiting qualification?", "Lead conversion rate", "Leads by owner this quarter"],
        "opportunity": ["Open pipeline by stage", "Which deals are most likely to win?", "Win rate", "Won deals by month this year"],
        "quote": ["Quotes by status this month", "Average discount on quotes this quarter", "Quotes waiting for approval"],
        "order": ["Orders by status this month", "Orders by month this year", "Which orders are in progress?", "What is our MRR?"],
        "ticket": ["Open tickets by severity", "Tickets by category this month", "What is our MTTR?", "Which critical tickets are open?"],
        "invoice": ["Overdue invoices by customer", "Total outstanding amount", "What is our DSO?", "Collection rate"],
        "payment": ["Payments by method this month", "Collection rate"],
        "work_order": ["Open work orders", "Work orders by status this week"],
    }
    return base.get(entity, ["What is our MRR?", "Open tickets by severity"])


def _related_actions(ctx: _Ctx, entity: str, noun: str) -> str:
    """What the actions next to the answer should act on ("prospect" next to a count of prospects) - the assistant looks them up."""
    return {"party": ("prospect" if "prospect" in noun else "customer"), "lead": "lead", "opportunity": "opportunity", "quote": "quote",
            "order": "order", "ticket": "ticket", "invoice": "invoice", "work_order": "work order", "payment": "payment"}.get(entity, "")


# ---------------------------------------------------------------- the AI analyst

_AI_PROMPT = (
    "You are the analyst inside a telecom CRM (BSS). Answer the user's question about their business. You can't see the data directly; "
    "you may ask for ONE query of it. Reply with JSON only: "
    '{"kind": "query"|"kpi"|"answer", '
    '"query": {"entity": "<key>", "dimensions": [{"field": "<key>", "grain": "year|quarter|month|week|day (dates only)"}], '
    '"measures": [{"field": "<numeric key, omit for count>", "agg": "count|count_distinct|sum|avg|min|max|median"}], '
    '"filters": [{"field": "<key>", "op": "eq|neq|in|not_in|gt|gte|lt|lte|contains|is_true|is_false|last_n_days|today|yesterday|this_week|last_week|'
    'this_month|last_month|this_quarter|last_quarter|this_year|last_year", "value": ...}], "top": {"n": <int>} or null, "list": true|false}, '
    '"kpi": "<one of the KPI keys>", "answer": "<a direct, expert answer when no data is needed: telecom knowledge, advice, best practice>", '
    '"follow_ups": ["<up to 3 useful next questions>"]}. '
    "Use only the entities, field keys and KPI keys given. Prefer data over opinion when the question is about their business."
)
_AI_PHRASE = (
    "You are the analyst inside a telecom CRM. Given the user's question and the result computed from their data, answer in 1-3 plain "
    'sentences with the key numbers, then what it means. JSON only: {"answer": "...", "follow_ups": ["...", "..."]}.'
)


def _ai(ctx: _Ctx) -> dict[str, Any] | None:
    """Anything the readers above didn't place - the tenant's AI model, given the data catalog; its query is checked and run here."""
    from app.analytics import engine, semantic
    from app.crm_core import llm

    if not (_QUESTIONISH.search(ctx.low) or ctx.text.rstrip().endswith("?")):
        return None
    target = llm.resolve(ctx.db, ctx.tenant_id)
    if target is None:
        return None
    entities = []
    for key, e in semantic.ENTITIES.items():
        m = semantic.model(ctx.db, ctx.tenant_id, key)
        fields = ", ".join(f"{f.key}({f.kind}{'=' + '|'.join(f.values[:6]) if f.values else ''})" for f in list(m.fields.values())[:40])
        entities.append(f"{key} - {e.label}: {fields}")
    kpis = ", ".join(k for k, _ in KPI_WORDS)
    try:
        with llm.budget(30):
            plan = llm.chat_json(target, _AI_PROMPT, "Entities:\n" + "\n".join(entities) + f"\nKPI keys: {kpis}\nToday: {dt.date.today()}\n\nQuestion: {ctx.text}",
                                 max_tokens=700)
            kind = plan.get("kind")
            follow = [str(x) for x in plan.get("follow_ups") or []][:3]
            if kind == "kpi" and plan.get("kpi"):
                k = _scorecard_kpi(ctx, str(plan["kpi"]))
                if k is not None:
                    ins = _kpi(_Ctx(ctx.db, ctx.tenant_id, ctx.user, ctx.text, k["label"].lower(), ctx._access))
                    if ins:
                        ins["source"] = target.label
                        return ins
            if kind == "query" and isinstance(plan.get("query"), dict):
                q = plan["query"]
                spec = {"entity": str(q.get("entity")), "dimensions": [d for d in q.get("dimensions") or [] if isinstance(d, dict) and d.get("field")],
                        "measures": [x for x in q.get("measures") or [] if isinstance(x, dict)] or [{"agg": "count"}],
                        "filters": [f for f in q.get("filters") or [] if isinstance(f, dict) and f.get("field") and f.get("op", "eq") in engine.OPERATORS]}
                if isinstance(q.get("top"), dict) and q["top"].get("n"):
                    spec["top"] = {"n": int(q["top"]["n"]), "dir": "desc", "other": False}
                m = semantic.model(ctx.db, ctx.tenant_id, spec["entity"])
                engine.validate(m, spec)
                if q.get("list") and not spec["dimensions"]:
                    res = engine.records(ctx.db, ctx.tenant_id, ctx.access, {"entity": spec["entity"], "filters": spec["filters"], "limit": 50})
                    table = {"columns": [{"key": c["key"], "label": c["label"], "fmt": c["fmt"]} for c in res["columns"]],
                             "rows": [{k: _plain(v) for k, v in r.items() if not k.startswith("__")} for r in res["rows"]]}
                    summary = f"{res['total']} records; first rows: {table['rows'][:5]}"
                    chart = None
                else:
                    res = engine.query(ctx.db, ctx.tenant_id, ctx.access, spec)
                    cols = res["columns"]
                    table = {"columns": [{"key": c["key"], "label": c["label"], "fmt": c.get("fmt")} for c in cols],
                             "rows": [{c["key"]: _plain(r.get(c["key"])) for c in cols} for r in res["rows"][:100]]}
                    summary = f"totals {res['totals']}; rows {table['rows'][:20]}"
                    dims = [c for c in cols if c["role"] == "dimension"]
                    meas = next(c for c in cols if c["role"] == "measure")
                    chart = ({"kind": "bar", "labels": [str(r.get(dims[0]["key"])) for r in res["rows"][:30]],
                              "series": [{"name": meas["label"], "values": [r.get(meas["key"]) for r in res["rows"][:30]]}], "fmt": meas.get("fmt")}
                             if dims else None)
                try:
                    said = llm.chat_json(target, _AI_PHRASE, f"Question: {ctx.text}\nResult: {summary[:3000]}", max_tokens=400)
                    sentence = str(said.get("answer") or "").strip()
                    follow = [str(x) for x in said.get("follow_ups") or []][:3] or follow
                except llm.LlmUnavailable:
                    sentence = ""
                if not sentence:
                    sentence = f"Here is what your data says ({res.get('total', res.get('scanned', 0))} records)."
                return _insight("data", plan.get("title") or ctx.text.rstrip("?")[:80], sentence, table=table, chart=chart, spec=spec,
                                follow_ups=follow, source=target.label, facts=["The AI read the question; the numbers come from your data."])
            if plan.get("answer"):
                return _insight("ai", ctx.text.rstrip("?")[:80], str(plan["answer"]).strip(), follow_ups=follow, source=target.label)
    except (llm.LlmUnavailable, ValueError, KeyError, TypeError, AttributeError):
        return None
    return None
