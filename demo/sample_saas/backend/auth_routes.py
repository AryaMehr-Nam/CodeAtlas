"""FastAPI authentication router exposing registration, login, and profile endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from .auth_service import (
    authenticate_user,
    register_user,
    resolve_current_user,
    rotate_user_password,
)
from .logger import get_logger
from .user_model import UserRole

router = APIRouter(prefix="/api/auth", tags=["auth"])
_log = get_logger("auth_routes")


class RegisterRequest(BaseModel):
    """Payload for registering a new organization user."""

    email: str = Field(min_length=5, max_length=255)
    full_name: str = Field(min_length=2, max_length=160)
    password: str = Field(min_length=8, max_length=128)
    organization_id: str = Field(min_length=2, max_length=64)
    role: UserRole = UserRole.MEMBER


class LoginRequest(BaseModel):
    """Credentials payload for issuing a bearer token."""

    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class PasswordRotateRequest(BaseModel):
    """Password rotation request body."""

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_endpoint(payload: RegisterRequest) -> dict[str, object]:
    """Register a new SaaS user account."""
    try:
        user = register_user(
            email=payload.email,
            full_name=payload.full_name,
            password=payload.password,
            organization_id=payload.organization_id,
            role=payload.role,
        )
    except ValueError as exc:
        _log.warning("auth.register_rejected", email=payload.email)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "organization_id": user.organization_id,
    }


@router.post("/login")
def login_endpoint(payload: LoginRequest) -> dict[str, str]:
    """Authenticate a user and return a signed bearer token."""
    token = authenticate_user(email=payload.email, password=payload.password)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
def current_user_endpoint(authorization: str = Header(default="")) -> dict[str, object]:
    """Return the authenticated user profile from the Authorization header."""
    token = authorization.removeprefix("Bearer ").strip()
    user = resolve_current_user(token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
        )
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "organization_id": user.organization_id,
    }


@router.post("/password/rotate")
def rotate_password_endpoint(
    payload: PasswordRotateRequest,
    authorization: str = Header(default=""),
) -> dict[str, bool]:
    """Rotate the authenticated user's password."""
    token = authorization.removeprefix("Bearer ").strip()
    user = resolve_current_user(token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized.")
    try:
        rotated = rotate_user_password(
            user_id=user.id,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if not rotated:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Current password check failed.",
        )
    return {"rotated": True}
