import re
import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, EmailStr, field_validator


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    display_name: str = Field(..., min_length=2, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_]+$", v):
            raise ValueError("Username can only contain Latin letters, numbers, and underscores")
        return v


class UserUpdate(BaseModel):
    display_name: Optional[str] = Field(None, min_length=2, max_length=50)
    bio: Optional[str] = Field(None, max_length=1000)
    avatar_url: Optional[str] = Field(None, max_length=512)


class UserOut(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str
    elo: int
    games_played: int
    wins: int
    losses: int
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserPrivateOut(UserOut):
    email: EmailStr

    class Config:
        from_attributes = True


class LeaderboardUserOut(UserOut):
    rank: str

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPrivateOut


class LoginRequest(BaseModel):
    username: str
    password: str
