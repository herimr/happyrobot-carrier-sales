import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.mocks.mock_tms_server import run as run_mock_tms

TEST_PORT = 19998
AUTH_HEADER = {"Authorization": f"Bearer {settings.api_auth_token}"}


@pytest.fixture(scope="module", autouse=True)
def _mock_tms_server():
    settings.tms_host = "127.0.0.1"
    settings.tms_port = TEST_PORT
    thread = threading.Thread(target=run_mock_tms, kwargs={"host": "127.0.0.1", "port": TEST_PORT}, daemon=True)
    thread.start()
    time.sleep(0.3)
    yield


client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200


def test_unauthenticated_request_is_rejected():
    resp = client.post("/tms/search", json={"origin": "Chicago, IL", "equipment_type": "dry_van"})
    assert resp.status_code == 401


def test_search_response_never_contains_max_rate_key_anywhere_in_payload():
    """Adversarial-by-design: this is the single most important regression
    test in the repo. If max_rate ever leaks into the HTTP response the
    voice agent reads, the core requirement of the whole build is broken."""
    resp = client.post(
        "/tms/search",
        json={"origin": "Chicago, IL", "equipment_type": "dry_van"},
        headers=AUTH_HEADER,
    )
    assert resp.status_code == 200
    body_text = resp.text
    assert "max_rate" not in body_text


def test_load_detail_never_contains_max_rate():
    resp = client.get("/tms/load/LOAD000001", headers=AUTH_HEADER)
    assert resp.status_code == 200
    assert "max_rate" not in resp.text


def test_negotiation_endpoint_requires_auth():
    resp = client.post(
        "/negotiation/evaluate",
        json={"load_id": "LOAD000001", "loadboard_rate": 1850, "max_rate": 2100, "carrier_ask": 2400, "round": 1},
    )
    assert resp.status_code == 401


def test_negotiation_endpoint_authenticated_flow():
    resp = client.post(
        "/negotiation/evaluate",
        json={"load_id": "LOAD000001", "loadboard_rate": 1850, "max_rate": 2100, "carrier_ask": 2400, "round": 1},
        headers=AUTH_HEADER,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["offer_amount"] <= 2100
