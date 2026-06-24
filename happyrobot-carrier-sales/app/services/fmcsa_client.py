"""FMCSA carrier authority verification.

The FMCSA QCMobile API is already REST/JSON, so the HappyRobot workflow could
call it directly from a Webhook action node with no adapter needed. This thin
wrapper exists only so the rest of this repo (and its tests) can run without
a live FMCSA web key, and to normalize the response into the shape our
workflow branches on. In production, point the Webhook node straight at
FMCSA and skip this service if you prefer fewer moving parts -- it's
optional, not required, unlike the TMS adapter.
"""
from __future__ import annotations

import httpx

from app.config import settings
from app.models import FmcsaVerifyResponse

FMCSA_BASE_URL = "https://mobile.fmcsa.dot.gov/qc/services/carriers"


async def verify_carrier(mc_number: str) -> FmcsaVerifyResponse:
    if settings.fmcsa_mock_mode:
        return _mock_lookup(mc_number)

    url = f"{FMCSA_BASE_URL}/{mc_number}"
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(url, params={"webKey": settings.fmcsa_web_key})
        resp.raise_for_status()
        data = resp.json()

    content = (data.get("content") or [{}])[0].get("carrier", {})
    active = bool(content.get("allowedToOperate") == "Y" or content.get("statusCode") == "A")
    return FmcsaVerifyResponse(
        mc_number=mc_number,
        active=active,
        legal_name=content.get("legalName"),
        reason=None if active else "Authority not active per FMCSA",
    )


def _mock_lookup(mc_number: str) -> FmcsaVerifyResponse:
    """Deterministic mock for local dev/tests: MC numbers ending in an even
    digit are active, odd are not, so test suites can cover both branches
    without network access."""
    last_digit = mc_number.strip()[-1] if mc_number.strip() else "0"
    active = last_digit.isdigit() and int(last_digit) % 2 == 0
    return FmcsaVerifyResponse(
        mc_number=mc_number,
        active=active,
        legal_name="Mock Carrier LLC" if active else None,
        reason=None if active else "Authority not active per FMCSA (mock)",
    )
