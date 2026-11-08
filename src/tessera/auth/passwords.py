"""Password hashing with the standard library's scrypt (no new dependency).

Stored as ``scrypt$N$r$p$salt$hash`` (base64), so the cost can rise later without breaking
existing hashes: verification always uses the parameters stored with the hash.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from tessera.core.errors import TesseraError

SCRYPT_N = 2**15  # about 32 MiB and a few tens of milliseconds per check
SCRYPT_R = 8
SCRYPT_P = 1
KEY_LENGTH = 32
MIN_LENGTH = 12
MAX_LENGTH = 1024


class PasswordError(TesseraError):
    """A password that does not meet the rules."""


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=max(64 * 1024 * 1024, 129 * n * r * p),
        dklen=KEY_LENGTH,
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = _derive(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, key = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(key)
        actual = _derive(password, base64.b64decode(salt), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


_DUMMY: list[str] = []


def burn_time(password: str) -> None:
    """Spend the same effort as a real check, so unknown usernames take as long as known ones."""
    if not _DUMMY:
        _DUMMY.append(hash_password(secrets.token_urlsafe(16)))
    verify_password(password, _DUMMY[0])


def check_rules(password: str, *, username: str = "") -> None:
    """Length rules in the spirit of NIST SP 800-63B: long, any characters, no composition rules."""
    if len(password) < MIN_LENGTH:
        raise PasswordError(f"Use at least {MIN_LENGTH} characters.")
    if len(password) > MAX_LENGTH:
        raise PasswordError(f"Use at most {MAX_LENGTH} characters.")
    if len(set(password)) < 4:
        raise PasswordError("Use more varied characters.")
    if username and len(username) >= 3 and username.lower() in password.lower():
        raise PasswordError("Do not include the username in the password.")


__all__ = ["PasswordError", "burn_time", "check_rules", "hash_password", "verify_password"]
