"""In-memory game room manager.

Phase 2 scope:
    * DRAFT_BAN / DRAFT_PICK / BATTLE / FINISHED stage machine.
    * Alternating ban (skipped if active roster < 12) and pick phases with a
      random first picker.
    * Auto-spawn picked characters onto a 5-zone battlefield based on
      `initial_position_type`. LEFT side owns zones 0/1, RIGHT owns 4/3,
      zone 2 is the empty centre.
    * BATTLE is a placeholder — units are placed and broadcast, but the
      combat engine itself lands in Phase 3+.

State is held in memory per-room; cleared when the room finishes. No combat
actions yet — `handle_action` only dispatches BAN_CHARACTER / PICK_CHARACTER
right now.

The disconnect-forfeit grace-period machinery from Phase 1 is preserved.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Set

from fastapi import WebSocket
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.user import User
from app.services import character as character_service
from app.services import effects

Side = Literal["LEFT", "RIGHT"]
Stage = Literal["DRAFT_BAN", "DRAFT_PICK", "BATTLE", "FINISHED"]

# Battlefield layout: indices 0..4. LEFT owns 0 (backline) + 1 (frontline);
# zone 2 is the empty centre; RIGHT owns 3 (frontline) + 4 (backline).
LEFT_FRONTLINE_ZONE = 1
LEFT_BACKLINE_ZONE = 0
RIGHT_FRONTLINE_ZONE = 3
RIGHT_BACKLINE_ZONE = 4
TOTAL_ZONES = 5

PICKS_PER_SIDE = 3
BANS_PER_SIDE = 3
MIN_ROSTER_FOR_BANS = 12

# Score-based win: first side to reach SCORE_TO_WIN kills wins the match.
# A kill = an enemy unit dropping to 0 HP (1 point to the killer's side).
SCORE_TO_WIN = 5
# Killed units come back at full HP/energy on their own backline this many
# rounds later (round_of_death + RESPAWN_DELAY_ROUNDS).
RESPAWN_DELAY_ROUNDS = 3

# Isolation: a unit standing in enemy-owned territory takes +2 damage and
# deals -1 damage until it leaves. Some characters are immune
# (base_stats.isolation_immune). The actual damage maths lives in effects.py;
# these names are shared so both sides agree on the status tag.
ISOLATION_STATUS = "ISOLATION"

# Grace window: how long a player has to reconnect before forfeiting, and how
# long we wait before telling the opponent the partner has gone dark.
RECONNECT_GRACE_SECONDS = 30
NOTIFY_DELAY_SECONDS = 3


# ── Player + battle data ────────────────────────────────────────────────

class PlayerSession:
    """Lightweight wrapper around a connected user. Lives for the room's
    lifetime. `websocket` is mutated on reconnect to point at the new socket.
    `score` is only consulted by `finish_game` for ELO math."""

    def __init__(self, user: User, websocket: WebSocket):
        self.user_id = str(user.id)
        self.username = user.username
        self.display_name = user.display_name
        self.avatar_url = user.avatar_url
        self.elo = user.elo
        self.websocket = websocket
        self.side: Side = "LEFT"  # set when joining a room
        self.score = 0  # forfeit / future-battle outcome, used by ELO calc


@dataclass
class UnitState:
    """Live state of a single placed character. The static blueprint lives in
    the DB (`Character` model); this dataclass is the per-match instance.

    `has_moved`     — set true once the unit has finished its activation this
                      round. Reset at round start.
    Action economy (reset at round start):
    `has_attacked`     — used the basic attack. Blocks re-attacking AND any
                         non-quick ability for the rest of this activation.
    `has_used_ability` — used at least one non-quick (slow/ult) ability. Blocks
                         attacking, but NOT further abilities — you may cast as
                         many abilities as energy/cooldowns allow. Quick
                         abilities set neither flag (they're free actions).
    `move_count`    — how many movement actions used this activation.
                      Reset at round start.
    `current_defense` / `current_regeneration` track flat base values; buffs
    will modify them once the effects engine lands in Phase 5.
    """

    unit_id: str
    char_id: str
    owner_side: Side
    current_hp: int
    max_hp: int
    current_energy: int
    max_energy: int
    current_defense: int = 0
    current_regeneration: int = 0
    cooldowns: Dict[str, int] = field(default_factory=dict)  # ability name → rounds left
    statuses: List[Dict[str, Any]] = field(default_factory=list)
    # Live aura modifiers, recomputed from the field by effects.apply_auras.
    # Keys: DAMAGE, PROC_CHANCE, ABILITY_RANGE, ABILITY_COST. Additive deltas.
    modifiers: Dict[str, int] = field(default_factory=dict)
    has_moved: bool = False
    has_attacked: bool = False
    has_used_ability: bool = False
    move_count: int = 0
    zone: int = 0
    # Death / respawn bookkeeping. `is_dead` latches on the activation that
    # drops the unit to 0 HP (so the kill is only scored once); `respawn_round`
    # is the round number on which it returns to its backline at full HP.
    is_dead: bool = False
    respawn_round: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "char_id": self.char_id,
            "owner_side": self.owner_side,
            "current_hp": self.current_hp,
            "max_hp": self.max_hp,
            "current_energy": self.current_energy,
            "max_energy": self.max_energy,
            "current_defense": self.current_defense,
            "current_regeneration": self.current_regeneration,
            "cooldowns": dict(self.cooldowns),
            "statuses": [dict(s) for s in self.statuses],
            "modifiers": dict(self.modifiers),
            "has_moved": self.has_moved,
            "has_attacked": self.has_attacked,
            "has_used_ability": self.has_used_ability,
            "move_count": self.move_count,
            "zone": self.zone,
            "is_dead": self.is_dead,
            "respawn_round": self.respawn_round,
        }


@dataclass
class DraftState:
    sub_stage: Literal["BAN", "PICK"]
    bans_enabled: bool
    bans_per_side: int
    picks_per_side: int
    first_pick_side: Side
    current_side: Side
    bans: Dict[Side, List[str]] = field(default_factory=lambda: {"LEFT": [], "RIGHT": []})
    picks: Dict[Side, List[str]] = field(default_factory=lambda: {"LEFT": [], "RIGHT": []})
    full_roster_ids: List[str] = field(default_factory=list)

    @property
    def available_pool(self) -> List[str]:
        taken: Set[str] = set(
            self.bans["LEFT"] + self.bans["RIGHT"]
            + self.picks["LEFT"] + self.picks["RIGHT"]
        )
        return [c for c in self.full_roster_ids if c not in taken]

    @property
    def is_ban_complete(self) -> bool:
        return (
            len(self.bans["LEFT"]) >= self.bans_per_side
            and len(self.bans["RIGHT"]) >= self.bans_per_side
        )

    @property
    def is_pick_complete(self) -> bool:
        return (
            len(self.picks["LEFT"]) >= self.picks_per_side
            and len(self.picks["RIGHT"]) >= self.picks_per_side
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sub_stage": self.sub_stage,
            "bans_enabled": self.bans_enabled,
            "bans_per_side": self.bans_per_side,
            "picks_per_side": self.picks_per_side,
            "first_pick_side": self.first_pick_side,
            "current_side": self.current_side,
            "bans": {"LEFT": list(self.bans["LEFT"]), "RIGHT": list(self.bans["RIGHT"])},
            "picks": {"LEFT": list(self.picks["LEFT"]), "RIGHT": list(self.picks["RIGHT"])},
            "available_pool": self.available_pool,
        }


@dataclass
class BattleState:
    """Live battle: 5 zones, units indexed by id, whose-turn pointers."""

    zones: List[List[str]]  # 5 lists of unit_ids
    units: Dict[str, UnitState]
    current_round: int
    current_actor_side: Side
    # Currently activated unit (clicked by its owner to start their micro-turn)
    # or None if the actor hasn't selected a unit yet.
    active_unit_id: Optional[str] = None
    # Kill scoreboard. First side to SCORE_TO_WIN wins the match.
    scores: Dict[Side, int] = field(default_factory=lambda: {"LEFT": 0, "RIGHT": 0})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "zones": [list(z) for z in self.zones],
            "units": {uid: u.to_dict() for uid, u in self.units.items()},
            "current_round": self.current_round,
            "current_actor_side": self.current_actor_side,
            "active_unit_id": self.active_unit_id,
            "scores": {"LEFT": self.scores["LEFT"], "RIGHT": self.scores["RIGHT"]},
            "score_to_win": SCORE_TO_WIN,
        }


# ── GameRoom ────────────────────────────────────────────────────────────

class GameRoom:
    def __init__(self, room_id: str, player1: PlayerSession, player2: PlayerSession):
        self.room_id = room_id
        self.player1 = player1
        self.player2 = player2
        self.player1.side = "LEFT"
        self.player2.side = "RIGHT"

        self.stage: Stage = "DRAFT_BAN"  # may be flipped to DRAFT_PICK after init
        self.draft: Optional[DraftState] = None
        self.battle: Optional[BattleState] = None
        # Per-room cache of character definitions. Populated in begin_battle so
        # the action handlers don't have to round-trip to the DB on every click.
        self.character_cache: Dict[str, Dict[str, Any]] = {}
        # Transient log of passives that fired since the last broadcast. Flushed
        # to clients as a PASSIVE_PROC message so the UI can flash them.
        self._proc_log: List[Dict[str, Any]] = []

    # ── routing helpers ────────────────────────────────────────────────
    def get_player_by_side(self, side: Side) -> Optional[PlayerSession]:
        if self.player1.side == side:
            return self.player1
        if self.player2.side == side:
            return self.player2
        return None

    def get_player_by_id(self, user_id: str) -> Optional[PlayerSession]:
        if self.player1.user_id == user_id:
            return self.player1
        if self.player2.user_id == user_id:
            return self.player2
        return None

    def get_opponent(self, player: PlayerSession) -> PlayerSession:
        return self.player2 if player.user_id == self.player1.user_id else self.player1

    async def broadcast(self, message: Dict[str, Any]):
        for p in (self.player1, self.player2):
            try:
                await p.websocket.send_json(message)
            except Exception:
                # The other socket may have died; matchmaker / disconnect
                # bookkeeping handles cleanup elsewhere. Don't let one bad
                # socket prevent the other from receiving the update.
                pass

    def _players_payload(self) -> Dict[str, Any]:
        return {
            "LEFT": {
                "user_id": self.player1.user_id,
                "display_name": self.player1.display_name,
                "avatar_url": self.player1.avatar_url,
                "elo": self.player1.elo,
            },
            "RIGHT": {
                "user_id": self.player2.user_id,
                "display_name": self.player2.display_name,
                "avatar_url": self.player2.avatar_url,
                "elo": self.player2.elo,
            },
        }

    # ── draft lifecycle ────────────────────────────────────────────────
    async def begin_draft(self, db: AsyncSession):
        roster_size = await character_service.count_active_characters(db)
        bans_enabled = roster_size >= MIN_ROSTER_FOR_BANS

        all_active = await character_service.list_characters(db, active_only=True)
        full_roster_ids = [c.id for c in all_active]

        # Random first picker / first banner — both phases use the same order.
        first_side: Side = random.choice(("LEFT", "RIGHT"))

        self.draft = DraftState(
            sub_stage="BAN" if bans_enabled else "PICK",
            bans_enabled=bans_enabled,
            bans_per_side=BANS_PER_SIDE if bans_enabled else 0,
            picks_per_side=PICKS_PER_SIDE,
            first_pick_side=first_side,
            current_side=first_side,
            full_roster_ids=full_roster_ids,
        )
        self.stage = "DRAFT_BAN" if bans_enabled else "DRAFT_PICK"

    async def begin_battle(self, db: AsyncSession):
        """Spawn picked characters onto the field. Called when picks complete."""
        assert self.draft is not None and self.draft.is_pick_complete

        all_picks: List[str] = self.draft.picks["LEFT"] + self.draft.picks["RIGHT"]
        chars = await character_service.list_by_ids(db, all_picks)
        chars_by_id = {c.id: c for c in chars}

        zones: List[List[str]] = [[] for _ in range(TOTAL_ZONES)]
        units: Dict[str, UnitState] = {}

        for side in ("LEFT", "RIGHT"):
            for pick_id in self.draft.picks[side]:
                char = chars_by_id.get(pick_id)
                if char is None:
                    # Character disappeared between pick and battle start —
                    # extremely unlikely but bail rather than silently skip.
                    raise RuntimeError(f"Picked character {pick_id!r} no longer exists")

                position_type = char.initial_position_type
                if side == "LEFT":
                    zone = LEFT_FRONTLINE_ZONE if position_type == "FRONTLINE" else LEFT_BACKLINE_ZONE
                else:
                    zone = RIGHT_FRONTLINE_ZONE if position_type == "FRONTLINE" else RIGHT_BACKLINE_ZONE

                stats = char.base_stats
                unit_id = f"{side.lower()}_{pick_id}"
                units[unit_id] = UnitState(
                    unit_id=unit_id,
                    char_id=pick_id,
                    owner_side=side,
                    current_hp=int(stats["hp"]),
                    max_hp=int(stats["hp"]),
                    # Spec: "energy is filled per the character's rules" — start
                    # at max so abilities are reachable on turn 1.
                    current_energy=int(stats["max_energy"]),
                    max_energy=int(stats["max_energy"]),
                    current_defense=int(stats.get("defense", 0)),
                    current_regeneration=int(stats.get("regeneration", 0)),
                    has_moved=False,
                    zone=zone,
                )
                zones[zone].append(unit_id)

                # Cache the static blueprint dict — handlers read it sync.
                self.character_cache[pick_id] = {
                    "base_stats": dict(char.base_stats),
                    "attack": dict(char.attack),
                    "abilities": [dict(a) for a in (char.abilities or [])],
                    "passives": [dict(p) for p in (char.passives or [])],
                }

        self.battle = BattleState(
            zones=zones,
            units=units,
            current_round=1,
            # First picker also takes the first micro-turn.
            current_actor_side=self.draft.first_pick_side,
        )
        self.stage = "BATTLE"
        # Seed isolation + aura modifiers for the opening turn.
        self._recompute_board()

    # ── action handling ────────────────────────────────────────────────
    async def handle_action(self, user_id: str, action: Dict[str, Any], db: AsyncSession):
        player = self.get_player_by_id(user_id)
        if not player:
            return

        action_type = action.get("type")

        # Inspection telegraph: relay which unit a player is looking at to the
        # opponent (works in any stage; purely cosmetic awareness).
        if action_type == "INSPECT":
            opp = self.get_opponent(player)
            try:
                await opp.websocket.send_json({
                    "type": "OPPONENT_INSPECT",
                    "unit_id": action.get("unit_id"),
                })
            except Exception:
                pass
            return

        if self.stage == "DRAFT_BAN":
            if action_type == "BAN_CHARACTER":
                await self._handle_ban(player, action, db)
            return

        if self.stage == "DRAFT_PICK":
            if action_type == "PICK_CHARACTER":
                await self._handle_pick(player, action, db)
            return

        if self.stage == "BATTLE":
            await self._handle_battle_action(player, action, db)
            return

    # ── battle action handling ─────────────────────────────────────────
    async def _handle_battle_action(
        self, player: PlayerSession, action: Dict[str, Any], db: AsyncSession,
    ):
        battle = self.battle
        if battle is None:
            return

        action_type = action.get("type")

        # The actor side must match the player. We let off-turn clicks be
        # silently dropped — UI prevents them, this is the defensive layer.
        if battle.current_actor_side != player.side:
            return

        if action_type == "ACTIVATE_UNIT":
            await self._battle_activate(player, action, db)
        elif action_type == "MOVE":
            await self._battle_move(player, action, db)
        elif action_type == "ATTACK":
            await self._battle_attack(player, action, db)
        elif action_type == "USE_ABILITY":
            await self._battle_use_ability(player, action, db)
        elif action_type == "END_TURN":
            await self._battle_end_turn(player, db)

    async def _battle_activate(
        self, player: PlayerSession, action: Dict[str, Any], db: AsyncSession,
    ):
        battle = self.battle
        assert battle is not None

        # Can't pick a new unit while another is in the middle of their turn.
        if battle.active_unit_id is not None:
            return

        unit_id = action.get("unit_id")
        unit = battle.units.get(unit_id) if isinstance(unit_id, str) else None
        if not unit:
            return
        if unit.owner_side != player.side:
            return
        if unit.has_moved or unit.current_hp <= 0:
            return

        # Run the activation tick: cooldowns, energy regen, HP regen, ticks.
        self._apply_activation_tick(unit)

        # Activation tick may kill the unit (poison). Score the death, then
        # end its turn quietly — or finish the match if the kill clinched it.
        if unit.current_hp <= 0:
            self._process_deaths()
            await self.broadcast_state()
            if self._score_reached():
                await self._end_game_combat(db)
                return
            await self._after_unit_done(unit)
            return

        # ON_TURN_START passives (energy-per-stack, Hell Week AoE / auto-cast).
        # Fire on the unit beginning its activation — distinct from round start.
        await self._fire_passive(unit, "ON_TURN_START", main_target=unit)
        self._process_deaths()      # turn-start AoE may have killed
        self._recompute_board()
        if self._score_reached():
            await self.broadcast_state()
            await self._end_game_combat(db)
            return

        battle.active_unit_id = unit.unit_id
        await self.broadcast_state()

    async def _battle_move(
        self, player: PlayerSession, action: Dict[str, Any], db: AsyncSession,
    ):
        battle = self.battle
        assert battle is not None

        unit = self._owned_active_unit(player)
        if unit is None:
            return
        if self._is_stunned(unit):
            return
        if unit.move_count >= 1:
            return  # one movement action per micro-turn (no movement boosts yet)

        target_zone = action.get("target_zone")
        if not isinstance(target_zone, int):
            return
        if not self._is_valid_move(unit, target_zone):
            return

        # Apply move
        battle.zones[unit.zone].remove(unit.unit_id)
        unit.zone = target_zone
        battle.zones[target_zone].append(unit.unit_id)
        unit.move_count += 1

        # Stepping onto / off enemy territory toggles isolation; positions also
        # feed range-based auras.
        self._recompute_board()
        # ON_ZONE_CHANGE passives (e.g. Pyromancer's poison trail).
        await self._fire_zone_change([unit])
        self._process_deaths()
        self._recompute_board()
        await self.broadcast_state()
        if self._score_reached():
            return await self._end_game_combat(db)

    async def _battle_attack(
        self, player: PlayerSession, action: Dict[str, Any], db: AsyncSession,
    ):
        battle = self.battle
        assert battle is not None

        attacker = self._owned_active_unit(player)
        if attacker is None:
            return
        if self._is_stunned(attacker):
            return
        # Attack is mutually exclusive with abilities: can't attack twice, and
        # can't attack once a (non-quick) ability has been used this activation.
        if attacker.has_attacked or attacker.has_used_ability:
            return

        target_id = action.get("target_unit_id")
        target = battle.units.get(target_id) if isinstance(target_id, str) else None
        if not target:
            return
        if target.owner_side == player.side:
            return  # no friendly-fire
        if target.current_hp <= 0:
            return
        if not self._is_in_attack_range(attacker, target):
            return

        char = self.character_cache.get(attacker.char_id)
        if char is None:
            return

        # Synthesise a DAMAGE step from the attack's damage_schema so the
        # engine handles base damage + the rest of execution_chain uniformly.
        attack_def = char["attack"]
        base_val = (attack_def.get("damage_schema") or {}).get("value", 0)
        # Attack-only damage buff (Hell Week's +18) — applies to the basic attack
        # but NOT to abilities or AoE ticks.
        atk_buff = sum(
            int(s.get("value", 0))
            for s in attacker.statuses
            if s.get("name") == "BUFF_ATTACK_DAMAGE"
        )
        if isinstance(base_val, int) and atk_buff:
            base_val = base_val + atk_buff
        chain: List[Dict[str, Any]] = [{
            "type": "DAMAGE",
            "target_selector": {"type": "MAIN_TARGET"},
            "value": base_val,
            "damage_type": attack_def.get("damage_type", "PHYSICAL"),
        }]
        chain.extend(attack_def.get("execution_chain") or [])

        ctx = effects.ExecutionContext(
            actor=attacker,
            main_target=target,
            attacker=attacker,
            attacker_range=int(attack_def.get("range", 1)),
            is_owner_turn=True,
        )
        await effects.execute_chain(chain, ctx, self)
        attacker.has_attacked = True

        # Reactive passives: retaliation, on-deal bonuses, after-action proc.
        await self._dispatch_action_passives(ctx, attacker)

        # A unit (the attacker via retaliation, or a target) may have died.
        self._process_deaths()
        self._recompute_board()
        await self.broadcast_state()
        await self.broadcast({"type": "ACTION_FX", "unit_id": attacker.unit_id, "kind": "attack"})

        if self._score_reached():
            await self._end_game_combat(db)
            return

    async def _battle_use_ability(
        self, player: PlayerSession, action: Dict[str, Any], db: AsyncSession,
    ):
        """Run the actor's ability with full validation.

        action: {type: USE_ABILITY, target_unit_id?: str, target_zone?: int}
        Engine handles target / zone validation via the selector definitions
        in the chain; we only enforce the cross-cutting rules here (energy,
        cooldown, action-token gating, stun-block)."""
        battle = self.battle
        assert battle is not None

        actor = self._owned_active_unit(player)
        if actor is None:
            return
        if self._is_stunned(actor):
            return

        char = self.character_cache.get(actor.char_id)
        if char is None:
            return
        # Pick the requested ability from the kit by its stable id; fall back to
        # the first ability if the client didn't specify one.
        abilities = char.get("abilities") or []
        wanted = action.get("ability_id")
        ability = None
        if isinstance(wanted, str):
            ability = next((a for a in abilities if a.get("id") == wanted), None)
        if ability is None:
            ability = abilities[0] if abilities else None
        if not ability:
            return
        # Cooldowns are keyed by ability id (stable across renames / dup names).
        ab_key = ability.get("id") or ability.get("name") or "ability"
        # Effective cost folds in the ABILITY_COST aura modifier (floored at 0).
        cost = max(0, int(ability.get("energy_cost", 0)) + actor.modifiers.get("ABILITY_COST", 0))
        cd = int(ability.get("cooldown", 0))
        is_quick = bool(ability.get("is_quick", False))

        # Resource / timing gates.
        if actor.current_energy < cost:
            return
        if actor.cooldowns.get(ab_key, 0) > 0:
            return
        # Non-quick (slow / ult) abilities are blocked once you've attacked this
        # activation — but NOT by each other: you may cast as many as energy and
        # cooldowns permit. Quick abilities are always free.
        if not is_quick and actor.has_attacked:
            return

        # Resolve client-supplied targets, if any.
        custom_target = None
        custom_target2 = None
        custom_zone = None
        target_id = action.get("target_unit_id")
        if isinstance(target_id, str):
            custom_target = battle.units.get(target_id)
        target_id2 = action.get("target_unit_id2")
        if isinstance(target_id2, str):
            custom_target2 = battle.units.get(target_id2)
        if isinstance(action.get("target_zone"), int):
            custom_zone = int(action["target_zone"])

        # Spend the ability's costs up front so a chain that loops back
        # somewhere can't double-charge.
        actor.current_energy = max(0, actor.current_energy - cost)
        if cd > 0:
            actor.cooldowns[ab_key] = cd
        if not is_quick:
            actor.has_used_ability = True

        ctx = effects.ExecutionContext(
            actor=actor,
            main_target=custom_target,  # used by MAIN_TARGET and SAME_ZONE selectors
            custom_target=custom_target,
            custom_target2=custom_target2,
            custom_zone=custom_zone,
            # Starlink & friends widen every range-based selector in this chain.
            range_bonus=actor.modifiers.get("ABILITY_RANGE", 0),
            is_owner_turn=True,
        )
        await effects.execute_chain(ability.get("execution_chain") or [], ctx, self)

        # Reactive passives provoked by the ability's damage.
        await self._dispatch_action_passives(ctx, actor)
        # ON_ZONE_CHANGE for anyone the ability relocated (actor dash / Tesla).
        await self._fire_zone_change(ctx.moved_units)

        # Ability may have moved the actor (Smoke Dash) or killed someone.
        self._process_deaths()
        self._recompute_board()
        await self.broadcast_state()
        await self.broadcast({
            "type": "ACTION_FX", "unit_id": actor.unit_id,
            "kind": "ability", "ability_id": ab_key,
        })

        if self._score_reached():
            await self._end_game_combat(db)
            return

    async def _battle_end_turn(self, player: PlayerSession, db: AsyncSession):
        battle = self.battle
        assert battle is not None
        unit = self._owned_active_unit(player)
        if unit is None:
            return
        unit.has_moved = True
        await self.broadcast_state()
        await self._after_unit_done(unit)

    # ── battle helpers ────────────────────────────────────────────────
    def _owned_active_unit(self, player: PlayerSession) -> Optional[UnitState]:
        battle = self.battle
        if battle is None or battle.active_unit_id is None:
            return None
        unit = battle.units.get(battle.active_unit_id)
        if unit is None or unit.owner_side != player.side:
            return None
        return unit

    @staticmethod
    def _opposite_side(side: Side) -> Side:
        return "RIGHT" if side == "LEFT" else "LEFT"

    @staticmethod
    def _is_stunned(unit: UnitState) -> bool:
        # Debuff immunity (Hell Week) suppresses an active stun.
        if any(s.get("name") == "DEBUFF_IMMUNE" for s in unit.statuses):
            return False
        return any(s.get("name") == "STUN" for s in unit.statuses)

    def _is_valid_move(self, unit: UnitState, target_zone: int) -> bool:
        if target_zone < 0 or target_zone >= TOTAL_ZONES:
            return False
        if target_zone == unit.zone:
            return False
        char = self.character_cache.get(unit.char_id)
        if char is None:
            return False
        movement_range = int(char["base_stats"].get("movement_range", 1))
        return abs(target_zone - unit.zone) <= movement_range

    def _is_in_attack_range(self, attacker: UnitState, target: UnitState) -> bool:
        char = self.character_cache.get(attacker.char_id)
        if char is None:
            return False
        attack_range = int(char["attack"].get("range", 1))
        return abs(target.zone - attacker.zone) <= attack_range

    def _apply_activation_tick(self, unit: UnitState):
        """On activation: ability cooldowns tick down, then energy regen, HP
        regen and POISON ticks — all happen when the unit is selected, not at
        round start (so cooldown/energy/HP all advance on the same beat).

        Status durations are NOT decremented here — they tick down once the unit
        finishes its turn (`_decrement_status_durations`). This keeps STUN
        active for the entire activation it was meant to lock.
        """
        # Cooldowns recharge on activation (same beat as energy/HP regen).
        for ability_name, cd in list(unit.cooldowns.items()):
            if cd > 0:
                unit.cooldowns[ability_name] = cd - 1

        char = self.character_cache.get(unit.char_id)
        if char is not None:
            stats = char["base_stats"]
            unit.current_energy = min(
                unit.max_energy,
                unit.current_energy + int(stats.get("energy_regen", 0)),
            )

        # HP regen: `current_regeneration` already sums base regen + REGEN +
        # BUFF_REGENERATION (see recompute_derived), so a single heal covers all.
        if unit.current_regeneration > 0 and unit.current_hp > 0:
            unit.current_hp = min(
                unit.max_hp, unit.current_hp + unit.current_regeneration,
            )

        # POISON ticks as damage — unless the unit is immune to debuffs.
        immune = any(s.get("name") == "DEBUFF_IMMUNE" for s in unit.statuses)
        if not immune:
            for status in unit.statuses:
                if status.get("name") == "POISON":
                    value = int(status.get("value", 0) or 0)
                    if value > 0:
                        unit.current_hp = max(0, unit.current_hp - value)

    def _decrement_status_durations(self, unit: UnitState):
        """Called after a unit's micro-turn completes. -1 to every duration;
        durations of -1 are permanent and pass through untouched. Recomputes
        derived stats so expired BUFF_* statuses drop their bonus."""
        remaining: List[Dict[str, Any]] = []
        for status in unit.statuses:
            duration = status.get("duration", 1)
            if duration is None or int(duration) < 0:
                remaining.append(dict(status))
                continue
            # A self-buff applied THIS turn skips its first tick — clear the
            # flag and keep full duration so its clock starts next turn.
            if status.get("fresh"):
                new_status = dict(status)
                new_status.pop("fresh", None)
                remaining.append(new_status)
                continue
            new_duration = int(duration) - 1
            if new_duration > 0:
                new_status = dict(status)
                new_status["duration"] = new_duration
                remaining.append(new_status)
            # else: expired, drop
        unit.statuses = remaining
        effects.recompute_derived(unit, self.character_cache)
        # Refresh aura/self-status modifiers (e.g. an expired BUFF_DAMAGE must
        # stop boosting damage immediately).
        effects.apply_auras(self)

    async def _after_unit_done(self, finished_unit: UnitState):
        """Hand the turn over after a unit finished (whether by END_TURN or by
        dying mid-activation). Status durations tick here, so effects that
        were meant to last "this activation" are gone by the next one."""
        battle = self.battle
        if battle is None:
            return

        self._decrement_status_durations(finished_unit)
        battle.active_unit_id = None

        # Look for the next actor. Strict alternation by default, but if the
        # other side has nothing left this round, stay on the current one.
        current = battle.current_actor_side
        other = self._opposite_side(current)

        if self._has_available_unit(other):
            battle.current_actor_side = other
        elif self._has_available_unit(current):
            pass  # other side is done for the round, current keeps going
        else:
            # Everyone has acted (or is dead) → next round. If both sides are
            # fully dead and waiting to respawn, keep advancing rounds until a
            # respawn restores a playable unit (bounded by RESPAWN_DELAY_ROUNDS,
            # so it always terminates).
            await self._start_new_round()
            while (
                not self._has_available_unit("LEFT")
                and not self._has_available_unit("RIGHT")
            ):
                await self._start_new_round()

        await self.broadcast_state()

    def _has_available_unit(self, side: Side) -> bool:
        if self.battle is None:
            return False
        return any(
            u.owner_side == side and not u.has_moved and u.current_hp > 0
            for u in self.battle.units.values()
        )

    # ── scoring / death / respawn ──────────────────────────────────────
    def _process_deaths(self) -> None:
        """Latch newly-killed units, award a kill point to the opposing side,
        and schedule each corpse's respawn. Idempotent: a unit is only scored
        on the activation it actually drops to 0 HP (`is_dead` guards it)."""
        battle = self.battle
        if battle is None:
            return
        for unit in battle.units.values():
            if unit.current_hp <= 0 and not unit.is_dead:
                unit.is_dead = True
                unit.has_moved = True       # corpses can't be activated
                unit.has_attacked = True
                unit.has_used_ability = True
                unit.respawn_round = battle.current_round + RESPAWN_DELAY_ROUNDS
                # Drop every status (incl. ISOLATION) so the corpse doesn't
                # keep ticking poison or carrying buffs while it waits to respawn.
                unit.statuses = []
                killer_side = self._opposite_side(unit.owner_side)
                battle.scores[killer_side] += 1

    def _score_reached(self) -> bool:
        battle = self.battle
        if battle is None:
            return False
        return (
            battle.scores["LEFT"] >= SCORE_TO_WIN
            or battle.scores["RIGHT"] >= SCORE_TO_WIN
        )

    def _respawn_unit(self, unit: UnitState) -> None:
        """Bring a dead unit back at full HP/energy on its own backline."""
        battle = self.battle
        if battle is None:
            return
        home = LEFT_BACKLINE_ZONE if unit.owner_side == "LEFT" else RIGHT_BACKLINE_ZONE
        if unit.unit_id in battle.zones[unit.zone]:
            battle.zones[unit.zone].remove(unit.unit_id)
        unit.zone = home
        battle.zones[home].append(unit.unit_id)

        unit.current_hp = unit.max_hp
        unit.current_energy = unit.max_energy
        unit.statuses = []
        unit.cooldowns = {}
        unit.is_dead = False
        unit.respawn_round = None
        unit.has_moved = False
        unit.has_attacked = False
        unit.has_used_ability = False
        unit.move_count = 0
        effects.recompute_derived(unit, self.character_cache)

    # ── isolation ──────────────────────────────────────────────────────
    def _in_enemy_territory(self, unit: UnitState) -> bool:
        """True when the unit stands on a zone owned by the other side. The
        neutral centre (zone 2) is never enemy territory."""
        if unit.owner_side == "LEFT":
            return unit.zone in (RIGHT_FRONTLINE_ZONE, RIGHT_BACKLINE_ZONE)
        return unit.zone in (LEFT_FRONTLINE_ZONE, LEFT_BACKLINE_ZONE)

    def _is_isolation_immune(self, unit: UnitState) -> bool:
        # Temporary immunity (Tesla passenger) or innate (assassin base stat).
        if any(s.get("name") == "ISO_IMMUNE" for s in unit.statuses):
            return True
        char = self.character_cache.get(unit.char_id)
        if not char:
            return False
        return bool((char.get("base_stats") or {}).get("isolation_immune", False))

    def _refresh_isolation(self) -> None:
        """Add/remove the ISOLATION status on every living unit based on its
        current zone. Called after any movement / respawn. Immune characters
        (base_stats.isolation_immune) never gain it."""
        battle = self.battle
        if battle is None:
            return
        for unit in battle.units.values():
            has = any(s.get("name") == ISOLATION_STATUS for s in unit.statuses)
            should = (
                unit.current_hp > 0
                and not unit.is_dead
                and self._in_enemy_territory(unit)
                and not self._is_isolation_immune(unit)
            )
            if should and not has:
                unit.statuses.append(
                    {"name": ISOLATION_STATUS, "value": 0, "duration": -1},
                )
            elif has and not should:
                unit.statuses = [
                    s for s in unit.statuses if s.get("name") != ISOLATION_STATUS
                ]

    def _recompute_board(self) -> None:
        """Refresh every position-derived value at once: isolation debuffs and
        aura modifiers. Call after any move / death / respawn / round start."""
        self._refresh_isolation()
        effects.apply_auras(self)

    # ── passive dispatch ───────────────────────────────────────────────
    async def _fire_passive(
        self,
        unit: UnitState,
        trigger: str,
        *,
        attacker: Optional[UnitState] = None,
        attacker_range: Optional[int] = None,
        main_target: Optional[UnitState] = None,
        hit_targets: Optional[List[UnitState]] = None,
    ) -> None:
        """Run `unit`'s passive if it matches `trigger` and its gate conditions
        pass. Passive chains run with triggers_passives=False so the damage they
        deal can't recurse back into the dispatcher."""
        if unit.current_hp <= 0 or unit.is_dead:
            return
        # A character can carry several passives; fire every one matching the
        # trigger whose gate conditions pass.
        for passive in (self.character_cache.get(unit.char_id) or {}).get("passives") or []:
            if passive.get("trigger_event") != trigger:
                continue
            ctx = effects.ExecutionContext(
                actor=unit,
                attacker=attacker,
                attacker_range=attacker_range,
                main_target=main_target,
                triggers_passives=False,
            )
            ctx.hit_targets = list(hit_targets or [])
            if not effects.evaluate_conditions(
                passive.get("conditions") or [], ctx, self.character_cache,
            ):
                continue
            # Only flash the proc if the chain actually changed something — a
            # no-op pass (e.g. +energy-per-stack with 0 stacks) shouldn't show.
            before = self._battle_fingerprint()
            await effects.execute_chain(passive.get("execution_chain") or [], ctx, self)
            if self._battle_fingerprint() != before:
                self._proc_log.append({
                    "unit_id": unit.unit_id,
                    "id": passive.get("id"),
                    "name": passive.get("name") or "Пассивка",
                    "trigger": trigger,
                })

    def _battle_fingerprint(self):
        """Cheap snapshot of mutable battle state — used to detect whether a
        passive actually did anything (hp / energy / zone / statuses / cd)."""
        battle = self.battle
        if battle is None:
            return None
        return tuple(
            (
                u.current_hp, u.current_energy, u.zone, len(u.statuses),
                sum(int(s.get("value", 0) or 0) for s in u.statuses),
                tuple(sorted(u.cooldowns.items())),
            )
            for u in battle.units.values()
        )

    async def _dispatch_action_passives(
        self, ctx: "effects.ExecutionContext", actor: UnitState,
    ) -> None:
        """After a primary attack/ability resolves, fire the reactive passives
        it provoked: each victim's BEFORE_TAKE_DAMAGE (retaliation), the actor's
        ON_DEAL_DAMAGE per enemy hit, and a single AFTER_ACTION (Islam's proc)
        carrying every enemy struck."""
        events = list(ctx.damage_events)
        if not events:
            return

        # Retaliation — once per distinct victim still standing.
        seen: Set[str] = set()
        for ev in events:
            victim: UnitState = ev["target"]
            if victim.unit_id in seen:
                continue
            seen.add(victim.unit_id)
            if victim.current_hp > 0:
                await self._fire_passive(
                    victim, "BEFORE_TAKE_DAMAGE",
                    attacker=actor, attacker_range=ev["source_range"],
                    main_target=actor,
                )

        # On-deal bonuses — once per enemy victim.
        for ev in events:
            victim = ev["target"]
            if victim.owner_side == actor.owner_side:
                continue
            await self._fire_passive(
                actor, "ON_DEAL_DAMAGE",
                main_target=victim, attacker_range=ev["source_range"],
            )

        # After-action proc — single roll, splashes onto every enemy hit.
        enemies_hit = [
            ev["target"] for ev in events
            if ev["target"].owner_side != actor.owner_side
        ]
        # De-dup while preserving order.
        uniq_enemies: List[UnitState] = []
        seen_e: Set[str] = set()
        for e in enemies_hit:
            if e.unit_id not in seen_e:
                seen_e.add(e.unit_id)
                uniq_enemies.append(e)
        if uniq_enemies:
            await self._fire_passive(
                actor, "AFTER_ACTION",
                hit_targets=uniq_enemies, main_target=uniq_enemies[0],
            )

    async def _fire_zone_change(self, moved: List[UnitState]) -> None:
        """ON_ZONE_CHANGE passives for every unit a chain relocated."""
        for unit in moved:
            await self._fire_passive(unit, "ON_ZONE_CHANGE", main_target=unit)

    async def _start_new_round(self):
        battle = self.battle
        if battle is None:
            return
        battle.current_round += 1
        for u in battle.units.values():
            if u.is_dead:
                # Respawn if its timer is up; otherwise the corpse waits
                # (flags already frozen by _process_deaths).
                if u.respawn_round is not None and u.respawn_round <= battle.current_round:
                    self._respawn_unit(u)
                continue
            u.has_moved = False
            u.has_attacked = False
            u.has_used_ability = False
            u.move_count = 0
            # Cooldowns now tick on activation (see _apply_activation_tick),
            # not here — keeps them in step with energy/HP regen.
        # Respawns reshuffle the board, so recompute isolation + auras before
        # round-start passives read their state.
        self._recompute_board()
        # First picker of the draft opens each round — but if that side has no
        # living unit to act (all dead, awaiting respawn) hand the open to the
        # other side so the round can actually progress.
        opener: Side = self.draft.first_pick_side if self.draft else battle.current_actor_side
        if not self._has_available_unit(opener) and self._has_available_unit(self._opposite_side(opener)):
            opener = self._opposite_side(opener)
        battle.current_actor_side = opener

        # ON_ROUND_START passives (energy auras, conditional self-buffs,
        # Hell Week auto-cast + AoE). Fire for living units only; snapshot the
        # list to guard against a passive mutating units mid-iteration.
        for u in list(battle.units.values()):
            await self._fire_passive(u, "ON_ROUND_START", main_target=u)
        # Round-start damage (e.g. Hell Week AoE) may kill — record deaths/score
        # now; the win is finalised on the next action's score check.
        self._process_deaths()
        # A round-start passive could reposition/score; keep the board coherent.
        self._recompute_board()

    async def _end_game_combat(self, db: AsyncSession):
        """Hand the final kill scores to the players so finish_game's ELO maths
        (which compares score_a vs score_b) picks the side with more kills."""
        battle = self.battle
        if battle is not None:
            self.player1.score = battle.scores["LEFT"]   # player1 == LEFT
            self.player2.score = battle.scores["RIGHT"]  # player2 == RIGHT
        await game_manager.finish_game(self, db)

    async def _handle_ban(self, player: PlayerSession, action: Dict[str, Any], db: AsyncSession):
        draft = self.draft
        assert draft is not None
        if draft.current_side != player.side:
            return
        char_id = action.get("char_id")
        if not isinstance(char_id, str):
            return
        if char_id not in draft.available_pool:
            return
        if len(draft.bans[player.side]) >= draft.bans_per_side:
            return

        draft.bans[player.side].append(char_id)

        if draft.is_ban_complete:
            # Move into pick phase, first picker starts.
            self.stage = "DRAFT_PICK"
            draft.sub_stage = "PICK"
            draft.current_side = draft.first_pick_side
        else:
            draft.current_side = "RIGHT" if player.side == "LEFT" else "LEFT"

        await self.broadcast_state()

    async def _handle_pick(self, player: PlayerSession, action: Dict[str, Any], db: AsyncSession):
        draft = self.draft
        assert draft is not None
        if draft.current_side != player.side:
            return
        char_id = action.get("char_id")
        if not isinstance(char_id, str):
            return
        if char_id not in draft.available_pool:
            return
        if len(draft.picks[player.side]) >= draft.picks_per_side:
            return

        draft.picks[player.side].append(char_id)

        if draft.is_pick_complete:
            await self.begin_battle(db)
            await self.broadcast_state()
        else:
            draft.current_side = "RIGHT" if player.side == "LEFT" else "LEFT"
            await self.broadcast_state()

    # ── outgoing state ─────────────────────────────────────────────────
    def state_payload(self, *, is_reconnect: bool = False) -> Dict[str, Any]:
        """Single shape for any stage. Frontend dispatches on `stage`."""
        return {
            "type": "RECONNECT" if is_reconnect else "ROOM_STATE",
            "room_id": self.room_id,
            "stage": self.stage,
            "players": self._players_payload(),
            "draft": self.draft.to_dict() if self.draft else None,
            "battle": self.battle.to_dict() if self.battle else None,
        }

    async def broadcast_state(self):
        await self.broadcast(self.state_payload(is_reconnect=False))
        # Flush any passive procs that fired while resolving this update so the
        # UI can flash them on top of the fresh state.
        if self._proc_log:
            procs = self._proc_log
            self._proc_log = []
            await self.broadcast({"type": "PASSIVE_PROC", "procs": procs})

    async def send_state_to(self, websocket: WebSocket, *, is_reconnect: bool):
        try:
            await websocket.send_json(self.state_payload(is_reconnect=is_reconnect))
        except Exception:
            pass


# ── GameManager ─────────────────────────────────────────────────────────

class GameManager:
    def __init__(self):
        self.active_rooms: Dict[str, GameRoom] = {}
        self.player_to_room: Dict[str, str] = {}
        self.pending_forfeits: Dict[str, asyncio.Task] = {}
        self.notified_disconnects: Set[str] = set()

    # ── creation / lookup ──────────────────────────────────────────────
    async def create_game(
        self,
        p1: PlayerSession,
        p2: PlayerSession,
        db: AsyncSession,
    ) -> GameRoom:
        room_id = str(uuid.uuid4())
        room = GameRoom(room_id, p1, p2)
        self.active_rooms[room_id] = room
        self.player_to_room[p1.user_id] = room_id
        self.player_to_room[p2.user_id] = room_id

        await room.begin_draft(db)

        # First message: announce the match. Then a state snapshot.
        await room.broadcast({
            "type": "GAME_FOUND",
            "room_id": room_id,
            "stage": room.stage,
            "players": room._players_payload(),
        })
        await room.broadcast_state()
        return room

    def get_room_for(self, user_id: str) -> Optional[GameRoom]:
        room_id = self.player_to_room.get(user_id)
        if not room_id:
            return None
        return self.active_rooms.get(room_id)

    # ── forfeit / reconnect helpers (unchanged from Phase 1) ───────────
    def cancel_pending_forfeit(self, user_id: str) -> bool:
        task = self.pending_forfeits.pop(user_id, None)
        if task and not task.done():
            task.cancel()
            return True
        return False

    def consume_disconnect_notification(self, user_id: str) -> bool:
        if user_id in self.notified_disconnects:
            self.notified_disconnects.discard(user_id)
            return True
        return False

    async def disconnect_player(self, user_id: str):
        room = self.get_room_for(user_id)
        if not room or room.stage == "FINISHED":
            return
        self.cancel_pending_forfeit(user_id)
        task = asyncio.create_task(self._handle_disconnect(user_id, room.room_id))
        self.pending_forfeits[user_id] = task

    async def _handle_disconnect(self, user_id: str, room_id: str):
        try:
            await asyncio.sleep(NOTIFY_DELAY_SECONDS)
        except asyncio.CancelledError:
            return

        room = self.active_rooms.get(room_id)
        if not room or room.stage == "FINISHED":
            self.pending_forfeits.pop(user_id, None)
            return
        disconnected = room.get_player_by_id(user_id)
        if not disconnected:
            self.pending_forfeits.pop(user_id, None)
            return

        opponent = room.get_opponent(disconnected)
        try:
            await opponent.websocket.send_json({
                "type": "OPPONENT_DISCONNECTED",
                "grace_seconds": RECONNECT_GRACE_SECONDS - NOTIFY_DELAY_SECONDS,
            })
            self.notified_disconnects.add(user_id)
        except Exception:
            pass

        try:
            await asyncio.sleep(RECONNECT_GRACE_SECONDS - NOTIFY_DELAY_SECONDS)
        except asyncio.CancelledError:
            return

        room = self.active_rooms.get(room_id)
        if not room or room.stage == "FINISHED":
            self.pending_forfeits.pop(user_id, None)
            self.notified_disconnects.discard(user_id)
            return
        disconnected = room.get_player_by_id(user_id)
        if not disconnected:
            self.pending_forfeits.pop(user_id, None)
            self.notified_disconnects.discard(user_id)
            return

        opp = room.get_opponent(disconnected)
        disconnected.score = -1
        opp.score = 999

        from app.core.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            try:
                await self.finish_game(room, db)
            finally:
                self.pending_forfeits.pop(user_id, None)
                self.notified_disconnects.discard(user_id)

    # ── game end / ELO (unchanged math, new shape) ─────────────────────
    def calculate_elo_change(
        self, elo_a: int, elo_b: int, score_a: int, score_b: int,
    ) -> tuple[int, int]:
        K = 20
        E_a = 1 / (1 + 10 ** ((elo_b - elo_a) / 400))
        if score_a > score_b:
            S_a = 1.0
        elif score_b > score_a:
            S_a = 0.0
        else:
            S_a = 0.5

        delta_a = round(K * (S_a - E_a))
        delta_b = -delta_a  # zero-sum after rounding

        if elo_a + delta_a < 100:
            delta_a = 100 - elo_a
            delta_b = -delta_a
        if elo_b + delta_b < 100:
            delta_b = 100 - elo_b
            delta_a = -delta_b

        return delta_a, delta_b

    async def finish_game(self, room: GameRoom, db: AsyncSession):
        room.stage = "FINISHED"
        p1, p2 = room.player1, room.player2
        delta_1, delta_2 = self.calculate_elo_change(p1.elo, p2.elo, p1.score, p2.score)

        result1 = await db.execute(select(User).filter(User.id == uuid.UUID(p1.user_id)))
        user1 = result1.scalars().first()
        result2 = await db.execute(select(User).filter(User.id == uuid.UUID(p2.user_id)))
        user2 = result2.scalars().first()

        if user1 and user2:
            user1.elo += delta_1
            user1.games_played += 1
            if p1.score > p2.score:
                user1.wins += 1
            elif p1.score < p2.score:
                user1.losses += 1

            user2.elo += delta_2
            user2.games_played += 1
            if p2.score > p1.score:
                user2.wins += 1
            elif p2.score < p1.score:
                user2.losses += 1

            await db.commit()

        await room.broadcast({
            "type": "GAME_FINISHED",
            "room_id": room.room_id,
            "players": room._players_payload(),
            "results": {
                "LEFT": {
                    "score": p1.score,
                    "elo_change": delta_1,
                    "new_elo": p1.elo + delta_1,
                    "is_winner": p1.score > p2.score,
                    "is_draw": p1.score == p2.score,
                },
                "RIGHT": {
                    "score": p2.score,
                    "elo_change": delta_2,
                    "new_elo": p2.elo + delta_2,
                    "is_winner": p2.score > p1.score,
                    "is_draw": p1.score == p2.score,
                },
            },
        })

        del self.active_rooms[room.room_id]
        self.player_to_room.pop(p1.user_id, None)
        self.player_to_room.pop(p2.user_id, None)


game_manager = GameManager()
