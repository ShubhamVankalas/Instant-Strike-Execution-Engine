import math

def calculate_atm_strike(spot_price: float) -> int:
    """
    Calculates the At-The-Money (ATM) strike price for NIFTY options.
    Standard NIFTY option strikes are spaced in intervals of 50.
    Uses round-half-up logic to ensure stable midpoints.
    
    Examples:
        22432 -> 22450
        22424 -> 22400
        22425 -> 22450
    """
    return int(math.floor(spot_price / 50.0 + 0.5) * 50)
