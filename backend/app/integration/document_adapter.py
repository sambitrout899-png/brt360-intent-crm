"""Internal adapter shaped like a Document Management system - where KYC
identity documents, signed contracts and other case artifacts would
actually be stored in a real deployment (today: nothing persists the
document itself, only a reference). Deterministic stand-in: returns a
fabricated document-store reference id; a real deployment swaps this for
an actual upload call (e.g. to a DMS or object store) without any
upstream caller needing to change.
"""

from __future__ import annotations

import uuid


def store_document_reference(category: str, related_entity: str) -> str:
    return f"DOC-{category.upper()}-{uuid.uuid4().hex[:10].upper()}"
