import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
import jwt

from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.services.game_manager import PlayerSession, game_manager
from app.services.matchmaker import matchmaker
from sqlalchemy.future import select

router = APIRouter()

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


@router.websocket("/match")
async def websocket_match(
    websocket: WebSocket,
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    user = await get_user_from_token(token, db)
    if not user:
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

    try:
        while True:
            data = await websocket.receive_text()
            try:
                action = json.loads(data)
            except json.JSONDecodeError:
                continue

            action_type = action.get("type")

            if action_type == "FIND_MATCH":
                if user_id in game_manager.player_to_room:
                    continue
                player_session = PlayerSession(user, websocket)
                matchmaker.add_player(player_session)
                await websocket.send_json({"type": "SEARCHING"})
            elif action_type == "CANCEL_SEARCH":
                matchmaker.remove_player(user_id)
                await websocket.send_json({"type": "SEARCH_CANCELLED"})
            else:
                # Everything else (BAN_CHARACTER, PICK_CHARACTER, future
                # battle actions) is dispatched into the active room.
                room = game_manager.get_room_for(user_id)
                if room is not None:
                    await room.handle_action(user_id, action, db)

    except WebSocketDisconnect:
        # Only clean up if this is still the active socket. A newer connection
        # has already taken the slot in `active_connections`, so this branch
        # is a no-op in that case.
        if active_connections.get(user_id) is websocket:
            del active_connections[user_id]
            matchmaker.remove_player(user_id)
            await game_manager.disconnect_player(user_id)
