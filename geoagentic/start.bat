@echo off
REM Starts backend (port 8000) and frontend (port 5173) in two windows.
cd /d %~dp0
start "GeoAgentic backend" cmd /k "cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload --port 8000"
start "GeoAgentic frontend" cmd /k "cd frontend && npm install && npm run dev"
