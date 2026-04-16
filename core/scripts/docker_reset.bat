@echo off
cd /d "%~dp0.."

echo [1/3] Stopping current stack...
docker compose down

echo [2/3] Starting restored stack...
docker compose up -d

echo [3/3] Open WebUI should be on http://localhost:8088
pause
