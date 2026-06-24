from fastapi import APIRouter, Depends, HTTPException
from app.auth import require_auth
from app.models import NegotiationRequest, NegotiationResponse
from app.services.negotiation_engine import evaluate_negotiation
from app.services import tms_client

router = APIRouter(prefix="/negotiation", tags=["negotiation"], dependencies=[Depends(require_auth)])

@router.post("/evaluate", response_model=NegotiationResponse)
def evaluate(req: NegotiationRequest):
    try:
        # If max_rate not provided, fetch it from TMS (never exposed to agent)
        if req.max_rate is None:
            load = tms_client.get_load_detail(req.load_id)
            if load is None:
                raise HTTPException(status_code=404, detail="load not found")
            req = req.model_copy(update={"max_rate": load.max_rate})
        return evaluate_negotiation(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
