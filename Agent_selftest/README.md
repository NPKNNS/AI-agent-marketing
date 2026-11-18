# ☕ Robusta Green Coffee Bean AI Trading Tournament & Competition Agent

A multi-round, multi-agent competition simulator and autonomous trading agent adapted for real-world challenge rules.

Powered by **Qwen 3.5 9B** (via **LM Studio** on `http://localhost:1235/v1`) running **All-AI vs AI** reverse-auction trading simulations sequentially and safely on local hardware.

---

## 🎯 Competition Rules & Mechanics

1. **Market Price**: Thai Robusta base market price (THB/kg) with realistic round-to-round market movements.
2. **Grading Multipliers**:
   - **Grade A (Specialty/Premium)**: $1.05 \times \text{Base Price}$ (+5% premium)
   - **Grade B (Standard)**: $1.00 \times \text{Base Price}$ (Base fair value)
   - **Grade C (Commercial)**: $0.85 \times \text{Base Price}$ (-15% grading discount)
   - Standard sack weight: **60 kg / sack**.
3. **Fair Initial Stock**: Each agent receives identical stock distribution (e.g. 8 Grade A, 5 Grade B, 7 Grade C) so head-to-head testing is 100% fair.
4. **Buyer Bots (Reverse Auction)**:
   - Configurable buyer bots (2 bots in prototype mode, 6 bots in full competition mode).
   - Total buyer demand is constrained to **90% of total stock** across the rounds (creating realistic competition).
   - Buyers buy matching quality from the **lowest price seller first**.
5. **Final Round Liquidation (The 80% Distress Rule)**:
   - Any sacks remaining after the final round are automatically liquidated at:
     $$\text{Distress Price} = 0.80 \times \text{Final Market Price} \times \text{Grade Multiplier}$$
   - This inflicts a severe 20% penalty on leftover stock!

---

## 🤖 The 4 Competing AI Personas (All-AI Tournament)

All 4 competitors are fully autonomous LLM agents powered by local `qwen/qwen3.5-9b` with distinct strategic prompts:

| Agent | Strategic Profile | Behavior in Arena |
| :--- | :--- | :--- |
| **`4. Delta-FastLiquidatorLLM`** | Risk-averse volume liquidator | Undercuts 2.5%–3.5% below ceiling to guarantee sales before the 80% penalty. **Finished 1st (134,661 THB)**. |
| **`2. Beta-GameTheoristLLM`** | Adaptive liquidation velocity & grade dumping | Calculates remaining sacks / rounds, dumps Grade C early. **100% clearance rate (128,001 THB)**. |
| **`1. Alpha-ValueLLM`** | Analytical margin trader | Undercuts 0.8%–1.5%, steady volume pace. Finished 3rd (126,440 THB). |
| **`3. Gamma-MarginMaxLLM`** | High-margin ceiling pricer | Bids within 0.5% of buyer ceiling. Repeatedly undercut; trapped with 15 sacks dumped at 80% (-20% loss). |

---

## 🏆 Verified All-AI Tournament Leaderboard

```text
=====================================================================================
 🏅 FINAL TOURNAMENT LEADERBOARD & PERFORMANCE EVALUATION 🏅
=====================================================================================
Rank  | LLM Agent Name                 | Total Revenue   | Sold in Rounds | Dumped (80%)
-------------------------------------------------------------------------------------
1     | 4. Delta-FastLiquidatorLLM     |   134,661.60 THB | 19 sacks (  95%) |  1 sacks
2     | 2. Beta-GameTheoristLLM        |   128,001.00 THB | 20 sacks ( 100%) |  0 sacks
3     | 1. Alpha-ValueLLM              |   126,440.40 THB | 14 sacks (  70%) |  6 sacks
4     | 3. Gamma-MarginMaxLLM          |   113,415.60 THB |  5 sacks (  25%) | 15 sacks
-------------------------------------------------------------------------------------
```

---

## 🚀 The Improved Champion Agent: `Apex-ChampionLLM`

Located in [`champion_agent.py`](file:///d:/Cloud/miror/NPK/PCfile/TESA/Train/AI_agent/champion_agent.py), this agent synthesizes the winning lessons:
1. **Pacing Calculator**: Tracks `sacks_left / rounds_left`. When behind pace, shifts to aggressive undercutting (2.0%–3.5%).
2. **Grade C De-risking**: Dumps Grade C in early rounds to eliminate holding penalty risk.
3. **Sub-20s Assistant Prefill**: Injects `{"role": "assistant", "content": '{"thought":'}` into Qwen 3.5 9B to bypass reasoning overhead and emit instant, schema-valid JSON decisions.

---

## 🎮 How to Run

### 1. Prototype Mode (6 Rounds, 20 Sacks, 2 Buyers)
```powershell
python main.py
```

### 2. Full Competition Mode (15 Rounds, 30 Sacks, 6 Buyers)
```powershell
python main.py --competition
```

### 3. Custom Configurations
```powershell
python main.py --rounds 10 --sacks 25 --buyers 4
```
