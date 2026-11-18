"""Quick verification script for Self Config Agent Bot: tests Market API (1 GET) + Local Qwen3.5-9B LLM."""
import os
import json
from pathlib import Path
import strategy
from appraisal import estimate_lots
from llm import LLM
from market import Market

HERE = Path(__file__).parent
for line in (HERE / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

api = Market(os.environ["MARKET_URL"], os.environ["TEAM_KEY"])
cal = api.calendar()
state = api.state()
tv = estimate_lots(cal["rules"], state["lots"])
print(f"[Market OK] Team: {state['team_id']} | Phase: {state['phase']} | TVs: {tv}")

bot_llm = LLM(
    os.environ["LLM_BASE_URL"],
    os.environ["LLM_MODEL"],
    os.environ.get("LLM_API_KEY", ""),
    timeout=45,
    thinking=os.environ.get("LLM_THINKING") == "1"
)
sample_user = (
    "Round R1 (real), market calendar: ปกติ. Rounds left: 6.\n"
    "Plan to offer about 4 bags in total this round.\n"
    "Candidates: [{\"candidate\": 0, \"lot_id\": \"LOT-T16-4\", \"buyer\": \"4 ชุมพรคั่วบด\", "
    "\"buyer_type\": \"COMMERCIAL\", \"rfq_bags\": 10, \"max_bags\": 4, \"tv\": 117.69}]"
)
ans = bot_llm.ask_json(strategy.SYSTEM, sample_user, strategy.SCHEMA)
print(f"[LLM OK] Answered in {getattr(bot_llm, 'seconds', '?')}s -> {json.dumps(ans, ensure_ascii=False)}")
if bot_llm.last_error:
    print(f"[LLM Error] {bot_llm.last_error}")
