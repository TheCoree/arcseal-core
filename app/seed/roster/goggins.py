"""Дэвид Гоггинс — Несгибаемый Марафонец (Tank).

Механика стаков «Стойкость» (RESOLVE): набирается при получении урона (макс 5),
даёт энергию за стак в начале хода, при 5 стаках авто-кастует Hell Week (если
тот не на КД) и сбрасывается смертью/ультой.
"""

CHARACTER = {
    "id": "goggins",
    "name": "Дэвид Гоггинс",
    "portrait_url": "/uploads/characters/portraits/goggins.png",
    "role": "TANK",
    "initial_position_type": "FRONTLINE",
    "base_stats": {
        "hp": 140, "defense": 5,
        "energy_regen": 1, "max_energy": 12,
        "regeneration": 1, "initiative": 0, "movement_range": 1,
    },
    "attack": {
        "name": "Stay Hard!",
        "description": "Гоггинс бьёт с криком «Stay hard!» — 18 урона. При 3+ стаках Стойкости +10 урона.",
        "range": 0,
        "damage_schema": {"type": "FIXED", "value": 18},
        "damage_type": "PHYSICAL",
        "execution_chain": [
            {
                "type": "CONDITIONAL",
                "conditions": [{"check": "self_status_at_least", "status_name": "RESOLVE", "value": 3}],
                "if_true": [
                    {"type": "DAMAGE", "target_selector": {"type": "MAIN_TARGET"}, "value": 10, "damage_type": "PHYSICAL"},
                ],
            },
        ],
    },
    "abilities": [
        {
            "id": "boats",
            "name": "Who's Gonna Carry the Boats?!",
            "description": "Гоггинс орёт — все враги в радиусе 1 получают 10 урона и понижение защита, равная числу стаков Стойкости. За каждого задетого — +1 стак.",
            "energy_cost": 5, "cooldown": 1, "is_quick": True, "range": 1,
            "execution_chain": [
                {"type": "DAMAGE", "target_selector": {"type": "ENEMIES_IN_RANGE", "range": 1}, "value": 10, "damage_type": "PHYSICAL"},
                {"type": "BUFF_DEFENSE", "target_selector": {"type": "ENEMIES_IN_RANGE", "range": 1}, "value": "-stacks:RESOLVE", "duration": 3},
                {"type": "ADD_STACK", "target_selector": {"type": "SELF"}, "status_name": "RESOLVE", "value": "hit_count", "max": 5},
            ],
        },
        {
            "id": "carry_log",
            "name": "Carry the Log",
            "description": "Хватает врага в радиусе 1, тащит в свою зону и наносит 20 урона. При 3+ стаках — Стан на 1 ход.",
            "energy_cost": 3, "cooldown": 1, "is_quick": True, "range": 1,
            "execution_chain": [
                {"type": "PULL_TARGET", "target_selector": {"type": "CUSTOM_SELECT", "range": 1, "filter": "ENEMIES"}},
                {"type": "DAMAGE", "target_selector": {"type": "MAIN_TARGET"}, "value": 20, "damage_type": "PHYSICAL"},
                {
                    "type": "CONDITIONAL",
                    "conditions": [{"check": "self_status_at_least", "status_name": "RESOLVE", "value": 3}],
                    "if_true": [
                        {"type": "APPLY_STATUS", "target_selector": {"type": "MAIN_TARGET"}, "status_name": "STUN", "value": 1, "duration": 1},
                    ],
                },
            ],
        },
        {
            "id": "hell_week",
            "name": "Hell Week",
            "description": "Гоггинс активирует Hell Week — на 3 хода +18 к урону атаки, +4 защиты, +10 регенерации, иммунитет к дебаффам, и 1 урон всем врагам в радиусе 1 каждый ход. Сбрасывает все стаки Стойкости.",
            "energy_cost": 10, "cooldown": 4, "is_quick": True, "is_ult": True, "range": 0,
            "execution_chain": [
                {"type": "REMOVE_STATUS", "target_selector": {"type": "SELF"}, "status_name": "RESOLVE"},
                {"type": "BUFF_DEFENSE", "target_selector": {"type": "SELF"}, "value": 4, "duration": 3, "group": "HELL_WEEK"},
                {"type": "BUFF_STAT", "target_selector": {"type": "SELF"}, "stat": "REGENERATION", "value": 10, "duration": 3, "group": "HELL_WEEK"},
                {"type": "BUFF_STAT", "target_selector": {"type": "SELF"}, "stat": "ATTACK_DAMAGE", "value": 18, "duration": 3, "group": "HELL_WEEK"},
                {"type": "APPLY_STATUS", "target_selector": {"type": "SELF"}, "status_name": "HELL_WEEK", "value": 1, "duration": 3, "group": "HELL_WEEK"},
                {"type": "APPLY_STATUS", "target_selector": {"type": "SELF"}, "status_name": "DEBUFF_IMMUNE", "value": 1, "duration": 3, "group": "HELL_WEEK"},
            ],
        },
    ],
    "passives": [
        {
            "id": "mental_toughness",
            "name": "Ментальная Стойкость",
            "description": "Каждый раз, получая урон, Гоггинс получает 1 стак Стойкости (макс. 5). Стаки сбрасываются при смерти.",
            "trigger_event": "BEFORE_TAKE_DAMAGE",
            "execution_chain": [
                {"type": "ADD_STACK", "target_selector": {"type": "SELF"}, "status_name": "RESOLVE", "value": 1, "max": 5},
                {
                    # Reaching 5 stacks instantly auto-casts Hell Week (off CD).
                    "type": "CONDITIONAL",
                    "conditions": [
                        {"check": "self_status_at_least", "status_name": "RESOLVE", "value": 5},
                        {"check": "self_ability_ready", "ability_id": "hell_week"},
                    ],
                    "if_true": [{"type": "TRIGGER_ABILITY", "ability_id": "hell_week"}],
                },
            ],
        },
        {
            "id": "unbreakable",
            "name": "Несгибаемость",
            "description": "В начале своего хода: +1 энергия за каждый стак Стойкости. При 5 стаках авто-активирует Hell Week (если не на КД). Во время Hell Week — 10 урона врагам в радиусе 1.",
            "trigger_event": "ON_TURN_START",
            "execution_chain": [
                {"type": "ENERGY_GAIN", "target_selector": {"type": "SELF"}, "value": "stacks:RESOLVE"},
                {
                    "type": "CONDITIONAL",
                    "conditions": [
                        {"check": "self_status_at_least", "status_name": "RESOLVE", "value": 5},
                        {"check": "self_ability_ready", "ability_id": "hell_week"},
                    ],
                    "if_true": [
                        {"type": "TRIGGER_ABILITY", "ability_id": "hell_week"},
                    ],
                },
                {
                    "type": "CONDITIONAL",
                    "conditions": [{"check": "self_has_status", "status_name": "HELL_WEEK"}],
                    "if_true": [
                        {"type": "DAMAGE", "target_selector": {"type": "ENEMIES_IN_RANGE", "range": 1}, "value": 10, "damage_type": "MAGICAL"},
                    ],
                },
            ],
        },
    ],
}
