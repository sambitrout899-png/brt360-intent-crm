"""Single source of truth for "now" timestamps.

Returns a naive UTC datetime (not timezone-aware) - this matches every
DateTime column in the schema (none declared with timezone=True) and avoids
SQLite's lack of real timezone-aware storage causing naive/aware comparison
bugs elsewhere. datetime.utcnow() itself is deprecated as of Python 3.12+;
this is the non-deprecated equivalent.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
