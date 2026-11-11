"""RFC 6238 time-based one-time passwords (what authenticator apps compute), with no dependencies."""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time


def totp(secret: str, at: float | None = None, digits: int = 6, step: int = 30) -> str:
    key = secret.replace(" ", "").replace("-", "").upper()
    key += "=" * (-len(key) % 8)
    try:
        raw = base64.b32decode(key)
    except Exception:
        raise ValueError("the TOTP secret is not valid base32 (expected letters A-Z and digits 2-7)") from None
    counter = int((time.time() if at is None else at) // step)
    digest = hmac.new(raw, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(code).zfill(digits)


def seconds_left(at: float | None = None, step: int = 30) -> float:
    return step - ((time.time() if at is None else at) % step)
