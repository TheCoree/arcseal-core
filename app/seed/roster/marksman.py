"""Дальний DPS."""

CHARACTER = {
    "id": "char_marksman",
    "name": "Меткий Стрелок",
    "role": "DPS",
    "initial_position_type": "BACKLINE",
    "base_stats": {
        "hp": 70, "defense": 2,
        "energy_regen": 4, "max_energy": 14,
        "regeneration": 0, "initiative": 0, "movement_range": 1,
    },
    "attack": {
        "name": "Прицельный выстрел",
        "description": "Точный выстрел через половину поля. Дистанция 3 зоны.",
        "range": 3,
        "damage_schema": {"type": "FIXED", "value": 15},
        "execution_chain": [],
    },
    "abilities": [{
        "id": "piercing_volley",
        "name": "Пронзающий залп",
        "description": "Поражает выбранную цель и всех остальных в её зоне.",
        "energy_cost": 8, "cooldown": 3, "is_quick": False,
        "execution_chain": [
            {
                "type": "DAMAGE",
                "target_selector": {"type": "MAIN_TARGET"},
                "value": 11,
            },
            {
                "type": "DAMAGE",
                "target_selector": {"type": "SAME_ZONE", "filter": "ENEMIES"},
                "value": 6,
            },
        ],
    }],
    "passives": [{
        "id": "hawk_eye",
        "name": "Соколиный глаз",
        "description": "+3 урона ко всем атакам с дистанции 2.",
        "trigger_event": "ON_DEAL_DAMAGE",
        "conditions": [{"check": "attacker_range", "value": 2}],
        "execution_chain": [
            {
                "type": "DAMAGE",
                "target_selector": {"type": "MAIN_TARGET"},
                "value": 3,
            },
        ],
    }],
}
