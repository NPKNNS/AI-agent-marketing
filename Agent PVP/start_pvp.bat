@echo off
set LLM_BASE_URL=http://161.246.199.191:4000/v1
set LLM_MODEL=event-llm
set LLM_API_KEY=sk-T16-MgkI9WNcvneRFQlqkxYzrrIu
set LLM_TIMEOUT=45

echo Using Competition Event LLM Gateway: %LLM_BASE_URL% (model=%LLM_MODEL%)
echo Starting Stage 3 Agent PVP Server on port 8000...
start "" "http://localhost:8000/trade"
python -u "%~dp0server.py"
pause
