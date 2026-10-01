import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.ext.asyncio import AsyncSession
import jwt

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.user import User
from app.services.game_manager import PlayerSession, game_manager
from app.services.matchmaker import matchmaker
from sqlalchemy.future import select

router = APIRouter()
logger = logging.getLogger(__name__)

# Global dict to track active websocket connections per user
active_connections: dict[str, WebSocket] = {}


async def get_user_from_token(token: str, db: AsyncSession) -> User | None:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("type") != "access":
            return None
        username: str = payload.get("sub")
        if not username:
            return None

        result = await db.execute(select(User).filter(User.username == username))
        user = result.scalars().first()
        if user and user.is_active:
            return user
    except Exception:
        pass
    return None


async def load_fresh_user(user: User) -> User:
    """Re-read the user's row so matchmaking and ELO maths use the rating as of
    now, not as of when this socket connected (it may span several matches)."""
    async with AsyncSessionLocal() as db:
        fresh = await db.get(User, user.id)
    return fresh if fresh is not None else user


@router.websocket("/match")
async def websocket_match(
    websocket: WebSocket,
    token: str = Query(...),
):
    # DB sessions here are short-lived: one for auth, one per action. Holding a
    # session for the socket's lifetime would pin a pooled connection (idle in
    # transaction) per online player and serve stale User rows.
    async with AsyncSessionLocal() as db:
        user = await get_user_from_token(token, db)
    if not user:
        # Accept first so the client actually receives code 4001 — closing
        # before accept() turns into a bare HTTP 403 (seen as 1006).
        await websocket.accept()
        await websocket.close(code=4001, reason="Unauthorized")
        return

    user_id = str(user.id)

    await websocket.accept()

    # Atomic take-over: claim the slot BEFORE awaiting the old close so the
    # old handler's WebSocketDisconnect skips cleanup (no spurious forfeit).
    old_ws = active_connections.get(user_id)
    active_connections[user_id] = websocket
    if old_ws is not None and old_ws is not websocket:
        try:
            await old_ws.close(code=4000, reason="New connection opened")
        except Exception:
            pass

    try:
        # Decide the opening state. We do NOT auto-add to the matchmaker — the
        # client must explicitly send FIND_MATCH. This means a reload of the
        # dashboard quietly re-establishes the socket without dragging the user
        # into a search.
        room = game_manager.get_room_for(user_id)
        if room is not None:
            existing_player = room.get_player_by_id(user_id)
            if existing_player:
                existing_player.websocket = websocket

                had_pending = game_manager.cancel_pending_forfeit(user_id)
                if had_pending and game_manager.consume_disconnect_notification(user_id):
                    opponent = room.get_opponent(existing_player)
                    try:
                        await opponent.websocket.send_json({"type": "OPPONENT_RECONNECTED"})
                    except Exception:
                        pass

                # Replay current room state — frontend renders the right screen
                # based on the `stage` field in the payload.
                await room.send_state_to(websocket, is_reconnect=True)
        else:
            # Not in a game. Tell the client they're idle, clear any stale queue.
            matchmaker.remove_player(user_id)
            await websocket.send_json({"type": "LOBBY"})

        while True:
            data = await websocket.receive_text()
            try:
                action = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(action, dict):
                continue

            action_type = action.get("type")

            if action_type == "FIND_MATCH":
                if user_id in game_manager.player_to_room:
                    continue
                user = await load_fresh_user(user)
                player_session = PlayerSession(user, websocket)
                matchmaker.add_player(player_session)
                await websocket.send_json({"type": "SEARCHING"})
            elif action_type == "CANCEL_SEARCH":
                matchmaker.remove_player(user_id)
                await websocket.send_json({"type": "SEARCH_CANCELLED"})
            else:
                # Everything else (BAN_CHARACTER, PICK_CHARACTER, battle
                # actions) is dispatched into the active room.
                room = game_manager.get_room_for(user_id)
                if room is None:
                    continue
                try:
                    async with AsyncSessionLocal() as db:
                        await room.handle_action(user_id, action, db)
                except WebSocketDisconnect:
                    raise
                except Exception:
                    # A bug in one action must not kill the socket (and with it
                    # the match). Log it and resync both clients to whatever
                    # state the room ended up in.
                    logger.exception(
                        "Action %r failed in room %s (user %s)", action_type, room.room_id, user_id,
                    )
                    if room.stage != "FINISHED":
                        await room.broadcast_state()

    except WebSocketDisconnect:
        pass
    except Exception:
        # A socket we closed ourselves on take-over errors out of receive();
        # that's expected, only log failures of the live connection.
        if active_connections.get(user_id) is websocket:
            logger.exception("WebSocket handler for user %s crashed", user_id)
    finally:
        # Only clean up if this is still the active socket. A newer connection
        # has already taken the slot in `active_connections`, so this branch
        # is a no-op in that case.
        if active_connections.get(user_id) is websocket:
            del active_connections[user_id]
            matchmaker.remove_player(user_id)
            await game_manager.disconnect_player(user_id)
