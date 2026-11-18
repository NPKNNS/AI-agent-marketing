"""The 4 LLM-Powered Competitor AI Agents for the Robusta Coffee Trading Tournament.
All 4 agents are autonomous LLMs running sequentially on Qwen 3.5 9B with distinct strategic personas.
"""

import json
import re
from typing import List, Dict, Optional
from openai import OpenAI
from Agent_selftest.config import LM_STUDIO_API_URL, MODEL_NAME, API_KEY
from Agent_selftest.models import Inventory, BuyerRequest, SellerOffer, RoundContext, Deal
from Agent_selftest.tools import calculate_grade_fair_price

class BaseAgent:
    """Base competitor class maintaining inventory and revenue state."""

    def __init__(self, name: str, inventory: Inventory):
        self.name = name
        self.inventory = inventory
        self.cash: float = 0.0
        self.deals: List[Deal] = []
        self.last_thought: str = ""

    def make_decision(self, ctx: RoundContext) -> List[SellerOffer]:
        raise NotImplementedError

    def add_revenue(self, amount: float):
        self.cash = round(self.cash + amount, 2)

    def summary(self) -> str:
        return f"{self.name:28} | Cash: {self.cash:10,.2f} THB | Stock left: {self.inventory.summary()}"


class LLMCompetitorAgent(BaseAgent):
    """
    Autonomous LLM Agent powered by local Qwen 3.5 9B via LM Studio.
    Each agent is configured with a unique strategic persona, risk appetite, and pricing behavior.
    """

    def __init__(
        self,
        name: str,
        inventory: Inventory,
        strategy_persona: str,
        temperature: float = 0.2,
        api_url: str = LM_STUDIO_API_URL,
        model_name: str = MODEL_NAME
    ):
        super().__init__(name, inventory)
        self.strategy_persona = strategy_persona
        self.temperature = temperature
        self.client = OpenAI(base_url=api_url, api_key=API_KEY)
        self.model_name = model_name

    def make_decision(self, ctx: RoundContext) -> List[SellerOffer]:
        if self.inventory.total_sacks == 0:
            self.last_thought = "Inventory exhausted. Holding cash."
            return []

        rounds_left = ctx.total_rounds - ctx.round_num + 1

        buyer_lines = []
        for b in ctx.buyers:
            buyer_lines.append(
                f"- Buyer {b.buyer_id} ({b.name}): Demands {b.demand_sacks} sacks | Accepts: {b.acceptable_grades} | Max Ceiling: {b.max_price_per_kg:.2f} THB/kg"
            )
        buyers_text = "\n".join(buyer_lines)

        user_prompt = (
            f"=== ROUND {ctx.round_num} OF {ctx.total_rounds} ({rounds_left} rounds remaining) ===\n"
            f"Thai Market Base Price: {ctx.market_base_price_thb:.2f} THB/kg.\n"
            f"Your Stock: Grade A: {self.inventory.grade_a}, Grade B: {self.inventory.grade_b}, Grade C: {self.inventory.grade_c} (Total: {self.inventory.total_sacks} sacks).\n"
            f"Available Buyers in this round:\n"
            f"{buyers_text}\n"
            f"Rules:\n"
            f"- Lowest price offer among all competing AI teams wins buyer demand first (reverse auction).\n"
            f"- Unsold sacks after Round {ctx.total_rounds} are liquidated at 80% market price (-20% penalty).\n"
            f"Submit your offers targeting the most profitable and suitable buyers."
        )

        system_prompt = (
            f"You are an AI commodity trading agent competing in a reverse-auction market.\n"
            f"YOUR STRATEGIC PROFILE: {self.strategy_persona}\n"
            f"Respond strictly in JSON format with your offers and a brief 1-sentence thought.\n"
            f'Format: {{"offers": [{{"buyer_id": <int>, "grade": "A"|"B"|"C", "sacks": <int>, "price_per_kg": <float>}}], "thought": "..."}}'
        )

        try:
            # Assistant prefill ensures instant start and bypasses long thinking delay
            resp = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": '{"offers":'}
                ],
                temperature=self.temperature,
                max_tokens=220
            )

            full_text = '{"offers":' + resp.choices[0].message.content.strip()

            # Clean JSON if truncated
            if not full_text.endswith("}"):
                # Close offers list and root object cleanly if truncated
                if ']' not in full_text:
                    full_text += ']}'
                else:
                    full_text += '}'

            data = json.loads(full_text)
            self.last_thought = str(data.get("thought", "Strategic allocation based on current buyer bids."))

            raw_offers = data.get("offers", [])
            valid_offers: List[SellerOffer] = []
            committed_counts = {"A": 0, "B": 0, "C": 0}

            for ro in raw_offers:
                grade = str(ro.get("grade", "A")).upper().replace("GRADE ", "").strip()
                sacks = int(ro.get("sacks", ro.get("quantity", 0)))
                price = float(ro.get("price_per_kg", ro.get("price", 0.0)))
                buyer_id = int(ro.get("buyer_id", 1))

                available = self.inventory.get_count(grade) - committed_counts.get(grade, 0)
                sacks_to_offer = min(sacks, max(0, available))

                if sacks_to_offer > 0 and price > 0:
                    committed_counts[grade] = committed_counts.get(grade, 0) + sacks_to_offer
                    valid_offers.append(
                        SellerOffer(
                            agent_name=self.name,
                            buyer_id=buyer_id,
                            grade=grade,
                            sacks=sacks_to_offer,
                            price_per_kg=round(price, 2)
                        )
                    )

            return valid_offers

        except Exception as e:
            # Safe algorithmic fallback if LLM response is malformed
            self.last_thought = f"Fallback strategy triggered ({e})"
            fallback_offers = []
            target = max(1, self.inventory.total_sacks // rounds_left)

            # Match available inventory to buyers accepting that grade
            allocated = 0
            for grade in ["C", "B", "A"]:  # Liquidate low grade first
                avail = self.inventory.get_count(grade)
                if avail <= 0:
                    continue
                # Find best buyer accepting this grade
                matching_buyers = [b for b in ctx.buyers if grade in b.acceptable_grades]
                if not matching_buyers:
                    continue
                best_buyer = max(matching_buyers, key=lambda b: b.max_price_per_kg)
                sacks = min(avail, target, best_buyer.demand_sacks)
                if sacks > 0:
                    price = round(best_buyer.max_price_per_kg * 0.985, 2)
                    fallback_offers.append(
                        SellerOffer(
                            agent_name=self.name,
                            buyer_id=best_buyer.buyer_id,
                            grade=grade,
                            sacks=sacks,
                            price_per_kg=price
                        )
                    )
                    allocated += sacks
                    if allocated >= target:
                        break

            return fallback_offers
