"""Generic execution-chain engine for abilities and attacks.

Every actionable thing in the battle (a basic attack's damage step, an
ability's full chain, a passive's response) compiles down to a list of
"steps" the engine walks. Each step is `{type, target_selector, value, ...}`.

Design rules:
  * Pure data in (chain list + ExecutionContext + a reference to the room
    for state mutation); side effects on the battle state out.
  * Side effects happen synchronously — broadcasts are the caller's job
    after the chain finishes (one update per action, not one per step).
  * Unknown step types / selectors silently no-op so a half-implemented
    schema in seed data doesn't crash the live engine. Validation lives in
    Pydantic at write time instead.
"""
from __future__ import annotations

import ast
import random
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from app.services.game_manager import BattleState, GameRoom, UnitState

# ── Constants ───────────────────────────────────────────────────────────
DICE_RE = re.compile(r"^\s*(\d+)d(\d+)\s*([+\-]\s*\d+)?\s*$")
TOTAL_ZONES = 5

# Isolation debuff (applied by game_manager when a unit is on enemy territory):
# the isolated unit takes more damage and deals less. Kept here because the
# damage maths lives here.
ISOLATION_STATUS = "ISOLATION"
ISOLATION_DAMAGE_TAKEN_BONUS = 2
ISOLATION_DAMAGE_DEALT_PENALTY = 1


def _has_status(unit: "UnitState", name: str) -> bool:
    return any(s.get("name") == name for s in unit.statuses)


def _status_value(unit: "UnitState", name: str) -> int:
    """Value (e.g. stack count) of a named status on the unit, 0 if absent."""
    for s in unit.statuses:
        if s.get("name") == name:
            return int(s.get("value", 0) or 0)
    return 0


# Statuses that count as debuffs for DEBUFF_IMMUNE.
_HARMFUL_STATUSES = {"STUN", "POISON", "ISOLATION"}


def _self_fresh(ctx: "ExecutionContext", target: "UnitState") -> bool:
    """A status the acting unit puts on ITSELF during its own turn is 'fresh':
    it shouldn't burn a tick at the end of that same turn."""
    return ctx.is_owner_turn and target.unit_id == ctx.actor.unit_id


# ── Context ─────────────────────────────────────────────────────────────
@dataclass
class ExecutionContext:
    """Shared state for one chain invocation. Created by the caller (attack /
    use-ability / passive trigger) and threaded through every step."""
    actor: "UnitState"
    # The unit that received the original click — used by MAIN_TARGET and
    # SAME_ZONE selectors. None for self-buffs / party-wide abilities.
    main_target: Optional["UnitState"] = None
    # For passive responses (BEFORE_TAKE_DAMAGE etc.) — the unit that hit us.
    attacker: Optional["UnitState"] = None
    # Range of the attack that triggered the passive (used by attacker_range).
    attacker_range: Optional[int] = None
    # CUSTOM_SELECT payload from the client. Steps with CUSTOM_SELECT pick
    # the appropriate one based on what they expect.
    custom_target: Optional["UnitState"] = None
    custom_zone: Optional[int] = None
    # Second client-picked unit, for two-target abilities (Neuralink swap).
    custom_target2: Optional["UnitState"] = None
    # ── modifier-aware extras (Phase 2) ────────────────────────────────
    # Added to every range-based selector while an ability runs (Elon's
    # Starlink aura bumps this). 0 for attacks / passives.
    range_bonus: int = 0
    # When False the chain is a passive response — its damage is applied but
    # NOT recorded as a new action event, so passives don't recurse.
    triggers_passives: bool = True
    # Damage targets accumulated during a primary action, consumed by the
    # passive dispatcher (retaliation / on-deal / after-action triggers).
    damage_events: List[Dict[str, Any]] = field(default_factory=list)
    # Units moved by a chain (for ON_ZONE_CHANGE dispatch).
    moved_units: List["UnitState"] = field(default_factory=list)
    # Enemies hit by the triggering action — read by the LAST_HIT_ENEMIES
    # selector (Islam's "Двоечка" splashes onto everyone he just hit).
    hit_targets: List["UnitState"] = field(default_factory=list)
    # True while resolving the acting unit's own attack/ability on its turn. A
    # status the actor puts on ITSELF this turn is flagged "fresh" so it doesn't
    # lose a tick at the end of the same turn (you didn't get to use that tick).
    is_owner_turn: bool = False


# ── Value resolver ──────────────────────────────────────────────────────
def resolve_value(
    value: Any, ctx: ExecutionContext, char_cache: Dict[str, Dict[str, Any]],
) -> int:
    """Resolve a step's `value` field. Accepts:
      * int → returned as-is
      * dice string "NdM[+K]" → rolled
      * formula string → safely evaluated with self/target/attacker scope
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        # Dynamic tokens:
        #   "stacks:NAME"  → actor's stack count of status NAME
        #   "-stacks:NAME" → negated (for defense-shred etc.)
        #   "hit_count"    → distinct enemies the current action has damaged
        if s.startswith("stacks:"):
            return _status_value(ctx.actor, s[len("stacks:"):])
        if s.startswith("-stacks:"):
            return -_status_value(ctx.actor, s[len("-stacks:"):])
        if s == "hit_count":
            seen = set()
            for ev in ctx.damage_events:
                t = ev.get("target")
                if t is not None and t.owner_side != ctx.actor.owner_side:
                    seen.add(t.unit_id)
            return len(seen)
        if DICE_RE.match(s):
            return _resolve_dice(s)
        return _resolve_formula(s, ctx, char_cache)
    return 0


def _resolve_dice(expr: str) -> int:
    m = DICE_RE.match(expr)
    if not m:
        return 0
    n, sides, mod = m.groups()
    total = sum(random.randint(1, int(sides)) for _ in range(int(n)))
    if mod:
        total += int(mod.replace(" ", ""))
    return total


def _resolve_formula(
    expr: str, ctx: ExecutionContext, char_cache: Dict[str, Dict[str, Any]],
) -> int:
    """Eval a tiny arithmetic expression with `self`/`target`/`attacker` in
    scope. Uses Python's ast module restricted to safe node kinds — no calls,
    no imports, no comprehensions. Anything unsupported raises ValueError
    rather than executing."""
    scope: Dict[str, Any] = {"self": _unit_view(ctx.actor, char_cache)}
    if ctx.main_target is not None:
        scope["target"] = _unit_view(ctx.main_target, char_cache)
    if ctx.attacker is not None:
        scope["attacker"] = _unit_view(ctx.attacker, char_cache)

    tree = ast.parse(expr, mode="eval")

    def visit(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return scope.get(node.id, 0)
        if isinstance(node, ast.Attribute):
            obj = visit(node.value)
            return getattr(obj, node.attr, 0)
        if isinstance(node, ast.BinOp):
            left = visit(node.left)
            right = visit(node.right)
            op = node.op
            if isinstance(op, ast.Add): return left + right
            if isinstance(op, ast.Sub): return left - right
            if isinstance(op, ast.Mult): return left * right
            if isinstance(op, (ast.Div, ast.FloorDiv)):
                return left // right if right else 0
            if isinstance(op, ast.Mod):
                return left % right if right else 0
        if isinstance(node, ast.UnaryOp):
            v = visit(node.operand)
            if isinstance(node.op, ast.USub): return -v
            if isinstance(node.op, ast.UAdd): return v
        raise ValueError(f"unsupported formula node: {type(node).__name__}")

    return int(visit(tree))


def _unit_view(
    unit: "UnitState", char_cache: Dict[str, Dict[str, Any]],
) -> SimpleNamespace:
    """Expose a unit's stats as attribute access for the formula evaluator."""
    char = char_cache.get(unit.char_id, {}) or {}
    base = char.get("base_stats", {}) or {}
    # `attack_damage` is the unit's *base* basic-attack value. The flat +DAMAGE
    # modifier is added uniformly in _apply_damage, so anything that resolves to
    # `self.attack_damage` (e.g. Islam's bonus splash) ends up dealing exactly
    # what a normal attack would — base + auras — without double-counting.
    atk_val = ((char.get("attack") or {}).get("damage_schema") or {}).get("value", 0)
    atk_base = atk_val if isinstance(atk_val, int) else 0
    return SimpleNamespace(
        hp=unit.current_hp,
        max_hp=unit.max_hp,
        energy=unit.current_energy,
        max_energy=unit.max_energy,
        defense=unit.current_defense,
        regeneration=unit.current_regeneration,
        movement_range=int(base.get("movement_range", 1)),
        initiative=int(base.get("initiative", 0)),
        zone=unit.zone,
        attack_damage=atk_base,
    )


# ── Target selectors ────────────────────────────────────────────────────
def resolve_targets(
    selector: Dict[str, Any], ctx: ExecutionContext, battle: "BattleState",
) -> List["UnitState"]:
    """Return live units matching the selector. Dead units (hp<=0) are
    filtered out everywhere so spent statuses don't keep zombies in play."""
    sel_type = (selector or {}).get("type")
    actor = ctx.actor

    if sel_type == "SELF":
        return [actor] if actor.current_hp > 0 else []

    if sel_type == "MAIN_TARGET":
        t = ctx.main_target
        return [t] if t and t.current_hp > 0 else []

    if sel_type == "ATTACKER":
        a = ctx.attacker
        return [a] if a and a.current_hp > 0 else []

    if sel_type == "SAME_ZONE":
        # Units in MAIN_TARGET's zone (excluding the actor). Honour an optional
        # `filter`: ENEMIES (default-safe for damage/poison so it never hits your
        # own side), ALLIES, or ALL (everyone except the actor).
        if not ctx.main_target:
            return []
        z = ctx.main_target.zone
        flt = (selector or {}).get("filter")
        out: List["UnitState"] = []
        for u in battle.units.values():
            if u.zone != z or u.unit_id == actor.unit_id or u.current_hp <= 0:
                continue
            if flt == "ENEMIES" and u.owner_side == actor.owner_side:
                continue
            if flt == "ALLIES" and u.owner_side != actor.owner_side:
                continue
            out.append(u)
        return out

    if sel_type == "CUSTOM_SELECT":
        # Always a single chosen entity. The MOVE step uses custom_zone;
        # everything else uses custom_target.
        t = ctx.custom_target
        return [t] if t and t.current_hp > 0 else []

    if sel_type == "ALL_ALLIES":
        return [
            u for u in battle.units.values()
            if u.owner_side == actor.owner_side and u.current_hp > 0
        ]

    if sel_type == "ALL_ENEMIES":
        return [
            u for u in battle.units.values()
            if u.owner_side != actor.owner_side and u.current_hp > 0
        ]

    if sel_type == "LAST_HIT_ENEMIES":
        # Enemies the triggering action just damaged (Islam's after-action proc).
        return [
            u for u in ctx.hit_targets
            if u.current_hp > 0 and u.owner_side != actor.owner_side
        ]

    if sel_type == "CUSTOM_PAIR":
        return [
            u for u in (ctx.custom_target, ctx.custom_target2)
            if u is not None and u.current_hp > 0
        ]

    if sel_type in ("ENEMIES_IN_ZONE", "ALLIES_IN_ZONE"):
        if ctx.custom_zone is None:
            return []
        allies = sel_type == "ALLIES_IN_ZONE"
        return [
            u for u in battle.units.values()
            if u.current_hp > 0
            and u.zone == ctx.custom_zone
            and (u.owner_side == actor.owner_side) == allies
        ]

    if sel_type in ("ALLIES_IN_RANGE", "ENEMIES_IN_RANGE"):
        # range_bonus folds in aura buffs (Starlink) while an ability runs.
        rng = int((selector or {}).get("range", 1)) + ctx.range_bonus
        same = sel_type == "ALLIES_IN_RANGE"
        return [
            u for u in battle.units.values()
            if u.current_hp > 0
            and abs(u.zone - actor.zone) <= rng
            and (u.owner_side == actor.owner_side) == same
        ]

    return []


# ── Conditions ──────────────────────────────────────────────────────────
def evaluate_conditions(
    conditions: List[Dict[str, Any]],
    ctx: ExecutionContext,
    char_cache: Dict[str, Dict[str, Any]],
) -> bool:
    """All conditions must pass (AND-logic). Side-effecting: a successful
    `target_has_status` with `consume: true` removes the matched status —
    that's how MARK_BOMB detonation consumes the mark."""
    for cond in conditions or []:
        check = (cond or {}).get("check")
        if check == "target_has_status":
            t = ctx.main_target
            if not t:
                return False
            name = cond.get("status_name")
            found = next((s for s in t.statuses if s.get("name") == name), None)
            if not found:
                return False
            if cond.get("consume", False):
                t.statuses = [s for s in t.statuses if s is not found]
        elif check == "target_hp_below_pct":
            t = ctx.main_target
            if not t or t.max_hp <= 0:
                return False
            pct = int(cond.get("value", 0))
            if (t.current_hp * 100 / t.max_hp) >= pct:
                return False
        elif check == "self_hp_below_pct":
            pct = int(cond.get("value", 0))
            if ctx.actor.max_hp <= 0:
                return False
            if (ctx.actor.current_hp * 100 / ctx.actor.max_hp) >= pct:
                return False
        elif check == "attacker_range":
            expected = int(cond.get("value", 0))
            if ctx.attacker_range != expected:
                return False
        elif check == "self_in_zone_type":
            wanted = cond.get("value")
            if not _is_unit_in_zone_type(ctx.actor, wanted):
                return False
        elif check == "random_chance":
            # Base chance shifted by the actor's PROC_CHANCE modifier (support
            # auras can raise/lower every proc), clamped to [0, 100].
            pct = int(cond.get("value", 0)) + ctx.actor.modifiers.get("PROC_CHANCE", 0)
            pct = max(0, min(100, pct))
            if random.randint(1, 100) > pct:
                return False
        elif check == "self_status_at_least":
            if _status_value(ctx.actor, cond.get("status_name")) < int(cond.get("value", 0)):
                return False
        elif check == "self_has_status":
            if not _has_status(ctx.actor, cond.get("status_name")):
                return False
        elif check == "self_ability_ready":
            aid = cond.get("ability_id") or cond.get("value")
            if ctx.actor.cooldowns.get(aid, 0) > 0:
                return False
        else:
            # Unknown check fails closed — safer than letting an effect fire
            # uncontrolled when the schema gets a new check before the
            # engine is updated.
            return False
    return True


def _is_unit_in_zone_type(unit: "UnitState", wanted: Optional[str]) -> bool:
    if wanted not in ("FRONTLINE", "BACKLINE"):
        return False
    if unit.owner_side == "LEFT":
        return (wanted == "FRONTLINE" and unit.zone == 1) or (
            wanted == "BACKLINE" and unit.zone == 0
        )
    return (wanted == "FRONTLINE" and unit.zone == 3) or (
        wanted == "BACKLINE" and unit.zone == 4
    )


# ── Step executor ───────────────────────────────────────────────────────
async def execute_chain(
    chain: List[Dict[str, Any]],
    ctx: ExecutionContext,
    room: "GameRoom",
) -> None:
    """Walk a chain in order. Steps may mutate units in place; the caller is
    responsible for broadcasting state once the chain finishes."""
    for step in chain or []:
        await execute_step(step, ctx, room)


async def execute_step(
    step: Dict[str, Any], ctx: ExecutionContext, room: "GameRoom",
) -> None:
    battle = room.battle
    if battle is None:
        return
    cache = room.character_cache
    step_type = step.get("type")

    if step_type == "CONDITIONAL":
        # Conditions may consume statuses (e.g. MARK_BOMB), so evaluate them
        # exactly once and dispatch to the matching branch.
        passed = evaluate_conditions(step.get("conditions", []), ctx, cache)
        branch = step.get("if_true") if passed else step.get("if_false")
        await execute_chain(branch or [], ctx, room)
        return

    if step_type == "MOVE":
        # Relocate either the actor (Smoke Dash) or a chosen ally (Elon's Tesla)
        # to the client-picked zone. `subject` selects which: SELF (default) or
        # TARGET (ctx.custom_target). Doesn't touch unit.move_count (the regular
        # move budget) — ability movement is separate.
        sel = step.get("target_selector", {}) or {}
        subject = str(step.get("subject", "SELF")).upper()
        mover = ctx.custom_target if subject == "TARGET" else ctx.actor
        if mover is None or ctx.custom_zone is None:
            return
        if subject == "TARGET":
            # Two distinct ranges for moving an ally (Elon's Tesla):
            #  * cast range  — how far the chosen ally may be from the caster.
            #    This is the "application radius" auras like Starlink widen.
            #  * move_range  — how far the ally is then flung. Fixed; auras do
            #    NOT extend the throw distance, only the reach to the ally.
            cast = sel.get("range")
            if cast is not None and abs(mover.zone - ctx.actor.zone) > int(cast) + ctx.range_bonus:
                return
            move_range = int(step.get("move_range", sel.get("range", 1) or 1))
            if abs(ctx.custom_zone - mover.zone) > move_range:
                return
        else:
            # Self-move (dash): the selector reach IS the move distance, and the
            # actor's own ability-range aura may extend it.
            reach = sel.get("range")
            if reach is not None and abs(ctx.custom_zone - mover.zone) > int(reach) + ctx.range_bonus:
                return
        if _move_unit(mover, ctx.custom_zone, battle):
            ctx.moved_units.append(mover)
        return

    if step_type == "GRANT_MODIFIER":
        # Auras are recomputed wholesale in apply_auras(); a GRANT_MODIFIER that
        # somehow lands in a normal chain is a no-op rather than a one-shot buff.
        return

    if step_type == "TRIGGER_ABILITY":
        # Run another of the actor's abilities for free (auto-cast). Sets that
        # ability's own cooldown; does NOT charge energy or the action token.
        aid = step.get("ability_id")
        char = cache.get(ctx.actor.char_id) or {}
        ab = next((a for a in char.get("abilities", []) if a.get("id") == aid), None)
        if ab:
            cd = int(ab.get("cooldown", 0))
            if cd > 0:
                ctx.actor.cooldowns[aid] = cd
            await execute_chain(ab.get("execution_chain") or [], ctx, room)
        return

    if step_type == "SWAP_POSITIONS":
        # Teleport-swap the two client-picked units (Neuralink).
        a, b = ctx.custom_target, ctx.custom_target2
        if a is None or b is None or a.unit_id == b.unit_id:
            return
        if a.current_hp <= 0 or b.current_hp <= 0:
            return
        za, zb = a.zone, b.zone
        # Pull both off their zones, then drop each onto the other's.
        if a.unit_id in battle.zones[za]:
            battle.zones[za].remove(a.unit_id)
        if b.unit_id in battle.zones[zb]:
            battle.zones[zb].remove(b.unit_id)
        a.zone, b.zone = zb, za
        battle.zones[zb].append(a.unit_id)
        battle.zones[za].append(b.unit_id)
        ctx.moved_units.extend([a, b])
        return

    targets = resolve_targets(step.get("target_selector", {}), ctx, battle)

    if step_type == "RELOCATE_GROUP":
        # Move every selected unit onto the client-picked zone (Mars landing).
        if ctx.custom_zone is None:
            return
        for u in targets:
            if _move_unit(u, ctx.custom_zone, battle):
                ctx.moved_units.append(u)
        return

    if step_type == "PULL_TARGET":
        # Drag selected target(s) into the actor's zone (Goggins' Carry the Log).
        for t in targets:
            if t.unit_id != ctx.actor.unit_id and _move_unit(t, ctx.actor.zone, battle):
                ctx.moved_units.append(t)
        return

    if step_type == "ADD_STACK":
        # Increment a stacking status's value (capped at `max`), creating it as
        # a permanent status if absent.
        name = step.get("status_name")
        if not name:
            return
        amt = resolve_value(step.get("value", 1), ctx, cache)
        mx = step.get("max")
        for t in targets:
            existing = next((s for s in t.statuses if s.get("name") == name), None)
            if existing:
                v = int(existing.get("value", 0)) + amt
                if mx is not None:
                    v = min(v, int(mx))
                existing["value"] = v
            else:
                v = amt
                if mx is not None:
                    v = min(v, int(mx))
                t.statuses.append({
                    "name": name, "value": v, "duration": -1,
                    "max_duration": int(mx) if mx is not None else None,
                })
            recompute_derived(t, cache)
        return

    if step_type == "DAMAGE":
        amount = resolve_value(step.get("value"), ctx, cache)
        dmg_type = step.get("damage_type") or "PHYSICAL"
        for t in targets:
            source_range = abs(t.zone - ctx.actor.zone)
            _apply_damage(t, amount, ctx.actor, source_range, dmg_type)
            # Record the hit so the post-action passive dispatcher can react
            # (retaliation / on-deal / after-action). Passive-sourced damage
            # runs with triggers_passives=False and is intentionally skipped.
            if ctx.triggers_passives:
                ctx.damage_events.append({"target": t, "source_range": source_range})
    elif step_type == "HEAL":
        amount = resolve_value(step.get("value"), ctx, cache)
        for t in targets:
            t.current_hp = min(t.max_hp, t.current_hp + amount)
    elif step_type == "APPLY_STATUS":
        name = step.get("status_name")
        if not name:
            return
        value = resolve_value(step.get("value", 1), ctx, cache)
        duration = int(step.get("duration", 1))
        harmful = name in _HARMFUL_STATUSES
        group = step.get("group")
        for t in targets:
            # Debuff immunity (Goggins' Hell Week) blocks harmful statuses.
            if harmful and _has_status(t, "DEBUFF_IMMUNE"):
                continue
            # Refresh: replace any existing status with the same name.
            t.statuses = [s for s in t.statuses if s.get("name") != name]
            # max_duration lets the client draw a "time remaining" ring.
            status = {
                "name": name, "value": value,
                "duration": duration, "max_duration": duration,
            }
            if group:
                status["group"] = group
            if _self_fresh(ctx, t):
                status["fresh"] = True
            t.statuses.append(status)
            recompute_derived(t, cache)
    elif step_type == "REMOVE_STATUS":
        name = step.get("status_name")
        for t in targets:
            t.statuses = [s for s in t.statuses if s.get("name") != name]
            recompute_derived(t, cache)
    elif step_type == "BUFF_DEFENSE":
        value = resolve_value(step.get("value", 0), ctx, cache)
        duration = int(step.get("duration", 1))
        group = step.get("group")
        if value == 0:
            return  # no-op buff (e.g. defense shred with 0 stacks) — skip entirely
        for t in targets:
            # A negative defense buff is a debuff — blocked by immunity.
            if value < 0 and _has_status(t, "DEBUFF_IMMUNE"):
                continue
            status = {"name": "BUFF_DEFENSE", "value": value,
                      "duration": duration, "max_duration": duration}
            if group:
                status["group"] = group
            if _self_fresh(ctx, t):
                status["fresh"] = True
            t.statuses.append(status)
            recompute_derived(t, cache)
    elif step_type == "BUFF_STAT":
        stat = str(step.get("stat", "DEFENSE")).upper()
        value = resolve_value(step.get("value", 0), ctx, cache)
        duration = int(step.get("duration", 1))
        group = step.get("group")
        if value == 0:
            return  # no-op stat buff — skip
        for t in targets:
            status = {"name": f"BUFF_{stat}", "value": value,
                      "duration": duration, "max_duration": duration}
            if group:
                status["group"] = group
            if _self_fresh(ctx, t):
                status["fresh"] = True
            t.statuses.append(status)
            recompute_derived(t, cache)
    elif step_type == "ENERGY_GAIN":
        amount = resolve_value(step.get("value", 0), ctx, cache)
        for t in targets:
            t.current_energy = min(t.max_energy, t.current_energy + amount)
    elif step_type == "ENERGY_LOSS":
        amount = resolve_value(step.get("value", 0), ctx, cache)
        for t in targets:
            t.current_energy = max(0, t.current_energy - amount)
    # unknown step types silently no-op


# ── Mutation helpers (re-exported for game_manager) ─────────────────────
def _apply_damage(
    target: "UnitState",
    amount: int,
    attacker: Optional["UnitState"],
    source_range: int,
    damage_type: str = "PHYSICAL",
) -> None:
    """Apply damage with a 1-minimum floor.

    PHYSICAL is reduced by the target's `current_defense`; MAGICAL ignores
    defense entirely. Isolation adjusts the raw amount first (an isolated
    attacker deals 1 less, an isolated target takes 2 more) and applies to
    both damage types.

    NOTE Phase 6 hook point: BEFORE_TAKE_DAMAGE passives plug in here."""
    raw = amount
    if attacker is not None:
        # Flat outgoing-damage modifier (damage auras). Applied uniformly so a
        # basic attack and a "damage equal to attack" splash scale the same way.
        raw += attacker.modifiers.get("DAMAGE", 0)
        if _has_status(attacker, ISOLATION_STATUS) and not _has_status(attacker, "DEBUFF_IMMUNE"):
            raw -= ISOLATION_DAMAGE_DEALT_PENALTY
    if _has_status(target, ISOLATION_STATUS) and not _has_status(target, "DEBUFF_IMMUNE"):
        raw += ISOLATION_DAMAGE_TAKEN_BONUS
    # Magical damage bypasses armour; physical is mitigated by defense.
    if str(damage_type).upper() == "MAGICAL":
        final = max(1, raw)
    else:
        final = max(1, raw - target.current_defense)
    target.current_hp = max(0, target.current_hp - final)


def _move_unit(unit: "UnitState", target_zone: int, battle: "BattleState") -> bool:
    """Returns True if the unit actually changed zone."""
    if target_zone == unit.zone:
        return False
    if target_zone < 0 or target_zone >= TOTAL_ZONES:
        return False
    battle.zones[unit.zone].remove(unit.unit_id)
    unit.zone = target_zone
    battle.zones[target_zone].append(unit.unit_id)
    return True


def apply_auras(room: "GameRoom") -> None:
    """Recompute every unit's `modifiers` dict from the AURA passives currently
    on the field. Called after any positional change (move / round start /
    death / respawn). Modifiers are additive and fully recomputed each time, so
    an aura's owner moving out of range or dying drops its contribution.

    A GRANT_MODIFIER step inside an AURA passive applies `value` to every unit
    matched by its target_selector (resolved from the aura owner's position)."""
    battle = room.battle
    if battle is None:
        return
    for unit in battle.units.values():
        unit.modifiers = {}
    cache = room.character_cache
    for owner in battle.units.values():
        if owner.current_hp <= 0 or owner.is_dead:
            continue
        for passive in (cache.get(owner.char_id) or {}).get("passives") or []:
            if passive.get("trigger_event") != "AURA":
                continue
            ctx = ExecutionContext(actor=owner)
            for step in passive.get("execution_chain") or []:
                if step.get("type") != "GRANT_MODIFIER":
                    continue
                stat = str(step.get("stat", "")).upper()
                if not stat:
                    continue
                value = resolve_value(step.get("value", 0), ctx, cache)
                for t in resolve_targets(step.get("target_selector", {}), ctx, battle):
                    t.modifiers[stat] = t.modifiers.get(stat, 0) + value

    # Self-buff statuses also feed live modifiers (e.g. Hell Week's BUFF_DAMAGE
    # → +DAMAGE on outgoing hits). Folded in after auras so both stack.
    for unit in battle.units.values():
        if unit.current_hp <= 0 or unit.is_dead:
            continue
        for s in unit.statuses:
            if s.get("name") == "BUFF_DAMAGE":
                unit.modifiers["DAMAGE"] = unit.modifiers.get("DAMAGE", 0) + int(s.get("value", 0))


def recompute_derived(
    unit: "UnitState", char_cache: Dict[str, Dict[str, Any]],
) -> None:
    """Recalculate the unit's derived stats from base + active buffs. Call
    after any change to `statuses` so the on-the-wire snapshot stays in sync
    with the buff stack."""
    char = char_cache.get(unit.char_id, {}) or {}
    base = char.get("base_stats", {}) or {}
    # While debuff-immune, negative defense buffs (shred) are ignored.
    immune = any(s.get("name") == "DEBUFF_IMMUNE" for s in unit.statuses)
    def_bonus = sum(
        int(s.get("value", 0))
        for s in unit.statuses
        if s.get("name") == "BUFF_DEFENSE" and not (immune and int(s.get("value", 0)) < 0)
    )
    # REGEN (timed heal-over-time, e.g. Priestess blessing) and BUFF_REGENERATION
    # both add to the unit's per-activation regen. Folding REGEN in here keeps the
    # displayed `current_regeneration` honest and lets one heal path handle both.
    reg_bonus = sum(
        int(s.get("value", 0))
        for s in unit.statuses
        if s.get("name") in ("BUFF_REGENERATION", "REGEN")
    )
    unit.current_defense = int(base.get("defense", 0)) + def_bonus
    unit.current_regeneration = int(base.get("regeneration", 0)) + reg_bonus
