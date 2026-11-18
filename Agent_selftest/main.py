"""Entry point for the Robusta Green Coffee Trading Competition Simulation.

Usage:
  python main.py                     # Runs 6-round benchmark (20 sacks, 2 buyers) with Champion AI
  python main.py --competition       # Runs full 15-round competition (30 sacks, 6 buyers)
"""

import sys
import argparse
from Agent_selftest.tournament import CoffeeTournament

def main():
    parser = argparse.ArgumentParser(description="Robusta Coffee Trading Multi-Agent Tournament")
    parser.add_argument("--competition", action="store_true", help="Run full competition mode (15 rounds, 30 sacks, 6 buyers)")
    parser.add_argument("--rounds", type=int, default=None, help="Number of trading rounds")
    parser.add_argument("--sacks", type=int, default=None, help="Sacks per agent")
    parser.add_argument("--buyers", type=int, default=None, help="Number of buyer bots (2 or 6)")
    parser.add_argument("--benchmark", action="store_true", help="Run the original 4 benchmark personas without champion")

    args = parser.parse_args()

    if args.competition:
        rounds = args.rounds or 15
        sacks = args.sacks or 30
        buyers = args.buyers or 6
    else:
        rounds = args.rounds or 6
        sacks = args.sacks or 20
        buyers = args.buyers or 2

    include_champion = not args.benchmark

    tournament = CoffeeTournament(
        total_rounds=rounds,
        stock_per_agent=sacks,
        num_buyers=buyers,
        include_champion=include_champion
    )
    tournament.run_tournament()

if __name__ == "__main__":
    main()
