import json
from typing import List, Optional, Union
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Database Settings
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str
    DATABASE_URL: str = ""

    # JWT Settings
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # Frontend Settings
    FRONTEND_URL: str = "http://localhost"

    # CORS Settings
    CORS_ORIGINS: Union[str, List[str]] = ["http://localhost", "http://localhost:3000"]
    # Starlette matches allow_origins by exact string only (no wildcards). To allow
    # a whole LAN subnet during local testing, set a regex here instead, e.g.
    # CORS_ORIGIN_REGEX=http://192\.168\.0\.\d{1,3}(:\d+)?
    CORS_ORIGIN_REGEX: Optional[str] = None

    # Refresh cookie behaviour. In local dev set COOKIE_SECURE=False so the
    # browser actually returns the cookie over plain HTTP, and use samesite=lax
    # (frontend and backend must share a registrable domain — use the same
    # hostname like `localhost` on both sides for `lax` to send the cookie).
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            try:
                # Handle JSON array string
                if v.strip().startswith("[") and v.strip().endswith("]"):
                    return json.loads(v)
                # Handle comma-separated list
                return [i.strip() for i in v.split(",") if i.strip()]
            except Exception:
                return [v]
        return v

    @model_validator(mode="after")
    def assemble_db_url(self) -> "Settings":
        # Resolve any dotenv variable interpolation manually if present
        if not self.DATABASE_URL or "${" in self.DATABASE_URL:
            self.DATABASE_URL = (
                f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
                f"@localhost:5432/{self.DB_NAME}"
            )
        elif self.DATABASE_URL.startswith("postgresql://"):
            self.DATABASE_URL = self.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
        return self


settings = Settings()
