"""Ислам Махачев — DPS с вероятностной добивающей пассивкой."""

CHARACTER = {
    "id": "islam_mahachev",
    "name": "Ислам Махачев",
    "portrait_url": "/uploads/characters/portraits/islam_mahachev.png",
    "role": "DPS",
    "initial_position_type": "FRONTLINE",
    "base_stats": {
        "hp": 135, "defense": 8,
        "energy_regen": 2, "max_energy": 10,
        "regeneration": 0, "initiative": 0, "movement_range": 1,
    },
    "attack": {
        "name": "Прямой удар",
        "description": "Наносит точный, прямой удар по цели.",
        "range": 1,
        "damage_schema": {"type": "FIXED", "value": 18},
        "execution_chain": [],
    },
    "abilities": [{
        "id": "throw",
        "name": "Бросок",
        "range": 1,
        "description": "Хватает и бросает врага: 12 урона и оглушение на 1 ход.",
        "energy_cost": 4, "cooldown": 1, "is_quick": False,
        "execution_chain": [
            {
                "type": "DAMAGE",
                "target_selector": {"type": "MAIN_TARGET"},
                "value": 12,
            },
            {
                "type": "APPLY_STATUS",
                "target_selector": {"type": "MAIN_TARGET"},
                "status_name": "STUN",
                "value": 1, "duration": 1,
            },
        ],
    }],
    "passives": [{
        "id": "one_two",
        "name": "Двоечка",
        "description": "С вероятностью 35% после своей атаки или способности по врагам наносит всем задетым целям дополнительный урон, равный урону его атаки.",
        "trigger_event": "AFTER_ACTION",
        "conditions": [{"check": "random_chance", "value": 35}],
        "execution_chain": [
            {
                "type": "DAMAGE",
                "target_selector": {"type": "LAST_HIT_ENEMIES"},
                "value": "self.attack_damage",
            },
        ],
    }],
}
