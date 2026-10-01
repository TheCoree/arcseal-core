"""Match bookkeeping: result persistence, room cleanup, matchmaker resilience."""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.services import character as character_service
from app.services import game_manager as gm
from app.services import matchmaker as mm
from conftest import make_player


class FakeDB:
    """Stands in for AsyncSession: hands back queued RETURNING values."""

    def __init__(self, returning=(), fail=False):
        self.returning = list(returning)
        self.fail = fail
        self.statements = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, stmt):
        if self.fail:
            raise ConnectionError("db down")
        self.statements.append(stmt)
        value = self.returning.pop(0)
        return SimpleNamespace(scalar_one_or_none=lambda: value)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


def registered_room(manager):
    p1, p2 = make_player("a"), make_player("b")
    room = gm.GameRoom("r1", p1, p2)
    manager.active_rooms[room.room_id] = room
    manager.player_to_room[p1.user_id] = room.room_id
    manager.player_to_room[p2.user_id] = room.room_id
    return room, p1, p2


def test_result_is_written_as_an_atomic_increment():
    manager = gm.GameManager()
    room, p1, p2 = registered_room(manager)
    db = FakeDB(returning=(517, 483))

    asyncio.run(manager.finish_game(room, db, winner_side="LEFT"))

    assert db.committed
    sql = str(db.statements[0].compile(dialect=postgresql.dialect()))
    assert "greatest(users.elo +" in sql
    assert "games_played=(users.games_played +" in sql
    assert "RETURNING users.elo" in sql
    # The client is told the ELO actually stored, not a value derived from
    # the possibly stale rating captured at queue time.
    results = p1.websocket.of_type("GAME_FINISHED")[0]["results"]
    assert results["LEFT"]["new_elo"] == 517
    assert results["RIGHT"]["new_elo"] == 483
    assert results["LEFT"]["is_winner"] and not results["RIGHT"]["is_winner"]


def test_room_is_freed_even_if_the_db_write_fails():
    manager = gm.GameManager()
    room, p1, p2 = registered_room(manager)
    db = FakeDB(fail=True)

    asyncio.run(manager.finish_game(room, db, winner_side="RIGHT", reason="surrender"))

    assert db.rolled_back
    assert manager.active_rooms == {}
    assert manager.player_to_room == {}
    assert p1.websocket.of_type("GAME_FINISHED") and p2.websocket.of_type("GAME_FINISHED")


def test_forfeit_reports_the_real_kill_score_and_reason(make_battle):
    async def scenario():
        b = await make_battle(["islam_mahachev"], ["char_mountain"])
        b.state.scores.update({"LEFT": 2, "RIGHT": 3})
        await gm.GameManager().finish_game(
            b.room, FakeDB(returning=(490, 510)), winner_side="RIGHT", reason="surrender",
        )
        return b

    b = asyncio.run(scenario())
    msg = b.left.websocket.of_type("GAME_FINISHED")[0]
    assert msg["reason"] == "surrender"
    assert msg["winner"] == "RIGHT"
    assert (msg["results"]["LEFT"]["score"], msg["results"]["RIGHT"]["score"]) == (2, 3)
    assert msg["results"]["RIGHT"]["is_winner"] and not msg["results"]["LEFT"]["is_draw"]
    assert {u["char_id"] for u in msg["summary"]["units"]} == {"islam_mahachev", "char_mountain"}


def test_failed_room_setup_registers_nothing(monkeypatch):
    async def db_down(db):
        raise ConnectionError("db down")

    monkeypatch.setattr(character_service, "count_active_characters", db_down)
    manager = gm.GameManager()

    with pytest.raises(ConnectionError):
        asyncio.run(manager.create_game(make_player("a"), make_player("b"), None))

    assert manager.active_rooms == {}
    assert manager.player_to_room == {}


def test_matchmaker_survives_a_failed_tick(monkeypatch):
    queue = mm.MatchmakerQueue()
    ticks = []

    async def flaky_tick():
        ticks.append(1)
        if len(ticks) == 1:
            raise ConnectionError("db down")
        queue.is_running = False

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(queue, "_try_match", flaky_tick)
    monkeypatch.setattr(mm.asyncio, "sleep", no_wait)
    queue.is_running = True

    asyncio.run(queue._matchmaking_loop())

    assert len(ticks) == 2


def test_elo_change_is_zero_sum_and_floored():
    manager = gm.GameManager()
    assert manager.calculate_elo_change(500, 500, 5, 3) == (10, -10)
    delta_a, delta_b = manager.calculate_elo_change(1000, gm.ELO_FLOOR, 5, 3)
    assert delta_b == 0 and delta_a == 0
