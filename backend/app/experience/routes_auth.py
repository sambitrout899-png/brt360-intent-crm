"""Login + "who am I" - the entry point for every other protected route.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import create_access_token, get_current_user, hash_password, verify_password
from app.db import get_db
from app.models.auth import User
from app.models.tenant import Tenant
from app.schemas.admin import BrandingOut
from app.schemas.auth import ChangePasswordRequest, CurrentUserOut, LoginRequest, LoginResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    user = db.query(User).filter(User.email == payload.email).first()
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password")
    return LoginResponse(access_token=create_access_token(user))


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
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(401, "Current password is incorrect")
    user.password_hash = hash_password(payload.new_password)
    db.commit()
