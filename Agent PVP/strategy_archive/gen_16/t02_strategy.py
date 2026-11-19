"""My Agent Strategy (T02 - Gen 2 User-Specified Phased Strategy).

Implements the user's exact Gen 2 trading rules:
1. First 5 Rounds (R1-R5):
   - SPECIALTY: target 1.07x (focusing 70% of sellable lot on it), floor 1.02x
   - COMMERCIAL: target 1.02x, floor 1.00x
   - INSTANT: target 0.98x, floor 0.95x
   - Weighting by largest qualified stock tier:
     * If SPECIALTY most -> prioritize SPECIALTY first
     * If COMMERCIAL most -> prioritize COMMERCIAL first
     * If INSTANT most -> split round capacity 50/50 between INSTANT and COMMERCIAL
2. Festival Rounds (R6-R8, เทศกาลกาแฟ):
   - Sell all SPECIALTY starting at 1.15x -> lower by market ceiling to floor 1.02x
   - Sell all COMMERCIAL starting at 1.05x -> lower by market ceiling to floor 1.00x
   - Sell all INSTANT starting at 1.00x -> lower by market ceiling to floor 0.92x
3. After Festival (R9-R15):
   - Sell everything remaining:
     * SPECIALTY & COMMERCIAL floor 0.88x
     * INSTANT floor 0.83x
"""
import json
import math
import random
from dataclasses import dataclass, field

from appraisal import grade as lot_grade

BAG = 60
MIN_MARKUP, MAX_MARKUP = 0.83, 1.30
PACE = 1.40

MARKUP = {"SPECIALTY": 1.070, "COMMERCIAL": 1.020, "INSTANT": 0.980}

STD_SPECS = {
    "SPECIALTY": {"max_defect_pct": 3.0, "max_moisture_pct": 12.5, "min_screen": 16},
    "COMMERCIAL": {"max_defect_pct": 7.0, "max_moisture_pct": 12.5, "min_screen": None},
    "INSTANT": {"max_defect_pct": 15.0, "max_moisture_pct": 15.0, "min_screen": None},
}


@dataclass
class Context:
    rules: dict
    calendar: list
    tv: dict
    rng: random.Random
    memory: object = None
    llm: object = None
    seconds_left: float = 999
    notes: list = field(default_factory=list)
    last_trace: dict = field(default_factory=dict)


def spec_ok(sample: dict, spec: dict) -> bool:
    return (
        sample["defect_pct"] <= spec["max_defect_pct"]
        and sample["moisture_pct"] <= spec["max_moisture_pct"]
        and (spec["min_screen"] is None or sample["screen"] >= spec["min_screen"])
    )


def spec_ok_for_lot(lot: dict, spec: dict, memory: object = None) -> bool:
    if memory is not None and hasattr(memory, "is_spec_failed"):
        if memory.is_spec_failed(lot["lot_id"], spec):
            return False
    return spec_ok(lot["sample"], spec)


def spec_safety_info(lot: dict, spec: dict, btype: str, memory: object = None) -> tuple[bool, float]:
    if btype == "INSTANT":
        return False, 99.0
    if memory is not None and hasattr(memory, "is_spec_verified"):
        if memory.is_spec_verified(lot["lot_id"], spec):
            return False, 99.0
    s = lot["sample"]
    d_margin = spec["max_defect_pct"] - s["defect_pct"]
    m_margin = spec["max_moisture_pct"] - s["moisture_pct"]
    min_margin = round(min(d_margin, m_margin), 3)
    return (min_margin <= 0.25), min_margin


def lot_best_tier(lot: dict, memory: object = None) -> tuple[str, bool]:
    if spec_ok_for_lot(lot, STD_SPECS["SPECIALTY"], memory):
        borderline, _ = spec_safety_info(lot, STD_SPECS["SPECIALTY"], "SPECIALTY", memory)
        return "SPECIALTY", (not borderline)
    if spec_ok_for_lot(lot, STD_SPECS["COMMERCIAL"], memory):
        borderline, _ = spec_safety_info(lot, STD_SPECS["COMMERCIAL"], "COMMERCIAL", memory)
        return "COMMERCIAL", (not borderline)
    return "INSTANT", True


def rounds_left(state: dict, ctx: Context) -> int:
    rnd, total = state["round"], ctx.rules["timeline"]["rounds"]
    return total if rnd["kind"] == "practice" else total - rnd["number"] + 1


def highest_buyer_ceiling_mk(state: dict, ctx: Context, buyer_type: str | None = None) -> float:
    """Returns the highest estimated ceiling markup among known/active buyers (optionally filtered by tier)."""
    tier_defaults = {"SPECIALTY": 1.165, "COMMERCIAL": 1.060, "INSTANT": 0.960}
    best = tier_defaults.get(buyer_type, 1.165) if buyer_type else 1.165
    found = []
    if ctx.memory and getattr(ctx.memory, "buyers", None):
        for b in ctx.memory.buyers.values():
            if buyer_type and b.get("buyer_type") != buyer_type:
                continue
            ub = b.get("upper_bound")
            est = b.get("est_ceiling")
            lb = b.get("lower_bound")
            val = ub if ub is not None else (est if est is not None else lb)
            if val is not None:
                found.append(float(val))
    if found:
        best = max(found)
    return round(min(MAX_MARKUP, max(MIN_MARKUP, best)), 3)


def phase_policy(state: dict, ctx: Context) -> dict:
    rnd = state["round"]
    total = max(1, ctx.rules["timeline"]["rounds"])
    is_prac = rnd.get("kind") == "practice"
    cur = 1 if is_prac else rnd["number"]
    left = rounds_left(state, ctx)
    cal = str(rnd.get("calendar", ""))
    total_rem_bags = sum(l["kg_available"] // BAG for l in state.get("lots", []))
    cap_bags = (state["capacity_left_kg"] // BAG) if state.get("capacity_left_kg") is not None else 5
    behind_pace = total_rem_bags > left * max(2.2, cap_bags * 0.52)
    # Zero-spare-round detector: if remaining bags exceed (left - 1) * cap_bags, even 1 missed round causes 0.80x salvage!
    zero_spare = (not is_prac) and left >= 3 and total_rem_bags > (left - 1) * cap_bags

    # End-of-season dump ALWAYS takes priority in final 2 rounds (or R13 if behind pace)
    if (not is_prac) and (left <= 2 or (left == 3 and behind_pace)):
        spec_hi = highest_buyer_ceiling_mk(state, ctx, "SPECIALTY")
        comm_hi = highest_buyer_ceiling_mk(state, ctx, "COMMERCIAL")
        inst_hi = highest_buyer_ceiling_mk(state, ctx, "INSTANT")
        dump_mult = 0.88 if left <= 1 else 0.91
        dump_targets = {
            "SPECIALTY": round(max(MIN_MARKUP, dump_mult * spec_hi), 3),
            "COMMERCIAL": round(max(MIN_MARKUP, dump_mult * comm_hi), 3),
            "INSTANT": round(max(MIN_MARKUP, dump_mult * inst_hi), 3),
        }
        return {
            "name": "END_SEASON_DUMP",
            "starts": dump_targets,
            "floors": {
                "SPECIALTY": min(0.92 if left >= 2 else 0.87, dump_targets["SPECIALTY"]),
                "COMMERCIAL": min(0.90 if left >= 2 else 0.87, dump_targets["COMMERCIAL"]),
                "INSTANT": 0.84 if left >= 2 else 0.83,
            },
            "final_caps": dump_targets if left <= 1 else {k: round(v + 0.025, 3) for k, v in dump_targets.items()},
            "dump_step": 0.0,
            "is_end_dump": True,
            "zero_spare": zero_spare,
            "guidance": f"Gen 15 End-Season Dump (left={left}, mult={dump_mult}): Sell at {dump_mult}x highest-price buyer ceiling ({dump_targets}).",
        }

    is_festival = (not is_prac) and ("เทศกาล" in cal or (total >= 12 and 6 <= cur <= 8))

    if is_festival:
        return {
            "name": "FESTIVAL_PEAK",
            "starts": {"SPECIALTY": 1.148, "COMMERCIAL": 1.032, "INSTANT": 0.928},
            "floors": {"SPECIALTY": 1.055, "COMMERCIAL": 0.995, "INSTANT": 0.890},
            "dump_step": 0.012 if zero_spare else 0.0,
            "zero_spare": zero_spare,
            "guidance": "Gen 15 Festival: Adaptive B2/B1 ceiling capture (cut 44% OUTBID) + zero-salvage pace protection.",
        }
    if not is_prac and cur <= 5:
        return {
            "name": "FIRST_5_ROUNDS",
            "starts": {"SPECIALTY": 1.115, "COMMERCIAL": 1.022, "INSTANT": 0.922},
            "floors": {"SPECIALTY": 1.045, "COMMERCIAL": 0.988, "INSTANT": 0.888},
            "dump_step": 0.0,
            "zero_spare": zero_spare,
            "guidance": "Gen 15 R1-R5: Clear INSTANT fast (B5<=0.905, B6<=0.928), harvest COMMERCIAL (1.022->0.988), preserve SPECIALTY.",
        }
    if is_prac:
        return {
            "name": "PRACTICE_PROBE",
            "starts": {"SPECIALTY": 1.13, "COMMERCIAL": 1.04, "INSTANT": 0.935},
            "floors": {"SPECIALTY": 1.00, "COMMERCIAL": 0.98, "INSTANT": 0.88},
            "dump_step": 0.0,
            "zero_spare": False,
            "guidance": "Practice round: Probe high to discover exact buyer ceilings.",
        }
    # After Festival (R9-R13 on-pace): balanced high-PR floors + zero-spare pace protection to keep >=99% sell-through!
    dump_step = (0.022 if zero_spare else 0.007) if left >= 4 else (0.032 if zero_spare else 0.018)
    comm_floor = (0.945 if zero_spare else 0.965) if left >= 4 else (0.915 if zero_spare else 0.930)
    spec_floor = (0.990 if zero_spare else 1.015) if left >= 4 else (0.945 if zero_spare else 0.965)
    return {
        "name": "AFTER_FESTIVAL_CLEAR",
        "starts": {"SPECIALTY": 1.142, "COMMERCIAL": 1.028, "INSTANT": 0.908},
        "floors": {"SPECIALTY": spec_floor, "COMMERCIAL": comm_floor, "INSTANT": 0.850},
        "final_caps": None,
        "dump_step": dump_step,
        "zero_spare": zero_spare,
        "guidance": "Gen 15 After Festival (R9-R13): High-PR ceiling tracking with zero-spare salvage protection.",
    }


def _qualified_stock_by_tier(state: dict, memory: object = None) -> dict[str, int]:
    counts = {"SPECIALTY": 0, "COMMERCIAL": 0, "INSTANT": 0}
    for lot in state.get("lots", []):
        bags = lot["kg_available"] // BAG
        if bags > 0:
            tier, _ = lot_best_tier(lot, memory)
            counts[tier] += bags
    return counts


def bounds_for_candidate(policy: dict, b_stats: dict | None, buyer_type: str) -> tuple[float, float, float]:
    start_mk = policy["starts"].get(buyer_type, 1.00)
    floor_mk = policy["floors"].get(buyer_type, 0.88)
    step = policy.get("dump_step", 0.0)

    ub = b_stats.get("upper_bound") if b_stats else None
    oc = b_stats.get("outbid_cap") if b_stats else None
    est_c = b_stats.get("est_ceiling") if b_stats else None
    mkt_mk = b_stats.get("mkt_clearing_mk") if b_stats else None
    fill_pct = float(b_stats.get("avg_fill_pct", 100.0)) if b_stats else 100.0
    crowding = float(b_stats.get("crowding", 1.0)) if b_stats else 1.0

    # End-season dump: anchor directly to 0.88x of highest-price buyer ceiling (stored in start_mk)
    if policy.get("is_end_dump"):
        cap_mk = min(MAX_MARKUP, start_mk if ub is None else min(start_mk, ub - 0.004))
        if policy.get("final_caps"):
            fcap = policy["final_caps"].get(buyer_type)
            if fcap is not None:
                cap_mk = min(cap_mk, fcap)
        rec_mk = cap_mk
        if oc is not None:
            rec_mk = min(rec_mk, oc - 0.005)
        eff_floor = round(max(MIN_MARKUP, min(floor_mk, cap_mk)), 3)
        rec_mk = round(min(cap_mk, max(eff_floor, rec_mk)), 3)
        return eff_floor, rec_mk, round(cap_mk, 3)

    # Normal / Festival / After-Festival: sell as close to buyer ceiling as possible
    # On SPECIALTY hug ceiling tightly; on crowded COMMERCIAL/INSTANT respect effective market clearing ceiling
    target_ceil = max(start_mk, est_c) if (buyer_type == "SPECIALTY" and est_c is not None) else start_mk
    rec_mk = target_ceil - step
    if ub is not None:
        rec_mk = min(rec_mk, ub - 0.004)
    if buyer_type != "SPECIALTY" and mkt_mk is not None and fill_pct >= 96.0 and crowding >= 1.15:
        rec_mk = min(rec_mk, mkt_mk + 0.012)
    if oc is not None and policy["name"] != "FESTIVAL_PEAK":
        rec_mk = min(rec_mk, oc - 0.004)
    elif b_stats and policy["name"] != "FESTIVAL_PEAK":
        lb = b_stats.get("lower_bound")
        ref_mk = max(b_stats.get("safe_mk", target_ceil), (lb or 0.0) + 0.012) - step
        rec_mk = min(rec_mk, ref_mk)

    cap_mk = min(MAX_MARKUP, target_ceil if ub is None else min(target_ceil, ub - 0.004))
    eff_floor = round(max(MIN_MARKUP, min(floor_mk, cap_mk)), 3)
    rec_mk = round(min(cap_mk, max(eff_floor, rec_mk)), 3)
    return eff_floor, rec_mk, round(cap_mk, 3)


def sell_budget(state: dict, ctx: Context) -> dict[str, int]:
    """Allocates round capacity according to the user's Gen 2 stock-weight rules."""
    cap_bags = (state["capacity_left_kg"] // BAG) if state.get("capacity_left_kg") is not None else 999
    policy = phase_policy(state, ctx)
    stock_counts = _qualified_stock_by_tier(state, ctx.memory)
    dominant_tier = max(stock_counts, key=lambda k: stock_counts[k])

    avail_map = {l["lot_id"]: l["kg_available"] // BAG for l in state["lots"]}
    alloc = {lid: 0 for lid in avail_map}

    if policy["name"] == "FIRST_5_ROUNDS":
        # If INSTANT is largest, split capacity 50/50 between INSTANT and COMMERCIAL
        if dominant_tier == "INSTANT":
            inst_cap = cap_bags // 2
            comm_cap = cap_bags - inst_cap
            tier_caps = {"INSTANT": inst_cap, "COMMERCIAL": comm_cap, "SPECIALTY": cap_bags}
            tier_order = ["INSTANT", "COMMERCIAL", "SPECIALTY"]
        elif dominant_tier == "COMMERCIAL":
            tier_caps = {"COMMERCIAL": cap_bags, "SPECIALTY": cap_bags, "INSTANT": cap_bags}
            tier_order = ["COMMERCIAL", "SPECIALTY", "INSTANT"]
        else:
            # SPECIALTY most in R1-R5: focus 30% on SPECIALTY, and use remaining 70% to clear INSTANT & COMMERCIAL early
            spec_cap = max(1, math.floor(cap_bags * 0.30))
            tier_caps = {"SPECIALTY": spec_cap, "INSTANT": cap_bags, "COMMERCIAL": cap_bags}
            tier_order = ["INSTANT", "COMMERCIAL", "SPECIALTY"]

        rem = cap_bags
        for t in tier_order:
            t_rem = min(rem, tier_caps[t])
            t_lots = [l for l in state["lots"] if avail_map[l["lot_id"]] > 0 and lot_best_tier(l, ctx.memory)[0] == t]
            for l in t_lots:
                if t_rem <= 0:
                    break
                take = min(avail_map[l["lot_id"]], t_rem)
                alloc[l["lot_id"]] = take
                t_rem -= take
                rem -= take
        return {lid: b * BAG for lid, b in alloc.items()}

    # Festival or After-Festival: sell all available up to round capacity, prioritizing SPECIALTY -> COMMERCIAL -> INSTANT
    rem = cap_bags
    for t in ("SPECIALTY", "COMMERCIAL", "INSTANT"):
        t_lots = [l for l in state["lots"] if avail_map[l["lot_id"]] > 0 and lot_best_tier(l, ctx.memory)[0] == t]
        for l in t_lots:
            if rem <= 0:
                break
            take = min(avail_map[l["lot_id"]], rem)
            alloc[l["lot_id"]] = take
            rem -= take
    return {lid: b * BAG for lid, b in alloc.items()}


def candidates(state: dict, ctx: Context) -> list[dict]:
    policy = phase_policy(state, ctx)
    stock_counts = _qualified_stock_by_tier(state, ctx.memory)
    dominant_tier = max(stock_counts, key=lambda k: stock_counts[k])
    cap_bags = (state["capacity_left_kg"] // BAG) if state.get("capacity_left_kg") is not None else 999

    out = []
    for lot in state["lots"]:
        lot_bags = lot["kg_available"] // BAG
        if lot_bags < 1:
            continue
        best_tier, _ = lot_best_tier(lot, ctx.memory)
        tv_factor = ctx.memory.get_lot_tv_factor(lot["lot_id"]) if ctx.memory and hasattr(ctx.memory, "get_lot_tv_factor") else 1.0
        eff_tv = round(ctx.tv[lot["lot_id"]] * tv_factor, 2)

        eligible_rfqs = [rfq for rfq in state["rfqs"] if spec_ok_for_lot(lot, rfq["spec"], ctx.memory)]
        if not eligible_rfqs:
            continue

        # In R13-R15 (or earlier if zero_spare / behind pace) allow SPECIALTY lots to also bid on COMMERCIAL RFQs so zero bags hit salvage
        left = rounds_left(state, ctx)
        total_rem_bags = sum(l["kg_available"] // BAG for l in state.get("lots", []))
        if (left <= 3 or policy.get("zero_spare") or (left == 4 and total_rem_bags > 11)) and best_tier == "SPECIALTY":
            filtered_rfqs = [r for r in eligible_rfqs if r["buyer_type"] in ("SPECIALTY", "COMMERCIAL")] or eligible_rfqs
        else:
            filtered_rfqs = [r for r in eligible_rfqs if r["buyer_type"] == best_tier]
            if not filtered_rfqs:
                filtered_rfqs = [r for r in eligible_rfqs if r["buyer_type"] != "INSTANT"] or eligible_rfqs

        # Split across 2+ buyers/RFQs whenever possible so a single ceiling or crowding spike never causes 0/5b
        split_cap = max(2, math.ceil(cap_bags * 0.60)) if (len(filtered_rfqs) >= 2 and cap_bags >= 4) else cap_bags

        for rfq in filtered_rfqs:
            rtype = rfq["buyer_type"]
            b_stats = (
                ctx.memory.get_buyer_estimate(rfq["buyer"], rtype)
                if ctx.memory
                else {"est_ceiling": MARKUP.get(rtype, 1.00), "safe_mk": MARKUP.get(rtype, 1.00)}
            )
            fl, rec_mk, cap_mk = bounds_for_candidate(policy, b_stats, rtype)
            # Gen 15 Buyer-Specific Ceiling Safety (eliminates 19.9% B5 ABOVE_LIMIT waste!)
            if rtype == "SPECIALTY" and "ดอยช้าง" in rfq["buyer"] and policy["name"] in ("FESTIVAL_PEAK", "AFTER_FESTIVAL_CLEAR"):
                rec_mk = min(rec_mk, 1.112)
                cap_mk = min(cap_mk, 1.120)
            elif rtype == "INSTANT" and "อินสแตนท์ไทย" in rfq["buyer"]:
                rec_mk = min(rec_mk, 0.905)
                cap_mk = min(cap_mk, 0.910)
            elif rtype == "INSTANT" and "ทรีอินวัน" in rfq["buyer"]:
                rec_mk = min(rec_mk, 0.930)
                cap_mk = min(cap_mk, 0.936)
            max_b = min(split_cap, lot_bags, rfq["bags"], cap_bags)
            if policy["name"] == "FIRST_5_ROUNDS" and best_tier == "SPECIALTY" and rtype == "SPECIALTY":
                max_b = min(max_b, max(1, math.floor(lot_bags * 0.25)))

            # Prefer B2 over B1 in SPECIALTY, and B6 over B5 in INSTANT (higher ceiling & fewer ABOVE_LIMIT)
            buyer_pref = 0.020 if (rtype == "SPECIALTY" and "บางกอกบลูม" in rfq["buyer"]) else (
                0.014 if (rtype == "INSTANT" and "ทรีอินวัน" in rfq["buyer"]) else 0.0
            )
            fill_bonus = 0.015 if float(b_stats.get("avg_fill_pct", 100.0)) < 94.0 else 0.0
            tier_prio = 0.0
            if policy["name"] == "FESTIVAL_PEAK":
                tier_prio = {"SPECIALTY": 0.20, "COMMERCIAL": 0.10, "INSTANT": 0.0}[best_tier]
            elif policy["name"] == "FIRST_5_ROUNDS":
                if dominant_tier == "INSTANT":
                    tier_prio = 0.15 if rtype in ("INSTANT", "COMMERCIAL") else 0.05
                elif rtype == dominant_tier:
                    tier_prio = 0.20
                elif rtype == best_tier:
                    tier_prio = 0.10

            score = round(rec_mk + tier_prio + buyer_pref + fill_bonus + (0.02 if rtype == best_tier else 0.0), 4)
            out.append({
                "lot_id": lot["lot_id"],
                "rfq_id": rfq["rfq_id"],
                "buyer": rfq["buyer"],
                "buyer_id": rfq.get("buyer_id", ""),
                "buyer_type": rtype,
                "best_tier": best_tier,
                "rfq_bags": rfq["bags"],
                "max_bags": max_b,
                "tv": ctx.tv[lot["lot_id"]],
                "eff_tv": eff_tv,
                "fl": fl,
                "rec_mk": rec_mk,
                "cap_mk": cap_mk,
                "score": score,
            })

    out.sort(key=lambda c: (-c["score"], -c["rec_mk"], -c["eff_tv"]))
    return out[:14]


def rule_plan(state: dict, ctx: Context, cands: list[dict]) -> list[dict]:
    budget_bags = {lid: kg // BAG for lid, kg in sell_budget(state, ctx).items()}
    lot_left = {l["lot_id"]: l["kg_available"] // BAG for l in state["lots"]}
    rfq_left = {r["rfq_id"]: r["bags"] for r in state["rfqs"]}
    cap_left = (state["capacity_left_kg"] // BAG) if state.get("capacity_left_kg") is not None else 999

    plan = []
    used_pairs = set()
    used_rfqs = set()

    for pass_distinct in (True, False):
        for c in cands:
            if cap_left <= 0:
                break
            lid, rid = c["lot_id"], c["rfq_id"]
            if (lid, rid) in used_pairs:
                continue
            if pass_distinct and rid in used_rfqs:
                continue
            bags = min(c["max_bags"], budget_bags.get(lid, 0), lot_left.get(lid, 0), rfq_left.get(rid, 0), cap_left)
            if bags > 0:
                plan.append({"lot_id": lid, "rfq_id": rid, "bags": bags, "markup": c["rec_mk"]})
                used_pairs.add((lid, rid))
                used_rfqs.add(rid)
                budget_bags[lid] -= bags
                lot_left[lid] -= bags
                rfq_left[rid] -= bags
                cap_left -= bags

    for c in cands:
        if cap_left <= 0:
            break
        lid, rid = c["lot_id"], c["rfq_id"]
        if (lid, rid) in used_pairs:
            continue
        bags = min(c["max_bags"], lot_left.get(lid, 0), rfq_left.get(rid, 0), cap_left)
        if bags > 0:
            plan.append({"lot_id": lid, "rfq_id": rid, "bags": bags, "markup": c["rec_mk"]})
            used_pairs.add((lid, rid))
            lot_left[lid] -= bags
            rfq_left[rid] -= bags
            cap_left -= bags

    return plan


def guard(state: dict, ctx: Context, plan: list[dict]) -> list[dict]:
    policy = phase_policy(state, ctx)
    lots = {l["lot_id"]: l for l in state["lots"]}
    rfqs = {r["rfq_id"]: r for r in state["rfqs"]}
    room = state["capacity_left_kg"] if state.get("capacity_left_kg") is not None else 10**9
    lot_left = {i: l["kg_available"] for i, l in lots.items()}
    rfq_left = {i: r["kg"] for i, r in rfqs.items()}
    offers_by_pair = {}

    for p in plan:
        lot, rfq = lots.get(p["lot_id"]), rfqs.get(p["rfq_id"])
        if lot is None or rfq is None or not spec_ok_for_lot(lot, rfq["spec"], ctx.memory):
            continue
        lid, rid = lot["lot_id"], rfq["rfq_id"]
        if (lid, rid) in offers_by_pair:
            continue
        b_stats = ctx.memory.get_buyer_estimate(rfq["buyer"], rfq["buyer_type"]) if ctx.memory else None
        fl, _, cap_mk = bounds_for_candidate(policy, b_stats, rfq["buyer_type"])
        if rfq["buyer_type"] == "INSTANT" and "อินสแตนท์ไทย" in rfq["buyer"]:
            cap_mk = min(cap_mk, 0.910)
            fl = min(fl, cap_mk)
        elif rfq["buyer_type"] == "INSTANT" and "ทรีอินวัน" in rfq["buyer"]:
            cap_mk = min(cap_mk, 0.936)
            fl = min(fl, cap_mk)
        markup = min(max(float(p["markup"]), fl, MIN_MARKUP), cap_mk, MAX_MARKUP)
        tv_factor = ctx.memory.get_lot_tv_factor(lid) if ctx.memory and hasattr(ctx.memory, "get_lot_tv_factor") else 1.0
        price = round(markup * ctx.tv[lid] * tv_factor, 2)
        kg = min(p["bags"] * BAG, lot_left[lid], rfq_left[rid], room) // BAG * BAG
        if kg >= BAG:
            offers_by_pair[(lid, rid)] = {"rfq_id": rid, "lot_id": lid, "kg": kg, "price_per_kg": price}
            lot_left[lid] -= kg
            rfq_left[rid] -= kg
            room -= kg

    # After-Festival final-round sweep if any capacity & stock remain
    if rounds_left(state, ctx) <= 1 and room >= BAG:
        permissive = sorted(state.get("rfqs", []), key=lambda r: (-r["spec"].get("max_moisture_pct", 0), -r["spec"].get("max_defect_pct", 0), -r["kg"]))
        for lid, rem_kg in list(lot_left.items()):
            if rem_kg < BAG or room < BAG:
                continue
            for rfq in permissive:
                rid = rfq["rfq_id"]
                if (lid, rid) in offers_by_pair or rfq_left.get(rid, 0) < BAG or room < BAG:
                    continue
                add_kg = min(rem_kg, rfq_left[rid], room) // BAG * BAG
                if add_kg >= BAG:
                    dump_mk = policy["starts"].get(rfq["buyer_type"], 0.85)
                    tv_factor = ctx.memory.get_lot_tv_factor(lid) if ctx.memory and hasattr(ctx.memory, "get_lot_tv_factor") else 1.0
                    offers_by_pair[(lid, rid)] = {"rfq_id": rid, "lot_id": lid, "kg": add_kg, "price_per_kg": round(dump_mk * ctx.tv[lid] * tv_factor, 2)}
                    lot_left[lid] -= add_kg
                    rem_kg -= add_kg
                    rfq_left[rid] -= add_kg
                    room -= add_kg
                    if rem_kg < BAG or room < BAG:
                        break

    return list(offers_by_pair.values())


def decide(state: dict, ctx: Context) -> tuple[list[dict], str]:
    if ctx.memory:
        if hasattr(ctx.memory, "observe_lots"):
            ctx.memory.observe_lots(state.get("lots", []), ctx.tv)
        ctx.memory.observe_rfqs(state.get("rfqs", []))
    cands = candidates(state, ctx)
    if not cands:
        if rounds_left(state, ctx) <= 1:
            sweep = guard(state, ctx, [])
            if sweep:
                return sweep, "rules"
        return [], "none"
    return guard(state, ctx, rule_plan(state, ctx, cands)), "rules"
