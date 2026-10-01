"""Патрик Джейн («Менталист») — поддержка-контроль со стаками «Наблюдательность».

Механика стаков INSIGHT: каждое действие, задевшее врага, даёт 1 стак (макс 3).
Стаки либо копятся — при 3 стаках в начале раунда вся команда получает
энергию, — либо сжигаются «Гипнозом», который отнимает у цели энергию по числу
стаков. «Чашка чая» снимает с союзников Оглушение и Яд, ульта «Тигр, тигр…»
добивает раненую цель.
"""

CHARACTER = {
    "id": "patrick_jane",
    "name": "Патрик Джейн",
    "portrait_url": "/uploads/characters/portraits/patrick_jane.jpg",
    "role": "SUPPORT",
    "initial_position_type": "BACKLINE",
    "base_stats": {
        "hp": 85, "defense": 3,
        "energy_regen": 4, "max_energy": 14,
        "regeneration": 1, "initiative": 0, "movement_range": 1,
    },
    "attack": {
        "name": "Холодное чтение",
        "description": "Джейн по мелочам вычисляет все секреты врага и бьёт по больному — 10 урона, игнорирующего защиту.",
        "range": 2,
        "damage_schema": {"type": "FIXED", "value": 10},
        "damage_type": "MAGICAL",
        "execution_chain": [],
    },
    "abilities": [
        {
            "id": "hypnosis",
            "name": "Гипноз",
            "description": "Щелчок пальцами — враг в радиусе 2 впадает в транс: Оглушение на 1 ход и потеря энергии, равная стакам Наблюдательности. Стаки сгорают.",
            "energy_cost": 4, "cooldown": 3, "is_quick": False, "range": 2,
            "execution_chain": [
                {"type": "APPLY_STATUS", "target_selector": {"type": "CUSTOM_SELECT", "range": 2, "filter": "ENEMIES"}, "status_name": "STUN", "value": 1, "duration": 1},
                {"type": "ENERGY_LOSS", "target_selector": {"type": "CUSTOM_SELECT"}, "value": "stacks:INSIGHT"},
                {"type": "REMOVE_STATUS", "target_selector": {"type": "SELF"}, "status_name": "INSIGHT"},
            ],
        },
        {
            "id": "cup_of_tea",
            "name": "Чашка чая",
            "description": "Джейн заваривает чай на всех: союзники в радиусе 1 (включая его самого) восстанавливают 10 HP и избавляются от Оглушения и Яда.",
            "energy_cost": 3, "cooldown": 2, "is_quick": True, "range": 1,
            "execution_chain": [
                {"type": "HEAL", "target_selector": {"type": "ALLIES_IN_RANGE", "range": 1, "filter": "ALLIES"}, "value": 10},
                {"type": "REMOVE_STATUS", "target_selector": {"type": "ALLIES_IN_RANGE", "range": 1, "filter": "ALLIES"}, "status_name": "STUN"},
                {"type": "REMOVE_STATUS", "target_selector": {"type": "ALLIES_IN_RANGE", "range": 1, "filter": "ALLIES"}, "status_name": "POISON"},
            ],
        },
        {
            "id": "tyger_tyger",
            "name": "Тигр, тигр…",
            "description": "Джейн дочитывает стих Красного Джона: 18 урона врагу в радиусе 3, игнорирующего защиту. Если после удара у цели меньше 40% HP — ещё 22 урона.",
            "energy_cost": 9, "cooldown": 4, "is_quick": False, "is_ult": True, "range": 3,
            "execution_chain": [
                {"type": "DAMAGE", "target_selector": {"type": "CUSTOM_SELECT", "range": 3, "filter": "ENEMIES"}, "value": 18, "damage_type": "MAGICAL"},
                {
                    # Checked after the first hit, so the opener can set up the execute.
                    "type": "CONDITIONAL",
                    "conditions": [{"check": "target_hp_below_pct", "value": 40}],
                    "if_true": [
                        {"type": "DAMAGE", "target_selector": {"type": "MAIN_TARGET"}, "value": 22, "damage_type": "MAGICAL"},
                    ],
                },
            ],
        },
    ],
    "passives": [
        {
            "id": "observation",
            "name": "Наблюдательность",
            "description": "После каждой атаки или способности, задевшей врага, Джейн получает 1 стак Наблюдательности (макс. 3).",
            "trigger_event": "AFTER_ACTION",
            "execution_chain": [
                {"type": "ADD_STACK", "target_selector": {"type": "SELF"}, "status_name": "INSIGHT", "value": 1, "max": 3},
            ],
        },
        {
            "id": "cbi_consultant",
            "name": "Консультант КБР",
            "description": "Джейн просчитывает план врага наперёд: в начале раунда при 3 стаках Наблюдательности все союзники получают +1 энергии.",
            "trigger_event": "ON_ROUND_START",
            "conditions": [{"check": "self_status_at_least", "status_name": "INSIGHT", "value": 3}],
            "execution_chain": [
                {"type": "ENERGY_GAIN", "target_selector": {"type": "ALL_ALLIES"}, "value": 1},
            ],
        },
    ],
}
