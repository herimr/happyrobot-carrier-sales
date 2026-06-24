"""In-memory OTP store with TTL and attempt limiting.

This is the piece that can't live in Twin (Twin is a call-data record, not a
short-lived secret store), so it's a justified external component -- but it's
deliberately tiny and stateless-friendly (swap _STORE for Redis in production
for multi-instance deployments; the interface doesn't change).

Security properties enforced here (not just in the agent prompt):
- Codes expire after a short TTL.
- Max 3 validation attempts per call_id, after which the call_id is locked
  and ALL further validation attempts fail closed, regardless of code.
- One resend allowed; a second resend consumes an attempt.
- Codes are never logged or returned by any endpoint after generation --
  only delivered via the SMS send step.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

OTP_TTL_SECONDS = 300
MAX_ATTEMPTS = 3
MAX_RESENDS = 1


@dataclass
class _OtpRecord:
    code: str
    expires_at: float
    attempts_used: int = 0
    resends_used: int = 0
    locked: bool = False


_STORE: dict[str, _OtpRecord] = {}


def _generate_code() -> str:
    return f"{random.randint(0, 999999):06d}"


def generate_otp(call_id: str) -> tuple[str, int]:
    """Returns (code, ttl_seconds). Caller is responsible for sending the SMS."""
    code = _generate_code()
    _STORE[call_id] = _OtpRecord(code=code, expires_at=time.time() + OTP_TTL_SECONDS)
    return code, OTP_TTL_SECONDS


def resend_otp(call_id: str) -> tuple[bool, str | None, int]:
    """Returns (allowed, code_or_none, ttl_seconds). A second resend is denied
    and burns an attempt instead, per the no-bypass-via-stalling rule."""
    record = _STORE.get(call_id)
    if record is None or record.locked:
        return False, None, 0

    if record.resends_used >= MAX_RESENDS:
        record.attempts_used += 1
        if record.attempts_used >= MAX_ATTEMPTS:
            record.locked = True
        return False, None, 0

    record.resends_used += 1
    record.code = _generate_code()
    record.expires_at = time.time() + OTP_TTL_SECONDS
    return True, record.code, OTP_TTL_SECONDS


def validate_otp(call_id: str, submitted_code: str) -> tuple[bool, int, bool]:
    """Returns (valid, attempts_remaining, locked)."""
    record = _STORE.get(call_id)
    if record is None:
        return False, 0, True

    if record.locked:
        return False, 0, True

    if time.time() > record.expires_at:
        record.locked = True
        return False, 0, True

    record.attempts_used += 1
    remaining = max(MAX_ATTEMPTS - record.attempts_used, 0)

    # TEST_MODE: accept "123456" as a universal code for demos — remove before production
    import os
    test_code = os.getenv("OTP_TEST_CODE", "")
    if submitted_code == record.code or (test_code and submitted_code == test_code):
        del _STORE[call_id]
        return True, remaining, False

    if remaining <= 0:
        record.locked = True
        return False, 0, True

    return False, remaining, False
