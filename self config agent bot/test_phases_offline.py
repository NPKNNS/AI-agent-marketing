"""Offline verification of EARLY_PROBE, MID_VALUE, and FINAL_DUMP with local Qwen 3.5 9B (0 Market API calls)."""
import json
import os
import random
from pathlib import Path

import strategy
from appraisal import estimate_lots
from llm import LLM
from memory import Memory

HERE = Path(__file__).parent
for line in (HERE / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

rules = {
    "bag_kg": 60,
    "p0": 120.0,
    "defect_steps": [
        {"max_defect_pct": 1.0, "step": 1.08, "grade": "PREMIUM"},
        {"max_defect_pct": 3.0, "step": 1.04, "grade": "GRADE_1"},
        {"max_defect_pct": 5.0, "step": 1.0, "grade": "GRADE_2"},
        {"max_defect_pct": 7.0, "step": 0.96, "grade": "GRADE_3"},
        {"max_defect_pct": 15.0, "step": 0.85, "grade": "BELOW_STANDARD"},
        {"max_defect_pct": 100.0, "step": 0.6, "grade": "REJECT"},
    ],
    "moisture": {"ref": 12.5, "penalty_per_pp": 0.02, "hard_limit": 15.0, "hard_factor": 0.6},
    "screen_factors": {"12": 0.92, "13": 0.97, "14": 1.0, "15": 1.0, "16": 1.03, "17": 1.03, "18": 1.06},
    "timeline": {"rounds": 6},
}

lots = [
    {"lot_id": "LOT-T16-1", "kg_available": 240, "sample": {"defect_pct": 10.0, "moisture_pct": 12.23, "screen": 15}},
    {"lot_id": "LOT-T16-2", "kg_available": 120, "sample": {"defect_pct": 0.5, "moisture_pct": 12.91, "screen": 14}},
    {"lot_id": "LOT-T16-3", "kg_available": 240, "sample": {"defect_pct": 2.71, "moisture_pct": 11.54, "screen": 18}},
    {"lot_id": "LOT-T16-4", "kg_available": 600, "sample": {"defect_pct": 4.78, "moisture_pct": 10.72, "screen": 16}},
]
rfqs = [
    {"rfq_id": "R1-01", "buyer": "4 ชุมพรคั่วบด", "buyer_id": "B4", "buyer_type": "COMMERCIAL", "kg": 600, "bags": 10,
     "spec": {"max_defect_pct": 7.0, "max_moisture_pct": 12.5, "min_screen": None}},
    {"rfq_id": "R1-02", "buyer": "3 สยามคอมเมิร์ซ", "buyer_id": "B3", "buyer_type": "COMMERCIAL", "kg": 600, "bags": 10,
     "spec": {"max_defect_pct": 7.0, "max_moisture_pct": 12.5, "min_screen": None}},
    {"rfq_id": "R1-03", "buyer": "6 ทรีอินวันฟู้ดส์", "buyer_id": "B6", "buyer_type": "INSTANT", "kg": 600, "bags": 10,
     "spec": {"max_defect_pct": 15.0, "max_moisture_pct": 14.0, "min_screen": None}},
    {"rfq_id": "R1-04", "buyer": "5 อินสแตนท์ไทย", "buyer_id": "B5", "buyer_type": "INSTANT", "kg": 600, "bags": 10,
     "spec": {"max_defect_pct": 15.0, "max_moisture_pct": 14.0, "min_screen": None}},
]

tv = estimate_lots(rules, lots)
llm = LLM(os.environ["LLM_BASE_URL"], os.environ["LLM_MODEL"], os.environ["LLM_API_KEY"], timeout=120, thinking=False)
mem_path = HERE / "logs" / "test_offline_mem.json"
if mem_path.exists():
    mem_path.unlink()
mem = Memory(str(mem_path))

# Test Round 1 (EARLY_PROBE), Round 3 (MID_VALUE with learned ceilings), Round 6 (FINAL_DUMP)
for rnum in [1, 3, 6]:
    if rnum == 3:
        # Simulate learned ceiling bounds from R1/R2
        mem.buyers["4 ชุมพรคั่วบด"]["lower_bound"] = 1.07
        mem.buyers["4 ชุมพรคั่วบด"]["upper_bound"] = 1.10
        mem.buyers["4 ชุมพรคั่วบด"]["est_ceiling"] = 1.085
        mem.buyers["3 สยามคอมเมิร์ซ"]["lower_bound"] = 1.04
        mem.buyers["3 สยามคอมเมิร์ซ"]["upper_bound"] = 1.08
        mem.buyers["3 สยามคอมเมิร์ซ"]["est_ceiling"] = 1.06
        mem.buyers["6 ทรีอินวันฟู้ดส์"]["lower_bound"] = 0.97
        mem.buyers["6 ทรีอินวันฟู้ดส์"]["upper_bound"] = 1.00
        mem.buyers["6 ทรีอินวันฟู้ดส์"]["est_ceiling"] = 0.985
        mem.buyers["5 อินสแตนท์ไทย"]["upper_bound"] = 0.96
        mem.buyers["5 อินสแตนท์ไทย"]["est_ceiling"] = 0.93

    state = {
        "round": {"round_id": f"R{rnum}", "kind": "real", "number": rnum, "of": 6, "calendar": "ปกติ"},
        "lots": lots,
        "rfqs": rfqs,
        "capacity_left_kg": None,
        "seconds_left": 170.0,
    }
    ctx = strategy.Context(rules, [], tv, random.Random(42), mem, llm, 170.0)
    offers, source = strategy.decide(state, ctx)
    print(f"\n=== TEST R{rnum} (source={source}, time={getattr(llm, 'seconds', '?')}s) ===", flush=True)
    print("Notes:", ctx.notes, flush=True)
    for o in offers:
        mk = round(o["price_per_kg"] / tv[o["lot_id"]], 3)
        print(f"  {o['rfq_id']} <- {o['lot_id']} | {o['kg']//60} bags @ {o['price_per_kg']} ({mk}x TV)", flush=True)

if mem_path.exists():
    mem_path.unlink()
