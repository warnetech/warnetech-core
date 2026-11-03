#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="warnetech_core_service"
CHECK_INTERVAL=30

echo "[*] Initializing health monitor for container: $CONTAINER_NAME"

while true; do
    # Check if container exists and get its runtime status
    if ! docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
        echo "[!] Container $CONTAINER_NAME does not exist. Waiting..."
        sleep "$CHECK_INTERVAL"
        continue
    fi

    STATUS=$(docker inspect --format='{{.State.Status}}' "$CONTAINER_NAME")
    
    if [ "$STATUS" = "running" ]; then
        # Verify container health status if a healthcheck is configured
        HEALTH=$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER_NAME")
        if [ "$HEALTH" = "unhealthy" ]; then
            echo "[!] Container $CONTAINER_NAME reported UNHEALTHY status. Restarting..."
            docker restart "$CONTAINER_NAME"
        else
            echo "[*] Container $CONTAINER_NAME is healthy and running."
        fi
    else
        echo "[!] Container $CONTAINER_NAME status is '$STATUS'. Attempting recovery..."
        docker compose up -d || docker start "$CONTAINER_NAME" || true
    fi

    sleep "$CHECK_INTERVAL"
done
