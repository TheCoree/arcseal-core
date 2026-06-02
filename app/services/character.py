from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.character import Character


async def list_characters(db: AsyncSession, *, active_only: bool = True) -> List[Character]:
    stmt = select(Character)
    if active_only:
        stmt = stmt.where(Character.is_active.is_(True))
    stmt = stmt.order_by(Character.role, Character.name)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_character(db: AsyncSession, char_id: str) -> Optional[Character]:
    result = await db.execute(select(Character).where(Character.id == char_id))
    return result.scalars().first()


async def list_by_ids(db: AsyncSession, ids: List[str]) -> List[Character]:
    """Bulk-fetch characters by id. Used when spawning a battle from picks."""
    if not ids:
        return []
    result = await db.execute(select(Character).where(Character.id.in_(ids)))
    return list(result.scalars().all())


async def count_active_characters(db: AsyncSession) -> int:
    """Used by the draft engine to decide whether to run the ban sub-phase."""
    from sqlalchemy import func
    result = await db.execute(
        select(func.count(Character.id)).where(Character.is_active.is_(True)),
    )
    return int(result.scalar_one())
