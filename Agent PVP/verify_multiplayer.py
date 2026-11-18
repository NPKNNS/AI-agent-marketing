"""Verify multiplayer LAN lobby, auto-appraisal, 90% demand, and zero unsellable lots."""
import sys
from pathlib import Path
import httpx

sys.path.insert(0, str(Path(__file__).parent))
from engine import PVPMarketEngine, passes_spec

# 1. Verify zero unsellable lots across 50 random generations (clean and noisy)
e = PVPMarketEngine(Path(__file__).parent / "logs")
inst_spec = {"max_defect_pct": 15.0, "max_moisture_pct": 14.0, "min_screen": None}
for noise in [False, True]:
    e.sample_noise = noise
    for _ in range(25):
        lots = e._generate_canonical_lots()
        for l in lots:
            assert l["true"]["moisture_pct"] <= 13.8 and l["sample"]["moisture_pct"] <= 13.8, l
            assert passes_spec(l["true"], inst_spec) and passes_spec(l["sample"], inst_spec), l
print("OK 1: Verified 50/50 lot sets (clean & noisy): zero moisture > 13.8%, 100% sellable!")

# 2. Verify live HTTP Multiplayer Lobby + Join + Rename + Auto-Appraisal + 90% Demand
c = httpx.Client(base_url="http://localhost:8000", timeout=10)
st0 = c.get("/v1/pvp/status").json()
assert st0["phase"] == "WAITING", st0
print("OK 2: Lobby active! LAN URLs:", st0["lan_urls"])

p1 = c.post("/v1/pvp/join", json={"team_name": "AlphaHuman", "university": "NU"}).json()
p2 = c.post("/v1/pvp/join", json={"team_name": "BetaHuman", "university": "NU"}).json()
p3 = c.post("/v1/pvp/join", json={"team_name": "GammaHuman", "university": "NU"}).json()

c.post("/v1/pvp/rename", headers={"Authorization": f"Bearer {p2['token']}"}, json={"team_name": "BetaRenamed"}).raise_for_status()
c.post("/v1/pvp/lobby_ready", headers={"Authorization": f"Bearer {p1['token']}"}, json={"ready": True}).raise_for_status()

start_st = c.post("/v1/pvp/start", json={"rounds": 6, "include_specialty": False, "sample_noise": False}).json()
assert start_st["phase"] == "ROUND_OPEN" and start_st["round_num"] == 1, start_st
assert start_st["total_teams"] == 12, start_st["total_teams"]  # 3 humans + 1 Qwen + 8 Bots
expected_90 = round(start_st["total_market_stock_bags"] * 0.90)
assert start_st["total_market_demand_bags"] == expected_90, (start_st["total_market_demand_bags"], expected_90)
print(
    f"OK 3: Match started with 12 teams (3 Humans + Qwen + 8 Bots)! "
    f"Stock={start_st['total_market_stock_bags']}b, 90% Demand={start_st['total_market_demand_bags']}b"
)

for p in [p1, p2, p3]:
    hs = c.get("/v1/state", headers={"Authorization": f"Bearer {p['token']}"}).json()
    assert hs["appraisal_submitted"] is True, hs
    assert len(hs["lots"]) == 4 and len(hs["rfqs"]) == 8
print("OK 4: Auto-Appraisal verified for all 3 humans; jumped straight into R1!")

# Clean up test players & return server cleanly to empty WAITING lobby
for p in [p1, p2, p3]:
    c.post("/v1/pvp/leave", headers={"Authorization": f"Bearer {p['token']}"}, json={"team_id": p["team_id"]})
c.post("/v1/pvp/reset_lobby", json={"rounds": 6, "include_specialty": False, "sample_noise": False})
print("OK 5: Reset cleanly back to WAITING lobby!")
