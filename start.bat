@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
  echo Virtual environment not found. Create it with: python -m venv venv
  exit /b 1
)

if not exist ".env" (
  if exist ".env.example" copy /Y ".env.example" ".env" >nul
)

REM OFFLINE_MODE comes from .env only. Real DATABASE_URL → live login.

if not defined FLASK_DEBUG set FLASK_DEBUG=true
if not defined FLASK_HOST set FLASK_HOST=0.0.0.0
if not defined FLASK_PORT set FLASK_PORT=5000

if not exist "instance\logs" mkdir "instance\logs"
if not exist "instance\generated_reports" mkdir "instance\generated_reports"
if not exist "instance\temp\inspections" mkdir "instance\temp\inspections"

echo Starting Municipal Road Inspection System...
"venv\Scripts\python.exe" run.py
endlocal
