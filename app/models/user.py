import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, Boolean, DateTime, text, func, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()")
    )
    username: Mapped[str] = mapped_column(
        String(32),
        unique=True,
        index=True,
        nullable=False
    )
    display_name: Mapped[str] = mapped_column(
        String(50),
        nullable=False
    )
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False
    )
    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )
    elo: Mapped[int] = mapped_column(
        Integer,
        default=500,
        nullable=False
    )
    games_played: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False
    )
    wins: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False
    )
    losses: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False
    )
    bio: Mapped[Optional[str]] = mapped_column(
        String(1000),
        nullable=True
    )
    avatar_url: Mapped[Optional[str]] = mapped_column(
        String(512),
        nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )
