from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.character import CharacterOut
from app.services import character as character_service

router = APIRouter()


@router.get("", response_model=List[CharacterOut])
async def list_characters(db: AsyncSession = Depends(get_db)):
    """Roster of all active characters available for drafting."""
    return await character_service.list_characters(db, active_only=True)


@router.get("/{char_id}", response_model=CharacterOut)
async def get_character(char_id: str, db: AsyncSession = Depends(get_db)):
    char = await character_service.get_character(db, char_id)
    if not char:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Character not found",
        )
    return char
