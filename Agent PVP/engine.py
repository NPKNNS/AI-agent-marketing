"""Stage 3 56-Team AI Training Arena & Market Engine (2 Practice + 15 Real Rounds, 5 Lots / 45 Bags, Seasonal Calendar).

Matches `สไลด์วันที่-20.pdf` (Main Challenge — 50% of Total Score):
- 56 Teams total:
  * T01: AI Refined Agent (Competition Event-LLM + our tunable `self config agent bot/strategy.py` & `memory.py`)
  * T02: My Agent Strategy (Competition Event-LLM + locked `my_agent_strategy.py` & `my_agent_memory.py`, only tuned on user command)
  * B01..B54: 54 Competitor Bots:
    - B01..B02 (2 bots, 3.7%): Random Bots (1-5%)
    - B03..B09 (7 bots, 13.0%): Stay Low Price Bots (10-15%)
    - B10..B16 (7 bots, 13.0%): Stay High Price Bots (10-15%)
    - B17..B23 (7 bots, 13.0%): Market Price / Starter 1.00x Bots (10-15%)
    - B24..B54 (31 bots, 57.4%): 6 families of High-Efficiency Adaptive Bots (Bayes, FestivalHoarder, ClearingSniper, DPPacer, PID, EVMax)
- 5 Lots totaling 45 bags (2,700 kg) per team, with slight per-team measurement variations and sample noise.
- 2 Practice Rounds (P1, P2 — counts_for_score=False, stock restored before R1) + 15 Real Scoring Rounds (R1-R15).
- Official Stage 3 Seasonal Calendar:
  * R1-R5: ปกติ (weight 1.0)
  * R6-R8: เทศกาลกาแฟ (weight 1.35, high Specialty demand)
  * R9-R12: ปกติ (weight 1.0, leaderboard freezes after R12)
  * R13-R14: ใกล้ปิดตลาด (weight 0.65, demand drops)
  * R15: รอบสุดท้าย (weight 0.40, lowest demand)
- 1.5x Per-Round Capacity Limit (ceil(1.5 * 45 / 15) = 5 bags = 300 kg per round).
"""
import copy
import json
import math
import random
import threading
import time
from datetime import datetime
from pathlib import Path

from csv_logger import PVPCSVLogger

BAG = 60

ORIGINS = [
    ("ชุมพร - ท่าแซะ", "Natural (Dry)"),
    ("ชุมพร - ปะทิว", "Honey"),
    ("ชุมพร - ปะทิว", "Washed"),
    ("สุราษฎร์ธานี - ท่าชนะ", "Honey"),
    ("ระนอง - กระบุรี", "Washed"),
    ("ชุมพร - สวี", "Natural (Dry)"),
]

BUYER_PERSONAS = [
    {
        "buyer_id": "B1",
        "buyer": "1 ดอยช้างโรสเตอร์",
        "buyer_type": "SPECIALTY",
        "base_ceiling": 1.135,
        "noise": 0.02,
        "spec": {"max_defect_pct": 3.0, "max_moisture_pct": 12.5, "min_screen": 16},
    },
    {
        "buyer_id": "B2",
        "buyer": "2 บางกอกบลูม",
        "buyer_type": "SPECIALTY",
        "base_ceiling": 1.165,
        "noise": 0.02,
        "spec": {"max_defect_pct": 3.0, "max_moisture_pct": 12.5, "min_screen": 17},
    },
    {
        "buyer_id": "B3",
        "buyer": "3 สยามคอมเมิร์ซ",
        "buyer_type": "COMMERCIAL",
        "base_ceiling": 1.045,
        "noise": 0.015,
        "spec": {"max_defect_pct": 7.0, "max_moisture_pct": 12.5, "min_screen": None},
    },
    {
        "buyer_id": "B4",
        "buyer": "4 ชุมพรคั่วบด",
        "buyer_type": "COMMERCIAL",
        "base_ceiling": 1.060,
        "noise": 0.015,
        "spec": {"max_defect_pct": 7.0, "max_moisture_pct": 12.5, "min_screen": None},
    },
    {
        "buyer_id": "B5",
        "buyer": "5 อินสแตนท์ไทย",
        "buyer_type": "INSTANT",
        "base_ceiling": 0.945,
        "noise": 0.015,
        "spec": {"max_defect_pct": 15.0, "max_moisture_pct": 14.0, "min_screen": None},
    },
    {
        "buyer_id": "B6",
        "buyer": "6 ทรีอินวันฟู้ดส์",
        "buyer_type": "INSTANT",
        "base_ceiling": 0.965,
        "noise": 0.015,
        "spec": {"max_defect_pct": 15.0, "max_moisture_pct": 15.0, "min_screen": None},
    },
]


def _build_56_team_roster(rng: random.Random | None = None) -> dict[str, dict]:
    """Randomly selects the 54 competitor bot types for a game while guaranteeing every bot type
    (random, stay_low, stay_high, market_price, and all 6 adaptive families) is present in the same game:
    - T01: AI Refined Agent (Event-LLM)
    - T02: My Agent Strategy (Locked Baseline)
    - B01..B54 (54 Competitor Bots):
      * Random Bot (1-5% of 56): randomly 1 to 3 bots
      * Stay Low Price Bot (10-15% of 56): randomly 6 to 8 bots
      * Stay High Price Bot (10-15% of 56): randomly 6 to 8 bots
      * Market Price Bot (10-15% of 56): randomly 6 to 8 bots
      * Adaptive Bots (remaining 27-35 bots): at least 2 of each of the 6 adaptive families + random draws
    """
    r = rng or random.Random()
    teams = {
        "T01": {
            "team_id": "T01",
            "key": "sk-T01-ai-refined",
            "team_name": "AI Refined Agent (Event-LLM)",
            "university": "NU XEUS · Refinable",
            "role": "agent_refined",
            "category": "agent_refined",
            "strategy": "ai_refined",
        },
        "T02": {
            "team_id": "T02",
            "key": "sk-T16-MgkI9WNcvneRFQlqkxYzrrIu",
            "team_name": "My Agent Strategy (Locked)",
            "university": "NU XEUS · Baseline",
            "role": "agent_user",
            "category": "agent_user",
            "strategy": "my_agent_locked",
        },
    }

    n_random = r.randint(1, 3)      # 1.8% - 5.4%
    n_low = r.randint(6, 8)         # 10.7% - 14.3%
    n_high = r.randint(6, 8)        # 10.7% - 14.3%
    n_market = r.randint(6, 8)      # 10.7% - 14.3%
    n_adaptive = 54 - (n_random + n_low + n_high + n_market)

    adaptive_families = [
        ("adaptive_bayes", "BayesBandit"),
        ("adaptive_festival", "FestivalHoarder"),
        ("adaptive_sniper", "ClearingSniper"),
        ("adaptive_dp_pacer", "DPPreDropPacer"),
        ("adaptive_pid", "PIDController"),
        ("adaptive_ev_max", "ExpectedValueMax"),
    ]
    # Guarantee at least 2 bots of every adaptive family in the same game, then randomize the rest
    adap_counts = {k: 2 for k, _ in adaptive_families}
    for _ in range(max(0, n_adaptive - 2 * len(adaptive_families))):
        chosen_k, _ = r.choice(adaptive_families)
        adap_counts[chosen_k] += 1

    bot_specs = []
    for k in range(1, n_random + 1):
        bot_specs.append(("random", "random_bot", f"RandomBot #{k:02d}", "Group: Random (1-5%)"))
    for k in range(1, n_low + 1):
        bot_specs.append(("stay_low", "stay_low", f"StayLowBot #{k:02d}", "Group: StayLow (10-15%)"))
    for k in range(1, n_high + 1):
        bot_specs.append(("stay_high", "stay_high", f"StayHighBot #{k:02d}", "Group: StayHigh (10-15%)"))
    for k in range(1, n_market + 1):
        bot_specs.append(("market_price", "market_price", f"MarketPriceBot #{k:02d}", "Group: MarketPrice (10-15%)"))
    for strat_key, label in adaptive_families:
        for k in range(1, adap_counts[strat_key] + 1):
            bot_specs.append(("adaptive", strat_key, f"{label} #{k:02d}", f"Adaptive: {label}"))

    r.shuffle(bot_specs)
    for i, (cat, strat, tname, univ) in enumerate(bot_specs, 1):
        tid = f"B{i:02d}"
        teams[tid] = {
            "team_id": tid,
            "key": f"sk-{tid}-bot",
            "team_name": tname,
            "university": univ,
            "role": "bot",
            "category": cat,
            "strategy": strat,
        }
    return teams


ALL_56_TEAMS = _build_56_team_roster()


def stage3_calendar(rounds: int = 15) -> list[dict]:
    if rounds == 15:
        return [
            {"rounds": "1-5", "label": "ปกติ", "demand_weight": 1.0, "specialty_share": 0.20},
            {"rounds": "6-8", "label": "เทศกาลกาแฟ", "demand_weight": 1.35, "specialty_share": 0.42},
            {"rounds": "9-12", "label": "ปกติ", "demand_weight": 1.0, "specialty_share": 0.20},
            {"rounds": "13-14", "label": "ใกล้ปิดตลาด", "demand_weight": 0.65, "specialty_share": 0.14},
            {"rounds": "15", "label": "รอบสุดท้าย", "demand_weight": 0.40, "specialty_share": 0.10},
        ]
    return [{"rounds": f"1-{rounds}", "label": "ปกติ", "demand_weight": 1.0, "specialty_share": 0.22}]


def default_rules(rounds: int = 15, practice_rounds: int = 2, round_capacity_factor: float | None = 1.5) -> dict:
    return {
        "bag_kg": 60,
        "round_capacity": "each round a team may have at most ceil(factor × its total bags ÷ rounds) bags on offer" if round_capacity_factor else None,
        "round_capacity_factor": round_capacity_factor,
        "salvage_ratio": 0.8,
        "true_value_formula": "TV = P0 × G(d) × M(m) × S(s)",
        "p0": 120.0,
        "defect_steps": [
            {"max_defect_pct": 1.0, "step": 1.08, "grade": "PREMIUM"},
            {"max_defect_pct": 3.0, "step": 1.04, "grade": "GRADE_1"},
            {"max_defect_pct": 5.0, "step": 1.0, "grade": "GRADE_2"},
            {"max_defect_pct": 7.0, "step": 0.96, "grade": "GRADE_3"},
            {"max_defect_pct": 15.0, "step": 0.85, "grade": "BELOW_STANDARD"},
            {"max_defect_pct": 100.0, "step": 0.6, "grade": "REJECT"},
        ],
        "G": "G(d) = (1 − d/100) × step(d)",
        "M": "M = 1 if m ≤ 12.5; (1 − m/100)/(1 − 12.5/100) − 0.02 × (m − 12.5) if m ≤ 15.0; 0.6 above",
        "moisture": {"ref": 12.5, "penalty_per_pp": 0.02, "hard_limit": 15.0, "hard_factor": 0.6},
        "screen_factors": {
            "12": 0.92,
            "13": 0.97,
            "14": 1.0,
            "15": 1.0,
            "16": 1.03,
            "17": 1.03,
            "18": 1.06,
        },
        "scoring": {
            "pr_points": 80.0,
            "appraisal_points": 20.0,
            "appraisal_zero_at_mape": 0.1,
            "leaderboard_freeze_after_round": 12,
        },
        "timeline": {
            "setup_s": 5,
            "appraisal_s": 60,
            "practice_rounds": practice_rounds,
            "rounds": rounds,
            "open_s": 300,
            "clearing_s": 10,
        },
    }


def grade(rules: dict, defect_pct: float) -> tuple[str, float]:
    for step in rules["defect_steps"]:
        if defect_pct <= step["max_defect_pct"]:
            return step["grade"], step["step"]
    last = rules["defect_steps"][-1]
    return last["grade"], last["step"]


def moisture_factor(rules: dict, m: float) -> float:
    mo = rules["moisture"]
    if m <= mo["ref"]:
        return 1.0
    if m <= mo["hard_limit"]:
        return (1 - m / 100) / (1 - mo["ref"] / 100) - mo["penalty_per_pp"] * (m - mo["ref"])
    return mo["hard_factor"]


def screen_factor(rules: dict, screen: int) -> float:
    factors = {int(k): v for k, v in rules["screen_factors"].items()}
    codes = sorted(factors)
    return factors[min(max(int(screen), codes[0]), codes[-1])]


def true_value(rules: dict, sample: dict) -> float:
    d, m = sample["defect_pct"], sample["moisture_pct"]
    g = (1 - d / 100) * grade(rules, d)[1]
    return round(rules["p0"] * g * moisture_factor(rules, m) * screen_factor(rules, sample["screen"]), 2)


def passes_spec(sample: dict, spec: dict) -> bool:
    return (
        sample["defect_pct"] <= spec["max_defect_pct"]
        and sample["moisture_pct"] <= spec["max_moisture_pct"]
        and (spec["min_screen"] is None or sample["screen"] >= spec["min_screen"])
    )


class PVPMarketEngine:
    def __init__(self, logs_dir: Path):
        self.logs_dir = logs_dir
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.csv_logger = PVPCSVLogger(logs_dir)

        self.human_players: dict[str, dict] = {}
        self.token_to_human: dict[str, str] = {}
        self.human_seq = 1

        self.rng = random.Random(int(time.time() * 1000))
        self.teams: dict[str, dict] = _build_56_team_roster(self.rng)
        self.key_to_team: dict[str, str] = {t["key"]: tid for tid, t in self.teams.items()}

        self.game_id = self.csv_logger.total_games_logged
        self.match_started_at = time.time()
        self.include_specialty = True
        self.sample_noise = True
        self.practice_rounds = 2
        self.total_rounds = 15
        self.round_sequence: list[dict] = []
        self.seq_idx = -1
        self.on_phase_change_callbacks = []

        self.reset_to_lobby(rounds=15, practice_rounds=2, include_specialty=True, sample_noise=True)

    def _rebuild_active_teams(self) -> None:
        teams = _build_56_team_roster(self.rng)
        for tid, p in self.human_players.items():
            teams[tid] = p
        self.teams = teams
        self.key_to_team = {t["key"]: tid for tid, t in self.teams.items()}

    def resolve_team(self, token: str) -> str | None:
        with self.lock:
            if token in self.token_to_human:
                return self.token_to_human[token]
            return self.key_to_team.get(token)

    def _build_round_sequence(self) -> list[dict]:
        seq = []
        for p in range(1, self.practice_rounds + 1):
            seq.append({
                "round_id": f"P{p}",
                "kind": "practice",
                "number": p,
                "of": self.practice_rounds,
                "counts_for_score": False,
                "calendar": "รอบซ้อม",
                "demand_weight": 1.0,
                "specialty_share": 0.25,
            })
        cal_entries = stage3_calendar(self.total_rounds)
        for r in range(1, self.total_rounds + 1):
            lbl, w, sp = "ปกติ", 1.0, 0.20
            for e in cal_entries:
                spec = str(e["rounds"])
                if "-" in spec:
                    lo, hi = map(int, spec.split("-", 1))
                    if lo <= r <= hi:
                        lbl, w, sp = e["label"], float(e["demand_weight"]), float(e["specialty_share"] or 0.20)
                elif int(spec) == r:
                    lbl, w, sp = e["label"], float(e["demand_weight"]), float(e["specialty_share"] or 0.20)
            seq.append({
                "round_id": f"R{r}",
                "kind": "real",
                "number": r,
                "of": self.total_rounds,
                "counts_for_score": True,
                "calendar": lbl,
                "demand_weight": w,
                "specialty_share": sp,
            })
        return seq

    def reset_to_lobby(self, rounds: int = 15, practice_rounds: int = 2, include_specialty: bool = True, sample_noise: bool = True) -> dict:
        with self.lock:
            self.total_rounds = rounds
            self.practice_rounds = practice_rounds
            self.include_specialty = include_specialty
            self.sample_noise = sample_noise
            self.rules = default_rules(rounds=rounds, practice_rounds=practice_rounds, round_capacity_factor=1.5)
            self.calendar = stage3_calendar(rounds)
            self.round_sequence = self._build_round_sequence()
            self.seq_idx = -1
            self._rebuild_active_teams()

            self.phase = "WAITING"
            self.round_num = 0
            self.phase_started_at = time.time()
            self.phase_ends_at = None
            self.team_lots = {tid: [] for tid in self.teams}
            self.canonical_lots = []
            self.appraisals = {}
            self.open_offers = {}
            self.round_rfqs = {}
            self.round_results = {}
            self.all_round_offers = {}
            self.round_demand_schedule = {}
            self.ready_status = {tid: False for tid in self.teams}
            self.qwen_status = {"state": "waiting_lobby", "round": "LOBBY", "seconds": None, "last_reason": "Ready to start 56-Team Stage 3 Training"}
            self.user_agent_status = {"state": "waiting_lobby", "round": "LOBBY", "seconds": None, "last_reason": "Ready (Locked Strategy)"}
            self.llm_traces = []
            self.user_agent_traces = []
            self.saved_game_log_path = None
        return self.get_pvp_status()

    def start_match(self, rounds: int | None = None, practice_rounds: int | None = None, include_specialty: bool | None = None, sample_noise: bool | None = None) -> dict:
        with self.lock:
            self.game_id = self.csv_logger.next_game_number()
            self.match_started_at = time.time()
            if rounds is not None:
                self.total_rounds = int(rounds)
            if practice_rounds is not None:
                self.practice_rounds = int(practice_rounds)
            if include_specialty is not None:
                self.include_specialty = bool(include_specialty)
            if sample_noise is not None:
                self.sample_noise = bool(sample_noise)

            self.rules = default_rules(rounds=self.total_rounds, practice_rounds=self.practice_rounds, round_capacity_factor=1.5)
            self.calendar = stage3_calendar(self.total_rounds)
            self.round_sequence = self._build_round_sequence()
            self.rng = random.Random(int(time.time() * 1000) ^ self.game_id)
            self._rebuild_active_teams()

            # 1. Generate 5 canonical Stage 3 lots totaling exactly 45 bags (2,700 kg) per team
            base_lots = self._generate_canonical_lots()
            self.canonical_lots = copy.deepcopy(base_lots)
            self.team_lots = {}
            for tid in self.teams:
                t_lots = []
                for idx, bl in enumerate(base_lots, 1):
                    lot_copy = copy.deepcopy(bl)
                    lot_copy["lot_id"] = f"LOT-{tid}-{idx}"
                    t_lots.append(lot_copy)
                self.team_lots[tid] = t_lots

            # 2. Build 90% total market demand schedule across R1..R15 weighted by calendar demand_weight
            per_team_bags = sum(l["kg_total"] for l in base_lots) // BAG
            total_teams = len(self.teams)
            total_real_stock_bags = per_team_bags * total_teams
            total_real_demand_bags = int(round(total_real_stock_bags * 0.90))
            self.round_demand_schedule = self._build_demand_schedule(total_real_demand_bags, per_team_bags * total_teams)

            self.appraisals = {}
            self.open_offers = {tid: [] for tid in self.teams}
            self.round_rfqs = {}
            self.round_results = {}
            self.all_round_offers = {}
            self.offer_seq = 1
            self.llm_traces = []
            self.user_agent_traces = []
            self.saved_game_log_path = None

            self.phase = "APPRAISAL"
            self.seq_idx = -1
            self.round_num = 0
            self.ready_status = {tid: False for tid in self.teams}

        # Synchronous appraisal for all 56 teams
        self._notify_phase_change()

        with self.lock:
            self.seq_idx = 0
            cur_r = self.round_sequence[self.seq_idx]
            self.round_num = 1
            self.phase = "ROUND_OPEN"
            rid = cur_r["round_id"]
            self.round_rfqs[rid] = self._generate_round_rfqs(cur_r)
            self.open_offers = {tid: [] for tid in self.teams}
            self.ready_status = {tid: False for tid in self.teams}
            self.phase_started_at = time.time()
            self.phase_ends_at = self.phase_started_at + self.rules["timeline"]["open_s"]

        self._notify_phase_change()
        return self.get_pvp_status()

    def _build_demand_schedule(self, total_real_demand_bags: int, total_stock_bags: int) -> dict[str, int]:
        sched = {}
        avg_real_bags = total_real_demand_bags / max(1, self.total_rounds)
        for rinfo in self.round_sequence:
            if rinfo["kind"] == "practice":
                sched[rinfo["round_id"]] = int(round(avg_real_bags))
        real_rounds = [r for r in self.round_sequence if r["kind"] == "real"]
        w_sum = sum(r["demand_weight"] for r in real_rounds) or 1.0
        allocs = [max(15, int(round(total_real_demand_bags * (r["demand_weight"] / w_sum)))) for r in real_rounds]
        diff = total_real_demand_bags - sum(allocs)
        idx = 0
        while diff != 0:
            step = 1 if diff > 0 else -1
            if allocs[idx] + step >= 12:
                allocs[idx] += step
                diff -= step
            idx = (idx + 1) % len(allocs)
        for rinfo, bags in zip(real_rounds, allocs):
            sched[rinfo["round_id"]] = bags
        return sched

    def _generate_canonical_lots(self) -> list[dict]:
        """Generates 5 Stage-3 Robusta lots totaling exactly 45 bags (2,700 kg) per team:
        - Lot 1 (9b): Solid SPECIALTY (screen 16 or 17, defect <= 2.3%, moisture <= 12.0%)
        - Lot 2 (9b): SPECIALTY / High COMMERCIAL (screen 16 or 17, defect 1.8-2.9%, moisture 11.5-12.4%)
        - Lot 3 (10b): Solid COMMERCIAL (screen 13-15, defect 3.5-5.8%, moisture 10.8-12.1%)
        - Lot 4 (9b): Borderline COMMERCIAL / High INSTANT (screen 14-18, defect 3.6-6.7%, moisture 12.2-13.2%)
        - Lot 5 (8b): Solid INSTANT (screen 12-15, defect 8.5-13.8%, moisture 11.2-13.8%)
        Total = 9 + 9 + 10 + 9 + 8 = 45 bags!
        """
        archetypes = [
            {"d_range": (0.6, 2.2), "m_range": (10.6, 11.9), "screens": [16, 17, 18], "bags": 9},
            {"d_range": (1.8, 2.85), "m_range": (11.4, 12.35), "screens": [16, 17], "bags": 9},
            {"d_range": (3.5, 5.8), "m_range": (10.8, 12.1), "screens": [13, 14, 15], "bags": 10},
            {"d_range": (3.6, 6.6), "m_range": (12.15, 13.2), "screens": [14, 15, 16, 18], "bags": 9},
            {"d_range": (8.5, 13.6), "m_range": (11.2, 13.7), "screens": [12, 13, 14, 15], "bags": 8},
        ]
        instant_spec = {"max_defect_pct": 15.0, "max_moisture_pct": 15.0, "min_screen": None}
        lots = []
        for arch in archetypes:
            origin, process = self.rng.choice(ORIGINS)
            true_d = round(min(14.5, self.rng.uniform(*arch["d_range"])), 2)
            true_m = round(min(13.8, self.rng.uniform(*arch["m_range"])), 2)
            true_s = self.rng.choice(arch["screens"])
            bags = arch["bags"]
            kg = bags * BAG

            true_q = {"defect_pct": true_d, "moisture_pct": true_m, "screen": true_s}
            if self.sample_noise:
                samp_d = round(max(0.2, min(14.5, true_d + self.rng.gauss(0, 0.35))), 1)
                samp_m = round(max(9.5, min(13.8, true_m + self.rng.gauss(0, 0.25))), 1)
                samp_q = {"defect_pct": samp_d, "moisture_pct": samp_m, "screen": true_s}
            else:
                samp_q = dict(true_q)

            assert passes_spec(true_q, instant_spec) and passes_spec(samp_q, instant_spec)

            tv_true = true_value(self.rules, true_q)
            gr_true = grade(self.rules, true_d)[0]
            lots.append({
                "lot_id": "LOT-BASE",
                "kg_total": kg,
                "kg_remaining": kg,
                "origin": origin,
                "process": process,
                "true": true_q,
                "sample": samp_q,
                "true_grade": gr_true,
                "true_value_per_kg": tv_true,
            })
        return lots

    def _generate_round_rfqs(self, rinfo: dict) -> list[dict]:
        rid = rinfo["round_id"]
        round_bags = self.round_demand_schedule.get(rid, 150)
        sp_share = float(rinfo.get("specialty_share") or 0.20) if self.include_specialty else 0.0
        comm_share = (1.0 - sp_share) * 0.52
        inst_share = 1.0 - sp_share - comm_share

        sp_bags = max(4, int(round(round_bags * sp_share))) if self.include_specialty else 0
        comm_bags = max(8, int(round(round_bags * comm_share)))
        inst_bags = max(8, round_bags - sp_bags - comm_bags)

        rfqs = []
        seq_no = 1

        def add_tier_rfqs(btype: str, total_b: int, n_rfq: int):
            nonlocal seq_no
            if total_b <= 0 or n_rfq <= 0:
                return
            personas = [p for p in BUYER_PERSONAS if p["buyer_type"] == btype]
            weights = [self.rng.uniform(0.75, 1.30) for _ in range(n_rfq)]
            w_sum = sum(weights)
            allocs = [max(2, int(round(total_b * (w / w_sum)))) for w in weights]
            diff = total_b - sum(allocs)
            i = 0
            while diff != 0:
                step = 1 if diff > 0 else -1
                if allocs[i] + step >= 2:
                    allocs[i] += step
                    diff -= step
                i = (i + 1) % n_rfq
            for k, b in enumerate(allocs):
                p = personas[k % len(personas)]
                ceiling = round(p["base_ceiling"] + self.rng.uniform(-p["noise"], p["noise"]), 4)
                rfqs.append({
                    "rfq_id": f"{rid}-{seq_no:02d}",
                    "buyer": p["buyer"],
                    "buyer_id": p["buyer_id"],
                    "buyer_type": p["buyer_type"],
                    "kg": b * BAG,
                    "bags": b,
                    "spec": copy.deepcopy(p["spec"]),
                    "hidden_ceiling": ceiling,
                })
                seq_no += 1

        # In late low-demand rounds (R13-R15), fewer RFQs appear (matching Slide 5 of สไลด์วันที่-20.pdf)
        is_low_demand = rinfo["demand_weight"] < 0.75
        add_tier_rfqs("SPECIALTY", sp_bags, 2 if is_low_demand else (4 if sp_share >= 0.35 else 3))
        add_tier_rfqs("COMMERCIAL", comm_bags, 2 if is_low_demand else 4)
        add_tier_rfqs("INSTANT", inst_bags, 2 if is_low_demand else 3)
        self.rng.shuffle(rfqs)
        for i, q in enumerate(rfqs, 1):
            q["rfq_id"] = f"{rid}-{i:02d}"
        return rfqs

    def _notify_phase_change(self) -> None:
        cur_r = self.round_sequence[self.seq_idx] if 0 <= self.seq_idx < len(self.round_sequence) else None
        rnum = (self.seq_idx + 1) if self.seq_idx >= 0 else 0
        for cb in list(self.on_phase_change_callbacks):
            try:
                cb(self.game_id, self.phase, rnum, cur_r)
            except TypeError:
                try:
                    cb(self.game_id, self.phase, rnum)
                except Exception as e:
                    print(f"[PVPMarketEngine] callback error: {e}", flush=True)
            except Exception as e:
                print(f"[PVPMarketEngine] callback error: {e}", flush=True)

    def mark_ready(self, tid: str, ready: bool = True) -> dict:
        with self.lock:
            if tid in self.ready_status:
                self.ready_status[tid] = ready
            all_ready = bool(self.ready_status) and all(self.ready_status.values())
            if all_ready and self.phase == "ROUND_OPEN":
                self.advance_phase()
            return self.get_pvp_status()

    def advance_phase(self) -> dict:
        with self.lock:
            if self.phase == "WAITING":
                return self.start_match()
            if self.phase == "ROUND_OPEN":
                prev_r = self.round_sequence[self.seq_idx]
                self._clear_current_round(prev_r)
                if self.seq_idx + 1 >= len(self.round_sequence):
                    self.phase = "CLOSED"
                    self.phase_started_at = time.time()
                    self.phase_ends_at = None
                    self._save_end_of_game_logs()
                else:
                    self.seq_idx += 1
                    next_r = self.round_sequence[self.seq_idx]
                    # If transitioning from last practice round (P2) to first real round (R1),
                    # restore all teams' lot remaining kg to full (practice rounds do not deduct real stock!)
                    if prev_r["kind"] == "practice" and next_r["kind"] == "real":
                        for tid, lots in self.team_lots.items():
                            for l in lots:
                                l["kg_remaining"] = l["kg_total"]
                    self.round_num = self.seq_idx + 1
                    self.phase = "ROUND_OPEN"
                    rid = next_r["round_id"]
                    self.round_rfqs[rid] = self._generate_round_rfqs(next_r)
                    self.open_offers = {tid: [] for tid in self.teams}
                    self.ready_status = {tid: False for tid in self.teams}
                    self.phase_started_at = time.time()
                    self.phase_ends_at = self.phase_started_at + self.rules["timeline"]["open_s"]

        self._notify_phase_change()
        return self.get_pvp_status()

    def _clear_current_round(self, rinfo: dict) -> None:
        rid = rinfo["round_id"]
        rfqs = self.round_rfqs.get(rid, [])
        lot_lookup = {}
        for tid, lots in self.team_lots.items():
            for l in lots:
                lot_lookup[l["lot_id"]] = (tid, l)

        all_offers = []
        for tid, olist in self.open_offers.items():
            for o in olist:
                if o["status"] == "OPEN":
                    all_offers.append((tid, o))

        self.all_round_offers[rid] = [copy.deepcopy(o) for _, o in all_offers]
        market_summary = []

        for rfq in rfqs:
            rfq_id = rfq["rfq_id"]
            matching = [(tid, o) for tid, o in all_offers if o["rfq_id"] == rfq_id]
            valid = []
            for tid, o in matching:
                _, lot = lot_lookup[o["lot_id"]]
                if not passes_spec(lot["true"], rfq["spec"]):
                    o["status"] = "SPEC_MISMATCH"
                    o["filled_kg"] = 0
                else:
                    ratio = o["price_per_kg"] / lot["true_value_per_kg"]
                    if ratio > rfq["hidden_ceiling"]:
                        o["status"] = "ABOVE_LIMIT"
                        o["filled_kg"] = 0
                    else:
                        valid.append((ratio, self.rng.random(), tid, o, lot))

            valid.sort(key=lambda x: (x[0], x[1]))
            rem_wanted = rfq["kg"]
            bought_kg = 0
            paid_sum = 0.0
            p_min, p_max = None, None

            for ratio, _, tid, o, lot in valid:
                if rem_wanted <= 0:
                    o["status"] = "OUTBID"
                    o["filled_kg"] = 0
                    continue
                avail = min(o["kg"], lot["kg_remaining"], rem_wanted) // BAG * BAG
                if avail <= 0:
                    o["status"] = "OUTBID"
                    o["filled_kg"] = 0
                    continue
                o["filled_kg"] = avail
                lot["kg_remaining"] -= avail
                rem_wanted -= avail
                bought_kg += avail
                paid_sum += avail * o["price_per_kg"]
                p_min = o["price_per_kg"] if p_min is None else min(p_min, o["price_per_kg"])
                p_max = o["price_per_kg"] if p_max is None else max(p_max, o["price_per_kg"])
                o["status"] = "ACCEPTED" if avail == o["kg"] else "PARTIAL"

            market_summary.append({
                "rfq_id": rfq_id,
                "buyer": rfq["buyer"],
                "buyer_id": rfq["buyer_id"],
                "buyer_type": rfq["buyer_type"],
                "wanted_kg": rfq["kg"],
                "bought_kg": bought_kg,
                "offers": len(matching),
                "price_min": round(p_min, 2) if p_min is not None else None,
                "price_max": round(p_max, 2) if p_max is not None else None,
                "price_avg": round(paid_sum / bought_kg, 2) if bought_kg > 0 else None,
                "hidden_ceiling": rfq["hidden_ceiling"],
            })

        self.round_results[rid] = {}
        for tid in self.teams:
            my_res = [
                {
                    "offer_id": o["offer_id"],
                    "round_id": rid,
                    "rfq_id": o["rfq_id"],
                    "lot_id": o["lot_id"],
                    "kg": o["kg"],
                    "price_per_kg": o["price_per_kg"],
                    "status": o["status"],
                    "filled_kg": o["filled_kg"],
                }
                for o in self.open_offers.get(tid, [])
            ]
            self.round_results[rid][tid] = {
                "round_id": rid,
                "kind": rinfo["kind"],
                "my_offers": my_res,
                "market_summary": [{k: v for k, v in m.items() if k != "hidden_ceiling"} for m in market_summary],
                "market_summary_with_ceilings": market_summary,
            }

    def get_standings(self, tid: str) -> dict:
        lots = self.team_lots.get(tid, [])
        kg_total = sum(l["kg_total"] for l in lots)
        kg_remaining = sum(l["kg_remaining"] for l in lots)
        rev = 0.0
        real_sold_kg = 0
        for rid, by_team in self.round_results.items():
            robj = by_team.get(tid, {})
            if robj.get("kind") != "real":
                continue
            for o in robj.get("my_offers", []):
                rev += o["filled_kg"] * o["price_per_kg"]
                real_sold_kg += o["filled_kg"]
        return {
            "revenue_thb": round(rev, 2),
            "kg_sold": real_sold_kg,
            "kg_remaining": kg_total - real_sold_kg,
            "kg_total": kg_total,
            "avg_price_per_kg": round(rev / real_sold_kg, 2) if real_sold_kg > 0 else None,
        }

    def compute_scores(self) -> dict[str, dict]:
        if self.phase == "WAITING" or not any(self.team_lots.values()):
            return {}
        raw = {}
        for tid, tinfo in self.teams.items():
            lots = self.team_lots.get(tid, [])
            if not lots:
                continue
            st = self.get_standings(tid)
            total_tv_baht = sum(l["kg_total"] * l["true_value_per_kg"] for l in lots)
            salvage_baht = sum(l["kg_remaining"] * l["true_value_per_kg"] * self.rules["salvage_ratio"] for l in lots)
            pr = (st["revenue_thb"] + salvage_baht) / max(1.0, total_tv_baht)

            ap = self.appraisals.get(tid)
            if ap and ap.get("items"):
                errs, total_w = [], 0
                for l in lots:
                    it = ap["items"].get(l["lot_id"])
                    if it and it.get("est_value_per_kg"):
                        err = abs(it["est_value_per_kg"] - l["true_value_per_kg"]) / l["true_value_per_kg"]
                        errs.append(err * l["kg_total"])
                        total_w += l["kg_total"]
                mape = (sum(errs) / total_w) if total_w > 0 else None
            else:
                mape = None

            ap_score = 0.0 if (mape is None or mape >= 0.10) else 20.0 * (1.0 - mape / 0.10)
            raw[tid] = {
                "team_id": tid,
                "name": f"{tinfo['team_name']} · {tinfo['university']}",
                "team_name": tinfo["team_name"],
                "category": tinfo.get("category", "bot"),
                "strategy": tinfo.get("strategy", "bot"),
                "role": tinfo["role"],
                "pr": round(pr, 4),
                "mape": round(mape, 4) if mape is not None else None,
                "appraisal_score": round(ap_score, 4),
                "revenue_thb": st["revenue_thb"],
                "salvage_thb": round(salvage_baht, 2),
                "kg_sold": st["kg_sold"],
                "kg_remaining": st["kg_remaining"],
                "kg_total": st["kg_total"],
            }

        if not raw:
            return {}
        best_pr = max(max(x["pr"] for x in raw.values()), 0.8001)
        for tid, sc in raw.items():
            pr_score = max(0.0, 80.0 * (sc["pr"] - 0.80) / (best_pr - 0.80)) if sc["pr"] > 0.80 else 0.0
            sc["pr_score"] = round(pr_score, 4)
            sc["total"] = round(pr_score + sc["appraisal_score"], 4)

        ranked = sorted(raw.values(), key=lambda x: (-x["total"], -x["pr"]))
        for idx, item in enumerate(ranked, 1):
            raw[item["team_id"]]["rank"] = idx
        return raw

    def get_state_for_team(self, tid: str) -> dict:
        with self.lock:
            now = time.time()
            sec_left = max(0.0, round(self.phase_ends_at - now, 1)) if self.phase_ends_at else None
            cur_r = self.round_sequence[self.seq_idx] if 0 <= self.seq_idx < len(self.round_sequence) else None
            rid = cur_r["round_id"] if cur_r else None
            my_open = [o for o in self.open_offers.get(tid, []) if o["status"] == "OPEN"]
            open_by_lot = {}
            for o in my_open:
                open_by_lot[o["lot_id"]] = open_by_lot.get(o["lot_id"], 0) + o["kg"]

            lots_out = []
            for l in self.team_lots.get(tid, []):
                held = open_by_lot.get(l["lot_id"], 0)
                lots_out.append({
                    "lot_id": l["lot_id"],
                    "kg_total": l["kg_total"],
                    "kg_remaining": l["kg_remaining"],
                    "kg_in_open_offers": held,
                    "kg_available": max(0, l["kg_remaining"] - held),
                    "origin": l["origin"],
                    "process": l["process"],
                    "sample": copy.deepcopy(l["sample"]),
                })

            rfqs_out = []
            if self.phase == "ROUND_OPEN" and rid in self.round_rfqs:
                for q in self.round_rfqs[rid]:
                    rfqs_out.append({k: v for k, v in q.items() if k != "hidden_ceiling"})

            last_res = {"round_id": None, "my_offers": [], "market_summary": []}
            if self.seq_idx > 0:
                prev_rid = self.round_sequence[self.seq_idx - 1]["round_id"]
                if prev_rid in self.round_results and tid in self.round_results[prev_rid]:
                    r_obj = self.round_results[prev_rid][tid]
                    last_res = {
                        "round_id": r_obj["round_id"],
                        "kind": r_obj["kind"],
                        "my_offers": r_obj["my_offers"],
                        "market_summary": r_obj["market_summary"],
                    }
            elif self.phase == "CLOSED" and self.round_sequence:
                last_rid = self.round_sequence[-1]["round_id"]
                if last_rid in self.round_results and tid in self.round_results[last_rid]:
                    r_obj = self.round_results[last_rid][tid]
                    last_res = {
                        "round_id": r_obj["round_id"],
                        "kind": r_obj["kind"],
                        "my_offers": r_obj["my_offers"],
                        "market_summary": r_obj["market_summary"],
                    }

            total_bags = sum(l["kg_total"] for l in lots_out) // BAG
            cap_factor = self.rules.get("round_capacity_factor")
            if cap_factor and total_bags > 0:
                round_cap_kg = math.ceil(cap_factor * total_bags / max(1, self.total_rounds)) * BAG
                cap_left_kg = max(0, round_cap_kg - sum(o["kg"] for o in my_open))
            else:
                round_cap_kg = None
                cap_left_kg = None

            return {
                "server_time": now,
                "market": "pvp_stage3",
                "team_id": tid,
                "phase": self.phase,
                "paused": False,
                "phase_ends_at": self.phase_ends_at,
                "seconds_left": sec_left,
                "round": (
                    {
                        "round_id": cur_r["round_id"],
                        "kind": cur_r["kind"],
                        "number": cur_r["number"],
                        "of": cur_r["of"],
                        "counts_for_score": cur_r["counts_for_score"],
                        "calendar": cur_r["calendar"],
                    }
                    if cur_r
                    else None
                ),
                "lots": lots_out,
                "rfqs": rfqs_out,
                "my_open_offers": my_open,
                "round_capacity_kg": round_cap_kg,
                "capacity_left_kg": cap_left_kg,
                "appraisal_submitted": tid in self.appraisals,
                "last_results": last_res,
                "standings": self.get_standings(tid),
                "salvage_ratio": self.rules["salvage_ratio"],
            }

    def submit_appraisal(self, tid: str, items: list[dict]) -> dict:
        with self.lock:
            if self.phase != "APPRAISAL":
                raise ValueError("WRONG_PHASE: Appraisal only allowed during APPRAISAL phase")
            if tid in self.appraisals:
                raise ValueError("APPRAISAL_ALREADY_SUBMITTED: Appraisal already submitted")
            valid_lots = {l["lot_id"] for l in self.team_lots.get(tid, [])}
            saved = {}
            for it in items:
                lid = it.get("lot_id")
                val = float(it.get("est_value_per_kg", 0))
                if lid not in valid_lots or val <= 0:
                    raise ValueError(f"INVALID_FIELD: Invalid lot_id or est_value_per_kg ({it})")
                saved[lid] = {"grade": it.get("grade"), "est_value_per_kg": round(val, 2)}
            rec = {"submitted_at": time.time(), "items": saved}
            self.appraisals[tid] = rec
            return rec

    def submit_offer(self, tid: str, body: dict) -> dict:
        with self.lock:
            if self.phase != "ROUND_OPEN":
                raise ValueError("WRONG_PHASE: Offers only allowed during ROUND_OPEN")
            cur_r = self.round_sequence[self.seq_idx]
            rid = cur_r["round_id"]
            rfqs = {q["rfq_id"]: q for q in self.round_rfqs.get(rid, [])}
            lots = {l["lot_id"]: l for l in self.team_lots[tid]}

            rfq_id = body.get("rfq_id")
            lot_id = body.get("lot_id")
            kg = int(body.get("kg", 0))
            price = float(body.get("price_per_kg", 0))

            if rfq_id not in rfqs:
                raise ValueError(f"UNKNOWN_RFQ: {rfq_id}")
            if lot_id not in lots:
                raise ValueError(f"NOT_YOUR_LOT: {lot_id}")
            if kg <= 0 or kg % BAG != 0:
                raise ValueError("NOT_MULTIPLE_OF_BAG: kg must be a positive multiple of 60")
            if kg > rfqs[rfq_id]["kg"] or not (0 < price <= 1000):
                raise ValueError("INVALID_FIELD: kg exceeds RFQ or price out of range")

            held = sum(o["kg"] for o in self.open_offers.get(tid, []) if o["status"] == "OPEN" and o["lot_id"] == lot_id)
            if kg > lots[lot_id]["kg_remaining"] - held:
                raise ValueError("EXCEEDS_REMAINING: Not enough available kg in lot")

            cap_factor = self.rules.get("round_capacity_factor")
            if cap_factor:
                total_bags = sum(l["kg_total"] for l in self.team_lots[tid]) // BAG
                round_cap_kg = math.ceil(cap_factor * total_bags / max(1, self.total_rounds)) * BAG
                used_cap_kg = sum(o["kg"] for o in self.open_offers.get(tid, []) if o["status"] == "OPEN")
                if used_cap_kg + kg > round_cap_kg:
                    raise ValueError(f"OVER_ROUND_CAPACITY: Exceeds round capacity ({round_cap_kg // BAG} bags max)")

            oid = f"O{self.offer_seq:05d}"
            self.offer_seq += 1
            offer = {
                "offer_id": oid,
                "round_id": rid,
                "rfq_id": rfq_id,
                "lot_id": lot_id,
                "kg": kg,
                "price_per_kg": round(price, 2),
                "status": "OPEN",
                "filled_kg": 0,
            }
            self.open_offers.setdefault(tid, []).append(offer)
            return offer

    def submit_offer_batch(self, tid: str, offers: list[dict], replace_existing: bool = True) -> dict:
        with self.lock:
            if self.phase != "ROUND_OPEN":
                raise ValueError("WRONG_PHASE: Offers only allowed during ROUND_OPEN")
            backup = copy.deepcopy(self.open_offers.get(tid, []))
            if replace_existing:
                self.open_offers[tid] = []
            created = []
            try:
                for o in offers:
                    created.append(self.submit_offer(tid, o))
            except Exception:
                self.open_offers[tid] = backup
                raise
            return {"offers": created}

    def get_reveal(self, tid: str) -> dict:
        with self.lock:
            scores = self.compute_scores()
            ap = self.appraisals.get(tid, {}).get("items", {})
            lots_out = []
            for l in self.team_lots.get(tid, []):
                lots_out.append({
                    "lot_id": l["lot_id"],
                    "true": l["true"],
                    "sample": l["sample"],
                    "true_grade": l["true_grade"],
                    "true_value_per_kg": l["true_value_per_kg"],
                    "your_estimate": ap.get(l["lot_id"]),
                })
            return {"lots": lots_out, "score": scores.get(tid, {})}

    def get_pvp_status(self) -> dict:
        with self.lock:
            scores = self.compute_scores()
            ranked_board = sorted(scores.values(), key=lambda x: x["rank"]) if scores else []
            cur_r = self.round_sequence[self.seq_idx] if 0 <= self.seq_idx < len(self.round_sequence) else None

            # Group summary statistics by bot category for the Training Dashboard
            cat_stats = {}
            for sc in ranked_board:
                cat = sc.get("category", "bot")
                g = cat_stats.setdefault(cat, {"category": cat, "count": 0, "pr_sum": 0.0, "best_pr": 0.0, "best_rank": 999, "sold_bags": 0, "total_bags": 0})
                g["count"] += 1
                g["pr_sum"] += sc["pr"]
                g["best_pr"] = max(g["best_pr"], sc["pr"])
                g["best_rank"] = min(g["best_rank"], sc["rank"])
                g["sold_bags"] += sc["kg_sold"] // BAG
                g["total_bags"] += sc["kg_total"] // BAG
            for g in cat_stats.values():
                g["avg_pr"] = round(g["pr_sum"] / max(1, g["count"]), 4)
                del g["pr_sum"]

            return {
                "game_id": self.game_id,
                "game_number": self.game_id,
                "current_generation": self.csv_logger.current_generation,
                "total_games_logged": self.csv_logger.total_games_logged,
                "session_id": self.csv_logger.session_id,
                "phase": self.phase,
                "round_num": self.round_num,
                "current_round": cur_r,
                "total_rounds": self.total_rounds,
                "practice_rounds": self.practice_rounds,
                "include_specialty": self.include_specialty,
                "sample_noise": self.sample_noise,
                "total_teams": len(self.teams),
                "qwen_status": self.qwen_status,
                "user_agent_status": self.user_agent_status,
                "category_summary": cat_stats,
                "leaderboard": ranked_board,
                "t01_traces": self.llm_traces[-5:],
                "t02_traces": self.user_agent_traces[-5:],
                "saved_game_log_path": str(self.saved_game_log_path) if self.saved_game_log_path else None,
            }

    def _save_end_of_game_logs(self) -> None:
        self.csv_logger.record_completed_game(self)
