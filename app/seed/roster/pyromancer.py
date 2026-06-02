"""Маг-метконанос с детонацией."""

CHARACTER = {
    "id": "char_pyromancer",
    "name": "Пиромант",
    "role": "DPS",
    "initial_position_type": "BACKLINE",
    "base_stats": {
        "hp": 65, "defense": 2,
        "energy_regen": 4, "max_energy": 16,
        "regeneration": 0, "initiative": 0, "movement_range": 1,
    },
    "attack": {
        "name": "Огненная стрела",
        "description": "Быстрый сгусток пламени по выбранной цели.",
        "range": 2,
        "damage_schema": {"type": "FIXED", "value": 11},
        "damage_type": "MAGICAL",
        "execution_chain": [],
    },
    "abilities": [{
        "id": "flame_mark",
        "name": "Метка пламени",
        "description":
            "Накладывает метку-бомбу. При повторной активации детонирует: "
            "18 урона цели и 8 всем в её зоне, метка сгорает.",
        "energy_cost": 7, "cooldown": 2, "is_quick": False,
        "execution_chain": [
            {
                "type": "CONDITIONAL",
                "conditions": [
                    {
                        "check": "target_has_status",
                        "status_name": "MARK_BOMB",
                        "consume": True,
                    },
                ],
                "if_true": [
                    {
                        "type": "DAMAGE",
                        "target_selector": {"type": "MAIN_TARGET"},
                        "value": 27,
                        "damage_type": "MAGICAL",
                    },
                    {
                        "type": "DAMAGE",
                        "target_selector": {"type": "SAME_ZONE", "filter": "ENEMIES"},
                        "value": 12,
                        "damage_type": "MAGICAL",
                    },
                ],
                "if_false": [
                    {
                        "type": "APPLY_STATUS",
                        "target_selector": {"type": "MAIN_TARGET"},
                        "status_name": "MARK_BOMB",
                        "value": 1, "duration": 3,
                    },
                ],
            },
        ],
    }],
    "passives": [{
        "id": "fire_trail",
        "name": "Огненный след",
        "description":
            "После перемещения накладывает яд на 2 хода всем противникам в новой зоне.",
        "trigger_event": "ON_ZONE_CHANGE",
        "execution_chain": [
            {
                "type": "APPLY_STATUS",
                "target_selector": {"type": "SAME_ZONE", "filter": "ENEMIES"},
                "status_name": "POISON",
                "value": 5, "duration": 2,
            },
        ],
    }],
}
