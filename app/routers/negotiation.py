from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_auth
from app.models import NegotiationRequest, NegotiationResponse
from app.services.negotiation_engine import evaluate_negotiation

router = APIRouter(prefix="/negotiation", tags=["negotiation"], dependencies=[Depends(require_auth)])


@router.post("/evaluate", response_model=NegotiationResponse)
def evaluate(req: NegotiationRequest):
    try:
        return evaluate_negotiation(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
