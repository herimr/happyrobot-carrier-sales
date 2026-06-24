import pytest

from app.models import NegotiationDecision, NegotiationRequest
from app.services.negotiation_engine import carrier_response_after_final_offer, evaluate_negotiation


def _req(round_, carrier_ask, loadboard_rate=1850.0, max_rate=2100.0):
    return NegotiationRequest(
        load_id="LOAD000001",
        loadboard_rate=loadboard_rate,
        max_rate=max_rate,
        carrier_ask=carrier_ask,
        round=round_,
    )


# --- Standard scenarios ---------------------------------------------------

def test_accepts_immediately_when_ask_is_below_loadboard_rate():
    resp = evaluate_negotiation(_req(round_=1, carrier_ask=1800.0))
    assert resp.decision == NegotiationDecision.ACCEPT
    assert resp.offer_amount == 1800.0


def test_accepts_when_ask_equals_max_rate_exactly():
    resp = evaluate_negotiation(_req(round_=1, carrier_ask=2100.0))
    assert resp.decision == NegotiationDecision.ACCEPT


def test_counters_when_ask_above_ceiling_round_1():
    resp = evaluate_negotiation(_req(round_=1, carrier_ask=2400.0))
    assert resp.decision == NegotiationDecision.COUNTER
    assert 1850.0 <= resp.offer_amount < 2100.0


def test_counter_moves_higher_each_round_when_still_rejected():
    r1 = evaluate_negotiation(_req(round_=1, carrier_ask=2400.0))
    r2 = evaluate_negotiation(_req(round_=2, carrier_ask=2400.0))
    assert r2.offer_amount > r1.offer_amount


def test_round_3_is_final_offer_at_ceiling():
    resp = evaluate_negotiation(_req(round_=3, carrier_ask=2400.0))
    assert resp.decision == NegotiationDecision.FINAL_OFFER
    assert resp.offer_amount == 2100.0


def test_carrier_accepts_final_offer():
    resp = carrier_response_after_final_offer(carrier_ask=2100.0, final_offer=2100.0)
    assert resp.decision == NegotiationDecision.ACCEPT


def test_carrier_rejects_final_offer_closes_without_transfer():
    resp = carrier_response_after_final_offer(carrier_ask=2500.0, final_offer=2100.0)
    assert resp.decision == NegotiationDecision.REJECT_CLOSE
    assert resp.offer_amount is None


# --- Edge cases ------------------------------------------------------------

def test_ask_exactly_at_loadboard_rate_accepts():
    resp = evaluate_negotiation(_req(round_=1, carrier_ask=1850.0))
    assert resp.decision == NegotiationDecision.ACCEPT


def test_zero_gap_between_loadboard_and_max_rate_never_exceeds_ceiling():
    resp = evaluate_negotiation(_req(round_=1, carrier_ask=5000.0, loadboard_rate=2000.0, max_rate=2000.0))
    assert resp.decision == NegotiationDecision.COUNTER
    assert resp.offer_amount == 2000.0


def test_negative_or_zero_ask_is_rejected_as_invalid_input():
    with pytest.raises(ValueError):
        evaluate_negotiation(_req(round_=1, carrier_ask=0))
    with pytest.raises(ValueError):
        evaluate_negotiation(_req(round_=1, carrier_ask=-100))


def test_max_rate_below_loadboard_rate_is_a_data_integrity_error():
    with pytest.raises(ValueError):
        evaluate_negotiation(_req(round_=1, carrier_ask=2000.0, loadboard_rate=2000.0, max_rate=1500.0))


# --- Adversarial: the counter must NEVER exceed max_rate, at any round ----

@pytest.mark.parametrize("round_", [1, 2, 3])
@pytest.mark.parametrize("absurd_ask", [10_000.0, 1_000_000.0, 99_999_999.0])
def test_counter_offer_never_exceeds_max_rate_however_high_the_ask(round_, absurd_ask):
    resp = evaluate_negotiation(_req(round_=round_, carrier_ask=absurd_ask))
    if resp.offer_amount is not None:
        assert resp.offer_amount <= 2100.0, "offer must never exceed max_rate, regardless of carrier pressure"


def test_repeated_identical_asks_do_not_creep_the_offer_past_ceiling():
    # Simulates a carrier hammering the same high number every round, trying
    # to see if persistence alone moves the offer above max_rate.
    last_offer = 0.0
    for round_ in (1, 2, 3):
        resp = evaluate_negotiation(_req(round_=round_, carrier_ask=999_999.0))
        if resp.offer_amount is not None:
            assert resp.offer_amount <= 2100.0
            assert resp.offer_amount >= last_offer
            last_offer = resp.offer_amount
