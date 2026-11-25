"""SQLAlchemy ORM model representing SaaS user accounts and roles."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class UserRole(StrEnum):
    """Access tiers within a SampleSaaS customer organization."""

    MEMBER = "member"
    ADMIN = "admin"
    BILLING_OWNER = "billing_owner"


class User(Base):
    """Registered SaaS account holder."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default=UserRole.MEMBER.value, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    organization_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    def is_privileged(self) -> bool:
        """Return True when the user can mutate billing or org settings."""
        return self.role in {UserRole.ADMIN.value, UserRole.BILLING_OWNER.value}

    def display_label(self) -> str:
        """Format a readable account descriptor for logs and audit exports."""
        return f"{self.full_name} <{self.email}> [{self.role}]"
