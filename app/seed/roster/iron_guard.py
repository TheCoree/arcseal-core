"""Танк со щитами."""

CHARACTER = {
    "id": "char_iron_guard",
    "name": "Железный Страж",
    "role": "TANK",
    "initial_position_type": "FRONTLINE",
    "base_stats": {
        "hp": 105, "defense": 8,
        "energy_regen": 3, "max_energy": 12,
        "regeneration": 0, "initiative": 0, "movement_range": 1,
    },
    "attack": {
        "name": "Тяжёлый удар",
        "description": "Мощный удар закованной в латы рукой по противнику вплотную.",
        "range": 1,
        "damage_schema": {"type": "FIXED", "value": 12},
        "execution_chain": [],
    },
    "abilities": [{
        "id": "shield",
        "name": "Энергетический щит",
        "range": 1,
        "description": "Поднимает защиту себе и соседним союзникам на 2 раунда.",
        "energy_cost": 5, "cooldown": 3, "is_quick": True,
        "execution_chain": [
            {
                "type": "BUFF_DEFENSE",
                "target_selector": {"type": "ALLIES_IN_RANGE", "range": 1, "filter": "ALLIES"},
                "value": 3, "duration": 2,
            },
        ],
    }],
    "passives": [{
        "id": "spiked_armor",
        "name": "Шипованная броня",
        "description": "Возвращает 5 урона любому атакующему в ближнем бою.",
        "trigger_event": "BEFORE_TAKE_DAMAGE",
        "conditions": [{"check": "attacker_range", "value": 1}],
        "execution_chain": [
            {
                "type": "DAMAGE",
                "target_selector": {"type": "ATTACKER"},
                "value": 5,
            },
        ],
    }],
}
