from typing import List, Optional
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate
from app.core.security import hash_password, verify_password


async def get_user_by_username(db: AsyncSession, username: str) -> Optional[User]:
    """Retrieve a user by username from the database."""
    stmt = select(User).filter(User.username == username)
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    """Retrieve a user by email from the database."""
    stmt = select(User).filter(User.email == email)
    result = await db.execute(stmt)
    return result.scalars().first()


async def create_user(db: AsyncSession, user_in: UserCreate) -> User:
    """Create a new user with hashed password and return the user object.

    Raises:
        HTTPException: If username or email is already registered.
    """
    # Check if username exists
    existing_username = await get_user_by_username(db, user_in.username)
    if existing_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username is already registered."
        )

    # Check if email exists
    existing_email = await get_user_by_email(db, user_in.email)
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is already registered."
        )

    hashed_pw = hash_password(user_in.password)
    
    # Standard starting Elo is 500, but ensuring Python floor logic (minimum 100 Elo)
    starting_elo = max(100, 500)

    db_user = User(
        username=user_in.username,
        display_name=user_in.display_name,
        email=user_in.email,
        hashed_password=hashed_pw,
        elo=starting_elo
    )

    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user


async def authenticate_user(db: AsyncSession, username: str, password: str) -> Optional[User]:
    """Authenticate a user by username and password."""
    user = await get_user_by_username(db, username)
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def _normalize_avatar_url(value: Optional[str]) -> Optional[str]:
    """Store avatars as a host-relative path ("/uploads/avatars/...").

    Older clients (and one frontend bug) sometimes sent an absolute URL like
    "http://192.168.0.64:8000/uploads/avatars/x.jpg", which bakes a specific
    host/IP into the DB and breaks when the app is reached from another host.
    Collapse any such value back to the "/uploads/..." segment.
    """
    if not value:
        return value
    idx = value.find("/uploads/")
    return value[idx:] if idx != -1 else value


async def update_user_profile(db: AsyncSession, db_user: User, user_update: UserUpdate) -> User:
    """Update profile fields for a user."""
    update_data = user_update.model_dump(exclude_unset=True)

    if "avatar_url" in update_data:
        update_data["avatar_url"] = _normalize_avatar_url(update_data["avatar_url"])

    # Directly map allowed fields
    for field, value in update_data.items():
        setattr(db_user, field, value)

    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user


async def change_user_elo(db: AsyncSession, db_user: User, elo_change: int) -> User:
    """Safely adjust a user's ELO rating.

    This ensures Elo does not drop below 100 in Python code,
    preventing negative ELO or database-breaking transaction exceptions.
    """
    new_elo = db_user.elo + elo_change
    db_user.elo = max(100, new_elo)

    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user


async def get_leaderboard(db: AsyncSession, limit: int = 10) -> List[User]:
    """Retrieve top users sorted by ELO rating descending."""
    stmt = select(User).order_by(User.elo.desc()).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())
