"""Time-based Multi-File CSV Logger & Historical Game Query Engine for Stage 3 Agent PVP Training.

Instead of large per-game JSON dumps, writes structured rows to 5 CSV tables (both per-session
timestamped CSV files `session_<YYYYMMDD_HHMMSS>_*.csv` and cumulative `all_*.csv` files):
1. `games_summary`: 1 row per completed game (time, game_number, T01/T02 metrics, winner, bot mix counts & avg PRs)
2. `team_standings`: 56 rows per game (final rank, PR, total score, revenue, bags sold for every team/bot)
3. `round_market`: 1 row per RFQ per round (P1-P2, R1-R15 clearing prices, crowding, fill %, hidden ceiling)
4. `round_offers`: 1 row per submitted offer (team, category, strategy, lot, RFQ, price, markup, filled bags, status)
5. `agent_traces`: 1 row per round for T01 and T02 (source, LLM latency, phase, offers, learned buyer safe_mk)
"""
import csv
import json
import re
import threading
from datetime import datetime
from pathlib import Path

BAG = 60
_FILLED_PAT = re.compile(r"\((\d+)/\d+b@")


def _quantile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(n - 1, lo + 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def _box_stats(vals: list[float], decimals: int = 2) -> dict:
    if not vals:
        return {"min": 0.0, "q1": 0.0, "median": 0.0, "q3": 0.0, "max": 0.0, "mean": 0.0, "std": 0.0, "count": 0}
    s = sorted(float(v) for v in vals)
    n = len(s)
    mean_v = sum(s) / n
    var_v = sum((x - mean_v) ** 2 for x in s) / (n - 1) if n > 1 else 0.0
    std_v = var_v ** 0.5
    return {
        "min": round(s[0], decimals),
        "q1": round(_quantile(s, 0.25), decimals),
        "median": round(_quantile(s, 0.50), decimals),
        "q3": round(_quantile(s, 0.75), decimals),
        "max": round(s[-1], decimals),
        "mean": round(mean_v, decimals),
        "std": round(std_v, decimals),
        "count": n,
    }


def _gen_sort_key(gen_name: str) -> tuple[int, str]:
    m = re.search(r"(\d+)", str(gen_name))
    return (int(m.group(1)) if m else 9999, str(gen_name))

SUMMARY_FIELDS = [
    "game_number",
    "generation",
    "session_id",
    "session_game_number",
    "game_start_time",
    "game_end_time",
    "duration_sec",
    "total_rounds",
    "practice_rounds",
    "t01_rank",
    "t01_pr",
    "t01_total_score",
    "t01_bags_sold",
    "t01_revenue_thb",
    "t01_avg_price",
    "t02_rank",
    "t02_pr",
    "t02_total_score",
    "t02_bags_sold",
    "t02_revenue_thb",
    "t02_avg_price",
    "winner_team_id",
    "winner_team_name",
    "winner_category",
    "winner_strategy",
    "winner_pr",
    "winner_total_score",
    "winner_bags_sold",
    "count_random",
    "count_stay_low",
    "count_stay_high",
    "count_market_price",
    "count_adaptive",
    "count_adaptive_bayes",
    "count_adaptive_festival",
    "count_adaptive_sniper",
    "count_adaptive_dp_pacer",
    "count_adaptive_pid",
    "count_adaptive_ev_max",
    "avg_pr_random",
    "avg_pr_stay_low",
    "avg_pr_stay_high",
    "avg_pr_market_price",
    "avg_pr_adaptive",
]

STANDINGS_FIELDS = [
    "game_number",
    "generation",
    "session_id",
    "game_start_time",
    "game_end_time",
    "team_id",
    "team_name",
    "role",
    "category",
    "strategy",
    "rank",
    "pr",
    "pr_score",
    "appraisal_score",
    "total_score",
    "mape",
    "bags_sold",
    "bags_remaining",
    "bags_total",
    "revenue_thb",
    "salvage_thb",
    "avg_price_per_kg",
]

MARKET_FIELDS = [
    "game_number",
    "generation",
    "session_id",
    "game_start_time",
    "round_seq",
    "round_id",
    "round_kind",
    "calendar_label",
    "demand_weight",
    "rfq_id",
    "buyer_id",
    "buyer",
    "buyer_type",
    "wanted_bags",
    "bought_bags",
    "fill_pct",
    "offers_count",
    "crowding",
    "hidden_ceiling",
    "price_min",
    "price_max",
    "price_avg",
]

OFFERS_FIELDS = [
    "game_number",
    "generation",
    "session_id",
    "game_start_time",
    "round_seq",
    "round_id",
    "round_kind",
    "calendar_label",
    "team_id",
    "team_name",
    "category",
    "strategy",
    "offer_id",
    "rfq_id",
    "buyer_id",
    "buyer_type",
    "lot_id",
    "lot_true_grade",
    "lot_true_tv",
    "bags_offered",
    "bags_filled",
    "price_per_kg",
    "markup_vs_true_tv",
    "status",
]

TRACES_FIELDS = [
    "game_number",
    "generation",
    "session_id",
    "game_start_time",
    "round_id",
    "team_id",
    "strategy",
    "source",
    "llm_seconds",
    "phase",
    "offers_summary",
    "reason_or_notes",
    "b1_safe_mk",
    "b2_safe_mk",
    "b3_safe_mk",
    "b4_safe_mk",
    "b5_safe_mk",
    "b6_safe_mk",
]


def _append_rows(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class PVPCSVLogger:
    def __init__(self, logs_dir: Path):
        self.logs_dir = logs_dir
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

        self.all_summary_path = self.logs_dir / "all_games_summary.csv"
        self.all_standings_path = self.logs_dir / "all_team_standings.csv"
        self.all_market_path = self.logs_dir / "all_round_market.csv"
        self.all_offers_path = self.logs_dir / "all_round_offers.csv"
        self.all_traces_path = self.logs_dir / "all_agent_traces.csv"

        self.gen_config_path = self.logs_dir / "generation_config.json"
        self.current_generation = self._load_generation()
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_game_count = 0
        self.total_games_logged = self._load_highest_game_number()

    def _load_generation(self) -> str:
        if self.gen_config_path.exists():
            try:
                data = json.loads(self.gen_config_path.read_text(encoding="utf-8"))
                return str(data.get("current_generation") or "gen 1")
            except Exception:
                pass
        return "gen 1"

    def set_generation(self, gen_name: str) -> str:
        with self.lock:
            self.current_generation = str(gen_name).strip() or "gen 1"
            self.gen_config_path.write_text(
                json.dumps({"current_generation": self.current_generation}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return self.current_generation

    def _load_highest_game_number(self) -> int:
        rows = _read_csv(self.all_summary_path)
        if not rows:
            return 0
        try:
            return max(int(r.get("game_number", 0)) for r in rows)
        except ValueError:
            return len(rows)

    def start_new_session(self, session_id: str | None = None) -> str:
        with self.lock:
            self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
            self.session_game_count = 0
            return self.session_id

    def next_game_number(self) -> int:
        with self.lock:
            return self.total_games_logged + 1

    def record_completed_game(self, engine) -> dict:
        """Extracts all 5 tables from a completed PVPMarketEngine game and appends them to CSV files."""
        with self.lock:
            self.total_games_logged += 1
            self.session_game_count += 1
            g_num = self.total_games_logged
            s_num = self.session_game_count
            sess_id = self.session_id
            gen_name = self.current_generation

            start_ts = getattr(engine, "match_started_at", None) or engine.phase_started_at
            end_ts = datetime.now().timestamp()
            start_str = datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d %H:%M:%S")
            end_str = datetime.fromtimestamp(end_ts).strftime("%Y-%m-%d %H:%M:%S")
            dur_sec = round(max(0.1, end_ts - start_ts), 2)

            scores = engine.compute_scores()
            ranked = sorted(scores.values(), key=lambda x: x["rank"])
            t01 = scores.get("T01", {})
            t02 = scores.get("T02", {})
            winner = ranked[0] if ranked else {}

            # Count bots per category & strategy and compute category average PRs
            cat_prs: dict[str, list[float]] = {}
            strat_counts: dict[str, int] = {}
            cat_counts: dict[str, int] = {}
            for tid, tinfo in engine.teams.items():
                cat = tinfo.get("category", "bot")
                strat = tinfo.get("strategy", "bot")
                cat_counts[cat] = cat_counts.get(cat, 0) + 1
                strat_counts[strat] = strat_counts.get(strat, 0) + 1
                if tid in scores:
                    cat_prs.setdefault(cat, []).append(float(scores[tid]["pr"]))

            def avg_cat(c: str) -> float:
                vals = cat_prs.get(c, [])
                return round(sum(vals) / len(vals), 4) if vals else 0.0

            t01_sold_kg = t01.get("kg_sold", 0)
            t02_sold_kg = t02.get("kg_sold", 0)
            t01_avg_p = round(t01.get("revenue_thb", 0.0) / t01_sold_kg, 2) if t01_sold_kg > 0 else 0.0
            t02_avg_p = round(t02.get("revenue_thb", 0.0) / t02_sold_kg, 2) if t02_sold_kg > 0 else 0.0

            summary_row = {
                "game_number": g_num,
                "generation": gen_name,
                "session_id": sess_id,
                "session_game_number": s_num,
                "game_start_time": start_str,
                "game_end_time": end_str,
                "duration_sec": dur_sec,
                "total_rounds": engine.total_rounds,
                "practice_rounds": engine.practice_rounds,
                "t01_rank": t01.get("rank", 56),
                "t01_pr": t01.get("pr", 0.0),
                "t01_total_score": t01.get("total", 0.0),
                "t01_bags_sold": t01_sold_kg // BAG,
                "t01_revenue_thb": t01.get("revenue_thb", 0.0),
                "t01_avg_price": t01_avg_p,
                "t02_rank": t02.get("rank", 56),
                "t02_pr": t02.get("pr", 0.0),
                "t02_total_score": t02.get("total", 0.0),
                "t02_bags_sold": t02_sold_kg // BAG,
                "t02_revenue_thb": t02.get("revenue_thb", 0.0),
                "t02_avg_price": t02_avg_p,
                "winner_team_id": winner.get("team_id", ""),
                "winner_team_name": winner.get("team_name", ""),
                "winner_category": winner.get("category", ""),
                "winner_strategy": winner.get("strategy", ""),
                "winner_pr": winner.get("pr", 0.0),
                "winner_total_score": winner.get("total", 0.0),
                "winner_bags_sold": winner.get("kg_sold", 0) // BAG,
                "count_random": cat_counts.get("random", 0),
                "count_stay_low": cat_counts.get("stay_low", 0),
                "count_stay_high": cat_counts.get("stay_high", 0),
                "count_market_price": cat_counts.get("market_price", 0),
                "count_adaptive": cat_counts.get("adaptive", 0),
                "count_adaptive_bayes": strat_counts.get("adaptive_bayes", 0),
                "count_adaptive_festival": strat_counts.get("adaptive_festival", 0),
                "count_adaptive_sniper": strat_counts.get("adaptive_sniper", 0),
                "count_adaptive_dp_pacer": strat_counts.get("adaptive_dp_pacer", 0),
                "count_adaptive_pid": strat_counts.get("adaptive_pid", 0),
                "count_adaptive_ev_max": strat_counts.get("adaptive_ev_max", 0),
                "avg_pr_random": avg_cat("random"),
                "avg_pr_stay_low": avg_cat("stay_low"),
                "avg_pr_stay_high": avg_cat("stay_high"),
                "avg_pr_market_price": avg_cat("market_price"),
                "avg_pr_adaptive": avg_cat("adaptive"),
            }

            # 2. Team Standings (56 rows)
            standings_rows = []
            for sc in ranked:
                sold_kg = sc.get("kg_sold", 0)
                avg_p = round(sc.get("revenue_thb", 0.0) / sold_kg, 2) if sold_kg > 0 else 0.0
                standings_rows.append({
                    "game_number": g_num,
                    "generation": gen_name,
                    "session_id": sess_id,
                    "game_start_time": start_str,
                    "game_end_time": end_str,
                    "team_id": sc["team_id"],
                    "team_name": sc["team_name"],
                    "role": sc["role"],
                    "category": sc.get("category", "bot"),
                    "strategy": sc.get("strategy", "bot"),
                    "rank": sc["rank"],
                    "pr": sc["pr"],
                    "pr_score": sc["pr_score"],
                    "appraisal_score": sc["appraisal_score"],
                    "total_score": sc["total"],
                    "mape": sc["mape"] if sc.get("mape") is not None else "",
                    "bags_sold": sold_kg // BAG,
                    "bags_remaining": sc.get("kg_remaining", 0) // BAG,
                    "bags_total": sc.get("kg_total", 0) // BAG,
                    "revenue_thb": sc["revenue_thb"],
                    "salvage_thb": sc["salvage_thb"],
                    "avg_price_per_kg": avg_p,
                })

            # 3. Round Market Summary & 4. Round Offers
            market_rows = []
            offers_rows = []
            lot_lookup = {}
            for tid, lots in engine.team_lots.items():
                for l in lots:
                    lot_lookup[l["lot_id"]] = (tid, l)
            rfq_lookup = {}
            for rid, rlist in engine.round_rfqs.items():
                for q in rlist:
                    rfq_lookup[q["rfq_id"]] = q

            for seq_i, rinfo in enumerate(engine.round_sequence, 1):
                rid = rinfo["round_id"]
                r_kind = rinfo["kind"]
                cal_lbl = rinfo["calendar"]
                d_w = rinfo["demand_weight"]
                by_team = engine.round_results.get(rid, {})
                any_team_res = next(iter(by_team.values()), None)
                if any_team_res:
                    for m in any_team_res.get("market_summary_with_ceilings", any_team_res.get("market_summary", [])):
                        w_bags = max(1, m["wanted_kg"] // BAG)
                        b_bags = m["bought_kg"] // BAG
                        fill_pct = round(m["bought_kg"] * 100.0 / max(1, m["wanted_kg"]), 1)
                        crowd = round(m["offers"] / w_bags, 2)
                        market_rows.append({
                            "game_number": g_num,
                            "generation": gen_name,
                            "session_id": sess_id,
                            "game_start_time": start_str,
                            "round_seq": seq_i,
                            "round_id": rid,
                            "round_kind": r_kind,
                            "calendar_label": cal_lbl,
                            "demand_weight": d_w,
                            "rfq_id": m["rfq_id"],
                            "buyer_id": m.get("buyer_id", ""),
                            "buyer": m["buyer"],
                            "buyer_type": m["buyer_type"],
                            "wanted_bags": w_bags,
                            "bought_bags": b_bags,
                            "fill_pct": fill_pct,
                            "offers_count": m["offers"],
                            "crowding": crowd,
                            "hidden_ceiling": m.get("hidden_ceiling", ""),
                            "price_min": m["price_min"] if m["price_min"] is not None else "",
                            "price_max": m["price_max"] if m["price_max"] is not None else "",
                            "price_avg": m["price_avg"] if m["price_avg"] is not None else "",
                        })

                for tid, tres in by_team.items():
                    tinfo = engine.teams.get(tid, {})
                    for o in tres.get("my_offers", []):
                        _, lobj = lot_lookup.get(o["lot_id"], (tid, {}))
                        qobj = rfq_lookup.get(o["rfq_id"], {})
                        true_tv = lobj.get("true_value_per_kg", 100.0)
                        offers_rows.append({
                            "game_number": g_num,
                            "generation": gen_name,
                            "session_id": sess_id,
                            "game_start_time": start_str,
                            "round_seq": seq_i,
                            "round_id": rid,
                            "round_kind": r_kind,
                            "calendar_label": cal_lbl,
                            "team_id": tid,
                            "team_name": tinfo.get("team_name", tid),
                            "category": tinfo.get("category", "bot"),
                            "strategy": tinfo.get("strategy", "bot"),
                            "offer_id": o["offer_id"],
                            "rfq_id": o["rfq_id"],
                            "buyer_id": qobj.get("buyer_id", ""),
                            "buyer_type": qobj.get("buyer_type", ""),
                            "lot_id": o["lot_id"],
                            "lot_true_grade": lobj.get("true_grade", ""),
                            "lot_true_tv": true_tv,
                            "bags_offered": o["kg"] // BAG,
                            "bags_filled": o["filled_kg"] // BAG,
                            "price_per_kg": o["price_per_kg"],
                            "markup_vs_true_tv": round(o["price_per_kg"] / max(1.0, true_tv), 4),
                            "status": o["status"],
                        })

            # 5. Agent Traces (T01 and T02)
            traces_rows = []
            for tid, strat_name, t_list in [
                ("T01", "ai_refined", engine.llm_traces),
                ("T02", "my_agent_locked", engine.user_agent_traces),
            ]:
                for tr in t_list:
                    rid = tr.get("round", tr.get("round_id", ""))
                    res_offers = engine.round_results.get(rid, {}).get(tid, {}).get("my_offers", [])
                    if res_offers:
                        os_str = "; ".join(
                            f"{o['lot_id'].split('-')[-1]}->{o['rfq_id']}({o['filled_kg']//BAG}/{o['kg']//BAG}b@{o['price_per_kg']}:{o['status'][:3]})"
                            for o in res_offers
                        )
                    else:
                        os_str = "; ".join(
                            f"{o['lot_id'].split('-')[-1]}->{o['rfq_id']}({o['kg']//BAG}b@{o['price_per_kg']})"
                            for o in tr.get("final_offers", [])
                        )
                    bsnap = tr.get("learned_buyers_snapshot") or {}
                    b_mks = {"B1": "", "B2": "", "B3": "", "B4": "", "B5": "", "B6": ""}
                    for bname, binfo in bsnap.items():
                        bid = binfo.get("buyer_id") or (bname.split()[0] if bname else "")
                        if bid.startswith("1") or bid == "B1":
                            b_mks["B1"] = binfo.get("safe_mk", "")
                        elif bid.startswith("2") or bid == "B2":
                            b_mks["B2"] = binfo.get("safe_mk", "")
                        elif bid.startswith("3") or bid == "B3":
                            b_mks["B3"] = binfo.get("safe_mk", "")
                        elif bid.startswith("4") or bid == "B4":
                            b_mks["B4"] = binfo.get("safe_mk", "")
                        elif bid.startswith("5") or bid == "B5":
                            b_mks["B5"] = binfo.get("safe_mk", "")
                        elif bid.startswith("6") or bid == "B6":
                            b_mks["B6"] = binfo.get("safe_mk", "")

                    notes = tr.get("notes") or []
                    reason_str = " | ".join(str(n) for n in notes[-2:])
                    traces_rows.append({
                        "game_number": g_num,
                        "generation": gen_name,
                        "session_id": sess_id,
                        "game_start_time": start_str,
                        "round_id": rid,
                        "team_id": tid,
                        "strategy": strat_name,
                        "source": tr.get("source", ""),
                        "llm_seconds": tr.get("llm_seconds") if tr.get("llm_seconds") is not None else "",
                        "phase": tr.get("phase", ""),
                        "offers_summary": os_str,
                        "reason_or_notes": reason_str,
                        "b1_safe_mk": b_mks["B1"],
                        "b2_safe_mk": b_mks["B2"],
                        "b3_safe_mk": b_mks["B3"],
                        "b4_safe_mk": b_mks["B4"],
                        "b5_safe_mk": b_mks["B5"],
                        "b6_safe_mk": b_mks["B6"],
                    })

            # Write to cumulative CSVs
            _append_rows(self.all_summary_path, SUMMARY_FIELDS, [summary_row])
            _append_rows(self.all_standings_path, STANDINGS_FIELDS, standings_rows)
            _append_rows(self.all_market_path, MARKET_FIELDS, market_rows)
            _append_rows(self.all_offers_path, OFFERS_FIELDS, offers_rows)
            _append_rows(self.all_traces_path, TRACES_FIELDS, traces_rows)

            # Also write to time-based session CSVs
            sess_summary_path = self.logs_dir / f"session_{sess_id}_games_summary.csv"
            sess_standings_path = self.logs_dir / f"session_{sess_id}_team_standings.csv"
            sess_market_path = self.logs_dir / f"session_{sess_id}_round_market.csv"
            sess_offers_path = self.logs_dir / f"session_{sess_id}_round_offers.csv"
            sess_traces_path = self.logs_dir / f"session_{sess_id}_agent_traces.csv"

            _append_rows(sess_summary_path, SUMMARY_FIELDS, [summary_row])
            _append_rows(sess_standings_path, STANDINGS_FIELDS, standings_rows)
            _append_rows(sess_market_path, MARKET_FIELDS, market_rows)
            _append_rows(sess_offers_path, OFFERS_FIELDS, offers_rows)
            _append_rows(sess_traces_path, TRACES_FIELDS, traces_rows)

            engine.saved_game_log_path = sess_summary_path
            return summary_row

    def get_all_games_summary(self, generation: str | None = None) -> dict:
        """Returns aggregate statistics across all games (optionally filtered by generation) + generation comparison."""
        with self.lock:
            all_games = _read_csv(self.all_summary_path)
            all_standings = _read_csv(self.all_standings_path)

            generations_present = sorted({(g.get("generation") or "gen 1") for g in all_games}, key=_gen_sort_key)
            if self.current_generation not in generations_present:
                generations_present.append(self.current_generation)

            # Per-generation comparison summary & box-plot distributions (T01 vs T02)
            gen_comp = {}
            gen_dists: dict[str, dict[str, list[float]]] = {}
            for g in all_games:
                gn = g.get("generation") or "gen 1"
                rec = gen_comp.setdefault(gn, {
                    "generation": gn,
                    "games": 0,
                    "t01_pr_sum": 0.0,
                    "t01_rank_sum": 0,
                    "t01_wins": 0,
                    "t01_top5": 0,
                    "t02_pr_sum": 0.0,
                    "t02_rank_sum": 0,
                    "t02_wins": 0,
                    "t02_top5": 0,
                })
                dist = gen_dists.setdefault(gn, {
                    "t01_pr": [], "t02_pr": [],
                    "t01_rank": [], "t02_rank": [],
                    "t01_sold_pct": [], "t02_sold_pct": [],
                })
                rec["games"] += 1
                r1 = int(g.get("t01_rank", 56) or 56)
                r2 = int(g.get("t02_rank", 56) or 56)
                p1 = float(g.get("t01_pr", 0) or 0)
                p2 = float(g.get("t02_pr", 0) or 0)
                s1 = round(int(g.get("t01_bags_sold", 0) or 0) * 100.0 / 45.0, 2)
                s2 = round(int(g.get("t02_bags_sold", 0) or 0) * 100.0 / 45.0, 2)
                dist["t01_pr"].append(p1)
                dist["t02_pr"].append(p2)
                dist["t01_rank"].append(r1)
                dist["t02_rank"].append(r2)
                dist["t01_sold_pct"].append(s1)
                dist["t02_sold_pct"].append(s2)
                rec["t01_pr_sum"] += p1
                rec["t01_rank_sum"] += r1
                if r1 == 1:
                    rec["t01_wins"] += 1
                if r1 <= 5:
                    rec["t01_top5"] += 1
                rec["t02_pr_sum"] += p2
                rec["t02_rank_sum"] += r2
                if r2 == 1:
                    rec["t02_wins"] += 1
                if r2 <= 5:
                    rec["t02_top5"] += 1

            generation_comparison = []
            gen_box_charts = []
            for gn, rec in sorted(gen_comp.items(), key=lambda kv: _gen_sort_key(kv[0])):
                n = max(1, rec["games"])
                generation_comparison.append({
                    "generation": gn,
                    "games": rec["games"],
                    "t01_avg_pr": round(rec["t01_pr_sum"] / n, 4),
                    "t01_avg_rank": round(rec["t01_rank_sum"] / n, 2),
                    "t01_wins": rec["t01_wins"],
                    "t01_top5_pct": round(rec["t01_top5"] * 100.0 / n, 1),
                    "t02_avg_pr": round(rec["t02_pr_sum"] / n, 4),
                    "t02_avg_rank": round(rec["t02_rank_sum"] / n, 2),
                    "t02_wins": rec["t02_wins"],
                    "t02_top5_pct": round(rec["t02_top5"] * 100.0 / n, 1),
                })
                d = gen_dists[gn]
                gen_box_charts.append({
                    "generation": gn,
                    "games": rec["games"],
                    "pr": {"t01": _box_stats(d["t01_pr"], 4), "t02": _box_stats(d["t02_pr"], 4)},
                    "rank": {"t01": _box_stats(d["t01_rank"], 2), "t02": _box_stats(d["t02_rank"], 2)},
                    "sold_pct": {"t01": _box_stats(d["t01_sold_pct"], 1), "t02": _box_stats(d["t02_sold_pct"], 1)},
                })

            gen_filter = generation if (generation and generation.lower() != "all") else None
            games = [g for g in all_games if not gen_filter or (g.get("generation") or "gen 1") == gen_filter]
            standings = [s for s in all_standings if not gen_filter or (s.get("generation") or "gen 1") == gen_filter]

            # Parse numeric fields in games
            parsed_games = []
            valid_gnums = set()
            for g in games:
                gnum_str = str(g.get("game_number", 0))
                valid_gnums.add(gnum_str)
                t01_b = int(g.get("t01_bags_sold", 0) or 0)
                t02_b = int(g.get("t02_bags_sold", 0) or 0)
                parsed_games.append({
                    "game_number": int(gnum_str or 0),
                    "generation": g.get("generation") or "gen 1",
                    "session_id": g.get("session_id", ""),
                    "session_game_number": int(g.get("session_game_number", 0) or 0),
                    "game_start_time": g.get("game_start_time", ""),
                    "game_end_time": g.get("game_end_time", ""),
                    "duration_sec": float(g.get("duration_sec", 0) or 0),
                    "total_rounds": int(g.get("total_rounds", 15) or 15),
                    "practice_rounds": int(g.get("practice_rounds", 2) or 2),
                    "t01_rank": int(g.get("t01_rank", 56) or 56),
                    "t01_pr": float(g.get("t01_pr", 0) or 0),
                    "t01_total_score": float(g.get("t01_total_score", 0) or 0),
                    "t01_bags_sold": t01_b,
                    "t01_sold_pct": round(t01_b * 100.0 / 45.0, 1),
                    "t01_revenue_thb": float(g.get("t01_revenue_thb", 0) or 0),
                    "t01_avg_price": float(g.get("t01_avg_price", 0) or 0),
                    "t02_rank": int(g.get("t02_rank", 56) or 56),
                    "t02_pr": float(g.get("t02_pr", 0) or 0),
                    "t02_total_score": float(g.get("t02_total_score", 0) or 0),
                    "t02_bags_sold": t02_b,
                    "t02_sold_pct": round(t02_b * 100.0 / 45.0, 1),
                    "t02_revenue_thb": float(g.get("t02_revenue_thb", 0) or 0),
                    "t02_avg_price": float(g.get("t02_avg_price", 0) or 0),
                    "winner_team_id": g.get("winner_team_id", ""),
                    "winner_team_name": g.get("winner_team_name", ""),
                    "winner_category": g.get("winner_category", ""),
                    "winner_strategy": g.get("winner_strategy", ""),
                    "winner_pr": float(g.get("winner_pr", 0) or 0),
                    "winner_total_score": float(g.get("winner_total_score", 0) or 0),
                    "winner_bags_sold": int(g.get("winner_bags_sold", 0) or 0),
                    "count_random": int(g.get("count_random", 0) or 0),
                    "count_stay_low": int(g.get("count_stay_low", 0) or 0),
                    "count_stay_high": int(g.get("count_stay_high", 0) or 0),
                    "count_market_price": int(g.get("count_market_price", 0) or 0),
                    "count_adaptive": int(g.get("count_adaptive", 0) or 0),
                })

            # Compute Round-by-Round Box Chart Statistics (R1..R15) for:
            # 1) Overall Market Bags Sold per Round
            # 2) First Agent (T01) Bags Sold per Round
            # 3) Second Agent (T02) Bags Sold per Round
            round_ids = [f"R{i}" for i in range(1, 16)]
            cal_by_rid = {
                f"R{i}": ("เทศกาลกาแฟ" if 6 <= i <= 8 else ("ใกล้ปิดตลาด" if i in (13, 14) else ("รอบสุดท้าย" if i == 15 else "ปกติ")))
                for i in range(1, 16)
            }
            overall_by_game_round = {g: {rid: 0 for rid in round_ids} for g in valid_gnums}
            t01_by_game_round = {g: {rid: 0 for rid in round_ids} for g in valid_gnums}
            t02_by_game_round = {g: {rid: 0 for rid in round_ids} for g in valid_gnums}

            if valid_gnums:
                for m in _read_csv(self.all_market_path):
                    gn = str(m.get("game_number", ""))
                    rid = m.get("round_id", "")
                    if gn in overall_by_game_round and rid in overall_by_game_round[gn]:
                        overall_by_game_round[gn][rid] += int(m.get("bought_bags", 0) or 0)

                for tr in _read_csv(self.all_traces_path):
                    gn = str(tr.get("game_number", ""))
                    rid = tr.get("round_id", "")
                    tid = tr.get("team_id", "")
                    if gn in t01_by_game_round and rid in t01_by_game_round[gn]:
                        filled_b = sum(int(x) for x in _FILLED_PAT.findall(tr.get("offers_summary", "")))
                        if tid == "T01":
                            t01_by_game_round[gn][rid] = filled_b
                        elif tid == "T02":
                            t02_by_game_round[gn][rid] = filled_b

            round_box_charts = {"overall": [], "t01": [], "t02": []}
            for rid in round_ids:
                ov_vals = [overall_by_game_round[g][rid] for g in valid_gnums]
                t1_vals = [t01_by_game_round[g][rid] for g in valid_gnums]
                t2_vals = [t02_by_game_round[g][rid] for g in valid_gnums]
                ov_box = _box_stats(ov_vals)
                ov_box["round_id"] = rid
                ov_box["calendar"] = cal_by_rid[rid]
                ov_box["per_team_mean"] = round(ov_box["mean"] / 56.0, 2)
                t1_box = _box_stats(t1_vals)
                t1_box["round_id"] = rid
                t1_box["calendar"] = cal_by_rid[rid]
                t2_box = _box_stats(t2_vals)
                t2_box["round_id"] = rid
                t2_box["calendar"] = cal_by_rid[rid]
                round_box_charts["overall"].append(ov_box)
                round_box_charts["t01"].append(t1_box)
                round_box_charts["t02"].append(t2_box)

            # Aggregate strategy performance across all games from `all_team_standings.csv`
            strat_agg = {}
            for s in standings:
                strat = s.get("strategy", "bot")
                cat = s.get("category", "bot")
                rk = int(s.get("rank", 56) or 56)
                pr = float(s.get("pr", 0) or 0)
                tot = float(s.get("total_score", 0) or 0)
                bags = int(s.get("bags_sold", 0) or 0)
                rec = strat_agg.setdefault(strat, {
                    "strategy": strat,
                    "category": cat,
                    "appearances": 0,
                    "pr_sum": 0.0,
                    "best_pr": 0.0,
                    "rank_sum": 0,
                    "best_rank": 999,
                    "wins_rank1": 0,
                    "top5_count": 0,
                    "top10_count": 0,
                    "bags_sum": 0,
                    "score_sum": 0.0,
                })
                rec["appearances"] += 1
                rec["pr_sum"] += pr
                rec["best_pr"] = max(rec["best_pr"], pr)
                rec["rank_sum"] += rk
                rec["best_rank"] = min(rec["best_rank"], rk)
                if rk == 1:
                    rec["wins_rank1"] += 1
                if rk <= 5:
                    rec["top5_count"] += 1
                if rk <= 10:
                    rec["top10_count"] += 1
                rec["bags_sum"] += bags
                rec["score_sum"] += tot

            strategy_leaderboard = []
            for strat, rec in strat_agg.items():
                n = max(1, rec["appearances"])
                strategy_leaderboard.append({
                    "strategy": strat,
                    "category": rec["category"],
                    "appearances": rec["appearances"],
                    "avg_pr": round(rec["pr_sum"] / n, 4),
                    "best_pr": round(rec["best_pr"], 4),
                    "avg_rank": round(rec["rank_sum"] / n, 2),
                    "best_rank": rec["best_rank"],
                    "wins_rank1": rec["wins_rank1"],
                    "top5_count": rec["top5_count"],
                    "top5_rate_pct": round(rec["top5_count"] * 100.0 / n, 1),
                    "top10_count": rec["top10_count"],
                    "avg_bags_sold": round(rec["bags_sum"] / n, 1),
                    "avg_total_score": round(rec["score_sum"] / n, 2),
                })
            strategy_leaderboard.sort(key=lambda x: (-x["avg_pr"], x["avg_rank"]))

            csv_files = []
            for p in sorted(self.logs_dir.glob("*.csv"), key=lambda x: x.stat().st_mtime, reverse=True):
                csv_files.append({
                    "name": p.name,
                    "path": str(p),
                    "size_kb": round(p.stat().st_size / 1024.0, 1),
                    "modified": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                })

            return {
                "total_games": len(parsed_games),
                "total_all_games": len(all_games),
                "current_generation": self.current_generation,
                "active_filter_generation": gen_filter or "all",
                "generations": generations_present,
                "generation_comparison": generation_comparison,
                "gen_box_charts": gen_box_charts,
                "current_session_id": self.session_id,
                "games": list(reversed(parsed_games)),  # newest first
                "games_chronological": parsed_games,     # oldest first for charts
                "round_box_charts": round_box_charts,
                "strategy_leaderboard": strategy_leaderboard,
                "csv_files": csv_files[:20],
            }

    def get_game_history(self, game_number: int) -> dict | None:
        """Returns full historical details for a specific game_number from the CSV tables."""
        with self.lock:
            target = str(game_number)
            summaries = [r for r in _read_csv(self.all_summary_path) if r.get("game_number") == target]
            if not summaries:
                return None
            summary = summaries[-1]

            standings = [r for r in _read_csv(self.all_standings_path) if r.get("game_number") == target]
            for s in standings:
                s["rank"] = int(s.get("rank", 56) or 56)
                s["pr"] = float(s.get("pr", 0) or 0)
                s["pr_score"] = float(s.get("pr_score", 0) or 0)
                s["appraisal_score"] = float(s.get("appraisal_score", 0) or 0)
                s["total_score"] = float(s.get("total_score", 0) or 0)
                s["bags_sold"] = int(s.get("bags_sold", 0) or 0)
                s["bags_remaining"] = int(s.get("bags_remaining", 0) or 0)
                s["bags_total"] = int(s.get("bags_total", 45) or 45)
                s["revenue_thb"] = float(s.get("revenue_thb", 0) or 0)
                s["avg_price_per_kg"] = float(s.get("avg_price_per_kg", 0) or 0)
            standings.sort(key=lambda x: x["rank"])

            market_rows = [r for r in _read_csv(self.all_market_path) if r.get("game_number") == target]
            traces_rows = [r for r in _read_csv(self.all_traces_path) if r.get("game_number") == target]
            offers_rows = [r for r in _read_csv(self.all_offers_path) if r.get("game_number") == target]

            # Group market rows and agent offers by round_id
            rounds_order = []
            market_by_round = {}
            for m in market_rows:
                rid = m["round_id"]
                if rid not in market_by_round:
                    rounds_order.append({
                        "round_id": rid,
                        "round_seq": int(m.get("round_seq", 0) or 0),
                        "round_kind": m.get("round_kind", ""),
                        "calendar_label": m.get("calendar_label", ""),
                        "demand_weight": float(m.get("demand_weight", 1.0) or 1.0),
                    })
                    market_by_round[rid] = []
                market_by_round[rid].append(m)

            t01_traces = [t for t in traces_rows if t.get("team_id") == "T01"]
            t02_traces = [t for t in traces_rows if t.get("team_id") == "T02"]
            t01_offers = [o for o in offers_rows if o.get("team_id") == "T01"]
            t02_offers = [o for o in offers_rows if o.get("team_id") == "T02"]

            return {
                "game_number": game_number,
                "summary": summary,
                "standings": standings,
                "rounds_order": rounds_order,
                "market_by_round": market_by_round,
                "t01_traces": t01_traces,
                "t02_traces": t02_traces,
                "t01_offers": t01_offers,
                "t02_offers": t02_offers,
            }
