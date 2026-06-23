from fastapi import Header, HTTPException

from app.config import settings


async def require_auth(authorization: str = Header(default="")) -> None:
    """All endpoints require authentication, per the brief's security requirement.
    HappyRobot Webhook action nodes send this as a static header configured
    on the node."""
    expected = f"Bearer {settings.api_auth_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="missing or invalid authorization header")
