"""Turn flow: clock, surrender, stun auto-skip, battle events and stats."""

import asyncio

from app.services import game_manager as gm
from conftest import ROSTER_BY_ID, make_player


def draft_room(stage="DRAFT_PICK"):
    """A room still in the draft (no battle yet), LEFT to move."""
    p1, p2 = make_player("left"), make_player("right")
    room = gm.GameRoom("draft", p1, p2)
    room.draft = gm.DraftState(
        sub_stage="PICK" if stage == "DRAFT_PICK" else "BAN",
        bans_enabled=stage == "DRAFT_BAN", bans_per_side=1 if stage == "DRAFT_BAN" else 0,
        picks_per_side=3, first_pick_side="LEFT", current_side="LEFT",
        full_roster_ids=list(ROSTER_BY_ID),
    )
    room.stage = stage
    return room, p1, p2


# ── surrender ───────────────────────────────────────────────────────────

def test_surrender_works_off_turn_and_hands_the_win_over(make_battle):
    async def scenario():
        b = await make_battle(["islam_mahachev"], ["char_mountain"], first="LEFT")
        await b.act(b.right, type="SURRENDER")  # RIGHT gives up on LEFT's turn
        return b

    b = asyncio.run(scenario())
    assert b.room.stage == "FINISHED"
    assert b.room.finish_args == {"winner_side": "LEFT", "reason": "surrender"}


def test_surrender_during_draft(make_battle):
    async def scenario():
        room, p1, p2 = draft_room()
        await room.handle_action(p1.user_id, {"type": "SURRENDER"}, None)
        return room

    room = asyncio.run(scenario())
    assert room.finish_args == {"winner_side": "RIGHT", "reason": "surrender"}


# ── turn clock ──────────────────────────────────────────────────────────

def test_state_carries_the_turn_clock(make_battle):
    async def scenario():
        b = await make_battle(["islam_mahachev"], ["char_mountain"])
        await b.room.broadcast_state()
        return b.left.websocket.of_type("ROOM_STATE")[-1]["turn_timer"]

    timer = asyncio.run(scenario())
    assert timer["seconds"] == gm.BATTLE_TURN_SECONDS
    assert 0 < timer["remaining_ms"] <= gm.BATTLE_TURN_SECONDS * 1000


def test_timeout_skips_an_idle_battle_turn(make_battle):
    async def scenario():
        b = await make_battle(["islam_mahachev", "char_mountain"], ["char_iron_guard"], first="LEFT")
        await b.room._on_turn_timeout(None)
        return b

    b = asyncio.run(scenario())
    spent = [u for u in b.state.units.values() if u.owner_side == "LEFT" and u.has_moved]
    assert len(spent) == 1
    assert b.state.current_actor_side == "RIGHT"
    assert b.state.active_unit_id is None
    assert b.left.timeouts == 1
    assert {"t": "skip", "unit": spent[0].unit_id, "reason": "timeout"} in b.events()


def test_timeout_ends_an_activation_in_progress(make_battle):
    async def scenario():
        b = await make_battle(["islam_mahachev"], ["char_iron_guard"], first="LEFT")
        await b.act(b.left, type="ACTIVATE_UNIT", unit_id="left_islam_mahachev")
        await b.room._on_turn_timeout(None)
        return b

    b = asyncio.run(scenario())
    assert b.unit("left_islam_mahachev").has_moved
    assert b.state.current_actor_side == "RIGHT"


def test_acting_resets_the_afk_counter(make_battle):
    async def scenario():
        b = await make_battle(["islam_mahachev", "char_mountain"], ["char_iron_guard", "char_shadow"])
        b.left.timeouts = 2
        await b.act(b.left, type="ACTIVATE_UNIT", unit_id="left_islam_mahachev")
        return b

    assert asyncio.run(scenario()).left.timeouts == 0


def test_idle_player_forfeits_after_repeated_timeouts(make_battle, monkeypatch):
    monkeypatch.setattr(gm, "BATTLE_TURN_SECONDS", 0.01)

    async def scenario():
        b = await make_battle(
            ["islam_mahachev", "char_mountain", "char_marksman"],
            ["char_iron_guard", "char_shadow", "char_priestess"],
            first="LEFT",
        )
        # Nobody acts: the clock alternates L, R, L, R, L — LEFT idles out first.
        for _ in range(100):
            if b.room.stage == "FINISHED":
                break
            await asyncio.sleep(0.01)
        return b

    b = asyncio.run(scenario())
    assert b.room.finish_args == {"winner_side": "RIGHT", "reason": "afk"}


def test_draft_timeout_picks_for_the_idle_side(make_battle):
    async def scenario():
        room, p1, p2 = draft_room()
        await room._on_turn_timeout(None)
        return room

    room = asyncio.run(scenario())
    assert len(room.draft.picks["LEFT"]) == 1
    assert room.draft.current_side == "RIGHT"


def test_stale_timer_does_nothing_after_the_turn_moved_on(make_battle, monkeypatch):
    async def scenario():
        b = await make_battle(["islam_mahachev"], ["char_iron_guard"], first="LEFT")
        old_turn = b.room._turn_id
        await b.act(b.left, type="ACTIVATE_UNIT", unit_id="left_islam_mahachev")
        await b.act(b.left, type="END_TURN")
        # The clock armed for LEFT's turn fires late: it must not touch RIGHT.
        await b.room._run_clock(old_turn, 0)
        return b

    b = asyncio.run(scenario())
    assert b.state.current_actor_side == "RIGHT"
    assert not b.unit("right_char_iron_guard").has_moved
    assert b.right.timeouts == 0


# ── stun auto-skip ──────────────────────────────────────────────────────

def test_stunned_unit_skips_its_activation_automatically(make_battle):
    async def scenario():
        b = await make_battle(["islam_mahachev", "char_mountain"], ["char_iron_guard", "char_shadow"])
        guard = b.unit("right_char_iron_guard")
        guard.statuses.append({"name": "STUN", "value": 1, "duration": 1, "max_duration": 1})
        await b.act(b.left, type="ACTIVATE_UNIT", unit_id="left_islam_mahachev")
        await b.act(b.left, type="END_TURN")
        await b.act(b.right, type="ACTIVATE_UNIT", unit_id=guard.unit_id)
        return b, guard

    b, guard = asyncio.run(scenario())
    assert guard.has_moved
    assert "STUN" not in [s["name"] for s in guard.statuses]
    assert b.state.active_unit_id is None
    assert b.state.current_actor_side == "LEFT"
    assert {"t": "skip", "unit": guard.unit_id, "reason": "stun"} in b.events()


# ── events + stats ──────────────────────────────────────────────────────

def test_pull_is_reported_with_its_cause(make_battle):
    async def scenario():
        b = await make_battle(["goggins"], ["char_pyromancer"])
        b.place("right_char_pyromancer", 2)
        await b.act(b.left, type="ACTIVATE_UNIT", unit_id="left_goggins")
        await b.act(b.left, type="USE_ABILITY", ability_id="carry_log", target_unit_id="right_char_pyromancer")
        return b

    b = asyncio.run(scenario())
    events = b.events(b.right)  # the opponent sees the same feed
    assert {
        "t": "move", "unit": "right_char_pyromancer", "from": 2, "to": 1,
        "cause": "pull", "by": "left_goggins",
    } in events
    damage = [e for e in events if e["t"] == "damage" and e["dst"] == "right_char_pyromancer"]
    assert damage and damage[0]["src"] == "left_goggins"
    assert b.unit("left_goggins").stats["damage_dealt"] == sum(e["amount"] for e in damage)


def test_kill_is_credited_to_the_unit_that_landed_it(make_battle):
    async def scenario():
        b = await make_battle(["joe_goldberg"], ["char_iron_guard", "char_pyromancer"])
        joe = b.unit("left_joe_goldberg")
        b.place(joe.unit_id, 2)
        joe.current_hp = 1
        await b.act(b.left, type="ACTIVATE_UNIT", unit_id=joe.unit_id)
        await b.act(b.left, type="ATTACK", target_unit_id="right_char_iron_guard")
        return b

    b = asyncio.run(scenario())
    assert b.unit("right_char_iron_guard").stats["kills"] == 1
    assert b.unit("left_joe_goldberg").stats["deaths"] == 1
    deaths = [e for e in b.events() if e["t"] == "death"]
    assert deaths[0]["unit"] == "left_joe_goldberg"
    assert deaths[0]["killer"] == "right_char_iron_guard"
