"""Sign-in security: lockout and two-step sign-in with an authenticator app.

Lockout. LOGIN_MAX_FAILURES wrong passwords or codes in a row lock the
account for LOGIN_LOCK_MINUTES; a correct sign-in resets the count, and a
tenant admin can unlock it sooner. An unknown email answers exactly like a
wrong password, so the sign-in page doesn't tell who has an account.

Two-step sign-in (TOTP, RFC 6238: 6 digits, 30 seconds, SHA-1 - what
Google Authenticator, Microsoft Authenticator, 1Password and the like
use). A user turns it on in Account security: scan the QR code, type one
code to confirm, keep the ten recovery codes. From then on sign-in asks for
a code after the password. Each code works once; a recovery code also
works once, in place of a code. The secret is stored encrypted with a key
derived from BRT_ID_SECRET. A tenant can require it of everyone
(tenants.mfa_required): a user without it sets it up at their next sign-in.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import get_settings
from app.models.auth import User
from app.timeutils import utcnow

ISSUER = "BRT 360"
STEP = 30
DIGITS = 6
RECOVERY_CODES = 10


# ---------------------------------------------------------------- lockout


def locked_for(user: User) -> int:
    """Minutes the account stays locked (0 = not locked)."""
    if user.locked_until is None or user.locked_until <= utcnow():
        return 0
    return max(1, -(-int((user.locked_until - utcnow()).total_seconds()) // 60))


def failed(user: User) -> None:
    s = get_settings()
    user.failed_logins = (user.failed_logins or 0) + 1
    if user.failed_logins >= s.login_max_failures:
        user.locked_until = utcnow() + dt.timedelta(minutes=s.login_lock_minutes)
        user.failed_logins = 0


def succeeded(user: User) -> None:
    user.failed_logins = 0
    user.locked_until = None


def unlock(user: User) -> None:
    succeeded(user)


# ---------------------------------------------------------------- TOTP


def _fernet() -> Fernet:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"brt360-mfa-secret").derive(
        get_settings().brt_id_secret.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def encrypt(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None


def code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** DIGITS).zfill(DIGITS)


def _matching_step(secret: str, code: str, now: float | None = None) -> int | None:
    """The time step `code` belongs to - this one or a neighbour, for clocks
    a little apart - or None."""
    current = int((now if now is not None else time.time()) // STEP)
    for step in (current, current - 1, current + 1):
        if hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def otpauth_uri(user: User, secret: str) -> str:
    label = quote(f"{ISSUER}:{user.email}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}&digits={DIGITS}&period={STEP}"


def qr_data_url(uri: str) -> str:
    import io

    import qrcode

    buffer = io.BytesIO()
    qrcode.make(uri, box_size=6, border=2).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _normalize(code: str) -> str:
    return "".join(ch for ch in (code or "") if ch.isalnum()).lower()


def _hash_recovery(code: str) -> str:
    return hashlib.sha256(_normalize(code).encode()).hexdigest()


def new_recovery_codes(user: User) -> list[str]:
    codes = [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(RECOVERY_CODES)]
    user.mfa_recovery_hashes = json.dumps([_hash_recovery(c) for c in codes])
    return codes


def recovery_codes_left(user: User) -> int:
    return len(json.loads(user.mfa_recovery_hashes or "[]"))


def check_code(user: User, code: str, *, allow_recovery: bool = True) -> bool:
    """A current authenticator code (used once), or an unused recovery code."""
    secret = decrypt(user.mfa_secret_enc) if user.mfa_secret_enc else None
    digits = "".join(ch for ch in (code or "") if ch.isdigit())
    if secret and len(digits) == DIGITS and len(_normalize(code)) == DIGITS:
        step = _matching_step(secret, digits)
        if step is not None and (user.mfa_last_step is None or step > user.mfa_last_step):
            user.mfa_last_step = step
            return True
        return False
    if allow_recovery and user.mfa_enabled:
        hashes_left = json.loads(user.mfa_recovery_hashes or "[]")
        given = _hash_recovery(code)
        if given in hashes_left:
            hashes_left.remove(given)
            user.mfa_recovery_hashes = json.dumps(hashes_left)
            return True
    return False


def start_setup(user: User) -> str:
    """A new secret for the user to scan; two-step sign-in starts only once
    they confirm a code from it (enable)."""
    if user.mfa_enabled:
        raise ValueError("Two-step sign-in is already on - turn it off first to use a new device")
    secret = new_secret()
    user.mfa_secret_enc = encrypt(secret)
    user.mfa_last_step = None
    return secret


def enable(user: User, code: str) -> list[str]:
    if user.mfa_enabled:
        raise ValueError("Two-step sign-in is already on")
    if not user.mfa_secret_enc:
        raise ValueError("Scan the QR code first")
    if not check_code(user, code, allow_recovery=False):
        raise ValueError("That code doesn't match - check the time on your phone and try the newest code")
    user.mfa_enabled = True
    return new_recovery_codes(user)


def disable(user: User) -> None:
    user.mfa_enabled = False
    user.mfa_secret_enc = None
    user.mfa_last_step = None
    user.mfa_recovery_hashes = None
