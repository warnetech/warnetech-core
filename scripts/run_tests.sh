#!/usr/bin/env bash
set -euo pipefail

echo "[*] Initializing test environment..."
export PYTHONPATH=.

if [ ! -d "venv" ]; then
    echo "[*] Creating virtual environment..."
    python3 -m venv venv
fi

echo "[*] Activating virtual environment..."
source venv/bin/activate

echo "[*] Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt pytest pytest-asyncio

echo "[*] Executing pytest test suite..."
pytest -v --tb=short

echo "[*] Test execution completed successfully."
