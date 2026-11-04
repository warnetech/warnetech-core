#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/warnetech/warnetech-core.git"

echo "[*] Initializing local Git repository..."
git init

echo "[*] Setting remote repository URL..."
git remote remove origin 2>/dev/null || true
git remote add origin "$REPO_URL"

echo "[*] Staging all repository files..."
git add .

echo "[*] Creating initial version commit..."
git commit -m "feat: initialize warnetech-core AGI framework architecture"

echo "[*] Pushing code to GitHub (main branch)..."
git branch -M main
git push -u origin main

echo "[*] Repository successfully initialized and pushed to GitHub."
