"""Internal adapter shaped like TMF629 (Customer Management) / TMF632 (Party) -
HLD 6.2. Used by INT-04 (prospect creation) and INT-07/09 (resolving the
paying customer an order/billing account belongs to).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.crm import Party


def get_or_create_party(db: Session, name: str, party_type: str = "organization") -> Party:
    party = db.query(Party).filter(Party.name == name).first()
    if party is None:
        party = Party(name=name, party_type=party_type)
        db.add(party)
        db.flush()
    return party


def get_party_by_name(db: Session, name: str) -> Party | None:
    return db.query(Party).filter(Party.name == name).first()
