"""Head-to-head competition sandbox evaluation: T02 Gen 9 (3 games) vs T01 Gen 10 (3 games).

Loads the archived strategies directly from `Agent PVP/strategy_archive/` without
modifying any active or archived strategy files.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ARCHIVE = ROOT / "Agent PVP" / "strategy_archive"
LOGS = HERE / "logs"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import main as starter_main
from appraisal import appraisal_items, estimate_lots
from llm import LLM
from market import Market, MarketError


def load_module(mod_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(mod_name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_competitor(tag: str, gen_dir: Path, strat_file: str, mem_file: str):
    strat_mod = load_module(f"h2h_{tag}_strategy", gen_dir / strat_file)
    mem_mod = load_module(f"h2h_{tag}_memory", gen_dir / mem_file)
    return strat_mod, mem_mod


def run_one_game(
    api: Market,
    llm: LLM | None,
    strat_mod,
    mem_mod,
    agent_label: str,
    rules: dict,
    calendar: list,
    game_idx: int,
) -> dict:
    reset_resp = api.call("POST", "/v1/sandbox/reset")
    ts_str = datetime.now().strftime("%H:%M:%S")
    print(
        f"\n==================== [{agent_label}] GAME {game_idx}/3 STARTED ({ts_str}) | reset={reset_resp} ====================",
        flush=True,
    )

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
            print(f"[{agent_label} G{game_idx}] state_error: {e}", flush=True)
            time.sleep(3)
            continue

        lots = state.get("lots") or []
        lots_key = tuple(sorted((l["lot_id"], json.dumps(l["sample"], sort_keys=True)) for l in lots))
        if lots and lots_key != game_key:
            game_key = lots_key
            acted = set()
            tv = estimate_lots(rules, lots)
            tag = hashlib.sha1(repr(lots_key).encode()).hexdigest()[:8]
            mem_path = LOGS / f"memory-h2h-{agent_label}-g{game_idx}-{tag}.json"
            memory = mem_mod.Memory(mem_path)
            print(
                f"[{agent_label} G{game_idx}] new_game market={state.get('market')} lots={len(lots)} tv={tv}",
                flush=True,
            )

        phase = state.get("phase")

        if phase == "APPRAISAL" and not state.get("appraisal_submitted") and tv:
            try:
                api.appraise(appraisal_items(rules, lots, tv))
                print(f"[{agent_label} G{game_idx}] appraisal_sent", flush=True)
            except MarketError as e:
                print(f"[{agent_label} G{game_idx}] appraisal_error: {e}", flush=True)

        elif phase == "ROUND_OPEN" and not state.get("paused") and state.get("round") and state["round"]["round_id"] not in acted:
            rid = state["round"]["round_id"]
            acted.add(rid)
            if memory is not None:
                memory.record_results(state.get("last_results"))

            if any(o["round_id"] == rid for o in state.get("my_open_offers", [])):
                print(f"[{agent_label} G{game_idx}] {rid} already_sent", flush=True)
                continue

            ctx = strat_mod.Context(
                rules=rules,
                calendar=calendar,
                tv=tv,
                rng=rng,
                memory=memory,
                llm=llm,
                seconds_left=state.get("seconds_left") or 999,
            )
            t0 = time.time()
            try:
                offers, source = strat_mod.decide(state, ctx)
            except Exception as e:
                print(f"[{agent_label} G{game_idx}] {rid} strategy_crashed: {e!r}", flush=True)
                offers = strat_mod.guard(state, ctx, strat_mod.rule_plan(state, ctx, strat_mod.candidates(state, ctx)))
                source = "rules_crash_fallback"

            sent = starter_main.send_offers(api, offers) if offers else []
            if memory is not None:
                memory.record_sent(rid, offers, state, tv)
            elapsed = round(time.time() - t0, 2)
            pol_name = strat_mod.phase_policy(state, ctx).get("name", "?")
            bags_sent = sum(o["kg"] for o in sent) // 60
            avg_mk = (
                round(
                    sum((o["price_per_kg"] / tv[o["lot_id"]]) * o["kg"] for o in sent)
                    / max(1, sum(o["kg"] for o in sent)),
                    4,
                )
                if sent
                else 0.0
            )
            print(
                f"[{agent_label} G{game_idx}] {rid} ({pol_name}) | src={source} | offers={len(sent)} ({bags_sent}b) | "
                f"avg_mk={avg_mk:.4f} | dt={elapsed}s | left={state.get('seconds_left')}s",
                flush=True,
            )
            round_traces.append(
                {
                    "round_id": rid,
                    "phase_policy": pol_name,
                    "source": source,
                    "decision_seconds": elapsed,
                    "notes": list(ctx.notes),
                    "offers_sent": [
                        {
                            **o,
                            "bags": o["kg"] // 60,
                            "markup": round(o["price_per_kg"] / tv[o["lot_id"]], 4),
                        }
                        for o in offers
                    ],
                }
            )

        elif phase == "CLOSED" and game_key is not None and "closed" not in acted:
            acted.add("closed")
            if memory is not None:
                memory.record_results(state.get("last_results"))

            time.sleep(1.5)
            reveal = None
            try:
                reveal = api.call("GET", "/v1/sandbox/reveal")
            except Exception as e:
                print(f"[{agent_label} G{game_idx}] reveal_error: {e}", flush=True)

            all_rounds = {}
            for rnum in range(1, rules["timeline"]["rounds"] + 1):
                rid = f"R{rnum}"
                try:
                    time.sleep(0.25)
                    all_rounds[rid] = api.call("GET", "/v1/results", params={"round": rid})
                except Exception:
                    pass

            st = state.get("standings", {})
            sc = (reveal or {}).get("score", {})
            record = {
                "agent": agent_label,
                "game": game_idx,
                "finished_at": datetime.now().isoformat(),
                "duration_s": round(time.time() - start_ts, 1),
                "tv_estimated": tv,
                "standings": st,
                "reveal": reveal,
                "round_traces": round_traces,
                "round_results": all_rounds,
            }
            print(
                f"FINISHED [{agent_label} G{game_idx}] | PR={sc.get('pr')} | PR_Score={sc.get('pr_score')} | "
                f"Appraisal={sc.get('appraisal_score')} | Total={sc.get('total')} | Rank={sc.get('rank')} | "
                f"Sold={st.get('kg_sold')}/{st.get('kg_total')}kg ({st.get('kg_sold', 0)//60}/{st.get('kg_total', 0)//60}b) | "
                f"Rev={st.get('revenue_thb', 0):,.2f} THB | dur={record['duration_s']}s",
                flush=True,
            )
            return record

        left = state.get("seconds_left") or 5
        time.sleep(min(5.0, max(1.0, float(left) + 0.3)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=3, help="Games per strategy")
    parser.add_argument("--only", choices=["all", "t02_gen9", "t01_gen10"], default="all")
    args = parser.parse_args()

    starter_main.load_env(HERE / ".env")
    api = Market(os.environ["MARKET_URL"], os.environ["TEAM_KEY"])
    llm = LLM(
        os.environ["LLM_BASE_URL"],
        os.environ["LLM_MODEL"],
        os.environ.get("LLM_API_KEY", ""),
        timeout=float(os.environ.get("LLM_TIMEOUT", "45")),
        thinking=os.environ.get("LLM_THINKING") == "1",
        temperature=float(os.environ.get("LLM_TEMPERATURE", "0.25")),
    )

    info = api.calendar()
    rules = info["rules"]
    calendar = info["calendar"]
    print(f"Connected to {os.environ['MARKET_URL']} | timeline={rules.get('timeline')} | cal={calendar}", flush=True)

    LOGS.mkdir(exist_ok=True)
    out_file = LOGS / "sandbox_head2head_t02g9_vs_t01g10.json"

    t02_strat, t02_mem = load_competitor("t02_gen9", ARCHIVE / "gen_9", "t02_strategy.py", "t02_memory.py")
    t01_strat, t01_mem = load_competitor("t01_gen10", ARCHIVE / "gen_10", "t01_strategy.py", "t01_memory.py")

    results = []
    if out_file.exists() and args.only != "all":
        try:
            results = json.loads(out_file.read_text(encoding="utf-8"))
        except Exception:
            results = []

    schedule = []
    if args.only in ("all", "t02_gen9"):
        for g in range(1, args.games + 1):
            schedule.append(("T02_Gen9", None, t02_strat, t02_mem, g))
    if args.only in ("all", "t01_gen10"):
        for g in range(1, args.games + 1):
            schedule.append(("T01_Gen10", llm, t01_strat, t01_mem, g))

    for label, use_llm, s_mod, m_mod, g_idx in schedule:
        rec = run_one_game(api, use_llm, s_mod, m_mod, label, rules, calendar, g_idx)
        results.append(rec)
        out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n==================== HEAD-TO-HEAD SUMMARY ====================", flush=True)
    for label in ("T02_Gen9", "T01_Gen10"):
        grp = [r for r in results if r["agent"] == label]
        if not grp:
            continue
        prs = [((r.get("reveal") or {}).get("score") or {}).get("pr", 0.0) for r in grp]
        totals = [((r.get("reveal") or {}).get("score") or {}).get("total", 0.0) for r in grp]
        ranks = [((r.get("reveal") or {}).get("score") or {}).get("rank", 99) for r in grp]
        solds = [r["standings"].get("kg_sold", 0) / max(1, r["standings"].get("kg_total", 1)) * 100 for r in grp]
        print(
            f"{label}: n={len(grp)} | avg_PR={sum(prs)/len(prs):.4f} | avg_Total={sum(totals)/len(totals):.2f} | "
            f"avg_Rank=#{sum(ranks)/len(ranks):.2f} | avg_Sold={sum(solds)/len(solds):.1f}% | "
            f"ranks={ranks} | prs={prs}",
            flush=True,
        )


if __name__ == "__main__":
    main()
