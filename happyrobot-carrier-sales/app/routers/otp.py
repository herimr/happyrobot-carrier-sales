from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_auth
from app.models import OtpGenerateRequest, OtpGenerateResponse, OtpValidateRequest, OtpValidateResponse
from app.services import otp_store

router = APIRouter(prefix="/otp", tags=["otp"], dependencies=[Depends(require_auth)])


@router.post("/generate", response_model=OtpGenerateResponse)
def generate(req: OtpGenerateRequest):
    code, ttl = otp_store.generate_otp(req.call_id)
    # In production this is where you'd call HappyRobot's native SMS sending
    # action (or a Twilio-backed notification step) with `code`. This service
    # never returns the code in the API response -- only the workflow's SMS
    # step sees it, and it never enters the voice agent's transcript context.
    _deliver_sms_stub(req.phone_number, code)
    return OtpGenerateResponse(sent=True, expires_in_seconds=ttl)


@router.post("/resend", response_model=OtpGenerateResponse)
def resend(req: OtpGenerateRequest):
    allowed, code, ttl = otp_store.resend_otp(req.call_id)
    if not allowed or code is None:
        raise HTTPException(status_code=429, detail="resend limit reached")
    _deliver_sms_stub(req.phone_number, code)
    return OtpGenerateResponse(sent=True, expires_in_seconds=ttl)


@router.post("/validate", response_model=OtpValidateResponse)
def validate(req: OtpValidateRequest):
    valid, remaining, locked = otp_store.validate_otp(req.call_id, req.code)
    return OtpValidateResponse(valid=valid, attempts_remaining=remaining, locked=locked)


def _deliver_sms_stub(phone_number: str, code: str) -> None:
    # Placeholder for local/demo runs. Swap for the HappyRobot SMS action
    # webhook callback or a Twilio call in production.
    print(f"[otp-stub] would SMS {phone_number}: your HappyRobot Logistics code is {code}")
