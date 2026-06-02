def get_rank_from_elo(elo: int) -> str:
    """Calculate the player's rank dynamically based on their ELO rating.

    Args:
        elo (int): Player's matchmaking rating (Elo).

    Returns:
        str: Player's rank name.
    """
    if elo < 600:
        return "Bronze"
    elif elo < 1000:
        return "Silver"
    elif elo < 1400:
        return "Gold"
    elif elo < 1800:
        return "Platinum"
    elif elo < 2200:
        return "Diamond"
    elif elo < 2600:
        return "Master"
    else:
        return "Elite"
