#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x "venv/bin/python" ]]; then
  echo "Virtual environment not found. Create it with: python -m venv venv"
  exit 1
fi

if [[ ! -f ".env" && -f ".env.example" ]]; then
  cp .env.example .env
fi

# OFFLINE_MODE comes from .env only. Real DATABASE_URL → live login.

export FLASK_DEBUG="${FLASK_DEBUG:-true}"
export FLASK_HOST="${FLASK_HOST:-0.0.0.0}"
export FLASK_PORT="${FLASK_PORT:-5000}"

mkdir -p instance/logs instance/generated_reports instance/temp/inspections

echo "Starting Municipal Road Inspection System..."
exec venv/bin/python run.py
