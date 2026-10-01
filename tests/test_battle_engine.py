"""Battle rules enforced server-side: ability targeting and turn hand-off."""

import asyncio

import pytest

from app.services import effects
from conftest import ROSTER_BY_ID


def ability(char_id, ability_id):
    return next(a for a in ROSTER_BY_ID[char_id]["abilities"] if a["id"] == ability_id)


def status_names(unit):
    return [s["name"] for s in unit.statuses]


# ── targeting spec ──────────────────────────────────────────────────────

@pytest.mark.parametrize("char_id, ability_id, expected", [
    ("islam_mahachev", "throw", {"kind": "unit", "range": 1, "filter": "ENEMIES"}),
    ("goggins", "carry_log", {"kind": "unit", "range": 1, "filter": "ENEMIES"}),
    ("char_pyromancer", "flame_mark", {"kind": "unit", "range": None, "filter": "ENEMIES"}),
    ("char_shadow", "smoke_dash", {"kind": "zone", "range": 3}),
    ("elon_musk", "mars", {"kind": "zone", "range": None}),
    ("elon_musk", "neuralink", {"kind": "two_units", "range": 2, "filter": "ALLIES"}),
    ("elon_musk", "tesla", {"kind": "unit_then_zone", "range": 1, "move_range": 1, "filter": "ALLIES"}),
    ("char_priestess", "blessing", {"kind": "none"}),
    ("goggins", "hell_week", {"kind": "none"}),
])
def test_targeting_spec_matches_what_the_ui_asks_for(char_id, ability_id, expected):
    assert effects.ability_targeting(ability(char_id, ability_id)) == expected


# ── ability target validation ───────────────────────────────────────────

def test_out_of_range_ability_is_rejected_without_paying_costs(make_battle):
    async def scenario():
        b = await make_battle(["islam_mahachev"], ["char_pyromancer"])
        islam, pyro = b.unit("left_islam_mahachev"), b.unit("right_char_pyromancer")
        await b.act(b.left, type="ACTIVATE_UNIT", unit_id=islam.unit_id)
        energy = islam.current_energy

        # Pyromancer sits on the enemy backline, 3 zones away; Throw reaches 1.
        await b.act(b.left, type="USE_ABILITY", ability_id="throw", target_unit_id=pyro.unit_id)

        assert "STUN" not in status_names(pyro)
        assert islam.current_energy == energy
        assert "throw" not in islam.cooldowns
        assert not islam.has_used_ability

    asyncio.run(scenario())


def test_in_range_ability_still_resolves(make_battle):
    async def scenario():
        b = await make_battle(["islam_mahachev"], ["char_pyromancer"])
        islam, pyro = b.unit("left_islam_mahachev"), b.unit("right_char_pyromancer")
        b.place(pyro.unit_id, 2)
        await b.act(b.left, type="ACTIVATE_UNIT", unit_id=islam.unit_id)
        energy = islam.current_energy

        await b.act(b.left, type="USE_ABILITY", ability_id="throw", target_unit_id=pyro.unit_id)

        assert "STUN" in status_names(pyro)
        assert islam.current_energy == energy - 4

    asyncio.run(scenario())


def test_pull_cannot_reach_across_the_map(make_battle):
    async def scenario():
        b = await make_battle(["goggins"], ["char_pyromancer"])
        goggins, pyro = b.unit("left_goggins"), b.unit("right_char_pyromancer")
        await b.act(b.left, type="ACTIVATE_UNIT", unit_id=goggins.unit_id)

        await b.act(b.left, type="USE_ABILITY", ability_id="carry_log", target_unit_id=pyro.unit_id)
        assert pyro.zone == 4

        b.place(pyro.unit_id, 2)
        await b.act(b.left, type="USE_ABILITY", ability_id="carry_log", target_unit_id=pyro.unit_id)
        assert pyro.zone == goggins.zone

    asyncio.run(scenario())


def test_ally_only_ability_rejects_an_enemy(make_battle):
    async def scenario():
        b = await make_battle(["elon_musk", "islam_mahachev"], ["char_mountain"])
        elon, islam, mountain = (
            b.unit("left_elon_musk"), b.unit("left_islam_mahachev"), b.unit("right_char_mountain"),
        )
        # Within Tesla's Starlink-boosted cast range (1 + 1) of the enemy.
        b.place(elon.unit_id, 1)
        await b.act(b.left, type="ACTIVATE_UNIT", unit_id=elon.unit_id)

        await b.act(b.left, type="USE_ABILITY", ability_id="tesla",
                    target_unit_id=mountain.unit_id, target_zone=2)
        assert mountain.zone == 3
        assert "ISO_IMMUNE" not in status_names(mountain)
        assert "tesla" not in elon.cooldowns  # the rejected cast cost nothing

        await b.act(b.left, type="USE_ABILITY", ability_id="tesla",
                    target_unit_id=islam.unit_id, target_zone=2)
        assert islam.zone == 2

    asyncio.run(scenario())


def test_swap_needs_two_distinct_allies(make_battle):
    async def scenario():
        b = await make_battle(["elon_musk", "islam_mahachev"], ["char_mountain"])
        elon, islam, mountain = (
            b.unit("left_elon_musk"), b.unit("left_islam_mahachev"), b.unit("right_char_mountain"),
        )
        await b.act(b.left, type="ACTIVATE_UNIT", unit_id=elon.unit_id)

        await b.act(b.left, type="USE_ABILITY", ability_id="neuralink",
                    target_unit_id=islam.unit_id, target_unit_id2=mountain.unit_id)
        assert (islam.zone, mountain.zone) == (1, 3)

        await b.act(b.left, type="USE_ABILITY", ability_id="neuralink",
                    target_unit_id=islam.unit_id, target_unit_id2=islam.unit_id)
        assert islam.zone == 1

        await b.act(b.left, type="USE_ABILITY", ability_id="neuralink",
                    target_unit_id=islam.unit_id, target_unit_id2=elon.unit_id)
        assert (islam.zone, elon.zone) == (0, 1)

    asyncio.run(scenario())


@pytest.mark.parametrize("target_zone", [None, 9, -1, 1])
def test_dash_rejects_missing_invalid_or_current_zone(make_battle, target_zone):
    async def scenario():
        b = await make_battle(["char_shadow"], ["char_mountain"])
        shadow = b.unit("left_char_shadow")
        await b.act(b.left, type="ACTIVATE_UNIT", unit_id=shadow.unit_id)
        energy = shadow.current_energy

        action = {"type": "USE_ABILITY", "ability_id": "smoke_dash"}
        if target_zone is not None:
            action["target_zone"] = target_zone
        await b.act(b.left, **action)

        assert shadow.zone == 1
        assert shadow.current_energy == energy

    asyncio.run(scenario())


def test_dash_within_reach_moves(make_battle):
    async def scenario():
        b = await make_battle(["char_shadow"], ["char_mountain"])
        shadow = b.unit("left_char_shadow")
        await b.act(b.left, type="ACTIVATE_UNIT", unit_id=shadow.unit_id)
        await b.act(b.left, type="USE_ABILITY", ability_id="smoke_dash", target_zone=4)
        assert shadow.zone == 4

    asyncio.run(scenario())


def test_untargeted_ability_needs_no_target(make_battle):
    async def scenario():
        b = await make_battle(["char_priestess", "islam_mahachev"], ["char_mountain"])
        priestess, islam = b.unit("left_char_priestess"), b.unit("left_islam_mahachev")
        islam.current_hp = 50
        await b.act(b.left, type="ACTIVATE_UNIT", unit_id=priestess.unit_id)

        await b.act(b.left, type="USE_ABILITY", ability_id="blessing")

        assert islam.current_hp == 62
        assert "REGEN" in status_names(islam)

    asyncio.run(scenario())


# ── turn hand-off when the actor dies ───────────────────────────────────

def test_actor_killed_by_retaliation_ends_its_turn(make_battle):
    async def scenario():
        b = await make_battle(["joe_goldberg"], ["char_iron_guard", "char_pyromancer"])
        joe = b.unit("left_joe_goldberg")
        b.place(joe.unit_id, 2)
        joe.current_hp = 1
        await b.act(b.left, type="ACTIVATE_UNIT", unit_id=joe.unit_id)

        # Iron Guard's Spiked Armor hits back for at least 1 from the next zone.
        await b.act(b.left, type="ATTACK", target_unit_id="right_char_iron_guard")

        assert joe.is_dead
        assert b.state.active_unit_id is None
        assert b.state.current_actor_side == "RIGHT"
        assert b.state.scores["RIGHT"] == 1

    asyncio.run(scenario())


def test_dead_active_unit_can_only_end_its_turn(make_battle):
    async def scenario():
        b = await make_battle(["joe_goldberg"], ["char_iron_guard"])
        joe, guard = b.unit("left_joe_goldberg"), b.unit("right_char_iron_guard")
        b.place(joe.unit_id, 2)
        await b.act(b.left, type="ACTIVATE_UNIT", unit_id=joe.unit_id)
        joe.current_hp = 0
        b.room._process_deaths()
        guard_hp = guard.current_hp

        await b.act(b.left, type="MOVE", target_zone=3)
        await b.act(b.left, type="USE_ABILITY", ability_id="lose_in_crowd", target_zone=4)
        await b.act(b.left, type="USE_ABILITY", ability_id="social_stalking", target_unit_id=guard.unit_id)
        assert joe.zone == 2
        assert "OBSESSION" not in status_names(guard)
        assert guard.current_hp == guard_hp

        await b.act(b.left, type="END_TURN")
        assert b.state.active_unit_id is None
        assert b.state.current_actor_side == "RIGHT"

    asyncio.run(scenario())
