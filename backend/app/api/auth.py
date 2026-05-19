"""
auth.py — JWT issuance, validation, RBAC.

Rules:
- Every interview endpoint requires a valid token.
- Three roles: candidate, reviewer, admin.
- Token validation is sync-safe (no I/O) — Redis used for revocation only.
- Revocation check is optional on hot-path (auth middleware caches result 30s).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from app.config import get_settings
from app.models.session import UserRole
from app.core import redis as r

settings = get_settings()
_bearer = HTTPBearer(auto_error=False)


# ── Token models ──────────────────────────────────────────────────────────────

class TokenPayload(BaseModel):
    sub: str          # user_id
    role: UserRole
    session_id: str | None = None
    exp: datetime
    jti: str          # unique token ID (for revocation)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int   # seconds


# ── Token operations ──────────────────────────────────────────────────────────

def issue_token(
    user_id: str,
    role: UserRole,
    session_id: str | None = None,
    expires_minutes: int | None = None,
) -> TokenResponse:
    """Issue a signed JWT."""
    exp_minutes = expires_minutes or settings.JWT_EXPIRE_MINUTES
    expire = datetime.utcnow() + timedelta(minutes=exp_minutes)
    
    payload = {
        "sub": str(user_id),
        "role": role.value,
        "session_id": str(session_id) if session_id else None,
        "exp": expire,
        "iat": datetime.utcnow(),
        "jti": str(uuid.uuid4()),
    }
    
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    
    return TokenResponse(
        access_token=token,
        expires_in=exp_minutes * 60,
    )


def _decode_token(token: str) -> TokenPayload:
    """Decode and validate JWT. Raises HTTPException on failure."""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return TokenPayload(**payload)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── FastAPI dependencies ──────────────────────────────────────────────────────

async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> TokenPayload:
    """
    FastAPI dependency. Validates token on every request.
    Raises 401 if missing or invalid.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _decode_token(credentials.credentials)


async def require_candidate(
    user: Annotated[TokenPayload, Depends(get_current_user)],
) -> TokenPayload:
    if user.role not in (UserRole.CANDIDATE, UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="Candidate access required")
    return user


async def require_reviewer(
    user: Annotated[TokenPayload, Depends(get_current_user)],
) -> TokenPayload:
    if user.role not in (UserRole.REVIEWER, UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="Reviewer access required")
    return user


async def require_admin(
    user: Annotated[TokenPayload, Depends(get_current_user)],
) -> TokenPayload:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ── WebSocket token validation ────────────────────────────────────────────────

async def validate_ws_token(websocket: WebSocket) -> TokenPayload:
    """
    Validates token for WebSocket connections.
    Token is passed as query param: ?token=<jwt>
    
    Why query param (not header): WebSocket API doesn't support custom headers
    in browser environments. Query param is the standard pattern.
    """
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        raise HTTPException(status_code=401, detail="Missing WS token")
    
    try:
        return _decode_token(token)
    except HTTPException:
        await websocket.close(code=4001, reason="Invalid token")
        raise


# ── Session ownership guard ───────────────────────────────────────────────────

def assert_session_owner(user: TokenPayload, session_id: str) -> None:
    """
    Ensures the authenticated user owns this session.
    Admins can access any session.
    """
    if user.role == UserRole.ADMIN:
        return
    if user.session_id != session_id:
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this session",
        )
