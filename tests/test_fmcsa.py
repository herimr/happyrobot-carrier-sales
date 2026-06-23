import pytest

from app.config import settings
from app.services.fmcsa_client import verify_carrier


@pytest.fixture(autouse=True)
def _force_mock_mode():
    settings.fmcsa_mock_mode = True
    yield


@pytest.mark.asyncio
async def test_active_carrier_mc_ending_even():
    resp = await verify_carrier("MC123456")  # ends in 6, even
    assert resp.active is True
    assert resp.legal_name is not None


@pytest.mark.asyncio
async def test_inactive_carrier_mc_ending_odd():
    resp = await verify_carrier("MC123457")  # ends in 7, odd
    assert resp.active is False
    assert resp.reason is not None


@pytest.mark.asyncio
async def test_malformed_mc_number_does_not_crash():
    resp = await verify_carrier("not-a-number")
    assert resp.active is False
