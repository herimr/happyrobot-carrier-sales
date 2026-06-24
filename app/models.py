"""Pydantic schemas shared across routers."""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# TMS / Load search
# ---------------------------------------------------------------------------
class EquipmentType(str, Enum):
    DRY_VAN = "dry_van"
    REEFER = "reefer"
    FLATBED = "flatbed"

class LoadSearchRequest(BaseModel):
    origin: str = Field(..., description="Carrier's preferred origin, e.g. 'Chicago, IL'")
    destination: Optional[str] = Field(None, description="Preferred destination, optional")
    equipment_type: EquipmentType

    @field_validator("equipment_type", mode="before")
    @classmethod
    def normalize_equipment_type(cls, v):
        """Accept any reasonable variation and normalize to enum value.
        Handles: 'Dry Van', 'DRY_VAN', 'dry van', 'reefer', 'FLATBED', etc.
        Also handles empty/None gracefully by defaulting to dry_van.
        """
        if not v:
            return EquipmentType.DRY_VAN
        s = str(v).lower().strip().replace(" ", "_").replace("-", "_")
        # Strip template artifacts like {{...}} that HappyRobot may send
        if s.startswith("{") or s.startswith("@"):
            return EquipmentType.DRY_VAN
        mapping = {
            "dry_van": EquipmentType.DRY_VAN,
            "dry": EquipmentType.DRY_VAN,
            "van": EquipmentType.DRY_VAN,
            "dryvan": EquipmentType.DRY_VAN,
            "reefer": EquipmentType.REEFER,
            "refrigerated": EquipmentType.REEFER,
            "temp_controlled": EquipmentType.REEFER,
            "temp": EquipmentType.REEFER,
            "flatbed": EquipmentType.FLATBED,
            "flat": EquipmentType.FLATBED,
            "flat_bed": EquipmentType.FLATBED,
        }
        return mapping.get(s, EquipmentType.DRY_VAN)

class Load(BaseModel):
    load_id: str
    origin: str
    destination: str
    pickup_datetime: datetime
    delivery_datetime: datetime
    equipment_type: EquipmentType
    loadboard_rate: float
    max_rate: float = Field(..., description="NEVER returned to the voice agent")
    weight: int
    commodity_type: str
    num_of_pieces: int
    miles: int
    dimensions: str
    notes: Optional[str] = None

class LoadSearchResponse(BaseModel):
    matches: list[Load]

class LoadPublic(BaseModel):
    """What the voice agent is allowed to see. No max_rate. Ever."""
    load_id: str
    origin: str
    destination: str
    pickup_datetime: datetime
    delivery_datetime: datetime
    equipment_type: EquipmentType
    loadboard_rate: float
    weight: int
    commodity_type: str
    num_of_pieces: int
    miles: int
    dimensions: str
    notes: Optional[str] = None

    @classmethod
    def from_load(cls, load: Load) -> "LoadPublic":
        data = load.model_dump()
        data.pop("max_rate")
        return cls(**data)

class BookLoadRequest(BaseModel):
    load_id: str
    mc_number: str
    agreed_rate: float
    call_id: str

class BookLoadResponse(BaseModel):
    confirmation_id: str
    load_id: str
    status: str

# ---------------------------------------------------------------------------
# OTP
# ---------------------------------------------------------------------------
class OtpGenerateRequest(BaseModel):
    call_id: str
    phone_number: str

class OtpGenerateResponse(BaseModel):
    sent: bool
    expires_in_seconds: int

class OtpValidateRequest(BaseModel):
    call_id: str
    code: str

class OtpValidateResponse(BaseModel):
    valid: bool
    attempts_remaining: int
    locked: bool = Field(False, description="True once attempts exhausted; call must end")

# ---------------------------------------------------------------------------
# Negotiation
# ---------------------------------------------------------------------------
class NegotiationDecision(str, Enum):
    ACCEPT = "accept"
    COUNTER = "counter"
    FINAL_OFFER = "final_offer"
    REJECT_CLOSE = "reject_close"

class NegotiationRequest(BaseModel):
    load_id: str
    loadboard_rate: float
    max_rate: Optional[float] = None
    carrier_ask: float = 0.0
    round: int = Field(1, ge=1, le=3)

    @field_validator("carrier_ask", mode="before")
    @classmethod
    def parse_carrier_ask(cls, v):
        if not v or str(v).strip() == "":
            return 0.0
        try:
            return float(str(v).replace(",", "").replace("$", ""))
        except (ValueError, TypeError):
            return 0.0

    @field_validator("round", mode="before")
    @classmethod
    def parse_round(cls, v):
        if not v or str(v).strip() == "":
            return 1
        try:
            return max(1, min(3, int(float(str(v)))))
        except (ValueError, TypeError):
            return 1

class NegotiationResponse(BaseModel):
    decision: NegotiationDecision
    offer_amount: Optional[float] = Field(
        None, description="Amount the agent says out loud. Never implies max_rate as a label."
    )

# ---------------------------------------------------------------------------
# FMCSA
# ---------------------------------------------------------------------------
class FmcsaVerifyResponse(BaseModel):
    mc_number: str
    active: bool
    legal_name: Optional[str] = None
    reason: Optional[str] = None
