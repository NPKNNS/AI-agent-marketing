"""Configuration for LM Studio connection, model parameters, and competition rules."""

# LM Studio Local API Endpoint
LM_STUDIO_API_URL = "http://localhost:1235/v1"
MODEL_NAME = "qwen/qwen3.5-9b"
API_KEY = "lm-studio"
LM_STUDIO_API_KEY = API_KEY

class CompetitionConfig:
    """
    Central parameters for the real coffee trading competition.
    If competition hosts change rules on match day, simply update values here.
    """
    # Tournament Structure
    TOTAL_ROUNDS: int = 15                    # Competition standard (or 6 for prototype)
    INITIAL_SACKS_PER_TEAM: int = 30          # Sacks allocated to each team (or 20)
    KG_PER_SACK: float = 60.0                 # Standard international Robusta coffee bag weight
    NUM_BUYERS: int = 6                       # Number of buyer bots (or 2)
    BUYER_DEMAND_RATIO: float = 0.90          # Total buyer capacity = 90% of all team inventory

    # Grading Equations & Multipliers
    # Fair Value = Market Base Price * Multiplier
    GRADE_MULTIPLIERS = {
        "A": 1.05,   # +5% premium for Specialty / High-screen
        "B": 1.00,   # Baseline standard commercial
        "C": 0.85,   # -15% discount for defects / smaller screen
    }

    # Penalties & Costs
    DISTRESS_LIQUIDATION_RATE: float = 0.80   # Leftover stock penalized at 80% of final market price
    STORAGE_COST_PER_SACK_ROUND: float = 0.0  # Storage/holding cost if introduced (e.g., 5.0 THB/sack/round)

    # Agent Strategic Tuning
    SAFETY_BUFFER_ROUNDS: int = 1             # Aim to fully liquidate 1 round before tournament ends
    MARGIN_UNDERCUT_PCT: float = 0.012        # 1.2% undercut below ceiling in balanced rounds
    AGGRESSIVE_UNDERCUT_PCT: float = 0.028    # 2.8% undercut when behind schedule
    PANIC_UNDERCUT_PCT: float = 0.045         # 4.5% undercut in final rounds to eliminate 20% loss risk
