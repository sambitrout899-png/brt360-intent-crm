from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class LoginRequest(BaseModel):
    email: str = Field(pattern=_EMAIL_PATTERN)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    """A session (access_token) - or, when the password was right but a
    second step is due, which step and a short-lived step_token for it."""

    access_token: str | None = None
    token_type: str = "bearer"
    mfa_required: bool = False  # send a code: POST /api/auth/login/mfa
    mfa_setup_required: bool = False  # the company requires two-step sign-in: set it up first
    step_token: str | None = None


class MfaLoginRequest(BaseModel):
    step_token: str
    code: str = Field(min_length=6, max_length=20)


class MfaStatusOut(BaseModel):
    enabled: bool
    required: bool  # by the user's company
    recovery_codes_left: int


class MfaSetupOut(BaseModel):
    secret: str  # for typing in, when the QR code can't be scanned
    otpauth_uri: str
    qr_data_url: str


class MfaCodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=20)


class MfaEnableOut(BaseModel):
    recovery_codes: list[str]
    access_token: str | None = None  # when set up during sign-in: the session it completes


class MfaDisableRequest(BaseModel):
    password: str = Field(min_length=1)
    code: str = Field(min_length=6, max_length=20)


class SecurityPolicy(BaseModel):
    mfa_required: bool


class CurrentUserOut(BaseModel):
    id: int
    tenant_id: int | None
    email: str
    name: str
    role: str

    model_config = {"from_attributes": True}


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


class UserCreate(BaseModel):
    email: str = Field(pattern=_EMAIL_PATTERN)
    password: str = Field(min_length=8)
    name: str = Field(min_length=1, max_length=200)
    role: str = Field(default="agent")


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: str
    is_active: bool
    created_at: dt.datetime
    mfa_enabled: bool = False
    locked_until: dt.datetime | None = None

    model_config = {"from_attributes": True}
