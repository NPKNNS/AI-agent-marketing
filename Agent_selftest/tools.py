"""Domain valuation equations and helper calculations for Thai Robusta Coffee."""

from Agent_selftest.models import KG_PER_SACK
from Agent_selftest.config import CompetitionConfig

# Thai Robusta Grading Value Multipliers (can be overridden by CompetitionConfig)
GRADE_MULTIPLIERS = CompetitionConfig.GRADE_MULTIPLIERS

def get_grade_multiplier(grade: str, custom_multipliers: dict = None) -> float:
    multipliers = custom_multipliers or GRADE_MULTIPLIERS
    g = grade.upper().replace("GRADE ", "").strip()
    return multipliers.get(g, 1.00)

def calculate_grade_fair_price(base_market_price: float, grade: str, custom_multipliers: dict = None) -> float:
    """Calculates fair market price in THB/kg for a specific coffee grade."""
    multiplier = get_grade_multiplier(grade, custom_multipliers)
    return round(base_market_price * multiplier, 2)

def calculate_distress_liquidation_price(
    final_market_price: float,
    grade: str,
    discount_rate: float = CompetitionConfig.DISTRESS_LIQUIDATION_RATE,
    custom_multipliers: dict = None
) -> float:
    """
    Competition Rule:
    Any stock left over at the end of the final round is liquidated at discount_rate (default 80%)
    of the market price after grade cut or up.
    """
    graded_market_price = calculate_grade_fair_price(final_market_price, grade, custom_multipliers)
    return round(graded_market_price * discount_rate, 2)

def calculate_batch_revenue(sacks: int, price_per_kg: float) -> float:
    """Calculates total revenue in THB for a given number of 60kg sacks."""
    return round(sacks * KG_PER_SACK * price_per_kg, 2)
