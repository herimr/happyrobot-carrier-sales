from fastapi import APIRouter, Depends

from app.auth import require_auth
from app.models import FmcsaVerifyResponse
from app.services.fmcsa_client import verify_carrier

router = APIRouter(prefix="/fmcsa", tags=["fmcsa"], dependencies=[Depends(require_auth)])


@router.get("/verify/{mc_number}", response_model=FmcsaVerifyResponse)
async def verify(mc_number: str):
    return await verify_carrier(mc_number)
