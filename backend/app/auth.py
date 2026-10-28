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
import functools
import hashlib

import bcrypt
import jwt
from cryptography.hazmat.primitives import serialization
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app import rls
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


def _key_id(public_key) -> str:
    """A key's id (the token header's `kid`): its fingerprint, so it never needs configuring."""
    der = public_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()[:16]


@functools.lru_cache
def _signing_key(path: str):
    with open(path, "rb") as f:
        key = serialization.load_pem_private_key(f.read(), password=None)
    return key, _key_id(key.public_key())


@functools.lru_cache
def _verification_keys(private_key_file: str, previous_public_key_files: str) -> dict[str, object]:
    """kid -> public key: the current signing key's, plus earlier keys still
    trusted while their tokens run out (key rotation)."""
    keys: dict[str, object] = {}
    if private_key_file:
        key, kid = _signing_key(private_key_file)
        keys[kid] = key.public_key()
    for path in filter(None, (p.strip() for p in previous_public_key_files.split(","))):
        with open(path, "rb") as f:
            public = serialization.load_pem_public_key(f.read())
        keys[_key_id(public)] = public
    return keys


def public_jwks() -> dict:
    """The public keys tokens are signed with (JWKS) - what another service
    needs to verify a BRT 360 token without any shared secret."""
    settings = get_settings()
    keys = _verification_keys(settings.jwt_private_key_file, settings.jwt_previous_public_key_files)
    return {"keys": [{**jwt.algorithms.RSAAlgorithm.to_jwk(public, as_dict=True), "kid": kid, "use": "sig", "alg": "RS256"}
                     for kid, public in keys.items()]}


def create_access_token(user: User, purpose: str | None = None, minutes: int | None = None) -> str:
    """A session token - or, with `purpose`, a short-lived token that is good
    only for that one step (the code after the password, setting up two-step
    sign-in) and never as a session (get_current_user refuses it)."""
    settings = get_settings()
    now = utcnow()
    payload = {
        "sub": str(user.id),
        "tenant_id": user.tenant_id,
        "role": user.role.value,
        "iat": now,
        "exp": now + dt.timedelta(minutes=minutes or settings.jwt_expiry_minutes),
    }
    if purpose:
        payload["purpose"] = purpose
    if settings.jwt_private_key_file:  # RS256: anyone with the public key can verify, only this server can sign
        key, kid = _signing_key(settings.jwt_private_key_file)
        return jwt.encode(payload, key, algorithm="RS256", headers={"kid": kid})
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict | None:
    """The token's verified claims, or None when it is invalid or expired.
    Each algorithm is verified only with its own kind of key."""
    settings = get_settings()
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") == "RS256":
            key = _verification_keys(settings.jwt_private_key_file, settings.jwt_previous_public_key_files).get(header.get("kid"))
            return jwt.decode(token, key, algorithms=["RS256"]) if key is not None else None
        if settings.jwt_accept_hs256:  # shared-secret tokens: dev and tests, and sessions from before RS256
            return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return None
    except jwt.PyJWTError:
        return None


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    # main.py's middleware already verified this request's token - reuse it
    payload = getattr(request.state, "token_payload", None) or decode_token(credentials.credentials)
    if payload is None or payload.get("purpose"):  # a one-step token (sign-in code, setup) is not a session
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")

    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or deactivated")
    _idle_check(db, user, background=request.headers.get("x-brt-background") == "1")
    # the rest of this request sees only this user's tenant, in the database too (app/rls.py)
    rls.scope(db, user.tenant_id)
    return user


SEEN_EVERY = dt.timedelta(minutes=1)
# last_seen_at moves once a minute and the screens report activity once a minute, so it
# can trail the user by up to two: the browser signs out on time, the server just after.
IDLE_GRACE = dt.timedelta(minutes=2)


def _idle_check(db: Session, user: User, background: bool) -> None:
    """The tenant's idle timeout: no signed-in request for that long ends the
    session. last_seen_at moves at most once a minute, and not for the
    screens' own polling (X-BRT-Background: 1) - only for what the user does."""
    from app.models.tenant import Tenant

    now = utcnow()
    tenant = db.get(Tenant, user.tenant_id) if user.tenant_id is not None else None
    limit = tenant.idle_timeout_minutes if tenant is not None else None
    if limit and user.last_seen_at is not None and now - user.last_seen_at > dt.timedelta(minutes=limit) + IDLE_GRACE:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Signed out after {limit} minutes without activity")
    if not background and (user.last_seen_at is None or now - user.last_seen_at >= SEEN_EVERY):
        user.last_seen_at = now
        db.commit()


def user_for_step(token: str, purpose: str, db: Session) -> User:
    """The user a one-step token (create_access_token(purpose=...)) was made for."""
    payload = decode_token(token)
    if payload is None or payload.get("purpose") != purpose:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This sign-in step has expired - sign in again")
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or deactivated")
    rls.scope(db, user.tenant_id)
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
