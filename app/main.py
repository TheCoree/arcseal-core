from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
from contextlib import asynccontextmanager

import logging

from app.core.config import settings
from app.api import auth, users, ws, characters
from app.services.matchmaker import matchmaker
from app.seed.characters import upsert_roster

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup. Re-seed the character roster on every boot — the upsert is
    # idempotent, so balance / selector edits in app/seed/characters.py take
    # effect on restart (incl. uvicorn --reload) without a manual seed step.
    try:
        await upsert_roster()
    except Exception as exc:  # don't let a seed hiccup block the API
        logging.getLogger("uvicorn.error").warning("Roster auto-seed skipped: %s", exc)
    matchmaker.start()
    yield
    # Shutdown
    matchmaker.stop()

app = FastAPI(
    title="Arcseal API",
    description="API for Arcseal Multiplayer Matchmaking",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Ensure upload directories exist. Static mount serves anything inside
# `uploads/` under the `/uploads` URL prefix — drop files here and reference
# them as `/uploads/<sub>/<file>` from character/ability JSON.
os.makedirs(os.path.join("uploads", "avatars"), exist_ok=True)
os.makedirs(os.path.join("uploads", "characters", "portraits"), exist_ok=True)
os.makedirs(os.path.join("uploads", "abilities"), exist_ok=True)

# Mount static files for avatars
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# Configure CORS Middleware using settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=settings.CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers with standard API v1 path prefixes
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(users.router, prefix="/api/v1/users", tags=["Users"])
app.include_router(characters.router, prefix="/api/v1/characters", tags=["Characters"])
app.include_router(ws.router, prefix="/api/v1/ws", tags=["WebSockets"])

@app.get("/", tags=["Health"])
async def health_check():
    return {"status": "healthy", "service": "arcseal-backend"}
