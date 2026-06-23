"""Deterministic negotiation engine.

This is intentionally NOT an LLM call. The rate ceiling enforcement requirement
("must never disclose max_rate directly or indirectly") is a hard business rule,
not a matter of judgement -- so it must live in code the voice agent calls via a
Webhook action, not in a prompt the agent might be talked out of.

The agent's prompt tells it to call this service and say whatever offer_amount
comes back, framed as its own offer. It never sees max_rate.
"""
from __future__ import annotations

from app.models import NegotiationDecision, NegotiationRequest, NegotiationResponse

# Round-2 decimal places for all dollar amounts spoken on a call.
_CENTS = 2


def evaluate_negotiation(req: NegotiationRequest) -> NegotiationResponse:
    if req.carrier_ask <= 0:
        raise ValueError("carrier_ask must be positive")
    if req.max_rate < req.loadboard_rate:
        # Defensive: shouldn't happen with real data, but never let a bad
        # record cause us to offer ABOVE max_rate.
        raise ValueError("max_rate cannot be lower than loadboard_rate")

    ceiling = req.max_rate
    opening = req.loadboard_rate
    gap = max(ceiling - opening, 0.0)

    # Carrier's ask is at or below what the broker is willing to pay -> accept.
    if req.carrier_ask <= ceiling:
        return NegotiationResponse(
            decision=NegotiationDecision.ACCEPT,
            offer_amount=round(req.carrier_ask, _CENTS),
        )

    if req.round < 3:
        # Move roughly a third of the way toward the ceiling each round,
        # but never propose more than the ceiling and never less than the
        # previous opening offer.
        step = gap * (req.round / 3)
        counter = min(opening + step, ceiling)
        counter = max(counter, opening)
        return NegotiationResponse(
            decision=NegotiationDecision.COUNTER,
            offer_amount=round(counter, _CENTS),
        )

    # Round 3: best and final. The broker CAN pay up to max_rate -- offering
    # exactly the ceiling is normal business behavior. What's prohibited is
    # *labeling* it as a ceiling/maximum in speech (that's a prompt-level rule,
    # see docs/workflow_spec.md). The carrier still has to agree; if their ask
    # is still above this, we close without a deal.
    return NegotiationResponse(
        decision=NegotiationDecision.FINAL_OFFER,
        offer_amount=round(ceiling, _CENTS),
    )


def carrier_response_after_final_offer(carrier_ask: float, final_offer: float) -> NegotiationResponse:
    """Call this once after presenting the round-3 final offer and hearing the
    carrier's reply, to decide whether to book or close as a failed negotiation."""
    if carrier_ask <= final_offer:
        return NegotiationResponse(decision=NegotiationDecision.ACCEPT, offer_amount=round(final_offer, _CENTS))
    return NegotiationResponse(decision=NegotiationDecision.REJECT_CLOSE, offer_amount=None)
