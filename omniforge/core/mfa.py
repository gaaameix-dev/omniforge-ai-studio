"""TOTP second factor (RFC 6238) with single-use backup codes.

Implemented on the standard library: HMAC-SHA1, 30 second steps, 6 digits, one
step of clock drift tolerated. Secrets are base32, backup codes are stored hashed.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import time

STEP = 30
DIGITS = 6
DRIFT = 1
BACKUP_CODES = 8


def new_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def _code(secret: str, counter: int) -> str:
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** DIGITS)).zfill(DIGITS)


def code_at(secret: str, timestamp: float | None = None) -> str:
    counter = int((timestamp if timestamp is not None else time.time()) // STEP)
    return _code(secret, counter)


def verify(secret: str, code: str, timestamp: float | None = None, drift: int = DRIFT) -> bool:
    if not secret or not code:
        return False
    candidate = str(code).strip().replace(" ", "")
    if not candidate.isdigit() or len(candidate) != DIGITS:
        return False
    counter = int((timestamp if timestamp is not None else time.time()) // STEP)
    for offset in range(-drift, drift + 1):
        if hmac.compare_digest(_code(secret, counter + offset), candidate):
            return True
    return False


def provisioning_uri(secret: str, account: str, issuer: str = "OmniForge") -> str:
    label = f"{issuer}:{account}".replace(" ", "%20")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={issuer}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP}")


def new_backup_codes(count: int = BACKUP_CODES) -> list[str]:
    return ["-".join([os.urandom(2).hex(), os.urandom(2).hex()]) for _ in range(count)]


def hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()
