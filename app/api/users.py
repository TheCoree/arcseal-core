from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
import os
import uuid
import shutil

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.user import UserOut, UserPrivateOut, UserUpdate, LeaderboardUserOut
from app.services import user as user_service
from app.services.rank import get_rank_from_elo

router = APIRouter()


@router.get("/me", response_model=UserPrivateOut)
async def get_me(current_user: User = Depends(get_current_user)):
    """Retrieve details of the authenticated user (includes private fields like email)."""
    return current_user


@router.put("/me", response_model=UserPrivateOut)
async def update_me(
    user_update: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update profile details of the authenticated user.

    Triggers updated_at automatically.
    """
    updated_user = await user_service.update_user_profile(db, current_user, user_update)
    return updated_user


@router.post("/me/avatar", response_model=UserPrivateOut)
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Upload and set the user's avatar."""
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    # Generate a unique filename using user ID and UUID
    ext = file.filename.split(".")[-1] if "." in file.filename else "jpg"
    filename = f"{current_user.id}_{uuid.uuid4().hex[:8]}.{ext}"
    filepath = os.path.join("uploads", "avatars", filename)
    
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # Standardize the URL. Depending on deployment, a full URL might be needed, 
    # but a relative URL from the backend host is sufficient if the frontend prepends the API_URL.
    # Alternatively, just store the absolute path /uploads/... since both front and back might run on same domain,
    # or the frontend will know to fetch it from the backend domain.
    avatar_url = f"/uploads/avatars/{filename}"
    
    user_update = UserUpdate(avatar_url=avatar_url)
    updated_user = await user_service.update_user_profile(db, current_user, user_update)
    return updated_user


@router.get("/leaderboard", response_model=List[LeaderboardUserOut])
async def get_leaderboard(
    limit: int = 10,
    db: AsyncSession = Depends(get_db)
):
    """Retrieve the players leaderboard sorted by ELO descending.

    Dynamically maps Elo to Rank titles on the API level.
    """
    users = await user_service.get_leaderboard(db, limit=limit)

    # Dynamic mapping to calculate ranks on the fly
    leaderboard_data = []
    for user in users:
        rank_title = get_rank_from_elo(user.elo)
        leaderboard_data.append({
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "elo": user.elo,
            "games_played": user.games_played,
            "wins": user.wins,
            "losses": user.losses,
            "bio": user.bio,
            "avatar_url": user.avatar_url,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
            "rank": rank_title
        })

    return leaderboard_data


@router.get("/{username}", response_model=UserOut)
async def get_public_profile(
    username: str,
    db: AsyncSession = Depends(get_db)
):
    """Retrieve public profile details of a user by username (excludes private fields like email)."""
    user = await user_service.get_user_by_username(db, username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return user
