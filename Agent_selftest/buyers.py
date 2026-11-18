"""Buyer bots and market matching engine for the Robusta coffee competition."""

import random
from typing import List, Dict, Tuple
from Agent_selftest.models import BuyerRequest, SellerOffer, Deal, Inventory, KG_PER_SACK
from Agent_selftest.tools import calculate_grade_fair_price

class BuyerEngine:
    """Manages buyer bots, their demands, and the reverse-auction matching engine."""

    def __init__(self, total_agents: int = 4, stock_per_agent: int = 20, total_rounds: int = 6, num_buyers: int = 2):
        self.total_stock = total_agents * stock_per_agent
        self.total_target_demand = int(round(self.total_stock * 0.90))  # Exactly 90% of total stock
        self.total_rounds = total_rounds
        self.num_buyers = num_buyers
        self.accumulated_demand = 0

        # Pre-plan round demands so total demand matches 90% of stock
        self.round_demands = self._generate_demand_schedule()

    def _generate_demand_schedule(self) -> List[int]:
        base = self.total_target_demand // self.total_rounds
        remainder = self.total_target_demand % self.total_rounds
        demands = [base] * self.total_rounds
        for i in range(remainder):
            demands[i] += 1
        return demands

    def get_round_buyers(self, round_num: int, base_market_price: float) -> List[BuyerRequest]:
        """Generates Buyer bots for the current round (2 or 6 buyers)."""
        total_round_demand = self.round_demands[round_num - 1]

        if self.num_buyers == 6:
            # 6 Diverse Buyers for full competition
            buyers_meta = [
                (1, "Specialty Roaster (Bangkok Boutique)", ["A"], 1.05, 0.12),
                (2, "Commercial Roaster (Domestic Brand)", ["A", "B"], 1.02, 0.20),
                (3, "Export Trader (Chumphon Hub)", ["A", "B", "C"], 1.00, 0.25),
                (4, "Instant Coffee Plant (Soluble Hub)", ["B", "C"], 0.98, 0.25),
                (5, "Local Middleman (Ranong Depot)", ["B", "C"], 0.95, 0.10),
                (6, "Southern Commodity Mill", ["A", "B", "C"], 0.99, 0.08),
            ]
            allocated_demands = []
            cur_sum = 0
            for _, _, _, _, ratio in buyers_meta[:-1]:
                d = max(1, int(round(total_round_demand * ratio)))
                allocated_demands.append(d)
                cur_sum += d
            allocated_demands.append(max(1, total_round_demand - cur_sum))

            buyers = []
            for (b_id, name, grades, mult, _), d_sacks in zip(buyers_meta, allocated_demands):
                # Calculate ceiling based on highest grade accepted
                top_grade = grades[0]
                fair_p = calculate_grade_fair_price(base_market_price, top_grade)
                ceiling = round(fair_p * mult * random.uniform(0.98, 1.02), 2)
                buyers.append(BuyerRequest(
                    buyer_id=b_id,
                    name=name,
                    acceptable_grades=grades,
                    demand_sacks=d_sacks,
                    max_price_per_kg=ceiling
                ))
            return buyers

        # Default 2 Buyers Mode (Prototype)
        buyer1_demand = max(2, total_round_demand // 2 + random.choice([-1, 0, 1]))
        buyer2_demand = total_round_demand - buyer1_demand

        b1_fair_a = calculate_grade_fair_price(base_market_price, "A")
        b1_max_price = round(b1_fair_a * random.uniform(0.99, 1.04), 2)
        buyer1 = BuyerRequest(
            buyer_id=1,
            name="Buyer 1 (Specialty Roaster)",
            acceptable_grades=["A", "B"],
            demand_sacks=buyer1_demand,
            max_price_per_kg=b1_max_price
        )

        b2_fair_b = calculate_grade_fair_price(base_market_price, "B")
        b2_max_price = round(b2_fair_b * random.uniform(0.96, 1.01), 2)
        buyer2 = BuyerRequest(
            buyer_id=2,
            name="Buyer 2 (Commercial Mill)",
            acceptable_grades=["A", "B", "C"],
            demand_sacks=buyer2_demand,
            max_price_per_kg=b2_max_price
        )

        return [buyer1, buyer2]

        return [buyer1, buyer2]

    def clear_market(
        self,
        round_num: int,
        buyers: List[BuyerRequest],
        all_offers: List[SellerOffer],
        agent_inventories: Dict[str, Inventory]
    ) -> Tuple[List[Deal], List[str]]:
        """
        Matching Algorithm:
        - Reviews all submitted offers simultaneously.
        - Validates acceptable grades and buyer max ceiling price.
        - Lowest price seller wins first!
        - Deducts stock from winning sellers and calculates deals.
        """
        confirmed_deals: List[Deal] = []
        logs: List[str] = []

        # Process each buyer independently
        for buyer in buyers:
            remaining_demand = buyer.demand_sacks
            logs.append(f"\n--- Matching for {buyer.name} (Wants: {buyer.demand_sacks} sacks, Max: {buyer.max_price_per_kg:.2f} THB/kg, Grades: {buyer.acceptable_grades}) ---")

            # Filter offers directed to this buyer with matching quality and acceptable price
            valid_offers = [
                o for o in all_offers
                if o.buyer_id == buyer.buyer_id
                and o.grade.upper() in [g.upper() for g in buyer.acceptable_grades]
                and o.price_per_kg <= buyer.max_price_per_kg
                and o.sacks > 0
            ]

            if not valid_offers:
                logs.append(f"  No valid matching offers received at or below {buyer.max_price_per_kg:.2f} THB/kg.")
                continue

            # Sort by price ASCENDING (Lowest price wins!)
            # In case of price tie, shuffle to be fair
            random.shuffle(valid_offers)
            valid_offers.sort(key=lambda x: x.price_per_kg)

            for offer in valid_offers:
                if remaining_demand <= 0:
                    break

                agent_inv = agent_inventories[offer.agent_name]
                available_sacks = agent_inv.get_count(offer.grade)
                if available_sacks <= 0:
                    continue

                # Sacks to trade is min of requested offer, available stock, and remaining buyer demand
                sacks_to_trade = min(offer.sacks, available_sacks, remaining_demand)
                if sacks_to_trade <= 0:
                    continue

                # Execute trade
                agent_inv.deduct(offer.grade, sacks_to_trade)
                deal_revenue = round(sacks_to_trade * KG_PER_SACK * offer.price_per_kg, 2)
                deal = Deal(
                    round_num=round_num,
                    buyer_id=buyer.buyer_id,
                    buyer_name=buyer.name,
                    seller_name=offer.agent_name,
                    grade=offer.grade.upper(),
                    sacks=sacks_to_trade,
                    price_per_kg=offer.price_per_kg,
                    revenue_thb=deal_revenue
                )
                confirmed_deals.append(deal)
                remaining_demand -= sacks_to_trade
                logs.append(
                    f"  ✅ SOLD: {offer.agent_name} sold {sacks_to_trade} sacks (Grade {offer.grade.upper()}) "
                    f"at {offer.price_per_kg:.2f} THB/kg -> {deal_revenue:,.2f} THB"
                )

            if remaining_demand > 0:
                logs.append(f"  Unfulfilled demand: {remaining_demand} sacks.")

        return confirmed_deals, logs

