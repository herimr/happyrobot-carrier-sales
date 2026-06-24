import socket
import threading
import time

import pytest

from app.config import settings
from app.mocks.mock_tms_server import run as run_mock_tms
from app.models import EquipmentType
from app.services import tms_client
from app.services.tms_client import TmsConnectionError, TmsProtocolError

TEST_PORT = 19999


@pytest.fixture(scope="module", autouse=True)
def _mock_tms_server():
    settings.tms_host = "127.0.0.1"
    settings.tms_port = TEST_PORT
    thread = threading.Thread(target=run_mock_tms, kwargs={"host": "127.0.0.1", "port": TEST_PORT}, daemon=True)
    thread.start()
    time.sleep(0.3)
    yield


@pytest.fixture(autouse=True)
def _reset_circuit_breaker():
    tms_client._breaker.consecutive_failures = 0
    tms_client._breaker.opened_at = None
    yield


# --- Standard ---------------------------------------------------------------

def test_search_returns_matching_loads():
    loads = tms_client.search_loads("Chicago, IL", None, EquipmentType.DRY_VAN)
    assert len(loads) >= 1
    assert all("Chicago" in l.origin for l in loads)


def test_search_strips_nothing_max_rate_present_for_internal_use():
    loads = tms_client.search_loads("Chicago, IL", None, EquipmentType.DRY_VAN)
    # max_rate must exist at the TMS-client layer (it's needed for negotiation)
    # -- it only gets stripped at the router/LoadPublic boundary, tested
    # separately. This asserts the internal contract isn't accidentally lost.
    assert loads[0].max_rate > loads[0].loadboard_rate


def test_get_load_detail_known_id():
    load = tms_client.get_load_detail("LOAD000001")
    assert load is not None
    assert load.load_id == "LOAD000001"


def test_book_load_returns_confirmation():
    resp = tms_client.book_load("LOAD000001", "MC123456", 1950.00)
    assert resp.status == "booked"
    assert resp.confirmation_id


# --- Edge cases --------------------------------------------------------------

def test_search_no_matches_returns_empty_list():
    loads = tms_client.search_loads("Anchorage, AK", None, EquipmentType.FLATBED)
    assert loads == []


def test_get_load_detail_unknown_id_returns_none():
    load = tms_client.get_load_detail("LOADNOPE99")
    assert load is None


# --- Resilience / adversarial: malformed + unreachable TMS ------------------

def test_malformed_response_raises_protocol_error_not_silent_bad_data():
    with pytest.raises(TmsProtocolError):
        tms_client.get_load_detail("LOAD999999")  # mock server returns a truncated record on purpose


def test_unreachable_tms_raises_connection_error_after_retries_not_hang_forever():
    original_port = settings.tms_port
    settings.tms_port = 1  # nothing listens here -- forces connection refusal
    try:
        start = time.time()
        with pytest.raises(TmsConnectionError):
            tms_client.search_loads("Chicago, IL", None, EquipmentType.DRY_VAN)
        elapsed = time.time() - start
        # Should fail fast via connection refusal + bounded retries, not via
        # the full 5s socket timeout each attempt.
        assert elapsed < 10
    finally:
        settings.tms_port = original_port


def test_circuit_breaker_opens_after_repeated_failures_and_fails_fast():
    original_port = settings.tms_port
    settings.tms_port = 1
    try:
        for _ in range(tms_client.CIRCUIT_FAILURE_THRESHOLD):
            with pytest.raises(TmsConnectionError):
                tms_client.search_loads("Chicago, IL", None, EquipmentType.DRY_VAN)

        assert tms_client._breaker.is_open() is True

        start = time.time()
        with pytest.raises(TmsConnectionError):
            tms_client.search_loads("Chicago, IL", None, EquipmentType.DRY_VAN)
        elapsed = time.time() - start
        # Once open, calls should fail near-instantly without retrying the socket.
        assert elapsed < 1.0
    finally:
        settings.tms_port = original_port
