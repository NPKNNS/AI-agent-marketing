# ☕ Agent PVP Multiplayer LAN Arena (Humans vs Qwen 3.5 9B vs 8 Strategy Bots)

Local multiplayer clone of the official TESA Robusta Coffee Market (`/trade` UI + `/v1/*` REST API) for playing with teammates over a private LAN router and collecting full human + bot + LLM logs.

## 🚀 Quick Start (1 Command)
```powershell
.\start_pvp.ps1
```
The server prints your LAN URL (e.g. `http://192.168.1.x:8000/trade`). Share that URL with 1–10+ teammates on the same router.
*(If teammates cannot connect on Windows, allow port `8000` in Windows Defender Firewall on Private Network.)*

## 🎮 Multiplayer Features
- **Waiting Lobby (`WAITING` phase)**:
  - Teammates open `/trade`, automatically receive a unique seat (`H01`, `H02`, ..., `H10`), and can **enter/change their Team Name** before starting.
  - Press **Ready**, then any player/host clicks **🚀 เริ่มเกมทันที (Start Match)**.
- **Keep All AI Competitors**:
  - Every match includes **all joined Humans + `T02` Qwen 3.5 9B + `T03–T10` 8 Strategy Bots**.
- **Auto Market Appraisal**:
  - When a match begins, True Value (`TV`) appraisals are automatically calculated & submitted for human players—your job is simply to bid and press **`✓ ยืนยันจบรอบนี้ (Complete)`**.
- **90% Total Stock Demand & Zero Unsellable Stock**:
  - Total buyer demand across all rounds equals **90% of global stock across all active players + AI**.
  - All generated lots enforce **moisture `<= 13.8%`** (never `> 14.5%`) and **defect `<= 14.5%`**, ensuring every lot is sellable to at least `INSTANT` buyers.
- **Full Logs Saved Automatically**:
  - `logs/pvp_game_<id>_<timestamp>_full.json`
  - `logs/qwen_adaptive_tuning_dataset.jsonl`
