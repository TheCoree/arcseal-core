from datetime import datetime
from typing import Any, Optional
from sqlalchemy import String, Boolean, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Character(Base):
    """Static character definition.

    `base_stats`, `attack`, `ability`, `passive_ability` are JSONB because
    their inner structure (execution chains, target selectors, conditions) is
    intentionally fluid — every new mechanic adds fields, and normalising it
    would mean a migration per idea. Top-level columns (role, name, position
    type) stay as scalar columns so they're cheap to filter/order on.
    """

    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    initial_position_type: Mapped[str] = mapped_column(String(16), nullable=False)

    # Portrait artwork: relative path under the static /uploads mount, e.g.
    # `/uploads/characters/portraits/iron_guard.jpg`. Nullable so seed data
    # without art still validates; frontend falls back to a placeholder.
    portrait_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    base_stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    attack: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # A character's KIT: `abilities` (active) and `passives` are JSONB LISTS.
    # They reuse the original `ability` / `passive_ability` columns (JSONB
    # already stores arrays) so no migration is needed — only the JSON shape
    # inside changed. The auto-seed rewrites every row on boot.
    abilities: Mapped[list[Any]] = mapped_column("ability", JSONB, nullable=False)
    passives: Mapped[list[Any]] = mapped_column("passive_ability", JSONB, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )
