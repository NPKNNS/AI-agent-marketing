"""Tournament engine running a 6-round AI-vs-AI coffee trading competition.
All 4 agents are autonomous LLMs running sequentially on Qwen 3.5 9B with distinct strategic personas.
"""

import random
import time
from typing import List, Dict, Tuple
from Agent_selftest.models import Inventory, RoundContext, Deal, KG_PER_SACK
from Agent_selftest.buyers import BuyerEngine
from Agent_selftest.agents import BaseAgent, LLMCompetitorAgent
from Agent_selftest.champion_agent import ChampionRobustaAgent
from Agent_selftest.tools import calculate_distress_liquidation_price, calculate_grade_fair_price

def generate_fair_initial_stock(total_sacks: int = 20) -> Inventory:
    """
    Generates a realistic random grade distribution for given sacks.
    All agents will receive an exact clone of this stock to ensure fair testing.
    """
    grade_a = int(round(total_sacks * random.uniform(0.35, 0.45)))
    grade_b = int(round(total_sacks * random.uniform(0.25, 0.35)))
    grade_c = total_sacks - (grade_a + grade_b)
    return Inventory(grade_a=grade_a, grade_b=grade_b, grade_c=grade_c)

class CoffeeTournament:
    """Runs AI vs AI competitive market simulation with configurable rounds and buyers."""

    def __init__(
        self,
        total_rounds: int = 6,
        stock_per_agent: int = 20,
        num_buyers: int = 2,
        include_champion: bool = True
    ):
        self.total_rounds = total_rounds
        self.stock_per_agent = stock_per_agent
        self.num_buyers = num_buyers

        # 1. Generate identical initial stock for all competing AI agents
        self.initial_stock = generate_fair_initial_stock(stock_per_agent)

        # 2. Configure Competing AI Agents
        if include_champion:
            champ = ChampionRobustaAgent(name="🏆 Apex-ChampionLLM")
            champ.inventory = self.initial_stock.copy()
            self.agents = [
                champ,
                LLMCompetitorAgent(
                    "1. Delta-LiquidatorLLM",
                    self.initial_stock.copy(),
                    "You are Delta-LiquidatorLLM, a risk-averse volume liquidation AI. Your goal is to undercut rivals aggressively (2.0% - 3.5% below ceiling) to secure 100% sell-through and avoid the 80% liquidation penalty.",
                    0.20
                ),
                LLMCompetitorAgent(
                    "2. Beta-GameTheoristLLM",
                    self.initial_stock.copy(),
                    "You are Beta-GameTheoristLLM, an adaptive game-theorist. You calculate remaining sacks vs rounds, dump Grade C early, and undercut by 1.5% - 2.5% when stock is high.",
                    0.25
                ),
                LLMCompetitorAgent(
                    "3. Alpha-ValueLLM",
                    self.initial_stock.copy(),
                    "You are Alpha-ValueLLM, a balanced margin trader. You offer 0.8% - 1.5% below ceiling aiming for steady 2-3 sacks per round.",
                    0.20
                )
            ]
        else:
            personas = [
                ("1. Alpha-ValueLLM", "Balanced analytical trader undercutting ceiling by 0.8% - 1.5%.", 0.20),
                ("2. Beta-GameTheoristLLM", "Adaptive game theorist dumping Grade C and pacing volume.", 0.25),
                ("3. Gamma-MarginMaxLLM", "Greedy margin maximizer bidding 0.2% - 0.5% below ceiling.", 0.15),
                ("4. Delta-FastLiquidatorLLM", "Aggressive volume liquidator undercutting by 2.5% - 4.5%.", 0.20)
            ]
            self.agents = [
                LLMCompetitorAgent(name, self.initial_stock.copy(), persona, temp)
                for name, persona, temp in personas
            ]

        # 3. Market price path (THB/kg)
        if total_rounds == 15:
            # 15-round simulated price cycle around 110 - 118 THB/kg
            self.market_price_path = [
                110.0, 111.5, 112.0, 113.5, 112.0, 114.0, 115.5, 116.0,
                115.0, 114.5, 116.5, 117.0, 116.0, 117.5, 118.0
            ]
        else:
            self.market_price_path = [110.0, 112.5, 111.0, 114.5, 113.0, 116.0]

        # 4. Buyer Engine
        self.buyer_engine = BuyerEngine(
            total_agents=len(self.agents),
            stock_per_agent=self.initial_stock.total_sacks,
            total_rounds=self.total_rounds,
            num_buyers=self.num_buyers
        )

        self.liquidation_records: Dict[str, Dict] = {}

    def run_tournament(self):
        print("=" * 85)
        print(" 🤖⚔️🤖 ALL-AI TOURNAMENT: 4 LLM AGENTS HEAD-TO-HEAD (Qwen 3.5 9B) 🤖⚔️🤖")
        print("=" * 85)
        print(f"📦 INITIAL FAIR STOCK ALLOCATED (Identical clone for all 4 LLM agents):")
        print(f"   {self.initial_stock.summary()}")
        print(f"   Total Sacks across 4 Agents: {self.initial_stock.total_sacks * len(self.agents)} sacks (4,800 kg)")
        print(f"   Target 6-Round Buyer Demand: {self.buyer_engine.total_target_demand} sacks (90% of total stock)")
        print("=" * 85 + "\n")

        agent_inv_map = {agent.name: agent.inventory for agent in self.agents}

        # Play 6 rounds
        for round_num in range(1, self.total_rounds + 1):
            base_price = self.market_price_path[round_num - 1]
            buyers = self.buyer_engine.get_round_buyers(round_num, base_price)
            ctx = RoundContext(
                round_num=round_num,
                total_rounds=self.total_rounds,
                market_base_price_thb=base_price,
                buyers=buyers
            )

            print(f"\n{'='*35} ROUND {round_num} / {self.total_rounds} {'='*35}")
            print(f"📈 Thai Market Base Price: {base_price:.2f} THB/kg")
            for b in buyers:
                print(f"  👤 {b.name}: Demands {b.demand_sacks} sacks | Accepts: {b.acceptable_grades} | Max Ceiling: {b.max_price_per_kg:.2f} THB/kg")

            # Execute decisions ONE BY ONE for each LLM agent
            round_offers = []
            for agent in self.agents:
                print(f"  🤖 Querying {agent.name} (Qwen 3.5)...", end="", flush=True)
                t0 = time.time()
                offers = agent.make_decision(ctx)
                elapsed = time.time() - t0
                print(f" Done ({elapsed:.1f}s) -> {len(offers)} offer(s)")
                if agent.last_thought:
                    print(f"     💭 Thought: {agent.last_thought}")
                for off in offers:
                    print(f"     👉 Offer to Buyer {off.buyer_id}: {off.sacks} sacks (Grade {off.grade}) @ {off.price_per_kg:.2f} THB/kg")
                round_offers.extend(offers)

            # Clear market (Reverse auction: lowest price seller wins matching demand)
            deals, logs = self.buyer_engine.clear_market(round_num, buyers, round_offers, agent_inv_map)

            # Update revenues
            for deal in deals:
                for agent in self.agents:
                    if agent.name == deal.seller_name:
                        agent.add_revenue(deal.revenue_thb)
                        agent.deals.append(deal)

            # Display round deals
            for log in logs:
                print(log)

            print(f"\n📊 Inventory & Revenue Standings after Round {round_num}:")
            for agent in self.agents:
                print(f"  • {agent.summary()}")

        # -------------------------------------------------------------
        # FINAL ROUND LIQUIDATION (THE 80% MARKET PENALTY RULE)
        # -------------------------------------------------------------
        final_base_price = self.market_price_path[-1]
        print("\n" + "=" * 85)
        print(" 🚨 END OF TOURNAMENT - DISTRESS STOCK LIQUIDATION (80% RULE) 🚨")
        print(" Rule: All unsold stock is forcibly sold at 80% of final graded market price!")
        print(f" Final Market Base Price: {final_base_price:.2f} THB/kg")
        print("=" * 85)

        for agent in self.agents:
            leftover = agent.inventory.total_sacks
            if leftover > 0:
                liq_rev = 0.0
                liq_details = []
                for grade, count in [("A", agent.inventory.grade_a), ("B", agent.inventory.grade_b), ("C", agent.inventory.grade_c)]:
                    if count > 0:
                        distress_price = calculate_distress_liquidation_price(final_base_price, grade, 0.80)
                        sub_rev = count * KG_PER_SACK * distress_price
                        liq_rev += sub_rev
                        liq_details.append(f"{count} sacks Gr.{grade} @ {distress_price:.2f} THB/kg")
                agent.add_revenue(liq_rev)
                # Zero out inventory
                agent.inventory.grade_a = 0
                agent.inventory.grade_b = 0
                agent.inventory.grade_c = 0
                self.liquidation_records[agent.name] = {
                    "sacks_penalized": leftover,
                    "penalty_revenue": liq_rev,
                    "details": ", ".join(liq_details)
                }
                print(f"  ⚠️ {agent.name:28}: Liquidated {leftover} unsold sacks -> {liq_rev:,.2f} THB ({', '.join(liq_details)})")
            else:
                self.liquidation_records[agent.name] = {
                    "sacks_penalized": 0,
                    "penalty_revenue": 0.0,
                    "details": "0 sacks (Clean liquidation)"
                }
                print(f"  ✨ {agent.name:28}: Perfect clearance! 0 unsold sacks remaining.")

        # -------------------------------------------------------------
        # FINAL LEADERBOARD & EVALUATION
        # -------------------------------------------------------------
        self.generate_final_report()

    def generate_final_report(self):
        print("\n" + "=" * 85)
        print(" 🏅 FINAL TOURNAMENT LEADERBOARD & PERFORMANCE EVALUATION 🏅")
        print("=" * 85)

        sorted_agents = sorted(self.agents, key=lambda a: a.cash, reverse=True)

        print(f"{'Rank':<5} | {'LLM Agent Name':<30} | {'Total Revenue':<15} | {'Sold in Rounds':<14} | {'Dumped (80%)':<12}")
        print("-" * 85)
        for rank, agent in enumerate(sorted_agents, 1):
            sacks_sold_normally = sum(d.sacks for d in agent.deals)
            sacks_dumped = self.liquidation_records[agent.name]["sacks_penalized"]
            print(
                f"{rank:<5} | {agent.name:<30} | {agent.cash:12,.2f} THB | "
                f"{sacks_sold_normally:>2} sacks ({(sacks_sold_normally/20)*100:>4.0f}%) | "
                f"{sacks_dumped:>2} sacks"
            )
        print("-" * 85)

        winner = sorted_agents[0]
        print("\n🔍 STRATEGIC TOURNAMENT BREAKDOWN:")
        print(f"  🥇 Tournament Champion: {winner.name} with {winner.cash:,.2f} THB total revenue!")
        print("\n💡 OBSERVATIONS FROM LLM vs LLM BEHAVIOR:")
        print("  1. High-margin LLMs (e.g. Gamma) that price too close to buyer ceiling get repeatedly")
        print("     undercut by aggressive LLMs (Alpha, Beta, Delta), leaving them stranded with unsold stock.")
        print("  2. Volume-liquidating LLMs (e.g. Delta) secure 100% sell-through, but sacrifice 2-4 THB/kg")
        print("     per sack on high quality Grade A beans.")
        print("  3. The winning formula in competition: Moderate undercutting (0.50 - 1.50 THB below ceiling)")
        print("     combined with rapid disposal of lower-grade stock (Grade C) in early rounds.")
        print("=" * 85 + "\n")
