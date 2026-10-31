"""The intent catalog: every intent a tenant can run, described for people.

For each built-in intent (INT-01..32) this module knows a business-friendly
label and description, a category and icon for the command bar, the record
type it acts on, and a typed field schema - what to ask for, how (record
lookup, picklist, money, ...), and where options come from. A tenant's
IntentSetting rows override presentation, switch intents on/off, restrict
them to roles, add phrases and set field defaults. Custom intents
(FLOW-<id>) are tenant-defined: their schema lives in IntentSetting.
fields_json and their behaviour in a custom_intent flow.

Who may run an intent: anyone, unless the intent lists roles - then a user
needs at least one of them. A user who holds no roles yet keeps every
intent (same rule as pages: nothing disappears before roles are set up);
a user whose roles are all switched off does not.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.db import cached
from app.intent_engine import reasoning
from app.models.auth import User
from app.models.flows import FlowDefinition
from app.models.intent_catalog import IntentSetting

CATEGORIES: list[tuple[str, str]] = [
    ("sales", "Sales"),
    ("customers", "Customers"),
    ("orders", "Orders & Delivery"),
    ("service", "Service & Support"),
    ("billing", "Billing & Credit"),
    ("products", "Products"),
    ("records", "Any record"),
    ("workspace", "Workspace setup"),
    ("custom", "Our processes"),
]
CATEGORY_KEYS = [k for k, _ in CATEGORIES]

FIELD_TYPES = ("text", "textarea", "number", "money", "percent", "picklist", "record", "list", "boolean", "updates", "transition", "attributes", "design",
               "page", "products")


@dataclass
class FieldSpec:
    key: str
    label: str
    type: str = "text"
    required: bool = False
    help: str = ""
    placeholder: str = ""
    values: list[str] = field(default_factory=list)
    # "@offerings" | "@regions" | "@users" | "@positions" | "@entity_types" | "@id_providers" | "@order_sites"
    source: str = ""
    # record lookups: the record type, or "@entity_type" = whatever the entity_type field says
    record_type: str = ""
    default: Any = None
    # Shown only when another field holds one of these values, e.g.
    # {"field": "segment", "in": ["B2C"]}; empty = always shown.
    show_when: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# Every field key the built-in intents use, described once.
_LIB: dict[str, dict[str, Any]] = {
    "name": {"label": "Name", "placeholder": "e.g. Fiber 800 Business"},
    "composition": {"label": "Included products", "type": "list", "help": "The products in the bundle - the first is the main connection", "placeholder": "Fiber500, VOIP"},
    "segment": {"label": "Customer segment", "type": "picklist", "values": ["B2C", "B2B", "B2B-SMB", "B2B-Enterprise"]},
    "channel": {"label": "Sales channel", "type": "picklist", "values": ["B2C", "B2B"]},
    "region": {"label": "Region", "type": "picklist", "source": "@regions", "default": "ALL"},
    "list_price": {"label": "Monthly price", "type": "money"},
    "requested_discount_pct": {"label": "Discount", "type": "percent", "default": 0},
    "offering_name": {"label": "Product / bundle", "type": "picklist", "source": "@offerings"},
    "new_list_price": {"label": "New monthly price", "type": "money"},
    "new_segment": {"label": "New segment", "type": "picklist", "values": ["B2C", "B2B", "B2B-SMB", "B2B-Enterprise"]},
    "new_channel": {"label": "New channel", "type": "picklist", "values": ["B2C", "B2B"]},
    "replacement_offering_name": {"label": "Move customers to", "type": "picklist", "source": "@offerings"},
    "company_name": {"label": "Company", "placeholder": "e.g. Acme Telecom"},
    "source": {"label": "Lead source", "type": "picklist", "values": ["referral", "website", "event", "partner", "cold_call", "campaign"]},
    "opportunity_id": {"label": "Opportunity", "type": "record", "record_type": "opportunity"},
    "quantity": {"label": "Quantity", "type": "number", "default": 1},
    "discount_justification": {"label": "Why this discount?", "type": "textarea", "help": "Needed when the discount is above 10%"},
    "quote_id": {"label": "Quote", "type": "record", "record_type": "quote"},
    "decision": {"label": "Decision", "type": "picklist", "values": ["approve", "reject"], "default": "approve"},
    "reasoning": {"label": "Comment", "type": "textarea"},
    "service_account_name": {"label": "Service account name", "help": "Defaults to the product name"},
    "sites": {"label": "Sites", "type": "list", "help": "For multi-site circuits, e.g. A-End, Z-End", "placeholder": "A-End, Z-End"},
    "order_id": {"label": "Order", "type": "record", "record_type": "order"},
    "new_monthly_price": {"label": "New monthly price", "type": "money"},
    "action": {"label": "What should happen", "type": "picklist", "values": ["suspend", "cancel", "terminate"]},
    "description": {"label": "What is the problem?", "type": "textarea", "placeholder": "e.g. internet down since this morning at the head office"},
    "sla_class": {"label": "Service level", "type": "picklist", "values": ["standard", "premium"], "default": "standard"},
    "ticket_id": {"label": "Ticket", "type": "record", "record_type": "ticket"},
    "required_skill": {"label": "Skill needed", "type": "picklist", "values": ["general", "fiber_install", "copper", "wireless", "enterprise_network"], "default": "general"},
    "scheduled_window": {"label": "Visit window", "placeholder": "e.g. tomorrow 9-12"},
    "party_id": {"label": "Customer", "type": "record", "record_type": "party"},
    "id_number": {"label": "ID number"},
    "id_type": {"label": "ID type", "type": "picklist", "values": ["national_id", "passport", "iqama", "commercial_registration"], "default": "national_id"},
    "provider": {"label": "Verification provider", "type": "picklist", "source": "@id_providers"},
    "site_label": {"label": "Site", "type": "picklist", "source": "@order_sites"},
    "email": {"label": "Email"},
    "phone": {"label": "Phone"},
    "lead_id": {"label": "Lead", "type": "record", "record_type": "lead"},
    "address": {"label": "Site address", "placeholder": "e.g. 12 King Fahd Road, Riyadh"},
    "new_quantity": {"label": "New quantity", "type": "number"},
    "proposal_id": {"label": "Proposal", "type": "record", "record_type": "proposal"},
    "work_order_id": {"label": "Work order", "type": "record", "record_type": "work_order"},
    "technical_parameters": {"label": "Technical parameters", "type": "textarea"},
    "signature": {"label": "Customer signature / acceptance note"},
    "document_ref": {"label": "Document reference"},
    "entity_type": {"label": "Record type", "type": "picklist", "source": "@entity_types"},
    "entity_id": {"label": "Record", "type": "record", "record_type": "@entity_type"},
    "fields": {"label": "Changes", "type": "updates"},
    "to_state": {"label": "Move to", "type": "transition"},
    "to_user": {"label": "Assign to person", "type": "picklist", "source": "@users"},
    "to_position": {"label": "Assign to team / position", "type": "picklist", "source": "@positions"},
    "to_department": {"label": "Or to a whole department", "type": "picklist", "source": "@departments"},
    "include_related": {"label": "Move its opportunity / open quotes too", "type": "boolean", "default": True},
    "reason": {"label": "Reason", "type": "textarea"},
    "instruction": {"label": "What should change?", "type": "textarea",
                    "placeholder": "e.g. add billing and network panes side by side, connect our billing system, use Vi red",
                    "help": "Say it in your own words - panes, charts, colours, layout, or which systems to pull data from"},
    # Sales-to-Order / B2C fields
    "price_list": {"label": "Pricelist", "type": "picklist", "source": "@price_lists", "help": "What this account pays - blank = the default list"},
    "industry": {"label": "Industry", "type": "picklist", "values": [
        "Banking & Finance", "Government", "Healthcare", "Education", "Retail", "Manufacturing", "IT & Services",
        "Media", "Hospitality", "Logistics", "Energy & Utilities", "Telecom", "Other"]},
    "pricing_attributes": {"label": "Pricing attributes", "type": "attributes", "source": "@offering_attributes:pricing",
                           "help": "The product's options that set its price - designed under Admin -> Product Configuration"},
    "technical_attributes": {"label": "Technical attributes", "type": "attributes", "source": "@offering_attributes:technical",
                             "help": "What provisioning needs - VLAN, CPE, IP block ... - carried to the order and work orders"},
    "price": {"label": "Monthly price on this list", "type": "money"},
    "design": {"label": "Attributes", "type": "design",
               "help": "Let the AI suggest them, tell it what to change, or edit the cards - nothing is saved until you run it"},
    "po_number": {"label": "PO number", "placeholder": "e.g. PO-2026-0142"},
    "po_amount": {"label": "PO amount", "type": "money", "help": "Defaults to the quote total"},
    "notes": {"label": "Notes", "type": "textarea"},
    # New Connect: the record's page from the Page Designer (record_type says which entity's page)
    "details": {"label": "Details", "type": "page",
                "help": "The fields your administrator set up for this kind of record - mandatory ones are marked"},
    "need": {"label": "What the customer needs", "type": "textarea",
             "placeholder": "e.g. 20-seat office on fibre, at least 2 Mbps, with a managed router, budget 4,000 a month",
             "help": "In plain words - the AI proposes choices, the configurator keeps only valid ones and completes the rest"},
    "max_monthly": {"label": "Monthly budget", "type": "money", "help": "Optional - the cheapest valid configuration within it"},
    "valid_from": {"label": "Valid from", "placeholder": "e.g. 2026-11-01 (blank = now)"},
    "products": {"label": "Interested products", "type": "products", "source": "@segment_offerings",
                 "help": "Only products sold to the prospect's segment are offered"},
}


@dataclass
class IntentMeta:
    code: str
    label: str
    description: str
    category: str
    icon: str
    target: str | None  # record type the intent acts on / creates
    fields: list[str]
    examples: list[str] = field(default_factory=list)
    # "B2B" / "B2C" for a step of one segment's process (hidden from tenants
    # that don't sell to it); None = the same for both.
    segment: str | None = None


_B = IntentMeta
BUILTIN: dict[str, IntentMeta] = {m.code: m for m in [
    _B("INT-34", "Sync inventory check types", "Load feasibility check types from your tenant inventory connection.", "workspace", "🔄", None, [], ["Sync inventory check types"]),
    _B("INT-01", "Launch a new product", "Create a bundle in the catalog with its price and publish it for sale.", "products", "🚀", None,
       ["name", "composition", "segment", "channel", "region", "list_price", "requested_discount_pct"],
       ["Launch Fiber 800 with Fiber500 and VOIP for B2B at 1200"]),
    _B("INT-02", "Change a product", "Update the price, segment or channel of a live bundle.", "products", "✏️", None,
       ["offering_name", "new_list_price", "new_segment", "new_channel"], ["Change Fiber 800 price to 1100"]),
    _B("INT-03", "Retire a product", "Stop selling a bundle and move its customers to a replacement.", "products", "📦", None,
       ["offering_name", "replacement_offering_name"], ["Retire Fiber 500, replace with Fiber 800"]),
    _B("INT-04", "Qualify a lead", "Register a company as a qualified lead - an opportunity is opened for it, with the product they want.", "sales", "🎯", "lead",
       ["company_name", "source", "region", "offering_name", "quantity"], ["New lead Acme Telecom from a referral in the North, interested in Fiber 800"]),
    _B("INT-05", "Create a quote", "Price a product for an opportunity, with discount if needed.", "sales", "🧾", "quote",
       ["opportunity_id", "offering_name", "quantity", "requested_discount_pct", "discount_justification", "pricing_attributes", "price_list"],
       ["Quote Fiber 800 for this opportunity with 5% off"]),
    _B("INT-06", "Approve a quote", "Check feasibility and approve or reject a quote.", "sales", "✅", "quote",
       ["quote_id", "decision", "reasoning"], ["Approve this quote - strategic account"]),
    _B("INT-07", "Activate an order", "Turn an approved quote into a live service, end to end - or place a consumer's order for a product.",
       "orders", "⚡", "order",
       ["quote_id", "service_account_name", "sites", "party_id", "offering_name", "quantity", "requested_discount_pct",
        "pricing_attributes", "technical_attributes"],
       ["Activate the order for this quote"]),
    _B("INT-08", "Change a live service", "Upgrade or downgrade an active service with a new monthly price.", "orders", "🔁", "order",
       ["order_id", "new_monthly_price"], ["Upgrade this service to 1500 a month"]),
    _B("INT-09", "Suspend or cancel a service", "Suspend, cancel or terminate a customer's service.", "orders", "⏸️", "order",
       ["order_id", "action", "decision"], ["Suspend this order"]),
    _B("INT-10", "Log a complaint", "Open a ticket for a customer problem - it is classified and prioritised for you.", "service", "🆘", "ticket",
       ["description", "company_name", "sla_class"], ["Acme's internet has been down since morning"]),
    _B("INT-11", "Diagnose a ticket", "Find the probable root cause of a ticket.", "service", "🩺", "ticket",
       ["ticket_id"], ["Diagnose this ticket"]),
    _B("INT-12", "Send a technician", "Dispatch a field technician for a ticket or an order.", "service", "🧰", "work_order",
       ["ticket_id", "order_id", "required_skill", "region", "scheduled_window"], ["Send a fiber technician for this ticket"]),
    _B("INT-13", "Verify customer identity", "Check a customer's ID with a verification provider.", "customers", "🪪", "party",
       ["party_id", "id_number", "id_type", "provider"], ["Verify ID 1012345678 for this customer"]),
    _B("INT-14", "Provision a site", "Bring one site of a multi-site order live.", "orders", "📍", "order",
       ["order_id", "site_label"], ["Provision the A-End of this order"]),
    _B("INT-15", "Add a prospect", "Add a new prospective customer - business (B2B) or consumer (B2C), with the details their page asks for.",
       "customers", "👤", "party",
       ["segment", "name", "email", "phone", "details", "products", "order_channel", "installation_address"],
       ["Add prospect Globex, ops@globex.com"]),
    _B("INT-16", "Add a product to a lead", "Record which product a lead is interested in.", "sales", "➕", "lead",
       ["lead_id", "offering_name"], ["This lead wants Fiber 800"]),
    _B("INT-17", "Approve a lead", "Approve or reject a lead at the qualification gate.", "sales", "👍", "lead",
       ["lead_id", "decision", "reasoning"], ["Approve this lead"]),
    _B("INT-18", "Check feasibility", "Check network coverage and capacity for an opportunity's site - Presales' feasible / not feasible moves it on.",
       "sales", "📡", "opportunity",
       ["opportunity_id", "address", "decision", "inventory_override", "override_reason", "notes", "technical_attributes"],
       ["Check feasibility for this opportunity at 12 King Fahd Road"]),
    _B("INT-19", "Revise a quote", "Change the discount or quantity of a draft quote.", "sales", "🛠️", "quote",
       ["quote_id", "requested_discount_pct", "quantity", "pricing_attributes"], ["Revise this quote to 8% discount"]),
    _B("INT-20", "Cancel a quote", "Withdraw a quote.", "sales", "🗑️", "quote",
       ["quote_id", "reasoning"], ["Cancel this quote"]),
    _B("INT-21", "Revise an order", "Change price or quantity of an order before it is fulfilled.", "orders", "🧮", "order",
       ["order_id", "new_monthly_price", "new_quantity"], ["Change this order to 3 lines"]),
    _B("INT-22", "Send a proposal", "Generate the customer proposal for an approved quote.", "sales", "📨", "quote",
       ["quote_id"], ["Send the proposal for this quote"]),
    _B("INT-23", "Record the customer's answer", "Record whether the customer accepted the proposal.", "sales", "✍️", "quote",
       ["proposal_id", "decision"], ["The customer accepted the proposal"]),
    _B("INT-24", "Create the order", "Create the order from an accepted quote.", "orders", "🛒", "order",
       ["quote_id"], ["Create the order for this quote"]),
    _B("INT-25", "Approve an order", "Order-control approval before fulfilment starts.", "orders", "🛂", "order",
       ["order_id", "decision", "reasoning"], ["Approve this order"]),
    _B("INT-26", "Complete installation", "Close the installation work order.", "orders", "🔧", "work_order",
       ["work_order_id", "technical_parameters"], ["Installation done for this work order"]),
    _B("INT-27", "Complete connectivity test", "Close the connectivity test with the customer's acceptance.", "orders", "📶", "work_order",
       ["work_order_id", "signature"], ["Connectivity test passed, signed by the customer"]),
    _B("INT-28", "Attach acceptance letter", "Close the final work order with the acceptance letter.", "orders", "📄", "work_order",
       ["work_order_id", "document_ref"], ["Acceptance letter received"]),
    _B("INT-29", "Update a record", "Edit the details of a customer, lead, opportunity, quote or order.", "records", "📝", "@entity_type",
       ["entity_type", "entity_id", "fields"], ["Update this opportunity's name"]),
    _B("INT-30", "New quote revision", "Create a new editable revision of a locked quote.", "sales", "🗂️", "quote",
       ["quote_id", "requested_discount_pct", "quantity"], ["Make a new revision of this quote"]),
    _B("INT-31", "Move to another stage", "Close won, close lost, reopen, or move to any configured state.", "records", "➡️", "@entity_type",
       ["entity_type", "entity_id", "to_state", "reasoning"], ["Close this deal as won"]),
    _B("INT-32", "Assign / hand over", "Give a record to another person, team or department - its deal comes along.", "records", "🤝",
       "@entity_type", ["entity_type", "entity_id", "to_user", "to_position", "to_department", "include_related", "reason"],
       ["Assign this to the Deal Desk", "Assign opportunity 12 to the Enterprise Sales department"]),
    _B("INT-33", "Configure 360", "Design the Customer 360 page - panes, charts, look & feel - and connect billing, service, network and inventory systems.",
       "workspace", "🧩", None, ["instruction"],
       ["Configure 360: add billing and network panes side by side", "Configure 360: connect our billing system and use Vi red"]),
    # Sales-to-Order (B2B) and B2C additions - see app/orchestration/actions_sales.py
    _B("INT-35", "Convert to an account", "Make a prospect an account and give it the pricelist it buys on.", "customers", "🏢", "party",
       ["party_id", "price_list", "industry"], ["Convert prospect Globex to an account on the Enterprise pricelist"], "B2B"),
    _B("INT-36", "Approve or reject", "Decide an approval a record is waiting for - a discount, a lead, an order or a stage.",
       "records", "⚖️", "@entity_type", ["entity_type", "entity_id", "decision", "reasoning"], ["Approve order 12"]),
    _B("INT-37", "Configure the product", "Set the product, quantity and pricing attributes on an opportunity.", "sales", "🧩", "opportunity",
       ["opportunity_id", "offering_name", "quantity", "pricing_attributes", "technical_attributes"],
       ["Configure this opportunity: Fiber 800, bandwidth 100 Mbps, 36 months"], "B2B"),
    _B("INT-38", "Send to Presales", "Hand a configured opportunity to Presales for the feasibility check, with the site and technical parameters.",
       "sales", "📐", "opportunity", ["opportunity_id", "address", "technical_parameters", "technical_attributes", "to_user"],
       ["Send this opportunity to presales, site 12 King Fahd Road"], "B2B"),
    _B("INT-39", "Attach the customer's PO", "Attach the purchase order to an approved quote - it then goes to Sales Ops.", "sales", "📎", "quote",
       ["quote_id", "po_number", "po_amount", "document_ref"], ["PO-2026-0142 received for this quote"], "B2B"),
    _B("INT-40", "Match all", "Sales Ops checks the PO against the quote; a full match creates the order and sends it for approval.",
       "orders", "✔️", "quote", ["quote_id"], ["Match all for this quote"], "B2B"),
    _B("INT-41", "Add a consumer", "Add a consumer (B2C) customer.", "customers", "🙋", "party",
       ["name", "email", "phone", "products", "order_channel", "installation_address"], ["Add consumer Priya Sharma, priya@example.com"], "B2C"),
    # Product configuration - see app/orchestration/actions_config.py
    _B("INT-42", "Design product attributes", "Describe a product's pricing and technical attributes in your own words - values, "
       "price impacts, what's required, and how they depend on each other.", "products", "🎛️", None,
       ["offering_name", "design"],
       ["Design Fiber 800: Bandwidth 100 Mbps +0, 1 Gbps +1500; technical: VLAN ID required number"]),
    _B("INT-43", "Add a product to a pricelist", "Set what a product costs on a pricelist.", "products", "🏷️", None,
       ["price_list", "offering_name", "price"], ["Put Fiber 800 on the Government pricelist at 850"]),
    _B("INT-44", "Apply a pricelist", "Price a customer, an opportunity or a quote from a pricelist - the quote's own wins, then the "
       "opportunity's, then the customer's.", "sales", "📋", "@entity_type",
       ["entity_type", "entity_id", "price_list"], ["Use the Strategic pricelist for quote 12"]),
    _B("INT-45", "Set technical attributes", "Record what provisioning needs on an order - VLAN, CPE, IP block ... - "
       "it goes to the work orders, the network and inventory.", "orders", "🔧", "order",
       ["order_id", "technical_attributes"], ["Order 12: VLAN ID 120, CPE type Cisco"]),
    _B("INT-46", "Generate a lead", "Open a lead for a prospect and select one or more products they're interested in.",
       "sales", "🌱", "lead", ["party_id", "products", "source", "region", "details"],
       ["Generate a lead for prospect 7 interested in Fiber 800 and VOIP"]),
    _B("INT-47", "Configure with AI", "Describe what the customer needs - the configurator builds a valid configuration (cheapest within a budget) on the opportunity.",
       "sales", "🤖", "opportunity", ["opportunity_id", "offering_name", "need", "max_monthly"],
       ["Configure with AI for opportunity 4: fibre office line with a router, budget 4000"]),
    _B("INT-48", "Release a product model", "Run the product's model tests and checks, then release its configuration model as a new version.",
       "products", "🏷️", None, ["offering_name", "valid_from", "notes"], ["Release the product model of Business Broadband Internet"]),
    _B("INT-49", "Check what a customer can buy", "Which products a customer qualifies for - eligibility, compatibility and what they already have.",
       "customers", "🧭", "party", ["party_id", "offering_name"], ["What can customer 7 buy?"]),
    _B("INT-50", "Change a live service's configuration", "Change the options of a live service - checked against the product's rules and applied as a delta.",
       "orders", "🔩", "order", ["order_id", "pricing_attributes"], ["Reconfigure order 12: Bandwidth 4 Mbps, CPE yes"]),
]}

# Where a shared field key means something specific in one intent.
_B2C_ONLY = {"field": "segment", "in": ["B2C"]}
_FIELD_OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    ("INT-46", "products"): {"label": "Product of Interest", "source": "@offerings_all",
                             "help": "Select one or more products - only those the prospect may buy are listed. "
                                     "They are carried to the opportunity and its quote."},
    ("INT-06", "decision"): {"values": ["approve", "reject", "conditional"]},
    ("INT-09", "decision"): {"label": "Retention", "values": ["retain"], "default": None,
                             "help": "Choose 'retain' if the customer accepted a retention offer"},
    ("INT-23", "decision"): {"label": "Customer's answer"},
    ("INT-12", "order_id"): {"help": "Or pick a ticket"},
    ("INT-12", "ticket_id"): {"help": "Or pick an order"},
    ("INT-10", "company_name"): {"label": "Customer"},
    ("INT-04", "offering_name"): {"label": "Product / bundle they want", "help": "Carried onto the opportunity and its quote"},
    ("INT-05", "offering_name"): {"help": "Taken from the opportunity when it has one"},
    ("INT-07", "quote_id"): {"help": "The approved quote - or, for a consumer, pick the customer and product below"},
    ("INT-07", "party_id"): {"help": "Consumer orders without a quote"},
    ("INT-07", "offering_name"): {"help": "Consumer orders without a quote"},
    ("INT-18", "decision"): {"label": "Presales decision", "values": ["feasible", "not_feasible"], "default": None,
                             "help": "Once Presales has it - leave blank to go by the checks"},
    ("INT-18", "inventory_override"): {"label": "Override the inventory check", "type": "boolean", "default": False,
                                       "show_when": {"field": "decision", "in": ["feasible"]},
                                       "help": "When Inventory is down or wrong: pass feasibility without holding the network resources - "
                                               "they are assigned at fulfilment"},
    ("INT-18", "override_reason"): {"label": "Why override?", "type": "textarea", "show_when": {"field": "decision", "in": ["feasible"]},
                                    "help": "Required with the override - kept on the opportunity's feasibility notes"},
    ("INT-35", "party_id"): {"label": "Prospect"},
    ("INT-15", "segment"): {"label": "Prospect type", "values": ["B2B", "B2C"], "help": "Decides which fields the prospect has"},
    ("INT-15", "name"): {"label": "Company or person name", "placeholder": "e.g. ITC Ltd. or Priya Sharma"},
    ("INT-15", "details"): {"label": "Prospect details", "record_type": "party"},
    ("INT-46", "party_id"): {"label": "Prospect", "help": "The prospect the lead is for"},
    ("INT-48", "offering_name"): {"label": "Product", "source": "@offerings_all"},
    ("INT-42", "offering_name"): {"source": "@offerings_all"},
    ("INT-43", "offering_name"): {"source": "@offerings_all"},
    ("INT-49", "offering_name"): {"label": "Only this product", "help": "Blank = every active product"},
    ("INT-50", "pricing_attributes"): {"label": "What changes", "help": "Only what changes - the rest stays as it is"},
    ("INT-46", "details"): {"label": "Lead details", "record_type": "lead"},
    ("INT-46", "source"): {"default": None},
    ("INT-46", "region"): {"default": None},
    ("INT-38", "to_user"): {"label": "Presales person", "help": "Blank = the Presales team"},
    ("INT-38", "address"): {"label": "Site address"},
    ("INT-39", "document_ref"): {"label": "PO document", "help": "File name or link"},
    ("INT-41", "name"): {"label": "Customer name", "placeholder": "e.g. Priya Sharma"},
    # B2C direct order: what the customer wants, and the order is placed straight away (INT-104)
    ("INT-15", "products"): {"label": "Products to order now", "help": "The order is placed straight away (no lead, "
                             "opportunity or quote) and the Order Module checks feasibility.", "show_when": _B2C_ONLY},
    ("INT-15", "order_channel"): {"label": "Order channel", "default": None, "help": "Where the order came in", "show_when": _B2C_ONLY},
    ("INT-15", "installation_address"): {"label": "Installation address", "help": "Where the service goes", "show_when": _B2C_ONLY},
    ("INT-41", "products"): {"label": "Products to order now", "help": "Blank = just add the customer; else the order is placed straight away"},
    ("INT-41", "order_channel"): {"default": None},
    ("INT-42", "instruction"): {"label": "Describe the attributes",
                                "placeholder": "e.g. Bandwidth: 100 Mbps +0, 500 Mbps +800, 1 Gbps +1500; Static IPs: 50 each; "
                                               "technical: VLAN ID required number; CPE type: Cisco, Huawei; if Bandwidth is 1 Gbps then SLA is required",
                                "help": "Pricing attributes change the price (+amount, +/-%, or an amount each); technical ones are what provisioning needs"},
    ("INT-05", "price_list"): {"label": "This quote's pricelist", "help": "Blank = the opportunity's, else the customer's, else the default"},
    ("INT-44", "entity_type"): {"label": "Apply to", "values": ["party", "opportunity", "quote"], "source": ""},
}

# Order Management intents (INT-51..71) - see om_intents.py.
from app.intent_engine import om_intents as _om  # noqa: E402

_LIB.update({k: v for k, v in _om.LIB.items() if k not in _LIB})
for _code, (_label, _desc, _cat, _icon, _target, _fields, _examples) in _om.SPECS.items():
    BUILTIN[_code] = _B(_code, _label, _desc, _cat, _icon, _target, _fields, _examples)
_FIELD_OVERRIDES.update(_om.OVERRIDES)

# Communications intents (INT-72..102) - see comm_intents.py.
from app.intent_engine import comm_intents as _cm  # noqa: E402

_LIB.update({k: v for k, v in _cm.LIB.items() if k not in _LIB})
for _code, (_label, _desc, _cat, _icon, _target, _fields, _examples) in _cm.SPECS.items():
    BUILTIN[_code] = _B(_code, _label, _desc, _cat, _icon, _target, _fields, _examples)
_FIELD_OVERRIDES.update(_cm.OVERRIDES)

# Configuration intents (INT-103) - see config_intents.py.
from app.intent_engine import config_intents as _cf  # noqa: E402

_LIB.update({k: v for k, v in _cf.LIB.items() if k not in _LIB})
for _code, (_label, _desc, _cat, _icon, _target, _fields, _examples) in _cf.SPECS.items():
    BUILTIN[_code] = _B(_code, _label, _desc, _cat, _icon, _target, _fields, _examples)
_FIELD_OVERRIDES.update(_cf.OVERRIDES)

# B2C direct-order intents (INT-104..105) - see b2c_intents.py.
from app.intent_engine import b2c_intents as _b2c  # noqa: E402

_LIB.update({k: v for k, v in _b2c.LIB.items() if k not in _LIB})
for _code, (_label, _desc, _cat, _icon, _target, _fields, _examples) in _b2c.SPECS.items():
    BUILTIN[_code] = _B(_code, _label, _desc, _cat, _icon, _target, _fields, _examples, segment="B2C" if _code in ("INT-104", "INT-105") else None)
_FIELD_OVERRIDES.update(_b2c.OVERRIDES)

# B2B Sales-to-Order documents (INT-109) - see sto_intents.py.
from app.intent_engine import sto_intents as _sto  # noqa: E402

_LIB.update({k: v for k, v in _sto.LIB.items() if k not in _LIB})
for _code, (_label, _desc, _cat, _icon, _target, _fields, _examples) in _sto.SPECS.items():
    BUILTIN[_code] = _B(_code, _label, _desc, _cat, _icon, _target, _fields, _examples, segment="B2B")
_FIELD_OVERRIDES.update(_sto.OVERRIDES)

# Siebel asset-based ordering around a change order (INT-110..115) - see abo_intents.py.
from app.intent_engine import abo_intents as _abo  # noqa: E402

_LIB.update({k: v for k, v in _abo.LIB.items() if k not in _LIB})
for _code, (_label, _desc, _cat, _icon, _target, _fields, _examples) in _abo.SPECS.items():
    BUILTIN[_code] = _B(_code, _label, _desc, _cat, _icon, _target, _fields, _examples)
_FIELD_OVERRIDES.update(_abo.OVERRIDES)
for _key in ("offering_name", "quantity", "pricing_attributes", "technical_attributes", "requested_discount_pct",
             "target_service_account_id", "target_site", "target_party_id", "port_number", "donor_operator", "recipient_operator",
             "promotion", "waive_penalty"):
    for _code in ("INT-112", "INT-114"):  # each operation shows only the details it uses, as on Change an asset
        if ("INT-107", _key) in _FIELD_OVERRIDES:
            _FIELD_OVERRIDES[(_code, _key)] = {**_FIELD_OVERRIDES[("INT-107", _key)], **_FIELD_OVERRIDES.get((_code, _key), {})}

# Quote negotiation - price override (INT-116) - see quote_intents.py.
from app.intent_engine import quote_intents as _qn  # noqa: E402

_LIB.update({k: v for k, v in _qn.LIB.items() if k not in _LIB})
for _code, (_label, _desc, _cat, _icon, _target, _fields, _examples) in _qn.SPECS.items():
    BUILTIN[_code] = _B(_code, _label, _desc, _cat, _icon, _target, _fields, _examples)
_FIELD_OVERRIDES.update(_qn.OVERRIDES)

# Fulfillment - COM / SOM / TOM order orchestration (INT-117..118) - see fulfillment_intents.py.
from app.intent_engine import fulfillment_intents as _fulfillment  # noqa: E402

_LIB.update({k: v for k, v in _fulfillment.LIB.items() if k not in _LIB})
for _code, (_label, _desc, _cat, _icon, _target, _fields, _examples) in _fulfillment.SPECS.items():
    BUILTIN[_code] = _B(_code, _label, _desc, _cat, _icon, _target, _fields, _examples)
_FIELD_OVERRIDES.update(_fulfillment.OVERRIDES)

# Inventory - the network record (INT-120..128) - see inventory_intents.py.
from app.intent_engine import inventory_intents as _inventory  # noqa: E402

_LIB.update({k: v for k, v in _inventory.LIB.items() if k not in _LIB})
for _code, (_label, _desc, _cat, _icon, _target, _fields, _examples) in _inventory.SPECS.items():
    BUILTIN[_code] = _B(_code, _label, _desc, _cat, _icon, _target, _fields, _examples)
_FIELD_OVERRIDES.update(_inventory.OVERRIDES)

# "At least one of" groups the form should explain (reasoning enforces them).
ONE_OF: dict[str, list[list[str]]] = {
    "INT-12": [["ticket_id", "order_id"]],
    "INT-123": [["opportunity_id", "order_id"]],  # a deal (Presales' feasibility) or an order
    "INT-124": [["opportunity_id", "order_id"]],
    "INT-32": [["to_user", "to_position", "to_department"]],
}


def field_spec(key: str, required: bool = False, **overrides: Any) -> FieldSpec:
    base = {"label": key.replace("_", " ").capitalize(), **_LIB.get(key, {}), **overrides}
    known = {k: v for k, v in base.items() if k in FieldSpec.__dataclass_fields__}
    return FieldSpec(key=key, required=required, **known)


def library() -> list[dict[str, Any]]:
    """Ready-made fields an admin can drop into a new intent."""
    return [field_spec(k).as_dict() for k in _LIB if k not in ("fields", "to_state", "entity_type", "entity_id")]


# ---------------------------------------------------------------- settings


def _settings(db: Session, tenant_id: int) -> dict[str, IntentSetting]:
    # read many times per request (every intent the catalog checks): once per session (app/db.py cached)
    rows = cached(db, ("intent_settings", tenant_id), IntentSetting,
                  lambda: {s.code: s for s in db.query(IntentSetting).filter(IntentSetting.tenant_id == tenant_id).all()})
    return dict(rows)


def _split(raw: str | None) -> list[str]:
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


def role_ids_of(setting: IntentSetting | None) -> list[int]:
    return [int(r) for r in _split(setting.role_ids if setting else "") if r.isdigit()]


def defaults_of(setting: IntentSetting | None) -> dict[str, Any]:
    try:
        return json.loads(setting.defaults_json or "{}") if setting else {}
    except json.JSONDecodeError:
        return {}


def custom_fields_of(setting: IntentSetting | None, flow: FlowDefinition) -> list[FieldSpec]:
    raw: list[dict[str, Any]] = []
    if setting is not None:
        try:
            raw = json.loads(setting.fields_json or "[]")
        except json.JSONDecodeError:
            raw = []
    required = {f.strip() for f in (flow.required_fields or "").split(",") if f.strip()}
    specs = [field_spec(f["key"], bool(f.get("required")), **{k: v for k, v in f.items() if k not in ("key", "required")}) for f in raw if f.get("key")]
    known = {s.key for s in specs}
    # Flows created in the Flow Studio only list required field names.
    specs += [field_spec(key, True) for key in required if key not in known]
    for spec in specs:
        if spec.key in required:
            spec.required = True
    return specs


# ---------------------------------------------------------------- catalog


def _builtin_entry(meta: IntentMeta, setting: IntentSetting | None) -> dict[str, Any]:
    required = set(reasoning.required_fields_for(meta.code))
    defaults = defaults_of(setting)
    fields = []
    for key in meta.fields:
        spec = field_spec(key, key in required, **_FIELD_OVERRIDES.get((meta.code, key), {}))
        if key in defaults:
            spec.default = defaults[key]
        fields.append(spec.as_dict())
    return {
        "code": meta.code,
        "kind": "builtin",
        "label": (setting.label if setting and setting.label else meta.label),
        "default_label": meta.label,
        "description": (setting.description if setting and setting.description else meta.description),
        "category": (setting.category if setting and setting.category in CATEGORY_KEYS else meta.category),
        "icon": (setting.icon if setting and setting.icon else meta.icon),
        "target": meta.target,
        "fields": fields,
        "one_of": ONE_OF.get(meta.code, []),
        "examples": meta.examples,
        "phrases": _split(setting.phrases if setting else ""),
        "is_active": setting.is_active if setting else True,
        "role_ids": role_ids_of(setting),
        "flow_id": None,
        "segment": meta.segment,
    }


def _custom_entry(flow: FlowDefinition, setting: IntentSetting | None) -> dict[str, Any]:
    from app.orchestration import flow_engine, process_journey

    defaults = defaults_of(setting)
    fields = []
    for spec in custom_fields_of(setting, flow):
        if spec.key in defaults:
            spec.default = defaults[spec.key]
        fields.append(spec.as_dict())
    phrases = _split(flow.trigger_phrases)
    return {
        "code": flow_engine.custom_intent_code(flow),
        "kind": "custom",
        "label": (setting.label if setting and setting.label else flow.name),
        "default_label": flow.name,
        "description": (setting.description if setting and setting.description else flow.description or ""),
        "category": (setting.category if setting and setting.category in CATEGORY_KEYS else "custom"),
        "icon": (setting.icon if setting and setting.icon else "✨"),
        "target": flow.entity_type,
        "fields": fields,
        "one_of": [],
        "examples": phrases[:2],
        "phrases": phrases,
        "is_active": bool(flow.is_active) and (setting.is_active if setting else True),
        "role_ids": role_ids_of(setting),
        "flow_id": flow.id,
        "segment": process_journey.scope(flow_engine.load_graph(flow), flow.name),
    }


def all_intents(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    """Every intent of the tenant, active or not (admin view)."""
    settings = _settings(db, tenant_id)
    entries = [_builtin_entry(meta, settings.get(code)) for code, meta in BUILTIN.items()]
    flows = (
        db.query(FlowDefinition)
        .filter(FlowDefinition.tenant_id == tenant_id, FlowDefinition.trigger_type == "custom_intent")
        .order_by(FlowDefinition.id)
        .all()
    )
    entries += [_custom_entry(f, settings.get(f"FLOW-{f.id}")) for f in flows]
    from app.licensing import intent_available
    return [e for e in entries if intent_available(db, tenant_id, e["code"])]


def _user_role_ids(db: Session, user: User) -> list[int]:
    from app.crm_core import access_service

    return [r.id for r in access_service.user_roles(db, user)]


def _has_assigned_roles(db: Session, user: User) -> bool:
    from app.crm_core import access_service

    return access_service.has_assigned_roles(db, user)


def allowed_for(entry: dict[str, Any], user_role_ids: list[int], has_roles: bool | None = None) -> bool:
    """has_roles: the user holds roles, switched-off ones included - a user
    whose roles are all switched off is not "a user without roles"."""
    if not entry["role_ids"]:
        return True
    if not user_role_ids:
        return not (has_roles if has_roles is not None else False)
    return bool(set(entry["role_ids"]) & set(user_role_ids))


# Change the workspace for the whole tenant - administrators only, whatever roles say.
ADMIN_ONLY = {"INT-33", "INT-34", "INT-48", "INT-103", "INT-119", "INT-127", "INT-128"}


def _is_admin(user: User) -> bool:
    return getattr(user.role, "value", user.role) == "admin"


def served_segments(db: Session, tenant_id: int) -> set[str]:
    from app.crm_core import process_service

    return process_service.segments(db, tenant_id)


def _lacks_role(db: Session, user: User) -> bool:
    from app.crm_core import access_service

    return access_service.lacks_role(db, user)


def intents_for_user(db: Session, tenant_id: int, user: User) -> list[dict[str, Any]]:
    if _lacks_role(db, user):
        return []
    role_ids = _user_role_ids(db, user)
    has_roles = bool(role_ids) or _has_assigned_roles(db, user)
    served = served_segments(db, tenant_id)
    return [
        e for e in all_intents(db, tenant_id)
        if e["is_active"] and allowed_for(e, role_ids, has_roles) and (e["code"] not in ADMIN_ONLY or _is_admin(user))
        and (e.get("segment") is None or e["segment"] in served)
        and availability(db, tenant_id, user, e["code"]) is None
    ]


def entry(db: Session, tenant_id: int, code: str | None) -> dict[str, Any] | None:
    if not code:
        return None
    from app.licensing import intent_available
    if not intent_available(db, tenant_id, code):
        return None
    settings = _settings(db, tenant_id)
    if code in BUILTIN:
        return _builtin_entry(BUILTIN[code], settings.get(code))
    if code.startswith("FLOW-"):
        try:
            flow = db.get(FlowDefinition, int(code.split("-", 1)[1]))
        except ValueError:
            return None
        if flow is not None and flow.tenant_id == tenant_id and flow.trigger_type == "custom_intent":
            return _custom_entry(flow, settings.get(code))
    return None


def availability(db: Session, tenant_id: int, user: User, code: str | None, entities: dict[str, Any] | None = None) -> str | None:
    """None when the user may run this intent, else the reason they can't.
    With the record it acts on (entities): the approver an approval waits
    for may decide it whatever their roles - the position decides."""
    from app.licensing import enabled, intent_module, MODULES
    module = intent_module(code)
    if module not in enabled(db, tenant_id):
        return f"{MODULES[module]['label']} is not licensed for your organization"
    from app.licensing import intent_available
    if not intent_available(db, tenant_id, code):
        return "This intent references a module that is not selected for your organization"
    found = entry(db, tenant_id, code)
    if found is None:
        return None  # unknown codes are handled by the classifier / reasoning
    if not found["is_active"]:
        return f"\"{found['label']}\" is switched off for your organization - ask your administrator."
    if found["code"] in ADMIN_ONLY and not _is_admin(user):
        return f"\"{found['label']}\" changes the workspace for everyone - only an administrator can run it."
    if found.get("segment") and found["segment"] not in served_segments(db, tenant_id):
        return f"\"{found['label']}\" is part of the {found['segment']} process, which your organization doesn't run."
    if _lacks_role(db, user):
        reason = "You don't have a role yet - ask your administrator for access."
    else:
        role_ids = _user_role_ids(db, user)
        if allowed_for(found, role_ids, bool(role_ids) or _has_assigned_roles(db, user)):
            return None
        reason = f"\"{found['label']}\" is not available to your roles - ask your administrator for access."
    if entities:
        from app.crm_core import approval_service

        if approval_service.decides_by_position(db, tenant_id, user.id, found["code"], entities):
            return None
    return reason


def label_of(db: Session, tenant_id: int, code: str | None) -> str | None:
    found = entry(db, tenant_id, code)
    return found["label"] if found else None


def requirements(db: Session, tenant_id: int, code: str | None) -> tuple[list[str], str | None]:
    """(the fields an intent needs, its label) - a Flow Studio flow's own
    fields when the code is a flow's, otherwise the built-in intent's."""
    if not code:
        return [], None
    from app.intent_engine import classifier
    from app.orchestration import flow_engine

    flow = flow_engine.flow_from_intent_code(db, tenant_id, code)
    if flow is not None:
        return flow_engine.required_fields(flow), label_of(db, tenant_id, code) or flow.name
    label = label_of(db, tenant_id, code) or classifier.INTENT_LABELS.get(code)
    return reasoning.required_fields_for(code), label


def extra_phrase_specs(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    """Admin-added phrases for built-in intents, in the classifier's
    custom-intent spec shape - so "raise a PO" can mean INT-24."""
    out = []
    for code, setting in _settings(db, tenant_id).items():
        if code in BUILTIN and setting.is_active and _split(setting.phrases):
            out.append({
                "code": code,
                "label": setting.label or BUILTIN[code].label,
                "description": setting.description or BUILTIN[code].description,
                "phrases": [p.lower() for p in _split(setting.phrases)],
                "required_fields": reasoning.required_fields_for(code),
                "entity_type": None,
                "builtin": True,
            })
    return out


def inactive_codes(db: Session, tenant_id: int) -> set[str]:
    return {code for code, s in _settings(db, tenant_id).items() if code in BUILTIN and not s.is_active}
