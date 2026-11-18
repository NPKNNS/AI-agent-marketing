"""54 Diverse Competitor Bots (B01-B54) + 2 Autonomous AI Agents (T01 Refined & T02 Locked User Agent) for Stage 3 Training."""
import json
import math
import os
import random
import sys
import threading
import time
from pathlib import Path

from engine import BAG, PVPMarketEngine, grade, passes_spec, true_value

PVP_DIR = Path(__file__).parent
SELF_BOT_DIR = PVP_DIR.parent / "self config agent bot"
if str(SELF_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(SELF_BOT_DIR))
if str(PVP_DIR) not in sys.path:
    sys.path.insert(0, str(PVP_DIR))

import appraisal as qwen_appraisal
import llm as qwen_llm_mod
import memory as refined_memory_mod
import strategy as refined_strategy
import my_agent_memory as user_locked_memory_mod
import my_agent_strategy as user_locked_strategy


class SmartBotController:
    """Manages state and round decisions for all 54 diverse competitor bots (B01-B54)."""

    def __init__(self, engine: PVPMarketEngine):
        self.engine = engine
        self.bot_memory = {}  # tid -> {buyer -> stats}

    def reset(self):
        self.bot_memory = {}

    def _get_buyer_mem(self, tid: str, buyer: str, btype: str) -> dict:
        priors = {"SPECIALTY": 1.09, "COMMERCIAL": 1.03, "INSTANT": 0.935}
        tmem = self.bot_memory.setdefault(tid, {})
        if buyer not in tmem:
            tmem[buyer] = {
                "est": priors.get(btype, 1.00),
                "lb": None,
                "ub": None,
                "mkt_mk": priors.get(btype, 1.00),
                "crowding": 1.0,
                "fill_pct": 100.0,
                "outbids": 0,
                "int_err": 0.0,
            }
        return tmem[buyer]

    def _update_from_last_results(self, tid: str, state: dict, tv: dict[str, float]) -> None:
        res = state.get("last_results") or {}
        if not res.get("round_id"):
            return
        avg_tv = sum(tv.values()) / max(1, len(tv))
        rfq_buyer = {}
        for r in res.get("market_summary", []):
            rfq_buyer[r["rfq_id"]] = (r["buyer"], r["buyer_type"])
            bm = self._get_buyer_mem(tid, r["buyer"], r["buyer_type"])
            w_bags = max(1, r["wanted_kg"] // BAG)
            bm["crowding"] = r["offers"] / w_bags
            bm["fill_pct"] = r["bought_kg"] * 100.0 / max(1, r["wanted_kg"])
            if r.get("price_avg") and avg_tv > 0:
                bm["mkt_mk"] = round(min(1.20, max(0.85, r["price_avg"] / avg_tv)), 3)

        for o in res.get("my_offers", []):
            if o["rfq_id"] not in rfq_buyer or o["lot_id"] not in tv:
                continue
            buyer, btype = rfq_buyer[o["rfq_id"]]
            bm = self._get_buyer_mem(tid, buyer, btype)
            mk = o["price_per_kg"] / tv[o["lot_id"]]
            st = o["status"]
            if st in ("ACCEPTED", "PARTIAL"):
                bm["outbids"] = 0
                bm["int_err"] = max(-0.08, bm["int_err"] - 0.008)
                bm["lb"] = mk if bm["lb"] is None else max(bm["lb"], mk)
                bm["est"] = round((bm["lb"] + bm["ub"]) / 2, 3) if bm["ub"] else round(mk + 0.010, 3)
            elif st == "OUTBID":
                bm["outbids"] += 1
                bm["int_err"] = min(0.12, bm["int_err"] + 0.015)
                bm["est"] = round(max(0.85, min(bm["est"], mk) - 0.020), 3)
            elif st == "ABOVE_LIMIT":
                bm["ub"] = mk if bm["ub"] is None else min(bm["ub"], mk)
                bm["int_err"] = min(0.12, bm["int_err"] + 0.020)
                bm["est"] = round(max(0.85, bm["ub"] - 0.025), 3)

    def act_for_all_bots(self, game_id: int, phase: str, round_num: int, rinfo: dict | None = None) -> None:
        for tid, tinfo in self.engine.teams.items():
            if tinfo["role"] != "bot":
                continue
            state = self.engine.get_state_for_team(tid)
            tv = {l["lot_id"]: true_value(self.engine.rules, l["sample"]) for l in state["lots"]}

            if phase == "APPRAISAL":
                if not state["appraisal_submitted"]:
                    items = [
                        {
                            "lot_id": l["lot_id"],
                            "grade": grade(self.engine.rules, l["sample"]["defect_pct"])[0],
                            "est_value_per_kg": tv[l["lot_id"]],
                        }
                        for l in state["lots"]
                    ]
                    try:
                        self.engine.submit_appraisal(tid, items)
                    except Exception:
                        pass
                self.engine.mark_ready(tid, True)

            elif phase == "ROUND_OPEN":
                self._update_from_last_results(tid, state, tv)
                offers = self._decide_bot_offers(tid, tinfo["strategy"], state, tv)
                if offers:
                    try:
                        self.engine.submit_offer_batch(tid, offers, replace_existing=True)
                    except Exception:
                        pass
                self.engine.mark_ready(tid, True)

    def _decide_bot_offers(self, tid: str, strat: str, state: dict, tv: dict[str, float]) -> list[dict]:
        rnd = state["round"]
        cur = rnd["number"]
        total = max(1, rnd["of"])
        is_prac = rnd["kind"] == "practice"
        left = total if is_prac else max(1, total - cur + 1)
        cal_lbl = str(rnd.get("calendar", "ปกติ"))
        is_festival = "เทศกาล" in cal_lbl
        rng = random.Random(hash((tid, self.engine.game_id, rnd["round_id"])))
        bot_idx = int("".join(ch for ch in tid if ch.isdigit()) or "1")

        lot_left = {l["lot_id"]: l["kg_available"] // BAG for l in state["lots"]}
        rfq_left = {r["rfq_id"]: r["bags"] for r in state["rfqs"]}
        cap_left = (state["capacity_left_kg"] // BAG) if state.get("capacity_left_kg") is not None else 999

        cands = []
        for l in state["lots"]:
            if lot_left[l["lot_id"]] <= 0:
                continue
            for r in state["rfqs"]:
                if passes_spec(l["sample"], r["spec"]):
                    bm = self._get_buyer_mem(tid, r["buyer"], r["buyer_type"])
                    cands.append((l, r, bm))

        if not cands:
            return []

        if strat == "random_bot":
            rng.shuffle(cands)
        elif strat in ("stay_low", "market_price"):
            # Weighted by RFQ size like Coffee-TESA-starter
            cands.sort(key=lambda x: -(rng.random() ** (1.0 / max(1, x[1]["bags"]))))
        elif strat == "adaptive_ev_max":
            cands.sort(
                key=lambda x: -(
                    min(x[2]["est"], x[2]["mkt_mk"]) * (1.0 / max(0.8, x[2]["crowding"]))
                    + 0.01 * rng.random()
                )
            )
        else:
            cands.sort(key=lambda x: (-x[2]["est"], -tv[x[0]["lot_id"]], rng.random()))

        offers = []
        seen = set()

        for l, r, bm in cands:
            if cap_left <= 0:
                break
            lid, rid = l["lot_id"], r["rfq_id"]
            if (lid, rid) in seen or lot_left[lid] <= 0 or rfq_left[rid] <= 0:
                continue

            btype = r["buyer_type"]
            est = bm["est"]
            mkt_mk = bm["mkt_mk"]

            # 1. Random Bot (1-5%)
            if strat == "random_bot":
                mk = rng.uniform(0.86, 1.16)
                share = rng.randint(1, max(1, min(3, lot_left[lid])))

            # 2. Stay Low Price Bot (10-15%)
            elif strat == "stay_low":
                low_base = {"SPECIALTY": 0.94, "COMMERCIAL": 0.91, "INSTANT": 0.87}[btype]
                offset = ((bot_idx % 5) - 2) * 0.012
                mk = max(0.85, low_base + offset)
                share = max(1, math.ceil(lot_left[lid] / left * 1.4))

            # 3. Stay High Price Bot (10-15%)
            elif strat == "stay_high":
                high_base = {"SPECIALTY": 1.14, "COMMERCIAL": 1.06, "INSTANT": 0.96}[btype]
                offset = (bot_idx % 4) * 0.01
                mk = (high_base + offset) if left > 2 else max(0.88, est - 0.02)
                share = max(1, math.ceil(lot_left[lid] / left * 1.25))

            # 4. Market Price Bot (10-15%: official starter 1.00x + market average trackers)
            elif strat == "market_price":
                if bot_idx % 2 == 0:
                    mk = 1.00 if btype != "INSTANT" else 0.93
                else:
                    mk = mkt_mk
                share = lot_left[lid] if left <= 1 else max(1, math.ceil(lot_left[lid] / left * 1.3))

            # 5A. Adaptive Bayes Bandit
            elif strat == "adaptive_bayes":
                mk = min(est, mkt_mk + 0.01) - (0.020 if left <= 2 else 0.005) + rng.gauss(0, 0.006)
                share = lot_left[lid] if left <= 1 else max(1, math.ceil(lot_left[lid] / left * 1.35))

            # 5B. Adaptive Festival Specialty Hoarder (holds Specialty in R1-R5 for R6-R8 Coffee Festival!)
            elif strat == "adaptive_festival":
                is_spec_lot = l["sample"]["defect_pct"] <= 3.0 and l["sample"]["moisture_pct"] <= 12.5 and l["sample"]["screen"] >= 16
                if is_spec_lot and not is_prac and cur <= 5 and btype != "SPECIALTY":
                    continue  # Hold Specialty lot for R6-R8 Coffee Festival
                if is_spec_lot and is_festival:
                    mk = min(est, mkt_mk + 0.015)
                    share = min(3, lot_left[lid])
                else:
                    mk = min(est, mkt_mk + 0.005) - (0.025 if left <= 2 else 0.008)
                    share = max(1, math.ceil(lot_left[lid] / left * 1.4))

            # 5C. Adaptive Market Clearing Sniper
            elif strat == "adaptive_sniper":
                undercut = 0.012 if bm["crowding"] >= 1.5 else 0.003
                mk = min(est, mkt_mk) - undercut
                share = max(1, math.ceil(lot_left[lid] / left * 1.45))

            # 5D. Adaptive DP Pre-Drop Pacer (clears >90% before R13 late-market demand contraction!)
            elif strat == "adaptive_dp_pacer":
                eff_left = max(1, (12 - cur + 1) if (not is_prac and cur <= 12) else left)
                mk = min(est, mkt_mk + 0.005) - (0.015 if cur >= 10 else 0.005)
                share = max(1, math.ceil(lot_left[lid] / eff_left * 1.45))

            # 5E. Adaptive PID Feedback Controller
            elif strat == "adaptive_pid":
                base = {"SPECIALTY": 1.07, "COMMERCIAL": 1.025, "INSTANT": 0.93}[btype]
                mk = base - bm["int_err"] - (0.020 if left <= 2 else 0.0)
                share = max(1, math.ceil(lot_left[lid] / left * 1.4))

            # 5F. Adaptive Expected Value Maximizer
            else:
                crowd_adj = min(0.04, max(0.0, (bm["crowding"] - 1.2) * 0.015))
                mk = min(est, mkt_mk + 0.01) - crowd_adj - (0.025 if left <= 2 else 0.0)
                share = max(1, math.ceil(lot_left[lid] / left * 1.4))

            if bm["ub"] is not None:
                mk = min(mk, bm["ub"] - 0.015)
            mk = round(min(max(mk, 0.85), 1.30), 3)

            bags = min(share, lot_left[lid], rfq_left[rid], cap_left)
            if bags > 0:
                offers.append({
                    "rfq_id": rid,
                    "lot_id": lid,
                    "kg": bags * BAG,
                    "price_per_kg": round(mk * tv[lid], 2),
                })
                seen.add((lid, rid))
                lot_left[lid] -= bags
                rfq_left[rid] -= bags
                cap_left -= bags

        return offers


class QwenPVPWorker:
    """Runs BOTH:
    - T01: AI Refined Agent (`self config agent bot/strategy.py` & `memory.py`)
    - T02: My Agent Strategy (`Agent PVP/my_agent_strategy.py` & `my_agent_memory.py` — LOCKED until user tells to tune)
    Sequentially with Competition Event-LLM (`<= 20 req/min`, strictly `1 request at a time` via `_LLM_LOCK`).
    """

    def __init__(self, engine: PVPMarketEngine):
        self.engine = engine
        self.llm = qwen_llm_mod.LLM(
            base_url=os.environ.get("LLM_BASE_URL", "http://161.246.199.191:4000/v1"),
            model=os.environ.get("LLM_MODEL", "event-llm"),
            api_key=os.environ.get("LLM_API_KEY", "sk-T16-MgkI9WNcvneRFQlqkxYzrrIu"),
            timeout=float(os.environ.get("LLM_TIMEOUT", "45")),
            thinking=False,
            temperature=0.25,
        )
        self.mem_t01 = None
        self.mem_t02 = None
        self.tv_t01 = {}
        self.tv_t02 = {}

    def trigger(self, game_id: int, phase: str, round_num: int, rinfo: dict | None = None) -> None:
        if phase == "APPRAISAL":
            self._run_step(game_id, phase, round_num)
            return
        t = threading.Thread(
            target=self._run_step,
            args=(game_id, phase, round_num),
            daemon=True,
        )
        t.start()

    def _run_single_agent(
        self,
        tid: str,
        strat_mod,
        mem_mod,
        mem_obj,
        tv_map: dict,
        game_id: int,
        round_num: int,
        status_attr: str,
        trace_list_attr: str,
        use_llm: bool = True,
    ):
        state = self.engine.get_state_for_team(tid)
        if self.engine.game_id != game_id or state["phase"] != "ROUND_OPEN":
            return mem_obj, tv_map

        rid = state["round"]["round_id"]
        setattr(
            self.engine,
            status_attr,
            {"state": "thinking", "round": rid, "seconds": None, "last_reason": f"{tid} analyzing {rid} ({state['round']['calendar']})..."},
        )
        if not tv_map:
            tv_map = qwen_appraisal.estimate_lots(self.engine.rules, state["lots"])
        if mem_obj is None:
            mem_file = self.engine.logs_dir / f"mem_{tid.lower()}_g{game_id}.json"
            mem_obj = mem_mod.Memory(str(mem_file))
            if hasattr(mem_obj, "observe_lots"):
                try:
                    mem_obj.observe_lots(state["lots"], tv_map)
                except TypeError:
                    mem_obj.observe_lots(state["lots"])

        mem_obj.record_results(state.get("last_results"))
        ctx = strat_mod.Context(
            self.engine.rules,
            self.engine.calendar,
            tv_map,
            random.Random(hash((tid, game_id, round_num))),
            mem_obj,
            self.llm if use_llm else None,
            state.get("seconds_left") or 240.0,
        )
        t0 = time.time()
        try:
            offers, source = strat_mod.decide(state, ctx)
        except Exception as e:
            offers = strat_mod.guard(
                state,
                ctx,
                strat_mod.rule_plan(state, ctx, strat_mod.candidates(state, ctx)),
            )
            source = f"crash_fallback({e})"
        elapsed = round(time.time() - t0, 2)

        if self.engine.game_id != game_id or self.engine.phase != "ROUND_OPEN" or self.engine.round_num != round_num:
            return mem_obj, tv_map

        if offers:
            try:
                self.engine.submit_offer_batch(tid, offers, replace_existing=True)
            except Exception:
                for o in offers:
                    try:
                        self.engine.submit_offer(tid, o)
                    except Exception:
                        pass
        mem_obj.record_sent(rid, offers, state, tv_map)

        trace_record = {
            "team_id": tid,
            "round_id": rid,
            "calendar": state["round"]["calendar"],
            "source": source,
            "elapsed_s": elapsed,
            "notes": ctx.notes,
            "raw_llm_answer": ctx.last_trace.get("raw_answer"),
            "llm_error": ctx.last_trace.get("llm_error"),
            "learned_buyers_snapshot": json.loads(json.dumps(mem_obj.buyers, ensure_ascii=False)),
            "offers_submitted": [
                {**o, "bags": o["kg"] // BAG, "markup": round(o["price_per_kg"] / tv_map[o["lot_id"]], 3)}
                for o in offers
            ],
        }
        with self.engine.lock:
            getattr(self.engine, trace_list_attr).append(trace_record)
            reason_str = "; ".join(ctx.notes) if ctx.notes else source
            setattr(
                self.engine,
                status_attr,
                {
                    "state": "ready",
                    "round": rid,
                    "calendar": state["round"]["calendar"],
                    "source": source,
                    "seconds": elapsed,
                    "offers_count": len(offers),
                    "bags_offered": sum(o["kg"] for o in offers) // BAG,
                    "last_reason": reason_str,
                },
            )
        self.engine.mark_ready(tid, True)
        return mem_obj, tv_map

    def _run_step(self, game_id: int, phase: str, round_num: int) -> None:
        if self.engine.game_id != game_id or self.engine.phase != phase:
            return

        if phase == "APPRAISAL":
            for tid, mem_mod, attr_tv, attr_mem, attr_status in [
                ("T01", refined_memory_mod, "tv_t01", "mem_t01", "qwen_status"),
                ("T02", user_locked_memory_mod, "tv_t02", "mem_t02", "user_agent_status"),
            ]:
                st = self.engine.get_state_for_team(tid)
                tv_map = qwen_appraisal.estimate_lots(self.engine.rules, st["lots"])
                setattr(self, attr_tv, tv_map)
                mem_file = self.engine.logs_dir / f"mem_{tid.lower()}_g{game_id}.json"
                if mem_file.exists():
                    mem_file.unlink()
                mem_obj = mem_mod.Memory(str(mem_file))
                if hasattr(mem_obj, "observe_lots"):
                    try:
                        mem_obj.observe_lots(st["lots"], tv_map)
                    except TypeError:
                        mem_obj.observe_lots(st["lots"])
                setattr(self, attr_mem, mem_obj)
                items = qwen_appraisal.appraisal_items(self.engine.rules, st["lots"], tv_map)
                try:
                    self.engine.submit_appraisal(tid, items)
                except Exception:
                    pass
                setattr(self.engine, attr_status, {"state": "ready", "round": "APPRAISAL", "seconds": 0.05, "last_reason": f"Appraised {len(tv_map)} lots"})
                self.engine.mark_ready(tid, True)
            return

        if phase == "ROUND_OPEN":
            use_llm_t01 = os.environ.get("USE_LLM", "1") == "1"
            use_llm_t02 = os.environ.get("T02_USE_LLM", "0") == "1"
            self.mem_t01, self.tv_t01 = self._run_single_agent(
                "T01", refined_strategy, refined_memory_mod, self.mem_t01, self.tv_t01,
                game_id, round_num, "qwen_status", "llm_traces", use_llm=use_llm_t01,
            )
            self.mem_t02, self.tv_t02 = self._run_single_agent(
                "T02", user_locked_strategy, user_locked_memory_mod, self.mem_t02, self.tv_t02,
                game_id, round_num, "user_agent_status", "user_agent_traces", use_llm=use_llm_t02,
            )
