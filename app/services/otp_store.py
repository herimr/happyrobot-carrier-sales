"""In-memory OTP store with TTL and attempt limiting.
...
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from app.config import settings

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
    code = _generate_code()
    _STORE[call_id] = _OtpRecord(code=code, expires_at=time.time() + OTP_TTL_SECONDS)
    return code, OTP_TTL_SECONDS


def resend_otp(call_id: str) -> tuple[bool, str | None, int]:
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

    # Demo/test bypass -- only active when OTP_TEST_CODE is set in the
    # environment. Leave unset in production.
    test_code = getattr(settings, "otp_test_code", None)
    if test_code and submitted_code == test_code:
        return True, MAX_ATTEMPTS, False

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
    if submitted_code == record.code:
        del _STORE[call_id]
        return True, remaining, False
    if remaining <= 0:
        record.locked = True
        return False, 0, True
    return False, remaining, False
