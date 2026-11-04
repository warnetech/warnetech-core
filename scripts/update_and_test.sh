#!/usr/bin/env bash
set -euo pipefail

echo "[*] Fetching and pulling latest changes from origin/main..."
git pull origin main

echo "[*] Setting up/activating virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate

echo "[*] Ensuring dependencies are up to date..."
pip install --upgrade pip
pip install -r requirements.txt pytest pytest-asyncio

echo "[*] Executing test suite..."
pytest -v --tb=short

echo "[*] Repository update and test run completed successfully."
