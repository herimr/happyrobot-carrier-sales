from fastapi import FastAPI

from app.routers import fmcsa, negotiation, otp, tms

app = FastAPI(
    title="HappyRobot Logistics -- Carrier Sales Integration API",
    description=(
        "Bridges the HappyRobot inbound carrier sales workflow to the legacy "
        "TMS (TCP), OTP delivery/validation, and the rate negotiation engine. "
        "Called from HappyRobot Webhook action nodes."
    ),
    version="1.0.0",
)

app.include_router(tms.router)
app.include_router(otp.router)
app.include_router(negotiation.router)
app.include_router(fmcsa.router)


@app.get("/health")
def health():
    return {"status": "ok"}
