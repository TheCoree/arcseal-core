"""Battle-engine harness: a real GameRoom driven like two clients would drive
it, with in-memory sockets and the seed roster standing in for the DB."""

import uuid
from types import SimpleNamespace

import pytest

from app.seed.roster import ROSTER
from app.services import character as character_service
from app.services import game_manager as gm

ROSTER_BY_ID = {c["id"]: c for c in ROSTER}


class FakeSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message)

    def of_type(self, message_type):
        return [m for m in self.sent if m.get("type") == message_type]


def make_player(name: str) -> gm.PlayerSession:
    user = SimpleNamespace(
        id=uuid.uuid4(), username=name, display_name=name, avatar_url=None, elo=500,
    )
    return gm.PlayerSession(user, FakeSocket())


async def _list_by_ids(db, ids):
    return [
        SimpleNamespace(
            id=c["id"],
            initial_position_type=c["initial_position_type"],
            base_stats=c["base_stats"],
            attack=c["attack"],
            abilities=c.get("abilities", []),
            passives=c.get("passives", []),
        )
        for c in ROSTER
        if c["id"] in ids
    ]


class NullSession:
    """Async context manager standing in for a DB session the code never uses."""

    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


class Battle:
    def __init__(self, room: gm.GameRoom, left: gm.PlayerSession, right: gm.PlayerSession):
        self.room = room
        self.left = left
        self.right = right

    @property
    def state(self) -> gm.BattleState:
        return self.room.battle

    def unit(self, unit_id: str) -> gm.UnitState:
        return self.room.battle.units[unit_id]

    def place(self, unit_id: str, zone: int) -> None:
        unit = self.unit(unit_id)
        self.state.zones[unit.zone].remove(unit_id)
        unit.zone = zone
        self.state.zones[zone].append(unit_id)
        self.room._recompute_board()

    async def act(self, player: gm.PlayerSession, **action) -> None:
        await self.room.handle_action(player.user_id, action, None)

    def events(self, player: gm.PlayerSession = None):
        """Every battle event the player's client has been sent so far."""
        sock = (player or self.left).websocket
        return [e for m in sock.of_type("ROOM_STATE") for e in m.get("events", [])]


@pytest.fixture
def make_battle(monkeypatch):
    """Factory: `await make_battle(left_picks, right_picks, first=...)`. The
    match end is recorded on `room.stage` instead of being written to the DB."""
    monkeypatch.setattr(character_service, "list_by_ids", _list_by_ids)
    monkeypatch.setattr(gm, "_db_session", NullSession)

    async def fake_finish(room, db, *, winner_side, reason="score"):
        room.stage = "FINISHED"
        room._stop_turn_clock()
        room.finish_args = {"winner_side": winner_side, "reason": reason}

    monkeypatch.setattr(gm.game_manager, "finish_game", fake_finish)

    async def factory(left, right, first="LEFT") -> Battle:
        p1, p2 = make_player("left"), make_player("right")
        room = gm.GameRoom("room", p1, p2)
        room.draft = gm.DraftState(
            sub_stage="PICK", bans_enabled=False, bans_per_side=0,
            picks_per_side=min(len(left), len(right)), first_pick_side=first, current_side=first,
            full_roster_ids=list(ROSTER_BY_ID),
        )
        room.draft.picks = {"LEFT": list(left), "RIGHT": list(right)}
        await room.begin_battle(None)
        return Battle(room, p1, p2)

    return factory
