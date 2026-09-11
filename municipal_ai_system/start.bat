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

set OFFLINE_MODE=true
set FLASK_DEBUG=true
set FLASK_HOST=0.0.0.0
set FLASK_PORT=5000

if not exist "app\instance\logs" mkdir "app\instance\logs"
if not exist "app\instance\generated_reports" mkdir "app\instance\generated_reports"
if not exist "app\instance\temp\inspections" mkdir "app\instance\temp\inspections"

echo Starting Municipal Road Inspection System (OFFLINE_MODE=%OFFLINE_MODE%)...
"venv\Scripts\python.exe" run.py
endlocal
