"""Users + roles - the authentication/authorization layer this platform
had none of before. Closes the gap where tenant_id came from a client-
supplied header anyone could set: tenant_id is now derived from the
authenticated user's JWT (see app/auth.py, app/tenancy.py).

Roles are intentionally coarse for now (four values, not a full
permission-matrix system) - easy to widen into separate sales/support/
billing roles later without a schema change, since nothing downstream
assumes exactly these four.
"""

from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow


class UserRole(str, enum.Enum):
    platform_admin = "platform_admin"  # cross-tenant: /api/platform/* only, no tenant_id
    admin = "admin"  # full access within their own tenant, including /api/admin/*
    agent = "agent"  # day-to-day CRM work: can submit intents, cannot touch /api/admin/*
    viewer = "viewer"  # read-only: blocked from submitting intents or any admin route


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Nullable only for platform_admin - every other role belongs to exactly one tenant.
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    # Sign-in security (app/crm_core/login_security.py).
    # Lockout: wrong passwords or codes in a row; at the limit the account
    # is locked until locked_until, and an admin can unlock it sooner.
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    # Two-step sign-in with an authenticator app (TOTP). The secret is
    # encrypted; it is set before mfa_enabled while the user is enrolling.
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    mfa_secret_enc: Mapped[str | None] = mapped_column(String(300), nullable=True)
    mfa_last_step: Mapped[int | None] = mapped_column(Integer, nullable=True)  # a code works once
    mfa_recovery_hashes: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of SHA-256
    # Last signed-in request (to the minute) - what the tenant's idle timeout is measured from.
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
