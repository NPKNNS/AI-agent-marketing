# Launch Multiplayer Agent PVP Arena (Competition Event LLM Gateway on :4000 + LAN Web Server on :8000)
$ErrorActionPreference = "Continue"

$env:LLM_BASE_URL = "http://161.246.199.191:4000/v1"
$env:LLM_MODEL = "event-llm"
$env:LLM_API_KEY = "sk-T16-MgkI9WNcvneRFQlqkxYzrrIu"
$env:LLM_TIMEOUT = "45"

Write-Host "Using Competition Event LLM Gateway: $env:LLM_BASE_URL (model=$env:LLM_MODEL)" -ForegroundColor Cyan
Write-Host "Starting Agent PVP Multiplayer LAN Server on port 8000..." -ForegroundColor Cyan
Start-Process "http://localhost:8000/trade"
python -u "$PSScriptRoot\server.py"
