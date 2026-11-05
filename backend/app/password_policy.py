"""The password rule for every password a person chooses (new users, a new organization's administrator, a
password change) - NIST SP 800-63B and OWASP ASVS 4.0.3 V2.1: at least 12 characters, at most 128, no
composition rules, and not a password on the common / breached list or one made of the account's own name.
"""

from __future__ import annotations

MIN_LENGTH = 12
MAX_LENGTH = 128  # bcrypt reads the first 72 bytes; a passphrase longer than this is never needed

# The most common passwords in public breach corpora (and their obvious 12+ character variants).
COMMON = {
    "password", "password1", "password12", "password123", "password1234", "password12345", "password123456",
    "passw0rd", "p@ssw0rd", "p@ssword", "123456", "1234567", "12345678", "123456789", "1234567890", "12345678910",
    "123456789012", "1234567890123", "qwerty", "qwerty123", "qwertyuiop", "qwertyuiop123", "qwerty123456",
    "1q2w3e4r", "1q2w3e4r5t6y", "1qaz2wsx3edc", "zaq12wsxcde3", "abc123", "abcdefghijkl", "abcd1234abcd",
    "iloveyou", "iloveyou1234", "admin", "admin123", "admin1234567", "administrator", "welcome", "welcome123",
    "welcome12345", "letmein", "letmein12345", "changeme", "change-me-now", "changemenow", "changeme1234",
    "monkey", "dragon", "football", "baseball", "sunshine", "princess", "master", "shadow", "superman",
    "trustno1", "starwars", "whatever", "freedom", "secret", "secret123456", "default", "default12345",
    "test", "test1234", "testtest", "testing123", "test12345678", "guest", "guest1234567", "root", "toor",
    "111111", "000000", "aaaaaa", "qazwsx", "asdfgh", "asdfghjkl", "zxcvbnm", "zxcvbnm12345", "michael",
    "password!", "password@123", "password#123", "passwordpassword", "summer2024", "winter2024", "spring2025",
    "autumn2025", "summer2025", "winter2025", "spring2026", "summer2026", "company123", "company12345",
}


def _plain(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def problem(password: str, email: str | None = None, name: str | None = None) -> str | None:
    """Why the password can't be used, or None."""
    if len(password) < MIN_LENGTH:
        return f"The password needs at least {MIN_LENGTH} characters - a short phrase is easiest to remember"
    if len(password) > MAX_LENGTH:
        return f"The password can be at most {MAX_LENGTH} characters"
    lowered = password.lower()
    stripped = _plain(password)
    if lowered in COMMON or stripped in COMMON or lowered.rstrip("!@#$%^&*.?") in COMMON:
        return "That password is on the list of common, easily guessed passwords - choose another"
    if len(set(password)) < 4:
        return "The password repeats too few characters - choose a phrase"
    run = "".join(ch for ch in stripped if ch.isdigit())
    if run == stripped and len(set(run)) < 6:
        return "The password is only a simple run of digits - choose a phrase"
    for own in ((email or "").split("@")[0], email or "", name or ""):
        own = _plain(own)
        if len(own) >= 4 and (own in stripped and len(stripped) - len(own) < 6):
            return "The password can't be your own name or email address"
    return None


def check(password: str, email: str | None = None, name: str | None = None) -> str:
    """For a pydantic validator: the password, or ValueError with the reason."""
    reason = problem(password, email, name)
    if reason:
        raise ValueError(reason)
    return password
