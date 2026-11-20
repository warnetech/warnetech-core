#!/usr/bin/env bash
# proot_debian_setup.sh — Set up proot Debian environment for warnetech-core.
#
# Idempotent setup: safe to run multiple times. Checks conditions before making changes.
# Supports: Termux (proot-distro), native Linux systems.
#
# Usage:
#     bash scripts/proot_debian_setup.sh
#
# After running, verify with:
#     warnetech doctor
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV="${WARNETECH_VENV:-${HOME}/.venvs/warnetech}"

log() {
  echo "==> $*" >&2
}

error() {
  echo "error: $*" >&2
  return 1
}

# Check if running in Termux
if [ -n "${TERMUX_VERSION:-}" ]; then
  log "Detected Termux environment"

  # Verify architecture
  case "$(uname -m)" in
    aarch64|arm64|x86_64) ;;
    *) error "Unsupported architecture '$(uname -m)': 64-bit required"; exit 1 ;;
  esac

  # Install proot-distro if needed
  if ! command -v proot-distro &>/dev/null; then
    log "Installing proot-distro"
    pkg update -y >/dev/null 2>&1 || true
    pkg install -y proot-distro
  fi

  # Install Debian if needed
  if ! proot-distro list --installed 2>/dev/null | grep -qw debian; then
    log "Installing Debian guest"
    proot-distro install debian
  fi

  # Re-run inside Debian
  log "Re-running setup inside Debian guest"
  exec proot-distro login debian --bind "${REPO_ROOT}:/root/warnetech-core" -- \
    bash /root/warnetech-core/scripts/proot_debian_setup.sh
fi

# Verify we're in a Debian-like environment (Termux or native Linux)
if [ ! -r /etc/os-release ] || ! grep -qiE '^ID(_LIKE)?=.*(debian|ubuntu|termux)' /etc/os-release; then
  error "This script must run in Termux, Debian, or Ubuntu. Detected: $(cat /etc/os-release | grep '^PRETTY_NAME' | cut -d= -f2- 2>/dev/null || echo 'unknown')"
  exit 1
fi

log "Setting up warnetech-core in $(hostname)"

# Check Python version
bits="$(python3 -c 'import struct; print(struct.calcsize("P") * 8)' 2>/dev/null || echo 0)"
if [ "$bits" != "64" ]; then
  error "Python is ${bits}-bit; 64-bit required"
  exit 1
fi

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  error "Python 3.10+ required (found: $(python3 --version 2>/dev/null || echo 'unknown'))"
  exit 1
fi

# Install system packages if needed
log "Checking system dependencies"
sudo=""
if [ "$(id -u)" -ne 0 ]; then sudo="sudo"; fi

for pkg in python3 python3-venv git; do
  if ! dpkg -l | grep -q "^ii  $pkg"; then
    log "Installing system package: $pkg"
    if [ -n "$sudo" ]; then
      $sudo apt-get update >/dev/null 2>&1 || true
    fi
    $sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "$pkg"
  fi
done

# Create virtualenv if needed
if [ ! -d "$VENV" ]; then
  log "Creating virtualenv at $VENV"
  python3 -m venv "$VENV"
fi

# Activate venv and install warnetech-core
# shellcheck disable=SC1091
. "$VENV/bin/activate"

log "Upgrading pip"
python -m pip install --quiet --upgrade pip setuptools wheel

log "Installing warnetech-core from $REPO_ROOT"
cd "$REPO_ROOT"
python -m pip install --quiet -e ".[dev]"

log "Setup complete. Run 'warnetech doctor' to verify."
log "To activate virtualenv: source ${VENV}/bin/activate"
