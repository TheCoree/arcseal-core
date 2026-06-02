"""Хилер с аурой регенерации."""

CHARACTER = {
    "id": "char_priestess",
    "name": "Боевая Жрица",
    "role": "SUPPORT",
    "initial_position_type": "BACKLINE",
    "base_stats": {
        "hp": 80, "defense": 3,
        "energy_regen": 5, "max_energy": 15,
        "regeneration": 2, "initiative": 0, "movement_range": 1,
    },
    "attack": {
        "name": "Священное копьё",
        "description": "Удар освящённым копьём на средней дистанции.",
        "range": 2,
        "damage_schema": {"type": "FIXED", "value": 8},
        "execution_chain": [],
    },
    "abilities": [{
        "id": "blessing",
        "name": "Благословение",
        "description": "Лечит союзников рядом и накладывает регенерацию на 3 хода.",
        "energy_cost": 6, "cooldown": 3, "is_quick": True,
        "execution_chain": [
            {
                "type": "HEAL",
                "target_selector": {"type": "ALLIES_IN_RANGE", "range": 1, "filter": "ALLIES"},
                "value": 12,
            },
            {
                "type": "APPLY_STATUS",
                "target_selector": {"type": "ALLIES_IN_RANGE", "range": 1, "filter": "ALLIES"},
                "status_name": "REGEN",
                "value": 4, "duration": 3,
            },
        ],
    }],
    "passives": [{
        "id": "vigor_aura",
        "name": "Аура бодрости",
        "description": "Все союзники получают +1 энергии в начале каждого раунда.",
        "trigger_event": "ON_ROUND_START",
        "execution_chain": [
            {
                "type": "ENERGY_GAIN",
                "target_selector": {"type": "ALL_ALLIES"},
                "value": 1,
            },
        ],
    }],
}
