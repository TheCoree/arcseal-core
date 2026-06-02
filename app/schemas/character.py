"""Pydantic schemas for characters.

These mirror the JSON structure documented in the Phase 4 spec. They're used
to validate seed data on load and to validate any future PUT/POST endpoints.

ChainStep / ChainCondition are kept flat with optional fields — the runtime
engine in Phase 5 will dispatch on `type` / `check`. We accept all currently
known step / check kinds; extending the literal lists is cheap and forces
us to update the engine when adding a new step type.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


# ── Step / condition primitives ─────────────────────────────────────────

DamageSchemaType = Literal["FIXED", "DICE", "FORMULA"]

# How damage interacts with the target's defense:
#   PHYSICAL — reduced by `current_defense` (min 1 after mitigation)
#   MAGICAL  — ignores defense entirely
DamageType = Literal["PHYSICAL", "MAGICAL"]

TargetSelectorType = Literal[
    "MAIN_TARGET",
    "SELF",
    "SAME_ZONE",        # everyone in the zone of MAIN_TARGET
    "CUSTOM_SELECT",    # requires a second click — `range` + `filter`
    "ATTACKER",         # for retaliation / on-hit passives
    "ALLIES_IN_RANGE",  # `range` + `filter: ALLIES`
    "ENEMIES_IN_RANGE",
    "ALL_ALLIES",
    "ALL_ENEMIES",
    "LAST_HIT_ENEMIES",  # enemies just hit by the triggering action (AFTER_ACTION)
    "CUSTOM_PAIR",       # the two client-picked units (custom_target + custom_target2)
    "ENEMIES_IN_ZONE",   # enemies standing in the client-picked custom_zone
    "ALLIES_IN_ZONE",    # allies standing in the client-picked custom_zone
]

TargetFilter = Literal["ALLIES", "ENEMIES", "ALL"]

ChainStepType = Literal[
    "DAMAGE",
    "HEAL",
    "APPLY_STATUS",
    "REMOVE_STATUS",
    "BUFF_STAT",
    "BUFF_DEFENSE",
    "MOVE",
    "ENERGY_GAIN",
    "ENERGY_LOSS",
    "CONDITIONAL",
    "GRANT_MODIFIER",  # AURA passives: add a value to targets' live modifiers
    "SWAP_POSITIONS",  # swap zones of custom_target & custom_target2
    "RELOCATE_GROUP",  # move every selected unit to the client-picked custom_zone
    "ADD_STACK",       # increment a stacking status (value, max) — e.g. Resolve
    "PULL_TARGET",     # drag selected target(s) into the actor's zone
    "TRIGGER_ABILITY", # run another of the actor's abilities for free (ability_id)
]

# Stat keys a modifier (aura) or GRANT_MODIFIER step can touch.
ModifierStat = Literal["DAMAGE", "PROC_CHANCE", "ABILITY_RANGE", "ABILITY_COST"]

ConditionCheck = Literal[
    "target_has_status",
    "target_hp_below_pct",
    "self_hp_below_pct",
    "attacker_range",
    "self_in_zone_type",  # FRONTLINE / BACKLINE relative to owner side
    "random_chance",      # value: int 0..100
    "self_status_at_least",  # status_name + value: actor's stack count >= value
    "self_has_status",       # status_name: actor currently has the status
    "self_ability_ready",    # ability_id: that ability is off cooldown
]

PassiveTrigger = Literal[
    "ON_ROUND_START",
    "ON_TURN_START",     # when the owner is activated (start of its own turn)
    "BEFORE_TAKE_DAMAGE",
    "ON_DEAL_DAMAGE",
    "ON_ZONE_CHANGE",
    "AFTER_ACTION",     # after the owner's own attack/ability resolves (procs)
    "AURA",             # always-on while alive; chain holds GRANT_MODIFIER steps
]


class DamageSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: DamageSchemaType
    # FIXED → int, DICE → "1d6"-style string, FORMULA → free-form expression
    # like "self.regeneration * 3". The engine resolves it in Phase 5.
    value: Union[int, str]


class TargetSelector(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: TargetSelectorType
    # Required for *_IN_RANGE / CUSTOM_SELECT
    range: Optional[int] = None
    filter: Optional[TargetFilter] = None


class ChainCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check: ConditionCheck
    # Shape depends on `check`:
    #   target_has_status        → status_name (str), consume (bool)
    #   target_hp_below_pct      → value (int 0..100)
    #   self_hp_below_pct        → value (int 0..100)
    #   attacker_range           → value (int — exact range)
    #   self_in_zone_type        → value ("FRONTLINE"|"BACKLINE")
    #   random_chance            → value (int 0..100)
    value: Optional[Any] = None
    status_name: Optional[str] = None
    consume: Optional[bool] = None
    ability_id: Optional[str] = None  # for self_ability_ready


class ChainStep(BaseModel):
    """One node in an execution_chain. See `type` for the dispatch contract.

    Examples by type:
      DAMAGE          → target_selector, value (int|str formula|dice)
      HEAL            → target_selector, value
      APPLY_STATUS    → target_selector, status_name, value (potency), duration
      REMOVE_STATUS   → target_selector, status_name
      BUFF_STAT       → target_selector, stat, value, duration
      BUFF_DEFENSE    → target_selector, value, duration
      MOVE            → target_selector (SELF or CUSTOM_SELECT), value (zone idx)
      ENERGY_GAIN     → target_selector, value
      ENERGY_LOSS     → target_selector, value
      CONDITIONAL     → conditions[], if_true[], if_false[] (no target_selector)
    """

    model_config = ConfigDict(extra="forbid")

    type: ChainStepType
    target_selector: Optional[TargetSelector] = None
    value: Optional[Union[int, str]] = None
    status_name: Optional[str] = None
    duration: Optional[int] = None  # rounds; -1 = permanent until trigger
    stat: Optional[str] = None      # for BUFF_STAT / GRANT_MODIFIER
    # DAMAGE only: PHYSICAL (default, blocked by defense) or MAGICAL (ignores it).
    damage_type: Optional[DamageType] = None
    # MOVE only: who relocates — SELF (default) or TARGET (the CUSTOM_SELECT ally).
    subject: Optional[Literal["SELF", "TARGET"]] = None
    # MOVE + subject TARGET only: fixed throw distance, NOT widened by auras.
    move_range: Optional[int] = Field(default=None, ge=1)
    # ADD_STACK: cap on the stacking status's value.
    max: Optional[int] = Field(default=None, ge=1)
    # TRIGGER_ABILITY: which of the actor's abilities to run for free.
    ability_id: Optional[str] = None
    # Status-applying steps may tag their result with a group so the UI can
    # collapse a multi-effect ability (e.g. Hell Week) into one composite badge.
    group: Optional[str] = None

    conditions: Optional[List[ChainCondition]] = None
    if_true: Optional[List["ChainStep"]] = None
    if_false: Optional[List["ChainStep"]] = None


ChainStep.model_rebuild()


# ── Top-level character pieces ──────────────────────────────────────────

class BaseStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hp: int = Field(gt=0)
    defense: int = Field(ge=0)
    energy_regen: int = Field(ge=0)
    max_energy: int = Field(ge=0)
    regeneration: int = Field(ge=0, default=0)
    initiative: int = Field(ge=0, default=0)
    movement_range: int = Field(ge=0, default=1)
    # When true the unit never gains the ISOLATION debuff on enemy territory —
    # used for infiltrator-style characters (e.g. assassins).
    isolation_immune: bool = False


class AttackDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: Optional[str] = None
    icon_url: Optional[str] = None  # relative path under /uploads, e.g. /uploads/abilities/heavy_strike.png
    range: int = Field(ge=0)  # 0 = same-zone only, 1 = melee/adjacent, 2+ = ranged
    damage_schema: DamageSchema
    damage_type: DamageType = "PHYSICAL"
    execution_chain: List[ChainStep] = Field(default_factory=list)


class AbilityDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Stable per-character key the engine/UI address abilities by (cooldowns,
    # USE_ABILITY, targeting). Must be unique within a character's kit.
    id: str = Field(min_length=1)
    name: str
    description: Optional[str] = None
    icon_url: Optional[str] = None
    energy_cost: int = Field(ge=0)
    cooldown: int = Field(ge=0)
    is_quick: bool = False
    is_ult: bool = False
    # Cast range for display only (the engine reads ranges off the selectors).
    range: Optional[int] = None
    execution_chain: List[ChainStep]


class PassiveDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str
    description: Optional[str] = None
    icon_url: Optional[str] = None
    trigger_event: PassiveTrigger
    conditions: Optional[List[ChainCondition]] = None
    execution_chain: List[ChainStep]


# ── Whole character ─────────────────────────────────────────────────────

PositionType = Literal["FRONTLINE", "BACKLINE"]
RoleType = Literal["TANK", "DPS", "SUPPORT", "ASSASSIN", "BRUISER"]


class CharacterDef(BaseModel):
    """Validated character payload. Used by seeds and any admin endpoint."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    name: str
    role: RoleType
    initial_position_type: PositionType
    # Portrait artwork served via the static /uploads mount. Convention:
    # /uploads/characters/portraits/<id>.<ext>. Frontend prefixes the backend
    # origin via absolutizeMediaUrl. None = render initial-letter placeholder.
    portrait_url: Optional[str] = None
    base_stats: BaseStats
    attack: AttackDef
    # A character now has a KIT: any number of active abilities + passives
    # (4+ total, any mix — including passive ults). The basic attack stays
    # separate. Stored as JSONB lists; the engine dispatches per entry.
    abilities: List[AbilityDef] = Field(default_factory=list)
    passives: List[PassiveDef] = Field(default_factory=list)


class CharacterOut(BaseModel):
    """Public character read shape. Includes timestamps and active flag."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    role: str
    initial_position_type: str
    portrait_url: Optional[str] = None
    base_stats: dict
    attack: dict
    abilities: list = Field(default_factory=list)
    passives: list = Field(default_factory=list)
    is_active: bool
    created_at: datetime
    updated_at: datetime
