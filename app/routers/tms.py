from fastapi import APIRouter, Depends, HTTPException
from typing import Any, Optional
from app.auth import require_auth
from app.models import BookLoadRequest, BookLoadResponse, LoadPublic, LoadSearchRequest
from app.services import tms_client
from app.services.tms_client import TmsConnectionError, TmsProtocolError, TmsBusinessError
from pydantic import BaseModel

router = APIRouter(prefix="/tms", tags=["tms"], dependencies=[Depends(require_auth)])


class BookLoadRequestFlex(BaseModel):
    """Accepts load_id directly or nested inside a load object from HappyRobot's data.0"""
    load_id: Optional[str] = None
    load: Optional[Any] = None  # data.0 object if sent as nested
    mc_number: str
    agreed_rate: float
    call_id: str


class SearchAndSelectRequest(BaseModel):
    """Flat-response search endpoint -- works around HappyRobot's inability
    to address array sub-fields (data.0.load_id) in webhook variable pickers.
    Returns the first match's fields directly at the top level instead of
    inside a list, so every field is selectable downstream."""
    origin: str
    destination: Optional[str] = None
    equipment_type: str = "dry_van"


@router.post("/search", response_model=list[LoadPublic])
def search_loads(req: LoadSearchRequest):
    try:
        loads = tms_client.search_loads(req.origin, req.destination, req.equipment_type)
    except TmsConnectionError:
        raise HTTPException(status_code=503, detail="TMS temporarily unavailable")
    except TmsProtocolError:
        raise HTTPException(status_code=502, detail="TMS returned an unexpected response")
    return [LoadPublic.from_load(load) for load in loads]


@router.post("/search-and-select")
def search_and_select(req: SearchAndSelectRequest):
    """Search loads and return the best match with all fields flattened at
    the top level (no array/list wrapper), so HappyRobot's variable picker
    can address load_id, loadboard_rate, etc. directly."""
    from app.models import EquipmentType, LoadSearchRequest

    # Reuse the same tolerant normalization as LoadSearchRequest by
    # constructing it -- this also validates equipment_type the same way.
    try:
        validated = LoadSearchRequest(
            origin=req.origin,
            destination=req.destination,
            equipment_type=req.equipment_type,
        )
    except Exception:
        validated = LoadSearchRequest(
            origin=req.origin,
            destination=req.destination,
            equipment_type=EquipmentType.DRY_VAN,
        )

    try:
        loads = tms_client.search_loads(
            validated.origin, validated.destination, validated.equipment_type
        )
    except TmsConnectionError:
        raise HTTPException(status_code=503, detail="TMS temporarily unavailable")
    except TmsProtocolError:
        raise HTTPException(status_code=502, detail="TMS returned an unexpected response")

    if not loads:
        return {
            "found": False,
            "load_id": None,
            "origin": None,
            "destination": None,
            "loadboard_rate": None,
            "equipment_type": None,
            "pickup_datetime": None,
        }

    best = loads[0]
    return {
        "found": True,
        "load_id": best.load_id,
        "origin": best.origin,
        "destination": best.destination,
        "loadboard_rate": best.loadboard_rate,
        "equipment_type": best.equipment_type,
        "pickup_datetime": str(best.pickup_datetime),
    }


@router.get("/load/{load_id}", response_model=LoadPublic)
def get_load(load_id: str):
    try:
        load = tms_client.get_load_detail(load_id)
    except TmsConnectionError:
        raise HTTPException(status_code=503, detail="TMS temporarily unavailable")
    except TmsProtocolError:
        raise HTTPException(status_code=502, detail="TMS returned an unexpected response")
    if load is None:
        raise HTTPException(status_code=404, detail="load not found")
    return LoadPublic.from_load(load)


@router.post("/book", response_model=BookLoadResponse)
def book_load(req: BookLoadRequestFlex):
    # Extract load_id from nested object if not provided directly
    load_id = req.load_id
    if not load_id and req.load:
        if isinstance(req.load, dict):
            load_id = req.load.get("load_id")
    if not load_id:
        raise HTTPException(status_code=400, detail="load_id is required")
    try:
        return tms_client.book_load(load_id, req.mc_number, req.agreed_rate)
    except TmsBusinessError as exc:
        raise HTTPException(status_code=422, detail=f"{exc.code}: {exc.msg}")
    except TmsConnectionError:
        raise HTTPException(status_code=503, detail="TMS temporarily unavailable")
    except TmsProtocolError:
        raise HTTPException(status_code=502, detail="TMS returned an unexpected response")
