import json
import re
import sys
from openai import OpenAI
from Agent_selftest.config import LM_STUDIO_API_URL, MODEL_NAME, API_KEY
from Agent_selftest.models import CoffeeLot, MarketData, TradingDecision
from Agent_selftest.tools import calculate_ice_parity_price, evaluate_quality_and_risk, calculate_holding_costs

class RobustaTradingAgent:
    def __init__(self, api_url: str = LM_STUDIO_API_URL, model_name: str = MODEL_NAME):
        self.client = OpenAI(base_url=api_url, api_key=API_KEY)
        self.model_name = model_name

    def make_decision(self, lot: CoffeeLot, market: MarketData, verbose: bool = True) -> TradingDecision:
        # Step 1: Pre-compute quantitative indicators via tools
        ice_parity_price = calculate_ice_parity_price(market.ice_robusta_usd_ton, market.usd_thb_rate)
        quality_eval = evaluate_quality_and_risk(lot.moisture_pct, lot.defect_pct)
        holding_eval = calculate_holding_costs(lot.weight_kg, lot.storage_days, lot.storage_cost_thb_per_kg_month)
        quality_adjusted_fair_price = round(ice_parity_price * (1.0 + quality_eval["price_adjustment_pct"]), 2)

        # Step 2: Formulate expert agent prompt
        system_prompt = (
            "You are an expert Chief Coffee Trader and Commodity Risk Manager specializing in Robusta Green Coffee Beans.\n"
            "Make an optimal risk-adjusted commercial decision on whether to SELL ALL, SELL PARTIAL, or HOLD.\n"
            "Evaluate:\n"
            "1. Physical Quality & Spoilage: Moisture > 12.5% is risky; > 13.5% requires immediate sale to prevent total spoilage.\n"
            "2. Market Parity & Arbitrage: Compare local spot offer with ICE parity.\n"
            "3. Warehouse Holding Drag: Factor in accumulated storage cost.\n"
            "Output your final decision strictly as a valid JSON object."
        )

        user_content = f"""
### LOT INVENTORY DATA
- Lot ID: {lot.lot_id} ({lot.variety})
- Total Quantity: {lot.weight_kg:,.0f} kg
- Moisture: {lot.moisture_pct:.1f}%
- Defect Rate: {lot.defect_pct:.1f}%
- Screen Size: {lot.screen_size}
- Storage Duration: {lot.storage_days} days
- Accumulated Warehouse Cost: {holding_eval['accumulated_cost_thb_per_kg']} THB/kg (Total: {holding_eval['total_accumulated_cost_thb']:,.2f} THB)

### QUANTITATIVE TOOL OUTPUTS
- Quality Assessment: {quality_eval['grade']} (Spoilage Risk: {quality_eval['spoilage_risk']})
- Quality Alerts: {', '.join(quality_eval['quality_warnings'])}
- ICE London Futures Benchmark Parity: {ice_parity_price:.2f} THB/kg
- Quality-Adjusted Benchmark Price: {quality_adjusted_fair_price:.2f} THB/kg

### MARKET ENVIRONMENT
- Local Spot Offer: {market.local_spot_price_thb_kg:.2f} THB/kg
- 7-Day Trend: {market.price_trend_7d}
- Harvest Cycle Phase: {market.harvest_season_phase}
- Market Context: {market.market_notes}

### REQUIRED JSON SCHEMA
Provide your decision strictly as a valid JSON object with the following fields:
{{
  "action": "SELL_ALL" | "SELL_PARTIAL" | "HOLD",
  "percentage_to_sell": <float between 0.0 and 100.0>,
  "quantity_to_sell_kg": <float>,
  "target_price_thb_kg": <float>,
  "minimum_acceptable_price_thb_kg": <float>,
  "urgency": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
  "primary_driver": "<main reason>",
  "reasoning": [
    "<reasoning point 1>",
    "<reasoning point 2>",
    "<reasoning point 3>"
  ],
  "risk_assessment": "<risk explanation>"
}}
"""

        if verbose:
            print(f"🤖 Invoking Trading Agent ({self.model_name}) with streaming...")

        # Step 3: Stream tokens from LM Studio
        stream = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=0.2,
            max_tokens=3000,
            stream=True
        )

        accumulated_content = []
        accumulated_reasoning = []

        for chunk in stream:
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                accumulated_reasoning.append(reasoning)
                if verbose:
                    sys.stdout.write("·")
                    sys.stdout.flush()

            content = delta.content
            if content:
                accumulated_content.append(content)
                if verbose:
                    sys.stdout.write(content)
                    sys.stdout.flush()

        if verbose:
            print("\n")

        full_content = "".join(accumulated_content).strip()
        full_reasoning = "".join(accumulated_reasoning).strip()

        # Step 4: Extract JSON from content or fallback to reasoning if needed
        json_target = full_content
        if not json_target and full_reasoning:
            json_target = full_reasoning

        # Clean markdown code blocks if wrapped in ```json
        if "```" in json_target:
            matches = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", json_target)
            if matches:
                json_target = matches[0]

        # Extract first valid { ... } block
        json_match = re.search(r"\{[\s\S]*\}", json_target)
        if json_match:
            json_target = json_match.group(0)

        data = json.loads(json_target)
        return TradingDecision(**data)
