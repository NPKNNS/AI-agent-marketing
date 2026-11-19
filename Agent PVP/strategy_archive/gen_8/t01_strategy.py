"""Tuned Adaptive Phased Trading Strategy for Stage 3 Main Competition (15 Rounds + Seasonal Calendar + 56 Teams).

Key Upgrades from Stage 2 56-Team Post-Mortem & Stage 3 Rules:
1. Market-Summary Clearing Calibration & Crowd-Aware Routing:
   - Uses per-buyer `mkt_clearing_mk`, `crowding` (offers/wanted_bags), and `avg_fill_pct` from `memory.py`.
   - Allows SPECIALTY lots to compete on BOTH SPECIALTY and COMMERCIAL RFQs, automatically shifting volume
     to COMMERCIAL when SPECIALTY RFQs are overcrowded (>1.8x offers/bag) or clearing below COMMERCIAL.
2. Strict Protection Against INSTANT Downgrades:
   - Never offers a solid SPECIALTY or COMMERCIAL lot to an overcrowded INSTANT RFQ when COMMERCIAL RFQs
     exist in the round (even in FINAL_DUMP), preventing the R8-04 mistake from Stage 2.
3. Stage 3 Seasonal Calendar Pacing (R1-R5 Normal -> R6-R8 Coffee Festival -> R9-R12 Pre-Drop -> R13-R15 Low Demand):
   - Front-loads sales before the R13-R15 late-market demand contraction.
   - Unleashes SPECIALTY inventory during R6-R8 Coffee Festival (`เทศกาลกาแฟ`).
4. 1-Bag Borderline Spec Probes (Prioritized in P1-P2 Practice Rounds) & Pass 3 Final-Round Sweep.
"""
import json
import math
import random
from dataclasses import dataclass, field

from appraisal import grade as lot_grade

BAG = 60
MIN_MARKUP, MAX_MARKUP = 0.83, 1.30
PACE = 1.40

MARKUP = {"SPECIALTY": 1.085, "COMMERCIAL": 1.030, "INSTANT": 0.935}

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
    """Raw sample spec check (compatible with starter interface)."""
    return (
        sample["defect_pct"] <= spec["max_defect_pct"]
        and sample["moisture_pct"] <= spec["max_moisture_pct"]
        and (spec["min_screen"] is None or sample["screen"] >= spec["min_screen"])
    )


def spec_ok_for_lot(lot: dict, spec: dict, memory: object = None) -> bool:
    """Checks both sample spec and memory of proven SPEC_MISMATCH failures."""
    if memory is not None and hasattr(memory, "is_spec_failed"):
        if memory.is_spec_failed(lot["lot_id"], spec):
            return False
    return spec_ok(lot["sample"], spec)


def spec_safety_info(lot: dict, spec: dict, btype: str, memory: object = None) -> tuple[bool, float]:
    """Returns (is_borderline_unverified, min_margin_pct)."""
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
    """Returns (best_tier, is_solid_for_tier) for a lot based on sample + SPEC_MISMATCH history."""
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


def _parse_calendar_info(calendar: list, total_rounds: int) -> dict[int, dict]:
    """Maps round number 1..total_rounds to {weight, specialty_share, label}."""
    info = {r: {"weight": 1.0, "specialty_share": None, "label": "ปกติ"} for r in range(1, total_rounds + 1)}
    for entry in calendar or []:
        w = float(entry.get("demand_weight") or 1.0)
        sp = entry.get("specialty_share")
        lbl = str(entry.get("label") or "ปกติ")
        spec = str(entry.get("rounds", "")).strip()
        if "-" in spec:
            parts = spec.split("-", 1)
            try:
                lo, hi = int(parts[0]), int(parts[1])
                for r in range(lo, hi + 1):
                    if 1 <= r <= total_rounds:
                        info[r] = {"weight": w, "specialty_share": sp, "label": lbl}
            except ValueError:
                pass
        else:
            try:
                r = int(spec)
                if 1 <= r <= total_rounds:
                    info[r] = {"weight": w, "specialty_share": sp, "label": lbl}
            except ValueError:
                pass
    return info


def phase_policy(state: dict, ctx: Context) -> dict:
    """Determines current game phase, price floors, and strategic instructions."""
    rnd = state["round"]
    total = max(1, ctx.rules["timeline"]["rounds"])
    cur = 1 if rnd["kind"] == "practice" else rnd["number"]
    left = rounds_left(state, ctx)
    progress = cur / total
    cal_map = _parse_calendar_info(ctx.calendar, total)
    cur_cal = cal_map.get(cur, {"weight": 1.0, "label": "ปกติ"})
    is_festival = "เทศกาล" in cur_cal["label"] or (cur_cal.get("specialty_share") or 0) >= 0.30

    if left <= 2 or cur_cal["weight"] < 0.75:
        dump_step = 0.030 if left >= 2 else 0.085
        final_caps = (
            {"SPECIALTY": 0.93, "COMMERCIAL": 0.91, "INSTANT": 0.86}
            if left <= 1
            else ({"SPECIALTY": 0.98, "COMMERCIAL": 0.95, "INSTANT": 0.88} if left == 2 else None)
        )
        return {
            "name": "FINAL_DUMP",
            "floor": 0.83,
            "dump_step": dump_step,
            "final_caps": final_caps,
            "type_floors": {"SPECIALTY": 0.86, "COMMERCIAL": 0.86, "INSTANT": 0.83},
            "guidance": (
                f"FINAL DUMP (left={left}, cal={cur_cal['label']}): Demand is shrinking and unsold beans get 0.80x! "
                "Fill 100% of round_cap at rec_mk across COMMERCIAL/SPECIALTY/INSTANT."
            ),
        }
    if is_festival:
        return {
            "name": "FESTIVAL_PEAK",
            "floor": 0.90,
            "dump_step": 0.0,
            "type_floors": {"SPECIALTY": 1.080, "COMMERCIAL": 0.995, "INSTANT": 0.89},
            "guidance": (
                "FESTIVAL PEAK (เทศกาลกาแฟ): High Specialty & market demand! "
                "Sell SPECIALTY lots to B2/B1 at 1.08x-1.13x and COMMERCIAL at >=0.995x."
            ),
        }
    if progress <= 0.36:
        return {
            "name": "EARLY_COMPETE",
            "floor": 0.885,
            "dump_step": 0.0,
            "type_floors": {"SPECIALTY": 1.075, "COMMERCIAL": 0.990, "INSTANT": 0.885},
            "guidance": (
                "EARLY COMPETE (R1-R5): Clear INSTANT fast at 0.885-0.92x; "
                "preserve SPECIALTY for R6-R12 peak demand unless >=1.075x."
            ),
        }
    if progress <= 0.72:
        return {
            "name": "MID_VALUE",
            "floor": 0.89,
            "dump_step": 0.004,
            "type_floors": {"SPECIALTY": 1.070, "COMMERCIAL": 0.985, "INSTANT": 0.885},
            "guidance": (
                "MID VALUE (R9-R11): StayLow bots are running low — capture 1.07-1.13x on SPECIALTY and 0.985-1.03x on COMMERCIAL!"
            ),
        }
    return {
        "name": "LATE_CLEAR",
        "floor": 0.87,
        "dump_step": 0.010,
        "type_floors": {"SPECIALTY": 0.98, "COMMERCIAL": 0.955, "INSTANT": 0.86},
        "guidance": (
            "LATE CLEAR (R12): Hold COMMERCIAL >=0.955x and SPECIALTY >=0.98x before R13-R15 demand drops!"
        ),
    }


def bounds_for_candidate(policy: dict, b_stats: dict | None, buyer_type: str, round_num: int) -> tuple[float, float, float]:
    """Returns (eff_floor, rec_mk, cap_mk) for a (lot, rfq) candidate."""
    base_floor = policy["type_floors"].get(buyer_type, policy["floor"])
    ub = b_stats.get("upper_bound") if b_stats else None
    oc = b_stats.get("outbid_cap") if b_stats else None
    mkt_mk = b_stats.get("mkt_clearing_mk") if b_stats else None
    crowding = float(b_stats.get("crowding", 1.0)) if b_stats else 1.0
    fill_pct = float(b_stats.get("avg_fill_pct", 100.0)) if b_stats else 100.0
    safe_mk = b_stats.get("safe_mk", MARKUP.get(buyer_type, 1.00)) if b_stats else MARKUP.get(buyer_type, 1.00)
    step = policy.get("dump_step", 0.0)

    cap_mk = MAX_MARKUP
    if ub is not None:
        cap_mk = min(cap_mk, round(max(MIN_MARKUP, ub - 0.015), 3))
    if oc is not None:
        cap_mk = min(cap_mk, round(max(MIN_MARKUP, oc - 0.008), 3))
    if buyer_type != "SPECIALTY" and mkt_mk is not None and fill_pct >= 98.0 and crowding >= 1.2:
        cap_mk = min(cap_mk, round(max(MIN_MARKUP, mkt_mk + 0.014), 3))
    cap_mk = min(cap_mk, round(max(MIN_MARKUP, safe_mk - step + 0.012), 3))
    if policy.get("final_caps"):
        fcap = policy["final_caps"].get(buyer_type)
        if fcap is not None:
            cap_mk = min(cap_mk, fcap)

    eff_floor = round(max(MIN_MARKUP, min(base_floor, safe_mk - step, cap_mk)), 3)
    rec_mk = round(min(cap_mk, max(eff_floor, safe_mk - step)), 3)
    return eff_floor, rec_mk, cap_mk


def sell_budget(state: dict, ctx: Context) -> dict[str, int]:
    """Per-lot target kg to offer this round:
    1. Weights current round by calendar demand_weight relative to remaining rounds.
    2. Normalizes per-lot Pass-1 budgets to fit within capacity_left_kg so every active lot advances.
    """
    rnd = state["round"]
    total = max(1, ctx.rules["timeline"]["rounds"])
    cur = 1 if rnd["kind"] == "practice" else rnd["number"]
    left = rounds_left(state, ctx)
    cal_map = _parse_calendar_info(ctx.calendar, total)
    cur_w = cal_map.get(cur, {"weight": 1.0})["weight"]
    rem_w = sum(cal_map.get(r, {"weight": 1.0})["weight"] for r in range(cur, total + 1)) or float(left)
    eff_pace = 0.75 if (rnd["kind"] != "practice" and cur <= 4) else (1.05 if cur == 5 else 1.65)
    frac = 1.0 if left <= 1 else min(1.0, (cur_w / rem_w) * eff_pace)
    cap_bags = (state["capacity_left_kg"] // BAG) if state.get("capacity_left_kg") is not None else 999

    raw_bags: dict[str, int] = {}
    avail_map: dict[str, int] = {}
    for lot in state["lots"]:
        lid = lot["lot_id"]
        avail_b = lot["kg_available"] // BAG
        avail_map[lid] = avail_b
        if avail_b < 1:
            raw_bags[lid] = 0
            continue
        has_el = any(spec_ok_for_lot(lot, r["spec"], ctx.memory) for r in state.get("rfqs", []))
        if not has_el:
            raw_bags[lid] = 0
            continue
        des = avail_b if left <= 1 else max(1, math.ceil(avail_b * frac))
        if rnd["kind"] != "practice" and cur <= 5:
            tier_l, _ = lot_best_tier(lot, ctx.memory)
            if tier_l == "INSTANT":
                des = avail_b if left <= 1 else max(2, math.ceil(avail_b * min(1.0, (cur_w / rem_w) * 1.85)))
        if lot["sample"]["moisture_pct"] > 13.7 and left > 1:
            des = min(avail_b, des + 1)
        raw_bags[lid] = min(avail_b, des)

    active_lids = [lid for lid, b in raw_bags.items() if b > 0]
    if sum(raw_bags.values()) > cap_bags and active_lids and cap_bags < 999:
        alloc = {lid: 0 for lid in raw_bags}
        rem_cap = cap_bags
        lot_by_id = {l["lot_id"]: l for l in state["lots"]}
        priority_lids = sorted(
            active_lids,
            key=lambda lid: (
                0 if lot_by_id[lid]["sample"]["moisture_pct"] > 13.7 else 1,
                -avail_map[lid],
            ),
        )
        for lid in priority_lids:
            if rem_cap > 0 and avail_map[lid] > 0:
                alloc[lid] = 1
                rem_cap -= 1
        while rem_cap > 0:
            needs = [lid for lid in priority_lids if alloc[lid] < raw_bags[lid]]
            if not needs:
                needs = [lid for lid in priority_lids if alloc[lid] < avail_map[lid]]
            if not needs:
                break
            needs.sort(key=lambda lid: (-(raw_bags[lid] - alloc[lid]), -avail_map[lid]))
            alloc[needs[0]] += 1
            rem_cap -= 1
        return {lid: alloc.get(lid, 0) * BAG for lid in avail_map}

    return {lid: raw_bags.get(lid, 0) * BAG for lid in avail_map}


def candidates(state: dict, ctx: Context) -> list[dict]:
    """Builds a crowd-aware, spec-safe, expected-value-ranked candidate list."""
    policy = phase_policy(state, ctx)
    left = rounds_left(state, ctx)
    cur_round = 1 if state["round"]["kind"] == "practice" else state["round"]["number"]
    cap_bags = (state["capacity_left_kg"] // BAG) if state.get("capacity_left_kg") is not None else 999

    has_commercial_rfq = any(r["buyer_type"] == "COMMERCIAL" for r in state["rfqs"])
    has_specialty_rfq = any(r["buyer_type"] == "SPECIALTY" for r in state["rfqs"])

    out = []
    for lot in state["lots"]:
        lot_bags = lot["kg_available"] // BAG
        if lot_bags < 1:
            continue
        best_tier, is_solid = lot_best_tier(lot, ctx.memory)
        tv_factor = ctx.memory.get_lot_tv_factor(lot["lot_id"]) if ctx.memory and hasattr(ctx.memory, "get_lot_tv_factor") else 1.0
        eff_tv = round(ctx.tv[lot["lot_id"]] * tv_factor, 2)

        eligible_rfqs = [rfq for rfq in state["rfqs"] if spec_ok_for_lot(lot, rfq["spec"], ctx.memory)]
        if not eligible_rfqs:
            continue

        # Protect SPECIALTY and COMMERCIAL lots from being dumped into overcrowded INSTANT RFQs
        # whenever COMMERCIAL or SPECIALTY RFQs exist in the round!
        filtered_rfqs = []
        for rfq in eligible_rfqs:
            rtype = rfq["buyer_type"]
            if best_tier in ("SPECIALTY", "COMMERCIAL") and is_solid:
                if rtype == "INSTANT" and (has_commercial_rfq or has_specialty_rfq):
                    continue
            filtered_rfqs.append(rfq)

        if not filtered_rfqs:
            filtered_rfqs = eligible_rfqs

        if len(filtered_rfqs) >= 2 and lot_bags >= 2:
            per_rfq_cap = max(1, math.ceil(min(lot_bags, cap_bags) * 0.55))
        else:
            per_rfq_cap = min(lot_bags, cap_bags)

        lot_cands = []
        for rfq in filtered_rfqs:
            rtype = rfq["buyer_type"]
            b_stats = (
                ctx.memory.get_buyer_estimate(rfq["buyer"], rtype)
                if ctx.memory
                else {"est_ceiling": MARKUP.get(rtype, 1.00), "safe_mk": MARKUP.get(rtype, 1.00), "crowding": 1.0, "avg_fill_pct": 100.0}
            )
            fl, rec_mk, cap_mk = bounds_for_candidate(policy, b_stats, rtype, cur_round)
            if rtype == "SPECIALTY" and "ดอยช้าง" in rfq["buyer"]:
                rec_mk = min(rec_mk, 1.110)
                cap_mk = min(cap_mk, 1.115)
            elif rtype == "SPECIALTY" and "บางกอกบลูม" in rfq["buyer"] and policy["name"] in ("FESTIVAL_PEAK", "MID_VALUE"):
                rec_mk = max(rec_mk, min(cap_mk, 1.095))
            borderline, margin = spec_safety_info(lot, rfq["spec"], rtype, ctx.memory)
            crowding = float(b_stats.get("crowding", 1.0))
            fill_pct = float(b_stats.get("avg_fill_pct", 100.0))
            outbids = int(b_stats.get("consecutive_outbids", 0))

            is_practice = state["round"].get("kind") == "practice"
            if borderline and left > 2:
                max_b = min(1, lot_bags, rfq["bags"], cap_bags)
                risk_pen = -0.040 if is_practice else 0.025
            elif rtype == "SPECIALTY" and (crowding >= 2.2 and outbids >= 2) and has_commercial_rfq and left <= 5:
                max_b = min(1, lot_bags, rfq["bags"], cap_bags)
                risk_pen = 0.0
            else:
                max_b = min(per_rfq_cap, lot_bags, rfq["bags"], cap_bags)
                risk_pen = 0.0

            est_price = round(rec_mk * eff_tv, 2)
            gain_per_kg = round(est_price - eff_tv, 2)

            # Crowd penalty vs Undersupply bonus (learns directly from 56-team market_summary)
            crowd_pen = min(0.055, max(0.0, (crowding - 1.4) * 0.018)) + (0.015 * min(3, outbids)) if fill_pct >= 98.0 else 0.0
            undersupply_bonus = 0.022 if fill_pct < 92.0 else 0.0

            tier_match_bonus = 0.036 if rtype == best_tier else (0.010 if best_tier == "SPECIALTY" and rtype == "COMMERCIAL" and rec_mk >= 1.025 else -0.025)
            # Gen 5: Prefer B2 (2 บางกอกบลูม, higher 1.165 ceiling) over B1 on SPECIALTY
            if rtype == "SPECIALTY" and "บางกอกบลูม" in rfq["buyer"]:
                tier_match_bonus += 0.018
            # Pre-Festival Pacing (R1-R5): clear INSTANT/COMMERCIAL early while saving SPECIALTY for R6-R12
            if policy["name"] == "EARLY_COMPETE" and not is_practice:
                if best_tier == "INSTANT" and rtype == "INSTANT":
                    tier_match_bonus += 0.090
                elif best_tier == "COMMERCIAL" and rtype == "COMMERCIAL":
                    tier_match_bonus += 0.032
                elif best_tier == "SPECIALTY" and rtype == "COMMERCIAL" and rec_mk < 1.045:
                    tier_match_bonus -= 0.060
            elif policy["name"] == "FESTIVAL_PEAK" and best_tier == "SPECIALTY" and rtype == "SPECIALTY":
                tier_match_bonus += 0.050

            b6_scarcity_bonus = 0.065 if lot["sample"]["moisture_pct"] > 13.7 else 0.0
            urgency_bonus = min(0.08, max(0.0, (lot_bags / max(1, left) - 0.75) * 0.045))
            crowd_jitter = (ctx.rng.random() ** (1.0 / max(1, rfq["bags"]))) * 0.008

            score = round(
                rec_mk + tier_match_bonus + b6_scarcity_bonus + urgency_bonus + undersupply_bonus - crowd_pen - risk_pen + crowd_jitter,
                4,
            )

            lot_cands.append({
                "lot_id": lot["lot_id"],
                "rfq_id": rfq["rfq_id"],
                "buyer": rfq["buyer"],
                "buyer_id": rfq.get("buyer_id", ""),
                "buyer_type": rtype,
                "best_tier": best_tier,
                "borderline": borderline,
                "spec_margin": margin,
                "rfq_bags": rfq["bags"],
                "max_bags": max_b,
                "tv": ctx.tv[lot["lot_id"]],
                "eff_tv": eff_tv,
                "gain_per_kg": gain_per_kg,
                "est_ceiling": b_stats["est_ceiling"],
                "fl": fl,
                "rec_mk": rec_mk,
                "cap_mk": cap_mk,
                "score": score,
            })

        lot_cands.sort(key=lambda c: (-c["score"], -c["rec_mk"], -c["rfq_bags"]))
        picked, seen_buyers = [], set()
        for c in lot_cands:
            if c["buyer"] not in seen_buyers and len(picked) < 4:
                picked.append(c)
                seen_buyers.add(c["buyer"])
        for c in lot_cands:
            if c not in picked and len(picked) < 4:
                picked.append(c)
        out.extend(picked)

    out.sort(key=lambda c: (-c["score"], -c["rec_mk"], -c["eff_tv"]))
    return out[:14]


def rule_plan(state: dict, ctx: Context, cands: list[dict]) -> list[dict]:
    """Adaptive algorithmic planner:
    Pass 1: Respects per-lot sell_budget across distinct RFQs.
    Pass 2: Uses any remaining round capacity (capacity_left_kg) on highest-scoring remaining candidates.
    """
    budget_bags = {lid: kg // BAG for lid, kg in sell_budget(state, ctx).items()}
    lot_left = {l["lot_id"]: l["kg_available"] // BAG for l in state["lots"]}
    rfq_left = {r["rfq_id"]: r["bags"] for r in state["rfqs"]}
    cap_left = (state["capacity_left_kg"] // BAG) if state.get("capacity_left_kg") is not None else 999

    plan = []
    used_pairs = set()
    used_rfqs = set()

    sorted_cands = sorted(cands, key=lambda c: (-c["score"], -c["rec_mk"]))
    for pass_distinct in (True, False):
        for c in sorted_cands:
            if cap_left <= 0:
                break
            lid, rid = c["lot_id"], c["rfq_id"]
            if (lid, rid) in used_pairs:
                continue
            if pass_distinct and rid in used_rfqs:
                continue
            bags = min(
                c["max_bags"],
                budget_bags.get(lid, 0),
                lot_left.get(lid, 0),
                rfq_left.get(rid, 0),
                cap_left,
            )
            if bags > 0:
                plan.append({
                    "lot_id": lid,
                    "rfq_id": rid,
                    "bags": bags,
                    "markup": c["rec_mk"],
                })
                used_pairs.add((lid, rid))
                used_rfqs.add(rid)
                budget_bags[lid] -= bags
                lot_left[lid] -= bags
                rfq_left[rid] -= bags
                cap_left -= bags

    for c in sorted(cands, key=lambda x: (-x["score"], -x["rec_mk"], -x["eff_tv"])):
        if cap_left <= 0:
            break
        lid, rid = c["lot_id"], c["rfq_id"]
        if (lid, rid) in used_pairs:
            continue
        if c["buyer_type"] == "SPECIALTY" and rid in used_rfqs:
            continue
        bags = min(c["max_bags"], lot_left.get(lid, 0), rfq_left.get(rid, 0), cap_left)
        if bags > 0:
            plan.append({
                "lot_id": lid,
                "rfq_id": rid,
                "bags": bags,
                "markup": c["rec_mk"],
            })
            used_pairs.add((lid, rid))
            used_rfqs.add(rid)
            lot_left[lid] -= bags
            rfq_left[rid] -= bags
            cap_left -= bags

    return plan


SYSTEM = """You are an autonomous Robusta coffee trading AI in a 56-team sealed-offer market.
Select offers: (candidate index, bags, markup). Price = markup x lot Effective True Value (eff_tv).
Rules:
1. Never exceed cap_mk or go below fl; set markup = rec_mk (already calibrated to live market_summary clearing prices).
2. Respect round_cap and diversify across distinct RFQs/buyers.
3. Candidates are sorted by expected clearing score (accounting for buyer crowding & fill rate); prefer top candidates across all lots.
Answer ONLY valid JSON matching the schema."""

SCHEMA = {
    "type": "object",
    "required": ["offers", "reason"],
    "properties": {
        "offers": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["candidate", "bags", "markup"],
                "properties": {
                    "candidate": {"type": "integer"},
                    "bags": {"type": "integer"},
                    "markup": {"type": "number"},
                },
            },
        },
        "reason": {"type": "string", "maxLength": 120},
    },
}


def llm_plan(state: dict, ctx: Context, cands: list[dict]) -> list[dict] | None:
    rnd = state["round"]
    policy = phase_policy(state, ctx)
    time_for_llm = ctx.seconds_left - 8
    if time_for_llm < 5:
        ctx.notes.append("no time for LLM this round")
        return None

    lot_rows = [
        {
            "lot": l["lot_id"],
            "gr": lot_grade(ctx.rules, l["sample"]["defect_pct"])[0],
            "tier": lot_best_tier(l, ctx.memory)[0][:4],
            "bags": l["kg_available"] // BAG,
            "eff_tv": round(
                ctx.tv[l["lot_id"]]
                * (ctx.memory.get_lot_tv_factor(l["lot_id"]) if ctx.memory and hasattr(ctx.memory, "get_lot_tv_factor") else 1.0),
                2,
            ),
        }
        for l in state["lots"]
        if l["kg_available"] >= BAG
    ]
    table = [
        {
            "c": i,
            "lot": c["lot_id"],
            "rfq": c["rfq_id"],
            "buyer": f"{c['buyer_id'] or c['buyer'][:8]}({c['buyer_type'][:4]})",
            "fl": c["fl"],
            "rec_mk": c["rec_mk"],
            "cap_mk": c["cap_mk"],
            "score": c["score"],
            "max_b": c["max_bags"],
        }
        for i, c in enumerate(cands)
    ]

    cur_round = 1 if rnd["kind"] == "practice" else rnd["number"]
    active_buyers = sorted({r["buyer"] for r in state["rfqs"]})
    buyer_intel = ctx.memory.buyer_intelligence_summary(active_buyers) if ctx.memory else "none"
    cap_bags = (state["capacity_left_kg"] // BAG) if state.get("capacity_left_kg") is not None else "all"

    user = "\n".join([
        f"R{cur_round}/{ctx.rules['timeline']['rounds']} ({rnd['calendar']}), left={rounds_left(state, ctx)}, phase={policy['name']}, round_cap={cap_bags}b.",
        f"Rule: {policy['guidance']}",
        f"Lots: {json.dumps(lot_rows, ensure_ascii=False)}",
        f"Buyers:\n{buyer_intel}",
        f"History:\n{ctx.memory.summary() if ctx.memory else 'none'}",
        f"Candidates (pick c, total bags <= {cap_bags}, bags<=max_b, fl<=markup<=cap_mk):\n{json.dumps(table, ensure_ascii=False)}",
    ])

    answer = ctx.llm.ask_json(SYSTEM, user, SCHEMA, timeout=time_for_llm)
    ctx.last_trace = {
        "round_id": rnd["round_id"],
        "phase": policy["name"],
        "system_prompt": SYSTEM,
        "user_prompt": user,
        "raw_answer": answer,
        "llm_error": getattr(ctx.llm, "last_error", None),
        "llm_seconds": getattr(ctx.llm, "seconds", None),
    }
    if answer is None or not isinstance(answer.get("offers"), list):
        ctx.notes.append(f"llm failed: {ctx.llm.last_error}")
        return None

    plan = []
    for o in answer["offers"]:
        try:
            c = cands[int(o["candidate"])]
            llm_mk = max(float(o["markup"]), round(c["rec_mk"] - 0.002, 3))
            plan.append({
                "lot_id": c["lot_id"],
                "rfq_id": c["rfq_id"],
                "bags": int(o["bags"]),
                "markup": llm_mk,
                "fl": c["fl"],
                "cap_mk": c["cap_mk"],
            })
        except (KeyError, IndexError, TypeError, ValueError):
            ctx.notes.append(f"llm bad offer ignored: {o}")
    ctx.notes.append(f"phase={policy['name']} | llm reason: {str(answer.get('reason'))[:140]}")
    return plan


def guard(state: dict, ctx: Context, plan: list[dict]) -> list[dict]:
    """Enforces API constraints, SPEC_MISMATCH blacklist, per-lot round pacing budget, phase floors, and rejection caps."""
    policy = phase_policy(state, ctx)
    cur_round = 1 if state["round"]["kind"] == "practice" else state["round"]["number"]
    lots = {l["lot_id"]: l for l in state["lots"]}
    rfqs = {r["rfq_id"]: r for r in state["rfqs"]}
    room = state["capacity_left_kg"] if state.get("capacity_left_kg") is not None else 10**9
    lot_left = {i: l["kg_available"] for i, l in lots.items()}
    lot_budget = sell_budget(state, ctx)
    rfq_left = {i: r["kg"] for i, r in rfqs.items()}
    offers_by_pair: dict[tuple[str, str], dict] = {}
    overflow_requests = []

    for p in plan:
        lot, rfq = lots.get(p["lot_id"]), rfqs.get(p["rfq_id"])
        if lot is None or rfq is None or not spec_ok_for_lot(lot, rfq["spec"], ctx.memory):
            ctx.notes.append(f"guard dropped {p}")
            continue
        lid, rid = lot["lot_id"], rfq["rfq_id"]
        if (lid, rid) in offers_by_pair:
            continue

        b_stats = ctx.memory.get_buyer_estimate(rfq["buyer"], rfq["buyer_type"]) if ctx.memory else None
        fl, _, cap_mk = bounds_for_candidate(policy, b_stats, rfq["buyer_type"], cur_round)
        markup = min(max(float(p["markup"]), fl, MIN_MARKUP), cap_mk, MAX_MARKUP)
        tv_factor = ctx.memory.get_lot_tv_factor(lid) if ctx.memory and hasattr(ctx.memory, "get_lot_tv_factor") else 1.0
        eff_tv = ctx.tv[lid] * tv_factor
        price = round(markup * eff_tv, 2)

        req_kg = p["bags"] * BAG
        pass1_kg = min(req_kg, lot_budget.get(lid, 0), lot_left[lid], rfq_left[rid], room) // BAG * BAG
        if pass1_kg >= BAG:
            offers_by_pair[(lid, rid)] = {
                "rfq_id": rid,
                "lot_id": lid,
                "kg": pass1_kg,
                "price_per_kg": price,
            }
            lot_budget[lid] -= pass1_kg
            lot_left[lid] -= pass1_kg
            rfq_left[rid] -= pass1_kg
            room -= pass1_kg
        if req_kg > pass1_kg:
            overflow_requests.append((lid, rid, req_kg - pass1_kg, price))

    # Pass 2: Top up any remaining round capacity from overflow requests
    for lid, rid, extra_kg, price in overflow_requests:
        if room < BAG:
            break
        add_kg = min(extra_kg, lot_left[lid], rfq_left[rid], room) // BAG * BAG
        if add_kg >= BAG:
            if (lid, rid) in offers_by_pair:
                offers_by_pair[(lid, rid)]["kg"] += add_kg
            else:
                offers_by_pair[(lid, rid)] = {
                    "rfq_id": rid,
                    "lot_id": lid,
                    "kg": add_kg,
                    "price_per_kg": price,
                }
            lot_left[lid] -= add_kg
            rfq_left[rid] -= add_kg
            room -= add_kg

    # Pass 3 (Last-Round Sell-It-All Sweep)
    if rounds_left(state, ctx) <= 1 and room >= BAG:
        permissive_rfqs = sorted(
            state.get("rfqs", []),
            key=lambda r: (
                -r["spec"].get("max_moisture_pct", 0),
                -r["spec"].get("max_defect_pct", 0),
                -r["kg"],
            ),
        )
        for lid, rem_kg in list(lot_left.items()):
            if rem_kg < BAG or room < BAG:
                continue
            for rfq in permissive_rfqs:
                rid = rfq["rfq_id"]
                if (lid, rid) in offers_by_pair or rfq_left.get(rid, 0) < BAG or room < BAG:
                    continue
                add_kg = min(rem_kg, rfq_left[rid], room) // BAG * BAG
                if add_kg >= BAG:
                    tv_factor = ctx.memory.get_lot_tv_factor(lid) if ctx.memory and hasattr(ctx.memory, "get_lot_tv_factor") else 1.0
                    f_mk = 0.85 if rfq["buyer_type"] in ("SPECIALTY", "COMMERCIAL") else 0.83
                    price = round(f_mk * ctx.tv[lid] * tv_factor, 2)
                    offers_by_pair[(lid, rid)] = {
                        "rfq_id": rid,
                        "lot_id": lid,
                        "kg": add_kg,
                        "price_per_kg": price,
                    }
                    ctx.notes.append(f"final_round_sweep: {lid}->{rid} ({add_kg//BAG}b @ {price})")
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

    plan = llm_plan(state, ctx, cands) if ctx.llm else None
    if plan:
        offers = guard(state, ctx, plan + rule_plan(state, ctx, cands))
        n_llm = len(guard(state, Context(ctx.rules, ctx.calendar, ctx.tv, ctx.rng, ctx.memory), plan))
        if offers:
            return offers, "llm" if len(offers) == n_llm else f"llm+rules ({n_llm}+{len(offers) - n_llm})"

    return guard(state, ctx, rule_plan(state, ctx, cands)), "rules"
