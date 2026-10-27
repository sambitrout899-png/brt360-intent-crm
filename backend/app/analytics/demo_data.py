"""Telecom demo history for Analytics & Reports: a believable operator's
last two years, end to end - prospects and leads by channel,
opportunities, quotes and proposals, orders with their go-live
milestones, installed services (assets) and installation work orders,
monthly bills, payments (some late, some failed), usage records, trouble
tickets with SLA clocks, repair visits and surveys, fraud and credit
alerts.

The history carries the patterns the analytics should find:
  - growth, with a festive lift (Oct-Dec) and a fiscal-year-end push (March, business);
  - referral and partner leads converting best, outbound calls worst;
  - activation getting faster over time, enterprise circuits slowest;
  - customers about to leave showing it first: more (and worse) faults,
    late and failed payments, falling usage, poor surveys - including some
    still live today, who are the ones the churn model should flag;
  - churn peaking when contracts end, involuntary churn after suspension;
  - weekend data peaks for consumers, weekday voice for business;
  - a fibre cut in the East region (ticket spike, usage dip, SLA breaches);
  - a few accounts with fraud-like international traffic this week.

Rows are written directly (not through the services) so each can carry
its historic timestamps. Idempotent per tenant: nothing happens once the
demo customers exist (their e-mails end in @demo-telco.example).

Run: python -m scripts.seed_analytics_demo --tenant-slug default [--customers 320 --months 24 --seed 42]
"""
from __future__ import annotations

import datetime as dt
import json
import math
import random
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core.product_model_service import ensure_offering
from app.crm_core.ticket_service import assign_priority
from app.models.accounts import ServiceAccount
from app.models.assurance import Technician, TechnicianAssignment, TroubleTicket, WorkOrder
from app.models.catalog import OfferingStatus
from app.models.comms import CmCreditAlert, CmFraudAlert, CmInvoice, CmPayment, CmSurvey, CmUsage
from app.models.crm import Lead, LeadStatus, Opportunity, OpportunityStage, Party, PartyProfile, Quote, QuoteStatus
from app.models.fulfillment import Asset, AssetStatus, Proposal, ProposalStatus
from app.models.orders import BillingAccount, Order, OrderItem, OrderMilestone, OrderProfile, OrderStatus, OrderType
from app.models.sla import SlaClock, SlaClockStatus
from app.models.tenant import Tenant
from app.timeutils import utcnow

DOMAIN = "demo-telco.example"
DAILY_DAYS = 120  # usage is one record a day for this long, one a week before that
TAX = 0.18

# name, segment, product line, monthly price, one-time fee, usage profile
CATALOG = [
    ("Fibre Home 100 Mbps", "B2C", "broadband", 699, 1000, "home"),
    ("Fibre Home 300 Mbps", "B2C", "broadband", 999, 1000, "home"),
    ("Fibre Home 1 Gbps", "B2C", "broadband", 1999, 0, "home"),
    ("Mobile Postpaid 30 GB", "B2C", "mobile", 499, 0, "mobile"),
    ("Mobile Postpaid Unlimited", "B2C", "mobile", 899, 0, "mobile"),
    ("IPTV Plus", "B2C", "iptv", 349, 500, None),
    ("Mesh Wi-Fi Add-on", "B2C", "addon", 199, 1500, None),
    ("Business Broadband 200 Mbps", "B2B", "broadband", 2999, 2500, "biz"),
    ("Dedicated Internet Access 1 Gbps", "B2B", "dia", 45000, 25000, "dia"),
    ("MPLS VPN (per site)", "B2B", "vpn", 18000, 15000, "dia"),
    ("Managed SD-WAN (per site)", "B2B", "sdwan", 32000, 20000, "dia"),
    ("SIP Trunk 30 Channels", "B2B", "voip", 9000, 5000, "voice"),
    ("Cloud PBX (10 seats)", "B2B", "voip", 4500, 2000, "voice"),
    ("Business Mobile Fleet (10 SIMs)", "B2B", "mobile", 6500, 0, "fleet"),
    ("IoT M2M SIM Pack (100)", "B2B", "iot", 5000, 0, "iot"),
    ("Managed Firewall", "B2B", "security", 12000, 10000, None),
]
PRODUCT = {p[0]: p for p in CATALOG}
INSTALLED = {"broadband", "dia", "vpn", "sdwan", "iptv"}

# daily volume per unit: data MB, voice minutes, SMS; and the rate per GB / minute / SMS
USAGE = {
    "home": ((9000, 0, 0), (0.6, 0, 0)),
    "mobile": ((1800, 35, 4), (2.0, 0.3, 0.2)),
    "biz": ((25000, 0, 0), (0.5, 0, 0)),
    "dia": ((180000, 0, 0), (0.15, 0, 0)),
    "voice": ((0, 600, 0), (0, 0.35, 0)),
    "fleet": ((15000, 300, 40), (1.5, 0.3, 0.2)),
    "iot": ((800, 0, 300), (2.0, 0, 0.05)),
}

REGIONS = {"North": ["Delhi", "Gurugram", "Noida", "Jaipur", "Chandigarh"], "West": ["Mumbai", "Pune", "Ahmedabad", "Surat"],
           "South": ["Bengaluru", "Chennai", "Hyderabad", "Kochi"], "East": ["Kolkata", "Bhubaneswar", "Patna", "Guwahati"]}
REGION_WEIGHT = {"North": 0.3, "West": 0.3, "South": 0.27, "East": 0.13}
# source: (weight for consumers, weight for business, base win rate, how healthy its customers are)
SOURCES = {"Website": (0.26, 0.14, 0.42, 1.0), "Referral": (0.14, 0.16, 0.68, 1.12), "Partner": (0.04, 0.22, 0.55, 1.05),
           "Retail store": (0.24, 0.0, 0.50, 1.0), "Outbound call": (0.08, 0.2, 0.18, 0.92), "Digital ads": (0.24, 0.12, 0.30, 0.85),
           "Trade event": (0.0, 0.16, 0.38, 1.0)}
OWNERS = {"Asha Menon": 1.15, "Rahul Verma": 1.0, "Priya Nair": 1.08, "Vikram Singh": 0.9, "Neha Gupta": 1.0, "Arjun Rao": 0.85}
INDUSTRIES = ["Retail", "Banking & Finance", "Healthcare", "Manufacturing", "Government", "Education", "IT Services", "Logistics", "Hospitality"]
FIRST = ["Aarav", "Vivaan", "Aditya", "Ishaan", "Rohan", "Kabir", "Ananya", "Diya", "Saanvi", "Meera", "Kavya", "Riya", "Arjun", "Sneha",
         "Karthik", "Pooja", "Siddharth", "Nisha", "Amit", "Divya", "Farhan", "Zoya", "Harpreet", "Lakshmi", "Manoj", "Tanvi", "Suresh", "Neel"]
LAST = ["Sharma", "Iyer", "Patel", "Reddy", "Banerjee", "Khan", "Singh", "Nair", "Gupta", "Das", "Joshi", "Mehta", "Kulkarni", "Chatterjee",
        "Pillai", "Verma", "Bose", "Rao", "Malhotra", "Fernandes", "Mishra", "Agarwal"]
CO_A = ["Apex", "Bluewave", "Crescent", "Deccan", "Everest", "Fortune", "Ganga", "Horizon", "Indus", "Jupiter", "Kaveri", "Lotus", "Meridian",
        "Nova", "Orion", "Pinnacle", "Quantum", "Sahyadri", "Sterling", "Trident", "Unity", "Vertex", "Zenith", "Saffron", "Coastal"]
CO_B = {"Retail": "Retail", "Banking & Finance": "Finserv", "Healthcare": "Healthcare", "Manufacturing": "Industries", "Government": "Municipal Services",
        "Education": "Academy", "IT Services": "Technologies", "Logistics": "Logistics", "Hospitality": "Hotels"}
TECHS = [("Ravi Kumar", "North"), ("Sunil Yadav", "North"), ("Imran Shaikh", "West"), ("Prakash Jadhav", "West"), ("Venkat Subramanian", "South"),
         ("Deepa Krishnan", "South"), ("Sanjay Ghosh", "East"), ("Bikash Nayak", "East"), ("Mohit Arora", "North"), ("Anil Pawar", "West")]
DIWALI = {2023: (11, 12), 2024: (11, 1), 2025: (10, 21), 2026: (11, 8), 2027: (10, 29), 2028: (10, 17)}
TICKET_CATEGORIES = {
    "broadband": [("Connectivity", 4), ("Slow speed", 3), ("Device / CPE fault", 2), ("Billing dispute", 1)],
    "mobile": [("Mobile coverage", 3), ("Data not working", 2), ("Billing dispute", 2), ("SIM / number port", 1)],
    "iptv": [("IPTV picture / channels", 3), ("Device / CPE fault", 1)],
    "dia": [("Link down", 4), ("Packet loss / latency", 3), ("Billing dispute", 1)],
    "vpn": [("Link down", 3), ("Packet loss / latency", 3)],
    "sdwan": [("Link down", 2), ("Policy / configuration change", 3)],
    "voip": [("Voice quality", 3), ("Calls failing", 3), ("Billing dispute", 1)],
    "iot": [("SIM / number port", 2), ("Data not working", 2)],
    "security": [("Policy / configuration change", 3)],
    "addon": [("Device / CPE fault", 1)],
}
RESOLVE_HOURS = {"critical": 3.5, "high": 9, "medium": 26, "low": 50}  # typical, before the SLA class and the customer's troubles
SLA_HOURS = {"premium": {"critical": 4, "high": 8, "medium": 24, "low": 72}, "standard": {"critical": 8, "high": 24, "medium": 72, "low": 120}}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _month_start(d: dt.datetime) -> dt.datetime:
    return dt.datetime(d.year, d.month, 1)


def _add_months(d: dt.datetime, n: int) -> dt.datetime:
    m = d.month - 1 + n
    return dt.datetime(d.year + m // 12, m % 12 + 1, 1)


class _Gen:
    def __init__(self, db: Session, tenant_id: int, customers: int, months: int, seed: int):
        self.db, self.tid = db, tenant_id
        self.r = random.Random(seed)
        self.now = utcnow().replace(microsecond=0)
        self.start = _month_start(self.now - dt.timedelta(days=30.44 * months))
        self.months = months
        self.target = customers
        self.incident = (self.now - dt.timedelta(days=126), self.now - dt.timedelta(days=123))  # East fibre cut
        self.names: set[str] = set()
        self.n = defaultdict(int)
        self.pending: list[Any] = []

    # ---------------------------------------------------------------- helpers

    def add(self, obj: Any) -> Any:
        self.pending.append(obj)
        if len(self.pending) >= 4000:
            self.flush()
        return obj

    def flush(self) -> None:
        self.db.add_all(self.pending)
        self.db.flush()
        self.pending = []

    def save(self, obj: Any) -> Any:
        """Add and flush now - for a row whose id the next rows need."""
        self.flush()
        self.db.add(obj)
        self.db.flush()
        self.n[type(obj).__name__] += 1
        return obj

    def at(self, day: dt.datetime, business: bool = True) -> dt.datetime:
        hour = self.r.choices(range(24), weights=[1, 1, 1, 1, 1, 1, 2, 4, 7, 9, 10, 10, 9, 9, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1] if business
                              else [1] * 24)[0]
        return dt.datetime(day.year, day.month, day.day, hour, self.r.randint(0, 59), self.r.randint(0, 59))

    def pick(self, weights: dict[str, float]) -> str:
        return self.r.choices(list(weights), weights=list(weights.values()))[0]

    def name(self, b2b: bool, industry: str) -> str:
        for _ in range(50):
            n = f"{self.r.choice(CO_A)} {CO_B[industry]} {self.r.choice(['Pvt Ltd', 'Ltd', 'LLP', 'Pvt Ltd'])}" if b2b \
                else f"{self.r.choice(FIRST)} {self.r.choice(LAST)}"
            if n not in self.names:
                break
            n = f"{n} {len(self.names) % 97 + 2}" if not b2b else f"{self.r.choice(CO_A)} {self.r.choice(CO_A)} {CO_B[industry]} Ltd"
        self.names.add(n)
        return n

    def season(self, d: dt.datetime, b2b: bool) -> float:
        lift = {10: 1.2, 11: 1.35, 12: 1.15, 1: 0.9, 6: 0.92}.get(d.month, 1.0) if not b2b else {3: 1.4, 12: 1.1, 5: 0.85}.get(d.month, 1.0)
        return lift

    def in_incident(self, region: str, d: dt.datetime) -> bool:
        return region == "East" and self.incident[0] <= d < self.incident[1]

    # ---------------------------------------------------------------- run

    def run(self) -> dict[str, Any]:
        self.offerings = {}
        for name, segment, _, price, _, _ in CATALOG:
            o, _ = ensure_offering(self.db, self.tid, name, segment, price, "active")
            o.status, o.effective_date = OfferingStatus.active, min(o.effective_date or self.start, self.start)
            self.offerings[name] = o.id
        self.techs = []
        for name, region in TECHS:
            t = self.db.query(Technician).filter_by(tenant_id=self.tid, name=name).first() or \
                self.save(Technician(tenant_id=self.tid, name=name, skills="fibre, copper, cpe, dia", region=region, status="available"))
            self.techs.append(t)
        # leads per month: growing ~3.5 % a month, with the seasonal lift; about 2 in 5 become customers
        weights = [(1 + 0.035 * m) * (0.5 * self.season(_add_months(self.start, m), False) + 0.5 * self.season(_add_months(self.start, m), True))
                   for m in range(self.months + 1)]
        total = self.target / 0.4
        for m, w in enumerate(weights):
            month = _add_months(self.start, m)
            end = min(_add_months(month, 1), self.now)
            if month >= self.now:
                break
            share = (end - month) / (_add_months(month, 1) - month)  # the current month is only partly over
            for _ in range(round(total * w / sum(weights) * share)):
                when = self.at(month + dt.timedelta(seconds=self.r.random() * (end - month).total_seconds()))
                self.lead(when)
        self.fraud_history()
        self.flush()
        return {"seeded": True, "from": self.start.date().isoformat(), "to": self.now.date().isoformat(),
                **{k: v for k, v in sorted(self.n.items())}}

    # ---------------------------------------------------------------- sales

    def lead(self, t0: dt.datetime) -> None:
        r = self.r
        b2b = r.random() < 0.34
        enterprise = b2b and r.random() < 0.3
        source = r.choices(list(SOURCES), weights=[v[1] if b2b else v[0] for v in SOURCES.values()])[0]
        region = self.pick(REGION_WEIGHT)
        city = r.choice(REGIONS[region])
        industry = r.choice(INDUSTRIES) if b2b else ""
        owner = self.pick({k: 1.0 for k in OWNERS})
        name = self.name(b2b, industry or "Retail")
        bundle = self.bundle(b2b, enterprise)
        sites = r.choice([1, 1, 2, 3, 4, 6, 8]) if enterprise else 1
        list_value = sum(PRODUCT[p][3] * q for p, q in bundle)
        # how likely this deal really is to close; the propensity score is a noisy estimate of it
        p_win = SOURCES[source][2] * OWNERS[owner] * (1.1 if not b2b else 0.95 if enterprise else 1.0)
        discount = round(_clamp(r.gauss(14 if enterprise else 8 if b2b else 3, 5), 0, 30), 1)
        revisions = r.choices([1, 2, 3], weights=[6, 3, 1] if b2b else [9, 1, 0.01])[0]
        p_win *= 1 + discount / 40
        p_win *= 0.85 if revisions >= 3 else 1.0
        p_win = _clamp(p_win, 0.03, 0.92)
        party = self.save(Party(tenant_id=self.tid, name=name, party_type="prospect", created_at=t0,
                                email=f"{name.lower().replace(' ', '.').replace('&', 'and')}{self.n['Party']}@{DOMAIN}",
                                phone=f"+91 9{r.randint(100000000, 999999999)}"))
        profile = self.save(PartyProfile(party_id=party.id, segment="B2B" if b2b else "B2C", account_status="prospect", industry=industry or None))
        rejected = r.random() < 0.17
        pending = t0 > self.now - dt.timedelta(days=10) and r.random() < 0.7
        status = LeadStatus.pending_qualification if pending else LeadStatus.rejected if rejected else LeadStatus.qualified
        lead = self.save(Lead(tenant_id=self.tid, party_id=party.id, source=source, region=region, owner_name=owner, status=status,
                              propensity_score=round(_clamp(p_win + r.gauss(0, 0.13), 0.02, 0.98), 2), interested_offering_name=bundle[0][0],
                              created_at=t0))
        if status != LeadStatus.qualified:
            return
        cycle = r.uniform(30, 110) if enterprise else r.uniform(10, 40) if b2b else r.uniform(2, 14)
        opened = t0 + dt.timedelta(days=r.uniform(0.2, 4))
        close = opened + dt.timedelta(days=cycle)
        won = r.random() < p_win
        quoted = won or r.random() < 0.65
        is_open = close > self.now
        if is_open:
            progress = (self.now - opened) / (close - opened)
            stage = OpportunityStage.quoted if progress > 0.7 and quoted else OpportunityStage.feasible if b2b and progress > 0.55 \
                else OpportunityStage.in_feasibility if b2b and progress > 0.35 else OpportunityStage.configured if progress > 0.2 else OpportunityStage.qualified
            quoted = stage == OpportunityStage.quoted
        else:
            stage = OpportunityStage.won if won else (OpportunityStage.feasibility_failed if b2b and not quoted and r.random() < 0.3 else OpportunityStage.lost)
        opp = self.save(Opportunity(tenant_id=self.tid, lead_id=lead.id, name=f"{name} - {bundle[0][0]}", stage=stage, offering_name=bundle[0][0],
                                    quantity=sites, created_at=opened, updated_at=min(close, self.now) if not is_open else self.now - dt.timedelta(days=r.uniform(0, 3))))
        quote = None
        if quoted:
            q_at = opened + (min(close, self.now) - opened) * r.uniform(0.35, 0.6)
            gap = (min(close, self.now) - q_at) / (revisions + 1)
            prev = None
            for rev in range(1, revisions + 1):
                d = round(discount * rev / revisions, 1)
                last = rev == revisions
                made = q_at + gap * (rev - 1)
                qstatus = QuoteStatus.superseded if not last else (QuoteStatus.approved if won or not is_open and r.random() < 0.6 else
                                                                   QuoteStatus.rejected if not is_open else
                                                                   QuoteStatus.pending_approval if d > 15 else QuoteStatus.approved)
                quote = self.save(Quote(tenant_id=self.tid, opportunity_id=opp.id, status=qstatus, total_price=round(list_value * sites * (1 - d / 100), 2),
                                        requested_discount_pct=d, discount_justification="Competitive match" if d > 10 else None,
                                        revision_number=rev, revision_of_quote_id=prev.id if prev else None,
                                        created_at=made, updated_at=made + gap * 0.5))
                prev = quote
            if b2b and quote.status == QuoteStatus.approved:
                sent = quote.created_at + dt.timedelta(days=1)
                self.add(Proposal(tenant_id=self.tid, quote_id=quote.id, document_ref=f"proposals/{quote.id}.pdf", sent_at=sent,
                                  status=ProposalStatus.customer_approved if won and not is_open else ProposalStatus.sent if is_open else ProposalStatus.customer_rejected,
                                  decided_at=None if is_open else close))
        if stage == OpportunityStage.won:
            party.party_type = "organization" if b2b else "individual"
            profile.account_status = "account"
            self.customer(party, region, city, b2b, enterprise, source, bundle, sites, discount, close, quote)

    def bundle(self, b2b: bool, enterprise: bool) -> list[tuple[str, int]]:
        r = self.r
        if not b2b:
            if r.random() < 0.72:
                out = [(r.choices(["Fibre Home 100 Mbps", "Fibre Home 300 Mbps", "Fibre Home 1 Gbps"], weights=[4, 4, 2])[0], 1)]
                if r.random() < 0.4:
                    out.append(("IPTV Plus", 1))
                if r.random() < 0.22:
                    out.append(("Mesh Wi-Fi Add-on", 1))
                if r.random() < 0.3:
                    out.append((r.choice(["Mobile Postpaid 30 GB", "Mobile Postpaid Unlimited"]), r.choice([1, 1, 2])))
            else:
                out = [(r.choices(["Mobile Postpaid 30 GB", "Mobile Postpaid Unlimited"], weights=[6, 4])[0], r.choice([1, 1, 1, 2, 3]))]
            return out
        if enterprise:
            out = [(r.choices(["Dedicated Internet Access 1 Gbps", "MPLS VPN (per site)", "Managed SD-WAN (per site)"], weights=[4, 3, 3])[0], 1)]
            if r.random() < 0.55:
                out.append(("Managed Firewall", 1))
            if r.random() < 0.45:
                out.append(("SIP Trunk 30 Channels", r.choice([1, 2])))
            if r.random() < 0.3:
                out.append(("Business Mobile Fleet (10 SIMs)", r.choice([1, 2, 5])))
            if r.random() < 0.15:
                out.append(("IoT M2M SIM Pack (100)", r.choice([1, 3])))
            return out
        out = [("Business Broadband 200 Mbps", 1)]
        if r.random() < 0.5:
            out.append(("Cloud PBX (10 seats)", r.choice([1, 1, 2])))
        if r.random() < 0.3:
            out.append(("Business Mobile Fleet (10 SIMs)", 1))
        if r.random() < 0.08:
            out.append(("IoT M2M SIM Pack (100)", 1))
        return out

    # ---------------------------------------------------------------- customer lifecycle

    def customer(self, party: Party, region: str, city: str, b2b: bool, enterprise: bool, source: str, bundle: list[tuple[str, int]],
                 sites: int, discount: float, won_at: dt.datetime, quote: Quote | None) -> None:
        r = self.r
        health = _clamp(r.betavariate(5, 2) * SOURCES[source][3] * (1.06 if b2b else 1.0), 0.05, 0.99)
        ba = self.save(BillingAccount(tenant_id=self.tid, party_id=party.id, status="active", rate_plan="business" if b2b else "residential",
                                      balance=0.0, created_at=won_at, updated_at=won_at))
        sa = self.save(ServiceAccount(tenant_id=self.tid, party_id=party.id, billing_account_id=ba.id, name=f"{party.name} - {city}",
                                      status="active", created_at=won_at))
        sla = "premium" if enterprise else "standard"
        contract = r.choice([12, 24, 36]) if b2b else r.choice([12, 12, 24])
        orders = [self.order(party, sa, region, bundle, sites, discount, won_at + dt.timedelta(days=r.uniform(0.1, 3)), contract, enterprise, quote, b2b)]
        first = orders[0]
        if first is None:
            return  # fell out at order entry
        live_at = first["live"]
        if live_at is None or live_at > self.now:
            return  # still being provisioned - no bills or usage yet
        # churn: a monthly hazard driven by health, low during the contract and high when it ends
        churn_at, kind = None, None
        base = 0.006 if b2b else 0.014
        cheap = any(p == "Fibre Home 100 Mbps" for p, _ in bundle)
        month = 1
        while churn_at is None:
            t = live_at + dt.timedelta(days=30.44 * month)
            if t > self.now + dt.timedelta(days=120):
                break
            h = base * (1 + 7 * (1 - health) ** 2) * (1.3 if cheap else 1.0)
            h *= 0.35 if month < contract else 3.2 if month in (contract, contract + 1) else 1.0
            if r.random() < h:
                churn_at = self.at(t - dt.timedelta(days=r.uniform(0, 30)))
                kind = "terminated" if r.random() < (0.45 if health < 0.4 else 0.2) else "cancelled"
            month += 1
        # upsell: healthy customers add services
        if health > 0.55 and r.random() < 0.35:
            add_at = live_at + dt.timedelta(days=r.uniform(60, 540))
            if add_at < min(self.now - dt.timedelta(days=5), churn_at or self.now):
                extra = self.upsell(b2b, enterprise, bundle)
                if extra:
                    o = self.order(party, sa, region, extra, sites, round(discount / 2, 1), add_at, contract, enterprise, None, b2b)
                    if o is not None:
                        orders.append(o)
        # close the books on the orders
        decline = churn_at - dt.timedelta(days=r.uniform(90, 150)) if churn_at else None
        for o in orders:
            order = o["order"]
            if o["live"] is None or o["live"] > self.now:
                continue
            if churn_at is None:
                continue
            if churn_at <= self.now:
                order.status = OrderStatus.terminated if kind == "terminated" else OrderStatus.cancelled
                order.updated_at = churn_at
                for a in o["assets"]:
                    a.status = AssetStatus.decommissioned
            elif kind == "terminated" and churn_at - self.now < dt.timedelta(days=30):
                order.status, order.updated_at = OrderStatus.suspended, churn_at - dt.timedelta(days=30)
                for a in o["assets"]:
                    a.status = AssetStatus.suspended
            elif kind == "cancelled" and churn_at - self.now < dt.timedelta(days=20) and r.random() < 0.5:
                order.status, order.updated_at = OrderStatus.pending_cancellation, self.now - dt.timedelta(days=r.uniform(1, 6))
        if churn_at is not None and churn_at <= self.now:
            ba.status, sa.status = "closed", "terminated"
        elif any(o["order"].status == OrderStatus.suspended for o in orders):
            ba.status, sa.status = "suspended", "suspended"
        end = min(churn_at or self.now, self.now)

        def stress(t: dt.datetime) -> float:
            ramp = _clamp((t - decline).total_seconds() / (churn_at - decline).total_seconds(), 0, 1) if decline and t > decline else 0.0
            return _clamp((1 - health) * 0.35 + ramp * 0.85, 0, 1)

        lines = [(PRODUCT[p], q * (sites if PRODUCT[p][2] in ("vpn", "sdwan") else 1), o["live"], o["order"]) for o in orders
                 if o["live"] and o["live"] <= self.now for p, q in o["bundle"]]
        usage = self.usage(party, region, b2b, lines, end, decline, churn_at)
        self.tickets(party, region, b2b, sla, lines, end, stress)
        self.billing(party, ba, b2b, orders, end, stress, usage, kind if churn_at else None, churn_at)

    def upsell(self, b2b: bool, enterprise: bool, have: list[tuple[str, int]]) -> list[tuple[str, int]]:
        owned = {p for p, _ in have}
        options = (["Managed Firewall", "SIP Trunk 30 Channels", "Business Mobile Fleet (10 SIMs)", "IoT M2M SIM Pack (100)"] if enterprise else
                   ["Cloud PBX (10 seats)", "Business Mobile Fleet (10 SIMs)", "Managed Firewall"] if b2b else
                   ["IPTV Plus", "Mesh Wi-Fi Add-on", "Mobile Postpaid Unlimited", "Mobile Postpaid 30 GB"])
        options = [p for p in options if p not in owned]
        return [(self.r.choice(options), 1)] if options else []

    def order(self, party: Party, sa: ServiceAccount, region: str, bundle: list[tuple[str, int]], sites: int, discount: float,
              created: dt.datetime, contract: int, enterprise: bool, quote: Quote | None, b2b: bool) -> dict[str, Any] | None:
        r = self.r
        if created > self.now:
            return None
        lines = [(p, q * (sites if PRODUCT[p][2] in ("vpn", "sdwan") else 1)) for p, q in bundle]
        mrr = round(sum(PRODUCT[p][3] * q * (1 - discount / 100) for p, q in lines), 2)
        # time to activate: consumer days, enterprise weeks - and the process gets ~35 % faster over the period
        improve = 1.3 - 0.45 * _clamp((created - self.start) / (self.now - self.start), 0, 1)
        lead_days = (r.uniform(15, 45) if enterprise else r.uniform(5, 15) if b2b else
                     r.uniform(0.2, 1.5) if all(PRODUCT[p][2] == "mobile" for p, _ in lines) else r.uniform(1, 6)) * improve
        live = created + dt.timedelta(days=lead_days)
        fallout = r.random() < 0.03
        if fallout:
            status, updated, live = OrderStatus.rejected, created + dt.timedelta(days=r.uniform(0.5, 4)), None
        elif live > self.now:
            status = OrderStatus.created if self.now - created < dt.timedelta(days=1) else \
                OrderStatus.pending_approval if discount > 15 and self.now - created < dt.timedelta(days=4) else OrderStatus.activating
            updated = self.now - dt.timedelta(hours=r.uniform(1, 30))
        else:
            status, updated = OrderStatus.active, live
        order = self.save(Order(tenant_id=self.tid, quote_id=quote.id if quote else None, party_id=party.id, service_account_id=sa.id,
                                order_type=OrderType.new, status=status, contract_months=contract, monthly_price=mrr, created_at=created, updated_at=updated))
        self.add(OrderProfile(order_id=order.id, segment="B2B" if b2b else "B2C", requested_discount_pct=discount))
        self.add(OrderMilestone(order_id=order.id, code="created", label="Order captured", detail="", reached_at=created))
        assets = []
        for p, q in lines:
            item = self.save(OrderItem(order_id=order.id, product_offering_id=self.offerings[p], quantity=q, status="active" if live and live <= self.now else "pending",
                                       unit_price=PRODUCT[p][3], discount_pct=discount, net_unit_price=round(PRODUCT[p][3] * (1 - discount / 100), 2)))
            if status != OrderStatus.active:
                continue
            line = PRODUCT[p][2]
            ident = f"+91 9{r.randint(100000000, 999999999)}" if line == "mobile" else f"{line.upper()}-{order.id:05d}-{item.id:06d}"
            assets.append(self.add(Asset(tenant_id=self.tid, service_account_id=sa.id, order_item_id=item.id, asset_type=p, identifier=ident,
                                         status=AssetStatus.active, created_at=live)))
            if line in INSTALLED:
                wo = self.save(WorkOrder(tenant_id=self.tid, order_id=order.id, order_item_id=item.id, kind="installation", status="complete",
                                         scheduled_window=f"{(live - dt.timedelta(hours=4)):%Y-%m-%d} 10:00-14:00",
                                         technical_parameters=json.dumps({"product": p, "region": region}),
                                         created_at=created + dt.timedelta(hours=2), updated_at=live))
                self.add(TechnicianAssignment(work_order_id=wo.id, technician_id=self.tech(region).id, assigned_at=created + dt.timedelta(hours=6)))
        if status == OrderStatus.active:
            self.add(OrderMilestone(order_id=order.id, code="live", label="Service live", detail="", reached_at=live))
        return {"order": order, "live": live, "bundle": bundle, "assets": assets, "mrr": mrr, "one_time": sum(PRODUCT[p][4] * q for p, q in lines)}

    def tech(self, region: str) -> Technician:
        return self.r.choice([t for t in self.techs if t.region == region] or self.techs)

    # ---------------------------------------------------------------- usage

    def usage(self, party: Party, region: str, b2b: bool, lines: list, end: dt.datetime, decline: Any, churn_at: Any) -> dict[tuple[int, int], float]:
        """Usage records; returns the rated amount by (year, month) for the bills."""
        r = self.r
        by_month: dict[tuple[int, int], float] = defaultdict(float)
        daily_from = self.now - dt.timedelta(days=DAILY_DAYS)
        intl = b2b and r.random() < 0.18
        main = lines[0][0][2] if lines else ""
        for kind_i, kind in enumerate(("data", "voice", "sms")):
            vol = [(USAGE[p[5]][0][kind_i] * q, USAGE[p[5]][1][kind_i], live) for p, q, live, _ in lines if p[5] and USAGE[p[5]][0][kind_i]]
            if not vol:
                continue
            first = min(v[2] for v in vol)
            day = dt.datetime(first.year, first.month, first.day) + dt.timedelta(days=1)
            while day < end:
                step = 1 if day >= daily_from else 7
                qty = amount = 0.0
                for s in range(step):
                    d = day + dt.timedelta(days=s)
                    if d >= end:
                        break
                    years = (d - self.start).days / 365.0
                    f = 1 + (0.28 * years if kind == "data" else -0.04 * years)
                    weekend = d.weekday() >= 5
                    f *= (1.22 if weekend else 0.95) if not b2b else (0.45 if weekend else 1.1)
                    if not b2b and kind == "data":
                        md = DIWALI.get(d.year)
                        if md and abs((d - dt.datetime(d.year, *md)).days) <= 3:
                            f *= 1.3
                    if kind == "sms" and d.month == 12 and d.day == 31:
                        f *= 3
                    if decline and d > decline:  # usage tails off, fastest in the last weeks before leaving
                        f *= 1 - 0.7 * _clamp((d - decline).days / (churn_at - decline).days, 0, 1) ** 2
                    if self.in_incident(region, d):
                        f *= 0.3
                    for v, rate, live in vol:
                        if d >= live:
                            n = v * f * r.lognormvariate(0, 0.22)
                            qty += n
                            amount += n / 1024 * rate if kind == "data" else n * rate
                if qty > 0:
                    at = day + dt.timedelta(hours=r.randint(8, 22))
                    self.add(CmUsage(tenant_id=self.tid, party_id=party.id, service=main, event_type=kind, at=at, quantity=round(qty, 1),
                                     rate_plan="business" if b2b else "standard", amount=round(amount, 2)))
                    self.n["CmUsage"] += 1
                    by_month[(at.year, at.month)] += amount
                day += dt.timedelta(days=step)
            if kind == "voice" and intl:
                day = dt.datetime(first.year, first.month, 1) + dt.timedelta(days=7)
                while day < end:
                    m = r.uniform(40, 160)
                    self.add(CmUsage(tenant_id=self.tid, party_id=party.id, service=main, event_type="voice", at=day + dt.timedelta(hours=11), quantity=round(m, 1),
                                     destination=r.choice(["+44", "+1", "+971", "+65"]), international=True, rate_plan="international", amount=round(m * 6, 2)))
                    self.n["CmUsage"] += 1
                    by_month[(day.year, day.month)] += m * 6
                    day += dt.timedelta(days=7)
        return by_month

    # ---------------------------------------------------------------- assurance

    def tickets(self, party: Party, region: str, b2b: bool, sla: str, lines: list, end: dt.datetime, stress: Any) -> None:
        r = self.r
        start = min(l[2] for l in lines)
        rate = (0.2 if b2b else 0.07) * math.sqrt(len(lines))
        t = start + dt.timedelta(days=r.expovariate(rate * (1 + 6 * stress(start)) / 30))
        while t < end:
            self.ticket(party, region, sla, lines, t, stress(t))
            t += dt.timedelta(days=r.expovariate(rate * (1 + 6 * stress(t)) / 30))
        if region == "East" and start < self.incident[0] < end and r.random() < 0.75:
            self.ticket(party, region, sla, lines, self.incident[0] + dt.timedelta(hours=r.uniform(1, 30)), 0.9, outage=True)

    def ticket(self, party: Party, region: str, sla: str, lines: list, created: dt.datetime, s: float, outage: bool = False) -> None:
        r = self.r
        product = r.choice(lines)[0]
        line = product[2]
        if outage:
            category, severity = ("Link down" if line in ("dia", "vpn", "sdwan") else "Connectivity"), r.choice(["critical", "high"])
        else:
            cats = TICKET_CATEGORIES.get(line, [("General enquiry", 1)])
            category = r.choices([c for c, _ in cats], weights=[w * (1 + 2 * s if c == "Billing dispute" else 1) for c, w in cats])[0]
            severity = r.choices(["low", "medium", "high", "critical"], weights=[5 - 3 * s, 4, 1 + 3 * s, 0.3 + 1.5 * s])[0]
        created = self.at(created)
        hours = RESOLVE_HOURS[severity] * (0.6 if sla == "premium" else 1.0) * (1 + 0.8 * s) * r.lognormvariate(0, 0.45) * (3 if outage else 1.0)
        resolved = created + dt.timedelta(hours=hours)
        done = resolved <= self.now
        status = r.choice(["resolved", "resolved", "closed"]) if done else r.choice(["open", "assigned", "in_progress", "pending_customer"])
        ticket = self.save(TroubleTicket(tenant_id=self.tid, party_id=party.id, category=category, severity=severity, product_line=line, sla_class=sla,
                                         priority=assign_priority(sla, severity), status=status,
                                         description=f"{category} on {product[0]}" + (" - East region fibre cut" if outage else ""),
                                         created_at=created, updated_at=resolved if done else self.now - dt.timedelta(hours=r.uniform(0, 12))))
        deadline = created + dt.timedelta(hours=SLA_HOURS[sla][severity])
        clock = SlaClockStatus.running if not done else SlaClockStatus.breached if resolved > deadline else SlaClockStatus.completed
        if not done and self.now > deadline:
            clock = SlaClockStatus.breached
        self.add(SlaClock(tenant_id=self.tid, clock_type="ticket_resolution", entity_type="ticket", entity_id=ticket.id, on_breach_action="escalate",
                          started_at=created, deadline=deadline, status=clock, resolved_at=resolved if done else None, detail=f"{severity} / {sla}"))
        if line in INSTALLED | {"addon"} and severity in ("high", "critical") and r.random() < 0.45:
            wo = self.save(WorkOrder(tenant_id=self.tid, ticket_id=ticket.id, kind="field_dispatch", status="complete" if done else "scheduled",
                                     scheduled_window=f"{created + dt.timedelta(hours=4):%Y-%m-%d} 14:00-18:00",
                                     technical_parameters=json.dumps({"fault": category}), created_at=created + dt.timedelta(hours=1),
                                     updated_at=resolved if done else created + dt.timedelta(hours=2)))
            self.add(TechnicianAssignment(work_order_id=wo.id, technician_id=self.tech(region).id, assigned_at=created + dt.timedelta(hours=2)))
        if done and r.random() < 0.32:
            breached = clock == SlaClockStatus.breached
            overall = int(_clamp(round(4.7 - 2.6 * s - (0.9 if breached else 0) + r.gauss(0, 0.7)), 1, 5))
            self.add(CmSurvey(tenant_id=self.tid, ticket_id=ticket.id, channel=r.choice(["sms", "email", "ivr"]), overall=overall,
                              timeliness=int(_clamp(overall - (1 if breached else 0) + r.choice([-1, 0, 0, 1]), 1, 5)),
                              professionalism=int(_clamp(overall + r.choice([0, 0, 1]), 1, 5)), resolved=overall >= 3 or r.random() < 0.5,
                              would_recommend=int(_clamp(round(overall * 2 + r.gauss(0, 1.2)), 0, 10)), score=overall * 20.0,
                              comments=r.choice(["", "", "Took too long", "Engineer was helpful", "Issue keeps coming back", "Quick fix, thanks"]) if overall else "",
                              created_at=resolved + dt.timedelta(hours=r.uniform(2, 48))))
            self.n["CmSurvey"] += 1

    # ---------------------------------------------------------------- billing

    def billing(self, party: Party, ba: BillingAccount, b2b: bool, orders: list[dict], end: dt.datetime, stress: Any,
                usage: dict[tuple[int, int], float], churn_kind: str | None, churn_at: dt.datetime | None) -> None:
        r = self.r
        live = [o for o in orders if o["live"] and o["live"] <= self.now]
        period = _month_start(min(o["live"] for o in live))
        outstanding = 0.0
        while period < end:
            nxt = _add_months(period, 1)
            issue = nxt.date()
            if nxt > self.now:
                break
            recurring = one_time = 0.0
            for o in live:
                on, off = max(o["live"], period), min(end, nxt)
                if off > on:
                    recurring += o["mrr"] * (off - on).total_seconds() / (nxt - period).total_seconds()
                if period <= o["live"] < nxt:
                    one_time += o["one_time"]
            used = usage.get((period.year, period.month), 0.0)
            sub = recurring + one_time + used
            if sub <= 0:
                period = nxt
                continue
            total = round(sub * (1 + TAX), 2)
            due = issue + dt.timedelta(days=30 if b2b else 15)
            s = stress(dt.datetime.combine(issue, dt.time()))
            final_unpaid = churn_kind == "terminated" and churn_at is not None and churn_at - dt.datetime.combine(issue, dt.time()) < dt.timedelta(days=75)
            late = r.random() < 0.05 + 0.6 * s
            pay_day = None if final_unpaid else dt.datetime.combine(due, dt.time()) + dt.timedelta(
                days=r.uniform(4, 25 + 60 * s) if late else -r.uniform(0, 12))
            paid = 0.0
            if pay_day is not None and pay_day <= self.now:
                paid = total if r.random() > 0.05 or not late else round(total * r.uniform(0.3, 0.8), 2)
            if r.random() < 0.02 + 0.3 * s:  # a payment attempt that bounced first
                fail_at = dt.datetime.combine(due, dt.time()) - dt.timedelta(days=r.uniform(0, 3))
                if fail_at <= self.now:
                    self.payment(party, b2b, total, fail_at, "failed", None)
            status = "paid" if paid >= total - 0.005 else "partially_paid" if paid > 0 else "overdue" if dt.datetime.combine(due, dt.time()) < self.now else "open"
            inv = self.save(CmInvoice(tenant_id=self.tid, party_id=party.id, invoice_number=f"INV-{issue:%Y%m}-{self.n['CmInvoice'] + 1:06d}",
                                      period_start=period.date(), period_end=(nxt - dt.timedelta(days=1)).date(), issue_date=issue, due_date=due,
                                      recurring=round(recurring, 2), one_time=round(one_time, 2), usage=round(used, 2), adjustments=0.0,
                                      taxes=round(sub * TAX, 2), total=total, paid=paid, credited=0.0, status=status,
                                      bill_media=r.choice(["email", "email", "app", "paper"]), late_fee_applied=bool(late and paid and pay_day and (pay_day.date() - due).days > 15),
                                      created_at=dt.datetime.combine(issue, dt.time(2))))
            if paid:
                self.payment(party, b2b, paid, pay_day, "posted", inv)
            outstanding += total - paid
            overdue_since = dt.datetime.combine(due, dt.time())
            closed = pay_day if pay_day and pay_day <= self.now and paid >= total - 0.005 else None
            if (closed or self.now) - overdue_since > dt.timedelta(days=30):
                opened = overdue_since + dt.timedelta(days=30)
                days = ((closed or self.now) - overdue_since).days
                self.n["CmCreditAlert"] += 1
                self.add(CmCreditAlert(tenant_id=self.tid, party_id=party.id, alert_number=f"CA-{self.n['CmCreditAlert']:06d}", invoice_id=inv.id,
                                       amount_owed=round(total - paid, 2), days_overdue=days, priority="high" if days > 60 or total > 50000 else "medium",
                                       status="closed" if closed else "open", sub_status="paid" if closed else "entered_collections" if days > 60 else "reminder_sent",
                                       action_type="suspension" if days > 60 else "reminder", due_date=due, source="collections", owner_name="Collections desk",
                                       created_at=opened, closed_at=closed))
            period = nxt
        ba.balance = round(outstanding, 2)
        ba.updated_at = self.now

    def payment(self, party: Party, b2b: bool, amount: float, at: dt.datetime, status: str, inv: CmInvoice | None) -> None:
        r = self.r
        method = r.choices(["eft", "cheque", "auto_debit"], weights=[7, 1.5, 1.5])[0] if b2b else \
            r.choices(["upi", "auto_debit", "card", "cash"], weights=[4, 2.5, 2.5, 0.5])[0]
        self.n["CmPayment"] += 1
        self.add(CmPayment(tenant_id=self.tid, party_id=party.id, payment_number=f"PAY-{self.n['CmPayment']:07d}", amount=amount, method=method,
                           card_brand=r.choice(["Visa", "Mastercard", "RuPay"]) if method == "card" else None,
                           card_last4=f"{r.randint(0, 9999):04d}" if method == "card" else None,
                           reference=f"UTR{r.randint(10 ** 9, 10 ** 10 - 1)}" if method in ("eft", "upi") else None,
                           applied_json=json.dumps([{"invoice_id": inv.id, "amount": amount}]) if inv else "[]", unapplied=0.0, status=status,
                           received_at=self.at(at)))

    # ---------------------------------------------------------------- fraud

    def fraud_history(self) -> None:
        """About one fraud alert a month (most false positives), and this week's live cases with the usage behind them."""
        self.flush()
        r = self.r
        # each customer's time in service: first go-live to churn (or today)
        served: dict[int, list[dt.datetime]] = {}
        for pid, status, created, updated in self.db.query(Order.party_id, Order.status, Order.created_at, Order.updated_at).filter(
                Order.tenant_id == self.tid, Order.party_id.in_(list(self.demo_ids()))):
            if status in (OrderStatus.rejected, OrderStatus.created, OrderStatus.activating, OrderStatus.pending_approval):
                continue
            span = served.setdefault(pid, [created, self.now if status not in (OrderStatus.cancelled, OrderStatus.terminated) else updated])
            span[0] = min(span[0], created)
        live = [pid for pid, (_, until) in served.items() if until >= self.now]
        if not live:
            return
        month = self.start
        while month < self.now - dt.timedelta(days=30):
            for _ in range(r.choice([0, 1, 1, 2])):
                at = self.at(month + dt.timedelta(days=r.uniform(0, 28)), business=False)
                who = [pid for pid, (since, until) in served.items() if since < at < until]
                if not who:
                    continue
                ftype, event = r.choice([("exceeds_threshold", "international_minutes"), ("irsf", "premium_destination"), ("wangiri", "short_calls"),
                                         ("sim_box", "simultaneous_calls"), ("subscription_fraud", "identity_mismatch")])
                threshold = r.choice([500, 1000, 2000])
                status = r.choices(["confirmed", "false_positive", "closed"], weights=[3.5, 5, 1.5])[0]
                self.fraud(r.choice(who), ftype, event, threshold, at, status, at + dt.timedelta(days=r.uniform(0.5, 6)))
            month = _add_months(month, 1)
        # this week: fraud-like international traffic on a few live accounts
        for i, pid in enumerate(r.sample(live, min(4, len(live)))):
            for d in range(6):
                at = self.now - dt.timedelta(days=d, hours=r.uniform(1, 10))
                m = r.uniform(300, 900)
                self.add(CmUsage(tenant_id=self.tid, party_id=pid, service="voice", event_type="voice", at=at, quantity=round(m, 1),
                                 destination=r.choice(["+882", "+881", "+675", "+252"]), international=True, premium=i % 2 == 0,
                                 rate_plan="international", amount=round(m * 12, 2)))
                self.n["CmUsage"] += 1
            if i < 3:
                self.fraud(pid, "exceeds_threshold", "international_minutes", 1000, self.now - dt.timedelta(days=r.uniform(0.2, 3)),
                           ["open", "investigating", "open"][i], None)

    def fraud(self, pid: int, ftype: str, event: str, threshold: float, at: dt.datetime, status: str, closed: dt.datetime | None) -> None:
        observed = round(threshold * self.r.uniform(1.1, 4), 1)
        self.n["CmFraudAlert"] += 1
        self.add(CmFraudAlert(tenant_id=self.tid, party_id=pid, alert_number=f"FA-{self.n['CmFraudAlert']:05d}", fraud_type=ftype, fraud_event=event,
                              level=self.r.choice(["possible", "likely", "certain"]), threshold=threshold, observed=observed,
                              threshold_overflow=round(observed - threshold, 1), service="voice", status=status,
                              contact_attempts=self.r.randint(0, 3), owner_name="Fraud desk", created_at=at, closed_at=closed))

    def demo_ids(self) -> set[int]:
        if not hasattr(self, "_demo"):
            self._demo = {pid for (pid,) in self.db.query(Party.id).filter(Party.tenant_id == self.tid, Party.email.like(f"%@{DOMAIN}"))}
        return self._demo


def seed_history(db: Session, tenant_id: int, customers: int = 320, months: int = 24, seed: int = 42) -> dict[str, Any]:
    """Generate the demo history into one organization; returns what was made. Idempotent."""
    if db.query(Party.id).filter(Party.tenant_id == tenant_id, Party.email.like(f"%@{DOMAIN}")).first():
        return {"seeded": False, "reason": "demo history already present"}
    out = _Gen(db, tenant_id, customers, months, seed).run()
    db.commit()
    return out


def main(argv=None) -> None:
    import argparse
    import time

    from app.db import SessionLocal
    parser = argparse.ArgumentParser(description="Seed a telecom demo history (sales, orders, billing, usage, tickets) for Analytics & Reports")
    parser.add_argument("--tenant-slug", default="default")
    parser.add_argument("--customers", type=int, default=320, help="about how many customers to win over the period (default 320)")
    parser.add_argument("--months", type=int, default=24, help="how many months of history (default 24)")
    parser.add_argument("--seed", type=int, default=42, help="random seed - the same seed gives the same history")
    args = parser.parse_args(argv)
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter_by(slug=args.tenant_slug).first()
        if tenant is None:
            raise SystemExit(f"No organization with slug {args.tenant_slug!r}")
        began = time.time()
        out = seed_history(db, tenant.id, args.customers, args.months, args.seed)
        out["seconds"] = round(time.time() - began, 1)
        print(json.dumps(out, indent=2))
