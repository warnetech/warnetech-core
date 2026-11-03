#!/usr/bin/env bash
set -euo pipefail

echo "[*] Pulling latest repository updates from origin/main..."
git pull origin main

echo "[*] Stopping active container services..."
docker compose down || true

echo "[*] Rebuilding Docker image and launching services..."
docker compose up --build -d

echo "[*] Displaying active container status and recent logs..."
docker compose ps
docker compose logs --tail=50
