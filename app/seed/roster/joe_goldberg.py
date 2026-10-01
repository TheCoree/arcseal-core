"""Джо Голдберг («Ты») — ассасин-сталкер с меткой «Одержимость».

Механика метки OBSESSION: «Сталкинг в соцсетях» вешает её на врага. По
одержимой цели атака бьёт сильнее, пассивка «Всё ради любви» лечит Джо, а
ульта «Стеклянная клетка» сжигает метку ради большого урона. «Идеальное
алиби» спасает Джо при низком HP один раз за жизнь (флаг ALIBI живёт до
смерти — смерть очищает все статусы).
"""

CHARACTER = {
    "id": "joe_goldberg",
    "name": "Джо Голдберг",
    "portrait_url": "/uploads/characters/portraits/joe_goldberg.jpg",
    "role": "ASSASSIN",
    "initial_position_type": "FRONTLINE",
    "base_stats": {
        "hp": 95, "defense": 4,
        "energy_regen": 3, "max_energy": 12,
        "regeneration": 0, "initiative": 0, "movement_range": 1,
    },
    "attack": {
        "name": "Hello, you.",
        "description": "Джо выходит из тени с тихим «Hello, you» — 14 урона. По цели под Одержимостью ещё +8 урона, игнорирующего защиту.",
        "range": 1,
        "damage_schema": {"type": "FIXED", "value": 14},
        "damage_type": "PHYSICAL",
        "execution_chain": [
            {
                "type": "CONDITIONAL",
                "conditions": [{"check": "target_has_status", "status_name": "OBSESSION"}],
                "if_true": [
                    {"type": "DAMAGE", "target_selector": {"type": "MAIN_TARGET"}, "value": 8, "damage_type": "MAGICAL"},
                ],
            },
        ],
    },
    "abilities": [
        {
            "id": "social_stalking",
            "name": "Сталкинг в соцсетях",
            "description": "Джо изучает Instagram врага в радиусе 3: вешает Одержимость на 3 хода, цель теряет 1 энергию.",
            "energy_cost": 2, "cooldown": 2, "is_quick": True, "range": 3,
            "execution_chain": [
                {"type": "APPLY_STATUS", "target_selector": {"type": "CUSTOM_SELECT", "range": 3, "filter": "ENEMIES"}, "status_name": "OBSESSION", "value": 1, "duration": 3},
                {"type": "ENERGY_LOSS", "target_selector": {"type": "CUSTOM_SELECT"}, "value": 1},
            ],
        },
        {
            "id": "lose_in_crowd",
            "name": "Затеряться в толпе",
            "description": "Кепка, капюшон — и Джо растворяется в толпе: перемещается на расстояние до 2 зон и 2 хода не получает Изоляцию.",
            "energy_cost": 3, "cooldown": 2, "is_quick": True, "range": 2,
            "execution_chain": [
                {"type": "MOVE", "target_selector": {"type": "CUSTOM_SELECT", "range": 2, "filter": "ALL"}},
                {"type": "APPLY_STATUS", "target_selector": {"type": "SELF"}, "status_name": "ISO_IMMUNE", "value": 1, "duration": 2},
            ],
        },
        {
            "id": "glass_cage",
            "name": "Стеклянная клетка",
            "description": "Джо затаскивает врага из радиуса 1 в свою зону и запирает в клетке: Оглушение на 1 ход и −4 защиты на 2 хода. Если цель была под Одержимостью — метка сгорает и цель получает 28 урона, игнорирующего защиту; иначе 12.",
            "energy_cost": 8, "cooldown": 4, "is_quick": False, "is_ult": True, "range": 1,
            "execution_chain": [
                {"type": "PULL_TARGET", "target_selector": {"type": "CUSTOM_SELECT", "range": 1, "filter": "ENEMIES"}},
                {"type": "APPLY_STATUS", "target_selector": {"type": "MAIN_TARGET"}, "status_name": "STUN", "value": 1, "duration": 1},
                {"type": "BUFF_DEFENSE", "target_selector": {"type": "MAIN_TARGET"}, "value": -4, "duration": 2},
                {
                    "type": "CONDITIONAL",
                    "conditions": [{"check": "target_has_status", "status_name": "OBSESSION", "consume": True}],
                    "if_true": [
                        {"type": "DAMAGE", "target_selector": {"type": "MAIN_TARGET"}, "value": 28, "damage_type": "MAGICAL"},
                    ],
                    "if_false": [
                        {"type": "DAMAGE", "target_selector": {"type": "MAIN_TARGET"}, "value": 12, "damage_type": "MAGICAL"},
                    ],
                },
            ],
        },
    ],
    "passives": [
        {
            "id": "for_love",
            "name": "Всё ради любви",
            "description": "«Всё, что я делаю, — ради тебя». После атаки или способности по цели под Одержимостью Джо восстанавливает 8 HP и 1 энергию.",
            "trigger_event": "AFTER_ACTION",
            "conditions": [{"check": "target_has_status", "status_name": "OBSESSION"}],
            "execution_chain": [
                {"type": "HEAL", "target_selector": {"type": "SELF"}, "value": 8},
                {"type": "ENERGY_GAIN", "target_selector": {"type": "SELF"}, "value": 1},
            ],
        },
        {
            "id": "perfect_alibi",
            "name": "Идеальное алиби",
            "description": "Раз за жизнь: когда после удара HP Джо падает ниже 35%, он становится «другим человеком» — восстанавливает 20 HP и получает +4 защиты на 2 хода.",
            "trigger_event": "BEFORE_TAKE_DAMAGE",
            "conditions": [{"check": "self_hp_below_pct", "value": 35}],
            "execution_chain": [
                {
                    # No ALIBI flag yet → this life's alibi is still unused.
                    "type": "CONDITIONAL",
                    "conditions": [{"check": "self_has_status", "status_name": "ALIBI"}],
                    "if_false": [
                        {"type": "HEAL", "target_selector": {"type": "SELF"}, "value": 20},
                        {"type": "BUFF_DEFENSE", "target_selector": {"type": "SELF"}, "value": 4, "duration": 2, "group": "ALIBI"},
                        {"type": "APPLY_STATUS", "target_selector": {"type": "SELF"}, "status_name": "ALIBI", "value": 1, "duration": -1, "group": "ALIBI"},
                    ],
                },
            ],
        },
    ],
}
