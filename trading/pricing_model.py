def calculate_entry_premium(spot_price: float) -> float:
    """
    Simulates the entry premium for an At-The-Money (ATM) option.
    Models entry premium as 0.75% of the underlying spot index price.
    """
    return round(spot_price * 0.0075, 2)

def calculate_current_premium(
    entry_price: float,
    entry_spot: float,
    current_spot: float,
    option_type: str
) -> float:
    """
    Dynamically estimates the current premium of an option using the Delta Greek.
    For ATM options:
        - Call option (CE) Delta is approximately +0.50
        - Put option (PE) Delta is approximately -0.50
        
    Formula:
        Current Premium = Entry Premium + (Delta * (Current Spot - Entry Spot))
    
    Enforces a floor of 1.0 to prevent option premium from going negative or becoming zero.
    """
    delta = 0.50 if option_type.upper() == "CE" else -0.50
    spot_diff = current_spot - entry_spot
    current_premium = entry_price + (delta * spot_diff)
    
    # Enforce minimum option value floor (option contract cannot trade below zero)
    return max(round(current_premium, 2), 1.00)
