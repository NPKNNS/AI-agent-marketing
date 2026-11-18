import json
from pathlib import Path

logs_dir = Path(__file__).parent / "logs"
for f in sorted(logs_dir.glob("pvp_game_*_full.json")):
    d = json.loads(f.read_text(encoding="utf-8"))
    print(f"\n==================== {f.name} (config={d['config']}) ====================")
    print("LEADERBOARD:")
    for row in d["leaderboard"]:
        print(
            f"  #{row['rank']:>2} {row['team_id']} {row['name']:<38} | PR={row['pr']:.4f} | "
            f"Total={row['total']:>6.2f} | Sold={row['kg_sold']:>4}/{row['kg_total']}kg | Rev={row['revenue_thb']:>9,.0f}"
        )

    # Compare T01, T02, and Top 2 Bots round by round
    top_bots = [r["team_id"] for r in d["leaderboard"] if r["role"] == "bot"][:2]
    inspect_teams = ["T01", "T02"] + top_bots
    lot_tvs = {}
    for l in d["canonical_lots_t01"]:
        idx = l["lot_id"].split("-")[-1]
        lot_tvs[idx] = l["true_value_per_kg"]

    print(f"\nROUND-BY-ROUND COMPARISON ({', '.join(inspect_teams)}):")
    for rid in sorted(d["round_results_by_team"].keys(), key=lambda x: int(x[1:])):
        print(f"  --- {rid} ---")
        for tid in inspect_teams:
            my_offers = d["round_results_by_team"][rid].get(tid, {}).get("my_offers", [])
            sold_b = sum(o["filled_kg"] // 60 for o in my_offers)
            off_b = sum(o["kg"] // 60 for o in my_offers)
            items = []
            for o in my_offers:
                idx = o["lot_id"].split("-")[-1]
                mk = round(o["price_per_kg"] / lot_tvs[idx], 3)
                items.append(f"{o['rfq_id']}:L{idx}({o['kg']//60}b@{mk}x->{o['status']}[{o['filled_kg']//60}b])")
            print(f"    {tid} ({sold_b}/{off_b}b): " + (" | ".join(items) if items else "NO OFFERS"))

    print("\nQWEN (T02) TRACES:")
    for t in d["qwen_adaptive_llm_traces"]:
        print(f"  {t['round_id']} | src={t['source']} | {t['elapsed_s']}s | notes={t['notes']} | err={t.get('llm_error')}")
