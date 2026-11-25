"""Authentication and credential lifecycle service for SampleSaaS."""

from __future__ import annotations

from sqlalchemy import select

from .crypto import hash_password, sign_token, verify_password, verify_token
from .db import session_scope
from .logger import get_logger
from .user_model import User, UserRole

_log = get_logger("auth_service")


def register_user(
    email: str,
    full_name: str,
    password: str,
    organization_id: str,
    role: UserRole = UserRole.MEMBER,
) -> User:
    """Create and persist a new SaaS user account with a salted password hash."""
    normalized_email = email.strip().lower()
    if len(password) < 8:
        raise ValueError("Password must contain at least 8 characters.")

    with session_scope() as session:
        existing = session.scalar(select(User).where(User.email == normalized_email))
        if existing is not None:
            raise ValueError(f"Account already exists for {normalized_email}.")

        user = User(
            email=normalized_email,
            full_name=full_name.strip(),
            password_hash=hash_password(password),
            role=role.value,
            is_active=True,
            organization_id=organization_id.strip(),
        )
        session.add(user)
        session.flush()
        session.refresh(user)
        _log.info("user.registered", user_id=user.id, org_id=user.organization_id)
        return user


def authenticate_user(email: str, password: str) -> str | None:
    """Validate credentials and issue a signed bearer access token."""
    normalized_email = email.strip().lower()
    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == normalized_email))
        if user is None or not user.is_active:
            _log.warning("auth.login_denied", reason="missing_or_inactive", email=normalized_email)
            return None

        if not verify_password(password, user.password_hash):
            _log.warning("auth.login_denied", reason="invalid_password", user_id=user.id)
            return None

        token = sign_token(subject=str(user.id), role=user.role)
        _log.info("auth.login_succeeded", user_id=user.id, role=user.role)
        return token


def resolve_current_user(token: str) -> User | None:
    """Verify an access token and load the active User record."""
    claims = verify_token(token)
    if claims is None:
        return None

    try:
        user_id = int(claims["sub"])
    except ValueError:
        return None

    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None or not user.is_active:
            return None
        return user


def rotate_user_password(user_id: int, current_password: str, new_password: str) -> bool:
    """Rotate a user's password after confirming their existing secret."""
    if len(new_password) < 8:
        raise ValueError("New password must contain at least 8 characters.")

    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None or not user.is_active:
            return False
        if not verify_password(current_password, user.password_hash):
            _log.warning("auth.password_rotate_failed", user_id=user_id)
            return False

        user.password_hash = hash_password(new_password)
        session.add(user)
        _log.info("auth.password_rotated", user_id=user_id)
        return True
