"""Idempotent seed for the character roster.

Run with::

    poetry run python -m app.seed.characters

Character definitions now live one-per-file under ``app/seed/roster/`` and are
auto-collected into ``ROSTER`` there. This module is just the DB upsert. Add a
new character by dropping an ``app/seed/roster/<name>.py`` with a ``CHARACTER``
dict — no edits here required.

Names and human-facing text are in Russian; stat abbreviations (HP / EN /
DEF) stay English to match the UI.
"""
from __future__ import annotations

import asyncio
import os

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import AsyncSessionLocal
from app.models.character import Character
from app.schemas.character import CharacterDef
from app.seed.roster import ROSTER

__all__ = ["ROSTER", "upsert_roster"]

# uploads/ lives at the backend root (this file is app/seed/characters.py).
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_UPLOAD_ROOT = os.path.join(_BACKEND_ROOT, "uploads")
_ICON_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def _find_media(subdir: str, stem: str) -> str | None:
    """Return the /uploads URL for the first file matching `<stem>.<ext>` in
    uploads/<subdir>, or None. Lets art be dropped in by filename convention
    without editing seed data."""
    base = os.path.join(_UPLOAD_ROOT, *subdir.split("/"))
    for ext in _ICON_EXTS:
        if os.path.isfile(os.path.join(base, stem + ext)):
            return f"/uploads/{subdir}/{stem}{ext}"
    return None


def _resolve_media(raw: dict) -> None:
    """Fill in missing icon_url / portrait_url by filename convention:
      * ability/passive icon  → uploads/abilities/<id>.<ext>
      * basic attack icon      → uploads/abilities/<char_id>_attack.<ext>
      * character portrait     → uploads/characters/portraits/<char_id>.<ext>
    Explicitly-set values are left untouched."""
    cid = raw["id"]
    attack = raw.get("attack") or {}
    if not attack.get("icon_url"):
        url = _find_media("abilities", f"{cid}_attack")
        if url:
            attack["icon_url"] = url
    for ability in raw.get("abilities", []):
        if not ability.get("icon_url"):
            url = _find_media("abilities", ability["id"])
            if url:
                ability["icon_url"] = url
    for passive in raw.get("passives", []):
        if not passive.get("icon_url"):
            url = _find_media("abilities", passive["id"])
            if url:
                passive["icon_url"] = url
    if not raw.get("portrait_url"):
        url = _find_media("characters/portraits", cid)
        if url:
            raw["portrait_url"] = url


async def upsert_roster() -> None:
    async with AsyncSessionLocal() as db:
        for raw in ROSTER:
            _resolve_media(raw)
            CharacterDef.model_validate(raw)

            # NOTE: pg_insert works at the TABLE level, so keys here are COLUMN
            # names. `abilities`/`passives` are ORM attributes mapped onto the
            # original `ability`/`passive_ability` JSONB columns — use the
            # column names here, not the attribute names.
            stmt = pg_insert(Character).values(
                id=raw["id"],
                name=raw["name"],
                role=raw["role"],
                initial_position_type=raw["initial_position_type"],
                portrait_url=raw.get("portrait_url"),
                base_stats=raw["base_stats"],
                attack=raw["attack"],
                ability=raw.get("abilities", []),
                passive_ability=raw.get("passives", []),
                is_active=True,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[Character.id],
                set_={
                    "name": stmt.excluded.name,
                    "role": stmt.excluded.role,
                    "initial_position_type": stmt.excluded.initial_position_type,
                    "portrait_url": stmt.excluded.portrait_url,
                    "base_stats": stmt.excluded.base_stats,
                    "attack": stmt.excluded.attack,
                    "ability": stmt.excluded.ability,
                    "passive_ability": stmt.excluded.passive_ability,
                    "is_active": True,
                },
            )
            await db.execute(stmt)
        await db.commit()

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Character).order_by(Character.role, Character.name))
        rows = result.scalars().all()
        print(f"Seeded {len(rows)} characters:")
        for c in rows:
            print(f"  - [{c.role}] {c.id} :: {c.name} ({c.initial_position_type})")


if __name__ == "__main__":
    asyncio.run(upsert_roster())
