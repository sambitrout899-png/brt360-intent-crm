"""Login + "who am I" - the entry point for every other protected route.

Sign-in is one or two steps (app/crm_core/login_security.py): the password,
then - when two-step sign-in is on - a code from the authenticator app. A
company that requires two-step sign-in sends a user without it to set it up
first. Wrong passwords and codes count towards the account's lockout.
"""

from __future__ import annotations

import functools

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app import ratelimit
from app.auth import create_access_token, decode_token, get_current_user, hash_password, user_for_step, verify_password
from app.config import get_settings
from app.crm_core import login_security
from app.db import get_db
from app.models.auth import User
from app.models.tenant import Tenant
from app.schemas.admin import BrandingOut
from app.schemas.auth import (
    ChangePasswordRequest,
    CurrentUserOut,
    LoginRequest,
    LoginResponse,
    MfaCodeRequest,
    MfaDisableRequest,
    MfaEnableOut,
    MfaLoginRequest,
    MfaSetupOut,
    MfaStatusOut,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

STEP_MFA = "mfa"  # the code after the password
STEP_SETUP = "mfa_setup"  # setting up two-step sign-in during sign-in


@functools.lru_cache
def _dummy_hash() -> str:
    return hash_password("no-such-user")


def _locked(user: User) -> None:
    minutes = login_security.locked_for(user)
    if minutes:
        raise HTTPException(429, f"Too many wrong attempts - this account is locked for {minutes} more minute"
                                 f"{'s' if minutes > 1 else ''}. An administrator can unlock it sooner.")


def _required(db: Session, user: User) -> bool:
    tenant = db.get(Tenant, user.tenant_id) if user.tenant_id else None
    return bool(tenant and tenant.mfa_required)


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    ip = request.client.host if request.client else "?"
    if not ratelimit.allow(f"login-ip:{ip}", get_settings().login_ip_per_minute):
        raise HTTPException(429, "Too many sign-in attempts from here - wait a minute and try again")
    user = db.query(User).filter(User.email == payload.email).first()
    if user is None or not user.is_active:
        verify_password(payload.password, _dummy_hash())  # the same time as a real check: no hint who exists
        raise HTTPException(401, "Incorrect email or password")
    _locked(user)
    if not verify_password(payload.password, user.password_hash):
        login_security.failed(user)
        db.commit()
        raise HTTPException(401, "Incorrect email or password")
    if user.mfa_enabled:
        return LoginResponse(mfa_required=True, step_token=create_access_token(user, purpose=STEP_MFA, minutes=5))
    if _required(db, user):
        return LoginResponse(mfa_setup_required=True, step_token=create_access_token(user, purpose=STEP_SETUP, minutes=15))
    login_security.succeeded(user)
    db.commit()
    return LoginResponse(access_token=create_access_token(user))


@router.post("/login/mfa", response_model=LoginResponse)
def login_mfa(payload: MfaLoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    """The second step: a code from the authenticator app, or a recovery code."""
    user = user_for_step(payload.step_token, STEP_MFA, db)
    _locked(user)
    if not login_security.check_code(user, payload.code):
        login_security.failed(user)
        db.commit()
        raise HTTPException(401, "That code isn't right - use the newest one from your authenticator app")
    login_security.succeeded(user)
    db.commit()
    return LoginResponse(access_token=create_access_token(user))


_optional_bearer = HTTPBearer(auto_error=False)


def _enrolling(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
               db: Session = Depends(get_db)) -> tuple[User, bool]:
    """(user, during sign-in?) - signed in, or holding the setup step's token."""
    payload = decode_token(credentials.credentials) if credentials else None
    if payload and payload.get("purpose") == STEP_SETUP:
        return user_for_step(credentials.credentials, STEP_SETUP, db), True
    return get_current_user(request, credentials, db), False


@router.get("/mfa", response_model=MfaStatusOut)
def mfa_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> MfaStatusOut:
    return MfaStatusOut(enabled=user.mfa_enabled, required=_required(db, user),
                        recovery_codes_left=login_security.recovery_codes_left(user) if user.mfa_enabled else 0)


@router.post("/mfa/setup", response_model=MfaSetupOut)
def mfa_setup(enrolling: tuple[User, bool] = Depends(_enrolling), db: Session = Depends(get_db)) -> MfaSetupOut:
    """A new secret to scan. Two-step sign-in starts once a code from it is confirmed."""
    user, _ = enrolling
    try:
        secret = login_security.start_setup(user)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    uri = login_security.otpauth_uri(user, secret)
    return MfaSetupOut(secret=secret, otpauth_uri=uri, qr_data_url=login_security.qr_data_url(uri))


@router.post("/mfa/enable", response_model=MfaEnableOut)
def mfa_enable(payload: MfaCodeRequest, enrolling: tuple[User, bool] = Depends(_enrolling),
               db: Session = Depends(get_db)) -> MfaEnableOut:
    """Confirms the first code. Returns the recovery codes - shown this once -
    and, when set up during sign-in, the session it completes."""
    user, during_sign_in = enrolling
    _locked(user)
    try:
        codes = login_security.enable(user, payload.code)
    except ValueError as exc:
        login_security.failed(user)
        db.commit()
        raise HTTPException(400, str(exc)) from exc
    login_security.succeeded(user)
    db.commit()
    return MfaEnableOut(recovery_codes=codes, access_token=create_access_token(user) if during_sign_in else None)


@router.post("/mfa/disable", status_code=204)
def mfa_disable(payload: MfaDisableRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    if _required(db, user):
        raise HTTPException(400, "Your company requires two-step sign-in, so it can't be turned off")
    _locked(user)
    if not verify_password(payload.password, user.password_hash) or not login_security.check_code(user, payload.code):
        login_security.failed(user)
        db.commit()
        # 400, not 401: the session is fine (the UI signs out on a 401)
        raise HTTPException(400, "The password or the code isn't right")
    login_security.disable(user)
    db.commit()


@router.post("/mfa/recovery-codes", response_model=MfaEnableOut)
def mfa_new_recovery_codes(payload: MfaCodeRequest, user: User = Depends(get_current_user),
                           db: Session = Depends(get_db)) -> MfaEnableOut:
    """Ten new recovery codes; the old ones stop working."""
    if not user.mfa_enabled:
        raise HTTPException(400, "Two-step sign-in is off")
    if not login_security.check_code(user, payload.code, allow_recovery=False):
        login_security.failed(user)
        db.commit()
        raise HTTPException(400, "That code isn't right")
    codes = login_security.new_recovery_codes(user)
    db.commit()
    return MfaEnableOut(recovery_codes=codes)


@router.get("/jwks")
def jwks() -> dict:
    """The public keys BRT 360 signs its tokens with (RFC 7517) - for other
    services to verify a token themselves. Public by design: no secrets here."""
    from app.auth import public_jwks

    return public_jwks()


@router.get("/me", response_model=CurrentUserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.get("/branding", response_model=BrandingOut)
def branding(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> BrandingOut:
    """The signed-in user's company: name, logo and accent for the header."""
    tenant = db.get(Tenant, user.tenant_id) if user.tenant_id else None
    if tenant is None:
        return BrandingOut(name="BRT 360", display_name="BRT 360")
    return BrandingOut(
        name=tenant.name,
        display_name=tenant.display_name or tenant.name,
        logo_data_url=tenant.logo_data_url,
        brand_color=tenant.brand_color,
    )


@router.post("/change-password", status_code=204)
def change_password(
    payload: ChangePasswordRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> None:
    _locked(user)
    if not verify_password(payload.current_password, user.password_hash):
        login_security.failed(user)
        db.commit()
        raise HTTPException(400, "The current password isn't right")  # 400: the session itself is fine
    user.password_hash = hash_password(payload.new_password)
    db.commit()
