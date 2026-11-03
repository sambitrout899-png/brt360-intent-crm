"""The system test suite (orchestration/system_tests.py): test cases generated from what this tenant has -
every intent it can run, every custom record type (fields, rules, states, lookups, relationships), every flow
and page rule - and background runs of them that leave step-by-step evidence.

Runs never change the tenant's data: each case runs inside a transaction the runner rolls back (the same
guarantee as rehearsal - every side effect, notifications included, is a row in this database)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow

CASE_KINDS = ("understanding", "required_fields", "end_to_end", "lifecycle", "mandatory", "type_check", "rule", "lookup",
              "relationship", "flow", "page_rule")
RUN_STATUSES = ("queued", "running", "passed", "failed", "cancelled")
RESULT_STATUSES = ("passed", "failed", "error", "skipped")


class SystemTestSuite(Base):
    __tablename__ = "system_test_suites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    # what it covers: {"areas": [...], "record_types": [...], "intents": n, ...}
    scope: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    case_count: Mapped[int] = mapped_column(Integer, default=0)
    generated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class SystemTestCase(Base):
    __tablename__ = "system_test_cases"
    __table_args__ = (UniqueConstraint("suite_id", "code", name="uq_system_test_cases_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    suite_id: Mapped[int] = mapped_column(ForeignKey("system_test_suites.id"), index=True)
    code: Mapped[str] = mapped_column(String(20))  # TC-0001
    area: Mapped[str] = mapped_column(String(100))  # "B2B Sales to Order", "Custom: Site Survey", "Flows"
    kind: Mapped[str] = mapped_column(String(30))  # CASE_KINDS
    title: Mapped[str] = mapped_column(String(300))
    target: Mapped[str] = mapped_column(String(60), default="")  # INT-05, x_site_survey, FLOW-12, rule:7
    priority: Mapped[str] = mapped_column(String(10), default="Medium")
    # what the runner does: [{"do": "classify"|"intent"|"flow"|"validate"|..., ..., "expect": {...}}]
    steps: Mapped[list | None] = mapped_column(JSON, nullable=True)
    expected: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class SystemTestRun(Base):
    __tablename__ = "system_test_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    suite_id: Mapped[int] = mapped_column(ForeignKey("system_test_suites.id"), index=True)
    number: Mapped[str] = mapped_column(String(30))  # RUN-000012
    status: Mapped[str] = mapped_column(String(20), default="queued")
    started_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # which cases: {"areas": [...], "kinds": [...], "case_ids": [...]} - empty = every active case
    selection: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class SystemTestResult(Base):
    __tablename__ = "system_test_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("system_test_runs.id"), index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("system_test_cases.id"), index=True)
    status: Mapped[str] = mapped_column(String(10))  # RESULT_STATUSES
    message: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    # step by step: request, response (status, intent, rationale, plan, records, impact), assertions
    evidence: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
