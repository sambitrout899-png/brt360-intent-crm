"""Security testing (orchestration/security_tests.py): VAPT assessments of the platform - automated checks run in
an isolated sandbox (the deployed code and configuration, synthetic tenants, a throwaway database), read-only
checks of the live configuration and database, checks of the public address, and the manual VAPT procedures a
tester records here. Each assessment keeps its findings and their evidence."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow

RUN_STATUSES = ("queued", "running", "completed", "cancelled", "error")
# passed / failed / error / skipped (could not run here) / not_applicable / pending (a manual procedure not recorded yet)
RESULT_STATUSES = ("passed", "failed", "error", "skipped", "not_applicable", "pending")


class SecurityTestRun(Base):
    __tablename__ = "security_test_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    number: Mapped[str] = mapped_column(String(30))  # SEC-000001
    name: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="queued")
    started_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # {"categories": [...]} - empty = every check; plus the environment it described
    scope: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    grade: Mapped[str | None] = mapped_column(String(5), nullable=True)
    # {"critical": n, "high": n, ...} of open findings; the sandbox's own facts (endpoints, requests sent ...)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class SecurityTestResult(Base):
    __tablename__ = "security_test_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("security_test_runs.id"), index=True)
    code: Mapped[str] = mapped_column(String(20))  # SEC-AUTH-02, VAPT-03
    category: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20))  # RESULT_STATUSES
    severity: Mapped[str] = mapped_column(String(10), default="info")  # of the finding when it fails
    cvss: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    # [{"probe": "...", "request": "GET /api/...", "status": 401, "result": "...", "ok": true}, ...]
    evidence: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # a manual procedure: what the tester recorded
    tester_note: Mapped[str] = mapped_column(Text, default="")
    recorded_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recorded_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
