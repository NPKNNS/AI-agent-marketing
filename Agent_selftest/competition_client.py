"""Competition Client Template for TESA / Coffee AI Agent Challenge.

Provides ready-to-use hooks for connecting your Champion Agent to the official competition:
1. REST API / Webhook Mode (HTTP POST/GET)
2. File-based Mode (Reading state.json / Writing action.json)
3. Direct Python Module Integration (def get_action(game_state))

How to customize if competition rules change:
- Adjust parameters in config.py (GRADE_MULTIPLIERS, DISTRESS_LIQUIDATION_RATE, etc.)
"""

import os
import json
import time
import argparse
from typing import Dict, Any
from Agent_selftest.champion_agent import ChampionRobustaAgent
from Agent_selftest.config import CompetitionConfig

class CompetitionRunner:
    """Wrapper to run your Champion Agent in the official competition environment."""

    def __init__(self, agent_name: str = "🏆 Apex-ChampionAI"):
        self.agent = ChampionRobustaAgent(name=agent_name)
        print(f"[{self.agent.name}] Initialized and ready for competition.")

    def on_round(self, round_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Universal entrypoint for each competition round.
        
        Expected round_data schema (or similar from host):
        {
            "round": 1,
            "total_rounds": 15,
            "base_price": 112.50,
            "inventory": {"A": 10, "B": 10, "C": 10},
            "buyers": [
                {"id": 1, "name": "Specialty Roaster", "grades": ["A"], "demand": 3, "max_price": 118.0},
                ...
            ]
        }
        """
        t0 = time.time()
        decision = self.agent.decide_from_payload(round_data)
        elapsed = time.time() - t0

        print(f"\n--- [Round {decision.get('round')}] Decision Generated in {elapsed:.2f}s ---")
        print(f"💭 Strategy Thought: {decision.get('thought')}")
        for off in decision.get("offers", []):
            print(f"👉 Offer: Buyer {off['buyer_id']} | Grade {off['grade']} | {off['sacks']} sacks @ {off['price_per_kg']:.2f} THB/kg")

        return decision

    def run_file_mode(self, input_file: str = "state.json", output_file: str = "action.json"):
        """Reads game state from input_file and writes action to output_file."""
        if not os.path.exists(input_file):
            print(f"Waiting for {input_file}...")
            return

        with open(input_file, "r", encoding="utf-8") as f:
            state = json.load(f)

        action = self.on_round(state)

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(action, f, indent=2)
        print(f"✅ Action written to {output_file}")

# Standalone simulation / verification test
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Competition Client")
    parser.add_argument("--test-mock", action="store_true", help="Run a quick mock round to verify connectivity")
    args = parser.parse_args()

    runner = CompetitionRunner()

    if args.test_mock:
        sample_state = {
            "round": 1,
            "total_rounds": 15,
            "base_price": 113.0,
            "inventory": {"A": 12, "B": 10, "C": 8},
            "buyers": [
                {"id": 1, "name": "Bangkok Specialty Roaster", "grades": ["A"], "demand": 3, "max_price": 119.50},
                {"id": 2, "name": "Domestic Commercial Roaster", "grades": ["A", "B"], "demand": 5, "max_price": 115.00},
                {"id": 3, "name": "Chumphon Export Hub", "grades": ["A", "B", "C"], "demand": 6, "max_price": 113.00},
                {"id": 4, "name": "Soluble Instant Coffee Plant", "grades": ["B", "C"], "demand": 8, "max_price": 110.00},
                {"id": 5, "name": "Ranong Depot Middleman", "grades": ["B", "C"], "demand": 4, "max_price": 107.50},
                {"id": 6, "name": "Southern Cooperative Mill", "grades": ["A", "B", "C"], "demand": 4, "max_price": 111.50}
            ]
        }
        runner.on_round(sample_state)

