"""Authentication & authorization - password hashing, JWT issuing/
verification, and the FastAPI dependencies every protected route uses.

This is the security boundary app/tenancy.py's docstrings have been
disclosing as missing since Phase 1: tenant_id now comes from a verified
JWT, not a client-supplied header - get_current_tenant_id (app/tenancy.py)
derives it from get_current_user below, so every existing route using
that dependency is automatically covered without being individually
rewritten.
"""

from __future__ import annotations

import datetime as dt

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models.auth import User, UserRole
from app.timeutils import utcnow

_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    rounds = get_settings().bcrypt_rounds
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=rounds)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user: User) -> str:
    settings = get_settings()
    now = utcnow()
    payload = {
        "sub": str(user.id),
        "tenant_id": user.tenant_id,
        "role": user.role.value,
        "iat": now,
        "exp": now + dt.timedelta(minutes=settings.jwt_expiry_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    settings = get_settings()
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")

    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or deactivated")
    return user


def require_roles(*roles: UserRole):
    """Dependency factory: raises 403 unless the authenticated user has
    one of the given roles. Use as Depends(require_roles(UserRole.admin)).
    """

    def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires one of: {', '.join(r.value for r in roles)}")
        return user

    return _check
