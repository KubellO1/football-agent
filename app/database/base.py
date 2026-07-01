"""SQLAlchemy declarative base.

All ORM models (defined later under ``app/repositories/sqlalchemy``) inherit from
``Base``. Keeping the base isolated here lets Alembic import metadata without
pulling in the whole application.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all persistence models."""


class TimestampMixin:
    """Reusable created/updated audit columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
