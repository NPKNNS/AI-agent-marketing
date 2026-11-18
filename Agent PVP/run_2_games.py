"""CLI Runner for Stage 3 56-Team AI Training Space (supports `--games N` or `--minutes M` / `--hours H`)."""
import argparse
import json
import time
import urllib.request


def post_json(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"http://localhost:8000{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def get_json(path: str) -> dict:
    with urllib.request.urlopen(f"http://localhost:8000{path}", timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def run_n_games(n: int) -> None:
    for g in range(1, n + 1):
        st = post_json("/v1/pvp/start", {"rounds": 15, "practice_rounds": 2, "include_specialty": True, "sample_noise": True})
        g_num = st.get("game_number", g)
        cs = st.get("category_summary", {})
        print(
            f"\n==================== STARTING GAME #{g_num} ({g}/{n}) | "
            f"Randomized Bots: R={(cs.get('random') or {}).get('count',0)} "
            f"L={(cs.get('stay_low') or {}).get('count',0)} "
            f"H={(cs.get('stay_high') or {}).get('count',0)} "
            f"M={(cs.get('market_price') or {}).get('count',0)} "
            f"A={(cs.get('adaptive') or {}).get('count',0)} ====================",
            flush=True,
        )
        last_rid = None
        while True:
            time.sleep(1.0)
            st = get_json("/v1/pvp/status")
            cr = st.get("current_round") or {}
            rid = cr.get("round_id")
            if rid != last_rid and rid is not None:
                last_rid = rid
                q1 = st.get("qwen_status", {})
                print(f"  [Game #{g_num}] Round {rid:<3} ({cr.get('calendar')}, w={cr.get('demand_weight')}) | T01 state={q1.get('state')}", flush=True)
            if st.get("phase") == "CLOSED":
                break

        hs = get_json("/v1/pvp/history/summary")
        latest_g = hs["games"][0] if hs.get("games") else {}
        print(f"\n--- GAME #{g_num} LOGGED TO CSV ({latest_g.get('game_start_time')} -> {latest_g.get('game_end_time')}, {latest_g.get('duration_sec')}s) ---", flush=True)
        print(f"  T01 (AI Refined) : Rank #{latest_g.get('t01_rank')}/56 | PR={latest_g.get('t01_pr'):.4f} | Score={latest_g.get('t01_total_score'):.2f} | Sold={latest_g.get('t01_bags_sold')}/45b", flush=True)
        print(f"  T02 (Locked)     : Rank #{latest_g.get('t02_rank')}/56 | PR={latest_g.get('t02_pr'):.4f} | Score={latest_g.get('t02_total_score'):.2f} | Sold={latest_g.get('t02_bags_sold')}/45b", flush=True)
        print(f"  #1 Winner        : {latest_g.get('winner_team_id')} ({latest_g.get('winner_strategy')}) PR={latest_g.get('winner_pr'):.4f}", flush=True)
        print(f"  CSV Files Ready  : {[f['name'] for f in hs.get('csv_files', [])[:6]]}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=2, help="Number of games to run")
    parser.add_argument("--hours", type=float, default=None, help="Start server-side time-based loop for H hours (e.g. 2.0)")
    args = parser.parse_args()

    if args.hours is not None:
        lp = post_json("/v1/pvp/loop/start", {"duration_minutes": args.hours * 60.0})
        print(f"Started server-side timed training loop for {args.hours} hours: {lp}", flush=True)
    else:
        run_n_games(args.games)


if __name__ == "__main__":
    main()
