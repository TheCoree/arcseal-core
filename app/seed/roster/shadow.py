"""Быстрый ассасин с рывком."""

CHARACTER = {
    "id": "char_shadow",
    "name": "Теневой Клинок",
    "role": "ASSASSIN",
    "initial_position_type": "FRONTLINE",
    "base_stats": {
        "hp": 80, "defense": 3,
        "energy_regen": 3, "max_energy": 12,
        "regeneration": 0, "initiative": 0, "movement_range": 2,
        # Infiltrator: dives into the enemy backline without the isolation
        # penalty (passive "Удар в спину" flavour).
        "isolation_immune": True,
    },
    "attack": {
        "name": "Парные кинжалы",
        "description": "Стремительный удар двумя клинками в ближнем бою.",
        "range": 1,
        "damage_schema": {"type": "FIXED", "value": 14},
        "execution_chain": [],
    },
    "abilities": [{
        "id": "smoke_dash",
        "name": "Дымовой рывок",
        "description":
            "Переместиться до 3 зон в любую сторону. Не тратит экшен-токен.",
        "energy_cost": 4, "cooldown": 2, "is_quick": True,
        "execution_chain": [
            {
                "type": "MOVE",
                "target_selector": {"type": "CUSTOM_SELECT", "range": 3, "filter": "ALL"},
            },
        ],
    }],
    "passives": [{
        "id": "backstab",
        "name": "Удар в спину",
        "description": "+6 урона при ударе по отравленной цели.",
        "trigger_event": "ON_DEAL_DAMAGE",
        "conditions": [
            {
                "check": "target_has_status",
                "status_name": "POISON",
                "consume": False,
            },
        ],
        "execution_chain": [
            {
                "type": "DAMAGE",
                "target_selector": {"type": "MAIN_TARGET"},
                "value": 6,
            },
        ],
    }],
}
