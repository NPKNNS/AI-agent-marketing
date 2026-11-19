"""Runs a single generation for up to `--max-games` (100) or `--max-minutes` (60),
and stops cleanly if `--until` ("YYYY-MM-DD HH:MM:SS", e.g. 2026-11-19 07:00:00) is reached.
"""
import argparse
import json
import time
import urllib.request
from datetime import datetime


def post_json(path: str, payload: dict) -> dict:
    for attempt in range(5):
        try:
            req = urllib.request.Request(
                f"http://localhost:8000{path}",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if attempt == 4:
                raise
            time.sleep(2.0 + attempt)


def get_json(path: str) -> dict:
    for attempt in range(6):
        try:
            with urllib.request.urlopen(f"http://localhost:8000{path}", timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if attempt == 5:
                raise
            time.sleep(2.0 + attempt)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gen", required=True, help="Generation tag (e.g. 'gen 5')")
    parser.add_argument("--max-games", type=int, default=100)
    parser.add_argument("--max-minutes", type=float, default=60.0)
    parser.add_argument("--until", default="2026-11-19 07:00:00")
    args = parser.parse_args()

    # Wait briefly for server if just launched
    for _ in range(15):
        try:
            get_json("/v1/pvp/status")
            break
        except Exception:
            time.sleep(1.0)

    post_json("/v1/pvp/generation", {"generation": args.gen})
    started = time.time()
    gen_deadline = started + args.max_minutes * 60.0
    hard_cutoff = datetime.strptime(args.until, "%Y-%m-%d %H:%M:%S").timestamp()
    deadline = min(gen_deadline, hard_cutoff)

    t01_prs, t02_prs, t01_rks, t02_rks = [], [], [], []
    games_done = 0

    print(f"=== STARTING {args.gen.upper()} | max_games={args.max_games} | max_min={args.max_minutes} | until={args.until} ===", flush=True)

    while games_done < args.max_games and time.time() < deadline:
        st = post_json("/v1/pvp/start", {"rounds": 15, "practice_rounds": 2, "include_specialty": True, "sample_noise": True})
        g_num = st.get("game_number", games_done + 1)
        while True:
            time.sleep(1.0)
            st = get_json("/v1/pvp/status")
            if st.get("phase") == "CLOSED":
                break
        games_done += 1
        lb = {r["team_id"]: r for r in st.get("leaderboard", [])}
        t1, t2 = lb.get("T01", {}), lb.get("T02", {})
        t01_prs.append(float(t1.get("pr", 0)))
        t02_prs.append(float(t2.get("pr", 0)))
        t01_rks.append(int(t1.get("rank", 56)))
        t02_rks.append(int(t2.get("rank", 56)))
        if games_done % 10 == 0 or games_done == 1:
            elapsed_m = (time.time() - started) / 60.0
            print(
                f"[{args.gen}] Game #{g_num} ({games_done}/{args.max_games}, {elapsed_m:.1f}m) | "
                f"T01=#{t1.get('rank')} PR={t1.get('pr'):.4f} ({t1.get('kg_sold',0)//60}b) | "
                f"T02=#{t2.get('rank')} PR={t2.get('pr'):.4f} ({t2.get('kg_sold',0)//60}b)",
                flush=True,
            )

    n = max(1, games_done)
    print(
        f"\n=== FINISHED {args.gen.upper()}: {games_done} games in {(time.time()-started)/60.0:.1f}m ===\n"
        f"  T01: avg_PR={sum(t01_prs)/n:.4f} avg_Rank=#{sum(t01_rks)/n:.2f} Top3={sum(1 for r in t01_rks if r<=3)*100/n:.1f}%\n"
        f"  T02: avg_PR={sum(t02_prs)/n:.4f} avg_Rank=#{sum(t02_rks)/n:.2f} Top3={sum(1 for r in t02_rks if r<=3)*100/n:.1f}%",
        flush=True,
    )


if __name__ == "__main__":
    main()
