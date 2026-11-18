import json
from pathlib import Path

data = json.loads((Path(__file__).parent / "logs" / "sandbox_eval_3games.json").read_text(encoding="utf-8"))
for g in data:
    sc = g["reveal"]["score"]
    st = g["standings"]
    print(f"\n================ GAME {g['game']} | PR={sc['pr']} | Rank={sc['rank']} | Total={sc['total']} | Sold={st['kg_sold']}/{st['kg_total']}kg ================")
    for l in g["reveal"]["lots"]:
        print(f"  Lot {l['lot_id']}: grade={l['true_grade']} TV={l['true_value_per_kg']} true={l['true']}")
    for trace in g["round_traces"]:
        rid = trace["round_id"]
        res = g["round_results"].get(rid, {})
        my_res = {(o["rfq_id"], o["lot_id"]): o for o in res.get("my_offers", [])}
        rfq_buyer = {row["rfq_id"]: f"{row['buyer_type']}:{row['buyer']}" for row in res.get("market_summary", [])}
        print(f"  --- {rid} ({trace['phase_policy']}, {trace['source']}, {trace['llm_seconds']}s) ---")
        print(f"    Notes: {trace['notes']}")
        for o in trace["offers_sent"]:
            r = my_res.get((o["rfq_id"], o["lot_id"]), {})
            bname = rfq_buyer.get(o["rfq_id"], "?")
            print(
                f"    {o['rfq_id']} ({bname}) <- {o['lot_id']} | {o['bags']}b @ {o['price_per_kg']} "
                f"(x{o['markup']}) -> {r.get('status', '?')} filled={r.get('filled_kg', 0)//60}b"
            )
    print("  Learned Buyers Final:")
    for bname, b in g["learned_buyers_final"].items():
        print(
            f"    {b['buyer_id']} {bname} ({b['buyer_type']}): est={b['est_ceiling']} "
            f"ok>={b['lower_bound']} rej>={b['upper_bound']} "
            f"acc={b['accepted_count']} outbid={b['outbid_count']} above={b['above_count']}"
        )
