"""Service accounts belong directly to a customer/prospect.

Their billing-account reference identifies the payer; it does not define
the service owner's place in the customer hierarchy.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow


class ServiceAccount(Base):
    __tablename__ = "service_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id"), nullable=True, index=True)
    billing_account_id: Mapped[int] = mapped_column(ForeignKey("billing_accounts.id"))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active/suspended/closed
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


from sqlalchemy import event
from sqlalchemy.orm import Session


@event.listens_for(Session, "before_flush")
def link_account_hierarchy(session, *_):
    from app.models.orders import BillingAccount
    from app.models.comms import CmBillingProfile
    for obj in list(session.new) + list(session.dirty):
        if isinstance(obj, ServiceAccount) and obj.billing_account_id:
            billing = session.get(BillingAccount, obj.billing_account_id)
            if billing is None or billing.tenant_id != obj.tenant_id:
                raise ValueError("Billing account not found in this workspace")
            if obj.party_id is None:
                obj.party_id = billing.party_id
            from app.models.crm import Party
            owner = session.get(Party, obj.party_id)
            if owner is None or owner.tenant_id != obj.tenant_id:
                raise ValueError("Service account owner not found in this workspace")
        elif isinstance(obj, CmBillingProfile):
            if obj.billing_account_id is None:
                billing = session.query(BillingAccount).filter_by(tenant_id=obj.tenant_id, party_id=obj.party_id).first()
                if billing:
                    obj.billing_account_id = billing.id
            else:
                billing = session.get(BillingAccount, obj.billing_account_id)
                if billing is None or billing.tenant_id != obj.tenant_id or billing.party_id != obj.party_id:
                    raise ValueError("Bill profile must belong to the customer's billing account")
