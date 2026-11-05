"""Secrets kept at rest (OWASP ASVS V6.1.1 / V6.4.1): a column of type Encrypted stores Fernet ciphertext
(AES-128-CBC + HMAC-SHA256) under a key derived from the platform secret, and reads back the plain value -
the code using the column doesn't change. A value written before encryption (plain text) still reads back as
it is and is encrypted the next time it is saved (and by the migration that introduced the column type).
"""

from __future__ import annotations

import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.config import get_settings

PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"brt360-secret-at-rest").derive(get_settings().brt_id_secret.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def seal(value: str) -> str:
    return PREFIX + _fernet().encrypt(value.encode()).decode()


def is_sealed(value: str | None) -> bool:
    return bool(value) and str(value).startswith(PREFIX)


def open_(value: str) -> str:
    """The plain value - a value that isn't sealed (written before encryption) as it is."""
    if not is_sealed(value):
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken:
        return ""  # sealed under another platform secret: unusable, never shown


class Encrypted(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or value == "" or is_sealed(value):
            return value
        return seal(str(value))

    def process_result_value(self, value, dialect):
        return None if value is None else open_(value)
