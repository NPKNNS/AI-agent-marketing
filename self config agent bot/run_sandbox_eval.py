"""Run N full sandbox games sequentially using ONLY Qwen 3.5 9B and collect full telemetry + reveal data.

Usage:
    python run_sandbox_eval.py --games 3
"""
import argparse
import hashlib
import json
import os
import random
import time
from datetime import datetime
from pathlib import Path

import main as starter_main
import strategy
from appraisal import appraisal_items, estimate_lots
from llm import LLM
from market import Market, MarketError
from memory import Memory

HERE = Path(__file__).parent
LOGS = HERE / "logs"


def run_one_game(api: Market, llm: LLM, rules: dict, calendar: list, game_idx: int) -> dict:
    # Start a completely fresh sandbox game
    r = api.call("POST", "/v1/sandbox/reset")
    print(f"\n==================== GAME {game_idx} STARTED ({datetime.now().strftime('%H:%M:%S')}) ====================", flush=True)

    rng = random.Random()
    tv = {}
    memory = None
    acted = set()
    game_key = None
    round_traces = []
    start_ts = time.time()

    while True:
        try:
            state = api.state()
        except Exception as e:
            starter_main.log("state_error", error=str(e))
            time.sleep(3)
            continue

        lots_key = tuple(sorted((l["lot_id"], json.dumps(l["sample"], sort_keys=True)) for l in state["lots"]))
        if lots_key != game_key:
            game_key = lots_key
            acted = set()
            tv = estimate_lots(rules, state["lots"])
            tag = hashlib.sha1(repr(lots_key).encode()).hexdigest()[:8]
            memory = Memory(LOGS / f"memory-{state['market']}-g{game_idx}-{tag}.json")
            starter_main.log("new_game", game=game_idx, market=state["market"], lots=len(state["lots"]), tv=tv)

        phase = state["phase"]

        if phase == "APPRAISAL" and not state["appraisal_submitted"]:
            try:
                api.appraise(appraisal_items(rules, state["lots"], tv))
                starter_main.log("appraisal_sent", game=game_idx, tv=tv)
            except MarketError as e:
                starter_main.log("appraisal_error", game=game_idx, error=str(e))

        elif phase == "ROUND_OPEN" and not state["paused"] and state["round"]["round_id"] not in acted:
            rid = state["round"]["round_id"]
            acted.add(rid)
            memory.record_results(state.get("last_results"))

            if any(o["round_id"] == rid for o in state.get("my_open_offers", [])):
                starter_main.log("round_already_sent", round=rid, n=len(state["my_open_offers"]))
                continue

            ctx = strategy.Context(rules, calendar, tv, rng, memory, llm, state["seconds_left"] or 999)
            t0 = time.time()
            try:
                offers, source = strategy.decide(state, ctx)
            except Exception as e:
                starter_main.log("strategy_crashed", error=repr(e))
                offers = strategy.guard(state, ctx, strategy.rule_plan(state, ctx, strategy.candidates(state, ctx)))
                source = "rules_crash_fallback"

            sent = starter_main.send_offers(api, offers) if offers else []
            memory.record_sent(rid, offers, state, tv)
            elapsed = round(time.time() - t0, 1)
            starter_main.log(
                "offers_sent",
                game=game_idx,
                round=rid,
                source=source,
                n=len(sent),
                kg=sum(o["kg"] for o in sent),
                seconds=elapsed,
                offers=offers,
                notes=ctx.notes,
            )
            round_traces.append({
                "round_id": rid,
                "phase_policy": strategy.phase_policy(state, ctx)["name"],
                "source": source,
                "llm_seconds": elapsed,
                "notes": list(ctx.notes),
                "offers_sent": [
                    {
                        **o,
                        "bags": o["kg"] // 60,
                        "markup": round(o["price_per_kg"] / tv[o["lot_id"]], 4),
                    }
                    for o in offers
                ],
                "buyers_snapshot_before_clear": json.loads(json.dumps(memory.buyers, ensure_ascii=False)),
            })

        elif phase == "CLOSED" and game_key is not None and "closed" not in acted:
            acted.add("closed")
            memory.record_results(state.get("last_results"))
            starter_main.log("market_closed", game=game_idx, standings=state["standings"])

            # Fetch reveal + all round results for complete analysis
            time.sleep(1.0)
            reveal = None
            try:
                reveal = api.call("GET", "/v1/sandbox/reveal")
            except Exception as e:
                starter_main.log("reveal_error", game=game_idx, error=str(e))

            all_rounds = {}
            for rnum in range(1, rules["timeline"]["rounds"] + 1):
                rid = f"R{rnum}"
                try:
                    time.sleep(0.3)
                    all_rounds[rid] = api.call("GET", "/v1/results", params={"round": rid})
                except Exception:
                    pass

            record = {
                "game": game_idx,
                "duration_s": round(time.time() - start_ts, 1),
                "tv_estimated": tv,
                "standings": state["standings"],
                "reveal": reveal,
                "learned_buyers_final": memory.buyers,
                "round_traces": round_traces,
                "round_results": all_rounds,
            }
            sc = (reveal or {}).get("score", {})
            print(
                f"🏁 GAME {game_idx} FINISHED | PR={sc.get('pr')} | PR_Score={sc.get('pr_score')} | "
                f"Appraisal={sc.get('appraisal_score')} | Total={sc.get('total')} | Rank={sc.get('rank')} | "
                f"Remaining_kg={state['standings'].get('kg_remaining')}",
                flush=True,
            )
            return record

        left = state.get("seconds_left") or 5
        time.sleep(min(5.0, max(1.0, left + 0.3)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=3)
    args = parser.parse_args()

    starter_main.load_env(HERE / ".env")
    api = Market(os.environ["MARKET_URL"], os.environ["TEAM_KEY"])
    llm = LLM(
        os.environ["LLM_BASE_URL"],
        os.environ["LLM_MODEL"],
        os.environ.get("LLM_API_KEY", ""),
        timeout=float(os.environ.get("LLM_TIMEOUT", "60")),
        thinking=os.environ.get("LLM_THINKING") == "1",
        temperature=float(os.environ.get("LLM_TEMPERATURE", "0.25")),
    )

    info = api.calendar()
    rules = info["rules"]
    calendar = info["calendar"]
    LOGS.mkdir(exist_ok=True)
    out_file = LOGS / "sandbox_eval_3games.json"

    all_games = []
    for g in range(1, args.games + 1):
        rec = run_one_game(api, llm, rules, calendar, g)
        all_games.append(rec)
        out_file.write_text(json.dumps(all_games, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n==================== ALL GAMES COMPLETE ====================", flush=True)
    for rec in all_games:
        sc = (rec.get("reveal") or {}).get("score", {})
        st = rec["standings"]
        print(
            f"Game {rec['game']}: PR={sc.get('pr')} | Total={sc.get('total')} | Rank={sc.get('rank')} | "
            f"Sold={st['kg_sold']}/{st['kg_total']} kg | Revenue={st['revenue_thb']:,.2f} THB"
        )


if __name__ == "__main__":
    main()
