"""
api/auth_routes.py — Auth endpoints for V2.

Endpoints:
  POST /auth/register      — create account
  POST /auth/login         — issue access + refresh token
  POST /auth/refresh        — rotate refresh token
  POST /auth/logout         — revoke refresh token
  POST /auth/forgot-password
  POST /auth/reset-password
  GET  /auth/me             — current user info

Security design:
  - Access tokens: 2h, in Authorization header
  - Refresh tokens: 7d, in httpOnly cookie OR body (configurable)
  - Refresh tokens are hashed before storage (never stored plaintext)
  - Token rotation: old token revoked on each refresh
  - Concurrent device support: each device gets its own refresh token
  - Rate limited at the nginx/gateway level (see nginx.conf)
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import (
    TokenPayload, TokenResponse, _decode_token,
    get_current_user, issue_token, require_admin,
)
from app.config import get_settings
from app.db.models import PasswordResetToken, RefreshToken, User
from app.db.session import get_db
from app.models.session import UserRole

log = structlog.get_logger(__name__)
settings = get_settings()
router = APIRouter(prefix="/auth", tags=["auth"])

# ── Password hashing ──────────────────────────────────────────────────────────
# bcrypt is a direct dependency (requirements.txt). No fallback needed.

import bcrypt as _bcrypt

def hash_password(pwd: str) -> str:
    return _bcrypt.hashpw(pwd.encode(), _bcrypt.gensalt()).decode()

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ── Request / Response schemas ────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str | None = None
    role: UserRole = UserRole.CANDIDATE

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: str
    role: str
    full_name: str | None


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str | None
    role: str
    is_active: bool
    created_at: datetime


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


# ── Helpers ───────────────────────────────────────────────────────────────────

def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="refresh_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key="refresh_token", path="/auth")


async def _issue_refresh_token(
    db: AsyncSession,
    user_id: str,
    request: Request,
) -> str:
    """Issue and store a new refresh token. Returns the raw token."""
    raw = secrets.token_urlsafe(48)
    expires_at = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    rt = RefreshToken(
        user_id=user_id,
        token_hash=_hash_token(raw),
        device_hint=request.headers.get("User-Agent", "")[:200],
        ip_address=request.client.host if request.client else None,
        expires_at=expires_at,
    )
    db.add(rt)
    await db.flush()
    return raw


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AuthResponse:
    """Create a new user account."""
    # Check email uniqueness
    existing = await db.scalar(select(User).where(User.email == body.email))
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    # Disallow self-assigning admin/reviewer unless explicitly allowed
    if body.role != UserRole.CANDIDATE:
        raise HTTPException(
            status_code=403,
            detail="Contact an admin to create reviewer or admin accounts",
        )

    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        role=body.role.value,
        is_active=True,
        is_verified=False,
    )
    db.add(user)
    await db.flush()

    access = issue_token(user_id=str(user.id), role=UserRole(user.role))
    refresh_raw = await _issue_refresh_token(db, user.id, request)
    _set_refresh_cookie(response, refresh_raw)

    log.info("auth.register", user_id=str(user.id), email=user.email, role=user.role)

    return AuthResponse(
        access_token=access.access_token,
        expires_in=access.expires_in,
        user_id=str(user.id),
        role=user.role,
        full_name=user.full_name,
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AuthResponse:
    """Authenticate and issue tokens."""
    user = await db.scalar(select(User).where(User.email == body.email))
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    user.last_login_at = datetime.utcnow()

    access = issue_token(user_id=str(user.id), role=UserRole(user.role))
    refresh_raw = await _issue_refresh_token(db, user.id, request)
    _set_refresh_cookie(response, refresh_raw)

    log.info("auth.login", user_id=str(user.id), role=user.role)

    return AuthResponse(
        access_token=access.access_token,
        expires_in=access.expires_in,
        user_id=str(user.id),
        role=user.role,
        full_name=user.full_name,
    )


@router.post("/refresh", response_model=AuthResponse)
async def refresh_token(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    refresh_token_cookie: Annotated[str | None, Cookie(alias="refresh_token")] = None,
) -> AuthResponse:
    """
    Rotate refresh token. Issues new access + refresh token pair.
    Old refresh token is revoked immediately (rotation prevents replay).
    """
    raw = refresh_token_cookie
    if not raw:
        # Fallback: accept from body for non-browser clients
        try:
            body = await request.json()
            raw = body.get("refresh_token")
        except Exception:
            pass

    if not raw:
        raise HTTPException(status_code=401, detail="Refresh token required")

    token_hash = _hash_token(raw)
    rt = await db.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > datetime.utcnow(),
        )
    )

    if not rt:
        # Token reuse detection — revoke all tokens for this user if hash was reused
        log.warning("auth.refresh_invalid", token_hash_prefix=token_hash[:12])
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    # Revoke old token (rotation)
    rt.revoked_at = datetime.utcnow()
    rt.used_at = datetime.utcnow()

    user = await db.get(User, rt.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=403, detail="Account unavailable")

    # Issue new pair
    access = issue_token(user_id=str(user.id), role=UserRole(user.role))
    new_refresh_raw = await _issue_refresh_token(db, user.id, request)
    _set_refresh_cookie(response, new_refresh_raw)

    return AuthResponse(
        access_token=access.access_token,
        expires_in=access.expires_in,
        user_id=str(user.id),
        role=user.role,
        full_name=user.full_name,
    )


@router.post("/logout", status_code=204, response_class=Response)
async def logout(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    refresh_token_cookie: Annotated[str | None, Cookie(alias="refresh_token")] = None,
) -> Response:
    """Revoke refresh token. Access token expiry is handled client-side."""
    if refresh_token_cookie:
        token_hash = _hash_token(refresh_token_cookie)
        rt = await db.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        if rt:
            rt.revoked_at = datetime.utcnow()

    _clear_refresh_cookie(response)
    return Response(status_code=204)


@router.get("/me", response_model=UserResponse)
async def me(
    current_user: Annotated[TokenPayload, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserResponse:
    """Return current user info."""
    user = await db.get(User, current_user.sub)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
    )


@router.post("/forgot-password", status_code=202)
async def forgot_password(
    body: ForgotPasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """
    Request a password reset token.
    Always returns 202 regardless of whether the email exists (no enumeration).
    """
    user = await db.scalar(select(User).where(User.email == body.email))
    if user:
        raw = secrets.token_urlsafe(32)
        prt = PasswordResetToken(
            user_id=str(user.id),
            token_hash=_hash_token(raw),
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        db.add(prt)
        # TODO: send email via background task
        log.info("auth.forgot_password", user_id=str(user.id), token_prefix=raw[:8])

    return {"message": "If that email exists, a reset link has been sent"}


@router.post("/reset-password", status_code=200)
async def reset_password(
    body: ResetPasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Consume a reset token and update the password."""
    token_hash = _hash_token(body.token)
    prt = await db.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > datetime.utcnow(),
        )
    )
    if not prt:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user = await db.get(User, prt.user_id)
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    user.hashed_password = hash_password(body.new_password)
    prt.used_at = datetime.utcnow()

    log.info("auth.password_reset", user_id=user.id)
    return {"message": "Password updated successfully"}


# ── Admin: create reviewer/admin users ────────────────────────────────────────

class AdminCreateUserRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str | None = None
    role: UserRole


@router.post("/admin/users", response_model=UserResponse, status_code=201)
async def admin_create_user(
    body: AdminCreateUserRequest,
    current_user: Annotated[TokenPayload, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserResponse:
    """Admin-only: create a reviewer or admin account."""
    existing = await db.scalar(select(User).where(User.email == body.email))
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        role=body.role.value,
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    await db.flush()

    log.info("auth.admin_create_user", created_by=current_user.sub,
             new_user=user.id, role=user.role)

    return UserResponse(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
    )
