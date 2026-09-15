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

REM Do not force OFFLINE_MODE here. run.py reads municipal_ai_system/.env
REM A real DATABASE_URL enables RDS login; placeholder URL stays offline.

if not defined FLASK_DEBUG set FLASK_DEBUG=true
if not defined FLASK_HOST set FLASK_HOST=0.0.0.0
if not defined FLASK_PORT set FLASK_PORT=5000

if not exist "app\instance\logs" mkdir "app\instance\logs"
if not exist "app\instance\generated_reports" mkdir "app\instance\generated_reports"
if not exist "app\instance\temp\inspections" mkdir "app\instance\temp\inspections"

echo Starting Municipal Road Inspection System...
"venv\Scripts\python.exe" run.py
endlocal
