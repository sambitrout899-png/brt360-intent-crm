"""Admin-triggered automated test runs.

Closes the "how do I verify this deployment is healthy without SSH" gap:
an admin clicks a button in the UI, the backend runs its own real pytest
suite as a subprocess and reports the result. Safe to trigger against a
live deployment because the suite runs entirely against its own isolated
in-memory SQLite fixture (see tests/conftest.py) - it never touches the
tenant's real Postgres data. See app/crm_core/test_runner_service.py.
"""

from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import Enum, Float, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow


class TestRunStatus(str, enum.Enum):
    # __test__ = False stops pytest from mistaking this for a test class
    # to collect, purely because of the "Test" name prefix.
    __test__ = False

    running = "running"
    passed = "passed"
    failed = "failed"
    error = "error"


class TestRun(Base):
    __test__ = False

    __tablename__ = "test_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    status: Mapped[TestRunStatus] = mapped_column(Enum(TestRunStatus), default=TestRunStatus.running)
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    output: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    completed_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
