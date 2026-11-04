#!/usr/bin/env bash
set -euo pipefail

# warnetech.sh — shell wrapper for the warnetech-core engine.
# Loads .env (if present), then forwards all arguments to core/engine.py.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "${SCRIPT_DIR}/.env"
    set +a
fi

exec python3 "${SCRIPT_DIR}/core/engine.py" "$@"
