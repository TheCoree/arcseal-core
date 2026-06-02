"""Бугай-танк с оглушением."""

CHARACTER = {
    "id": "char_mountain",
    "name": "Горный Молот",
    "role": "BRUISER",
    "initial_position_type": "FRONTLINE",
    "base_stats": {
        "hp": 105, "defense": 7,
        "energy_regen": 2, "max_energy": 10,
        "regeneration": 0, "initiative": 0, "movement_range": 1,
    },
    "attack": {
        "name": "Сокрушающий удар",
        "description": "Тяжёлый удар молотом; оглушает цель на один ход.",
        "range": 1,
        "damage_schema": {"type": "FIXED", "value": 11},
        "execution_chain": [
            {
                "type": "APPLY_STATUS",
                "target_selector": {"type": "MAIN_TARGET"},
                "status_name": "STUN",
                "value": 1, "duration": 1,
            },
        ],
    },
    "abilities": [{
        "id": "earthquake",
        "name": "Землетрясение",
        "description": "Удар по земле — урон всем в выбранной зоне.",
        "energy_cost": 7, "cooldown": 3, "is_quick": False,
        "execution_chain": [
            {
                "type": "DAMAGE",
                "target_selector": {"type": "SAME_ZONE", "filter": "ENEMIES"},
                "value": 9,
            },
        ],
    }],
    "passives": [{
        "id": "bastion",
        "name": "Бастион",
        "description": "+2 защиты в начале раунда, если HP ниже 50%.",
        "trigger_event": "ON_ROUND_START",
        "conditions": [{"check": "self_hp_below_pct", "value": 50}],
        "execution_chain": [
            {
                "type": "BUFF_DEFENSE",
                "target_selector": {"type": "SELF"},
                "value": 2, "duration": 1,
            },
        ],
    }],
}
