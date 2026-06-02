"""Илон Маск — поддержка-перемещатель с аурой радиуса (Starlink)."""

CHARACTER = {
    "id": "elon_musk",
    "name": "Илон Маск",
    "portrait_url": "/uploads/characters/portraits/elon_musk.png",
    "role": "SUPPORT",
    "initial_position_type": "BACKLINE",
    "base_stats": {
        "hp": 110, "defense": 5,
        "energy_regen": 2, "max_energy": 13,
        "regeneration": 0, "initiative": 0, "movement_range": 1,
    },
    "attack": {
        "name": "Tweet бомба",
        "description": "Публичное унижение врага в твиттере: 11 урона и цель теряет 1 энергию.",
        "range": 1,
        "damage_schema": {"type": "FIXED", "value": 11},
        "execution_chain": [
            {
                "type": "ENERGY_LOSS",
                "target_selector": {"type": "MAIN_TARGET"},
                "value": 1,
            },
        ],
    },
    "abilities": [
        {
            "id": "tesla",
            "name": "Tesla в кар",
            "description": "Сажаешь союзника в Tesla Cybertruck и перемещаешь его в любую соседнюю зону (игнорируя изоляцию).",
            "energy_cost": 1, "cooldown": 1, "is_quick": True, "range": 1,
            "execution_chain": [
                {
                    "type": "MOVE",
                    "subject": "TARGET",
                    "target_selector": {"type": "CUSTOM_SELECT", "range": 1, "filter": "ALLIES"},
                    "move_range": 1,
                },
                {
                    # The passenger ignores isolation until their next turn.
                    "type": "APPLY_STATUS",
                    "target_selector": {"type": "CUSTOM_SELECT"},
                    "status_name": "ISO_IMMUNE",
                    "value": 1, "duration": 1,
                },
            ],
        },
        {
            "id": "neuralink",
            "name": "Neuralink-Синхронизация",
            "description": "Два союзника в радиусе 2 меняются местами (телепорт). Каждый получает +1 энергии.",
            "energy_cost": 2, "cooldown": 0, "is_quick": False, "range": 2,
            "execution_chain": [
                {"type": "SWAP_POSITIONS"},
                {
                    "type": "ENERGY_GAIN",
                    "target_selector": {"type": "CUSTOM_PAIR"},
                    "value": 1,
                },
            ],
        },
        {
            "id": "mars",
            "name": "Полёт на Марс",
            "description":
                "Все союзники в радиусе 1 садятся в Starship и летят на Марс: +30 HP и +3 энергии. "
                "Затем высаживаются в любую выбранную зону — все враги в ней оглушены на 1 ход.",
            "energy_cost": 5, "cooldown": 4, "is_quick": False, "is_ult": True,
            "execution_chain": [
                {
                    "type": "HEAL",
                    "target_selector": {"type": "ALLIES_IN_RANGE", "range": 1, "filter": "ALLIES"},
                    "value": 30,
                },
                {
                    "type": "ENERGY_GAIN",
                    "target_selector": {"type": "ALLIES_IN_RANGE", "range": 1, "filter": "ALLIES"},
                    "value": 3,
                },
                {
                    "type": "RELOCATE_GROUP",
                    "target_selector": {"type": "ALLIES_IN_RANGE", "range": 1, "filter": "ALLIES"},
                },
                {
                    "type": "APPLY_STATUS",
                    "target_selector": {"type": "ENEMIES_IN_ZONE"},
                    "status_name": "STUN",
                    "value": 1, "duration": 1,
                },
            ],
        },
    ],
    "passives": [
        {
            "id": "starlink",
            "name": "Starlink-Сеть",
            "description": "Аура: все способности союзников (включая себя) получают +1 к радиусу.",
            "trigger_event": "AURA",
            "execution_chain": [
                {
                    "type": "GRANT_MODIFIER",
                    "target_selector": {"type": "ALL_ALLIES"},
                    "stat": "ABILITY_RANGE",
                    "value": 1,
                },
            ],
        },
    ],
}
