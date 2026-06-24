from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_auth
from app.models import (
    BookLoadRequest,
    BookLoadResponse,
    LoadPublic,
    LoadSearchRequest,
)
from app.services import tms_client
from app.services.tms_client import TmsConnectionError, TmsProtocolError

router = APIRouter(prefix="/tms", tags=["tms"], dependencies=[Depends(require_auth)])


@router.post("/search", response_model=list[LoadPublic])
def search_loads(req: LoadSearchRequest):
    try:
        loads = tms_client.search_loads(req.origin, req.destination, req.equipment_type)
    except TmsConnectionError:
        raise HTTPException(status_code=503, detail="TMS temporarily unavailable, please try again shortly")
    except TmsProtocolError:
        raise HTTPException(status_code=502, detail="TMS returned an unexpected response")

    # Critical: max_rate is stripped here, server-side, before this ever
    # reaches the voice agent's context. The agent literally cannot see it.
    return [LoadPublic.from_load(load) for load in loads]


@router.get("/load/{load_id}", response_model=LoadPublic)
def get_load(load_id: str):
    try:
        load = tms_client.get_load_detail(load_id)
    except TmsConnectionError:
        raise HTTPException(status_code=503, detail="TMS temporarily unavailable, please try again shortly")
    except TmsProtocolError:
        raise HTTPException(status_code=502, detail="TMS returned an unexpected response")

    if load is None:
        raise HTTPException(status_code=404, detail="load not found")
    return LoadPublic.from_load(load)


@router.post("/book", response_model=BookLoadResponse)
def book_load(req: BookLoadRequest):
    try:
        return tms_client.book_load(req.load_id, req.mc_number, req.agreed_rate)
    except TmsConnectionError:
        raise HTTPException(status_code=503, detail="TMS temporarily unavailable, please try again shortly")
    except TmsProtocolError:
        raise HTTPException(status_code=502, detail="TMS returned an unexpected response")
