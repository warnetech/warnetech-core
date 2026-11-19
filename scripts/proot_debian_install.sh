#!/usr/bin/env bash
# Install warnetech-core in a Debian guest under proot-distro on Android (Termux).
#
# Run from the repository root. In Termux it sets up Debian and re-runs itself inside it;
# inside Debian it installs the CLI into a virtualenv:
#
#     bash scripts/proot_debian_install.sh            # core library + CLI
#     bash scripts/proot_debian_install.sh --service  # also the FastAPI service extra
#
# Debian is glibc, so pip uses prebuilt aarch64 wheels (no Rust build, unlike native
# Termux). No root needed on the phone. Run `warnetech doctor` afterwards.
#
# STATUS: checked with bash -n and the guest steps on Linux x86_64. The Termux->proot
# hand-off has NOT been run on a real Android device by the author.
set -euo pipefail

want_service=0
for arg in "$@"; do
  case "$arg" in
    --service) want_service=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script_args=()
if [ "$want_service" -eq 1 ]; then script_args+=(--service); fi

# --- Host side: Termux ------------------------------------------------------------
if [ -n "${TERMUX_VERSION:-}" ]; then
  case "$(uname -m)" in
    aarch64|arm64|x86_64) ;;
    *) echo "Unsupported architecture '$(uname -m)': a 64-bit device is required." >&2; exit 1 ;;
  esac
  echo "==> Installing proot-distro and Debian"
  pkg update -y
  pkg install -y proot-distro
  if ! proot-distro list --installed 2>/dev/null | grep -qw debian; then
    proot-distro install debian
  fi
  echo "==> Re-running inside Debian"
  exec proot-distro login debian --bind "$repo_root:/root/warnetech-core" -- \
    bash /root/warnetech-core/scripts/proot_debian_install.sh "${script_args[@]}"
fi

# --- Guest side: Debian -----------------------------------------------------------
if [ ! -r /etc/os-release ] || ! grep -qiE '^ID(_LIKE)?=.*(debian|ubuntu)' /etc/os-release; then
  echo "Run this in Termux (it sets up Debian) or inside a Debian/Ubuntu guest." >&2
  echo "For native Termux use scripts/termux_install.sh." >&2
  exit 1
fi

venv="${WARNETECH_VENV:-$HOME/.venvs/warnetech}"
sudo=""
if [ "$(id -u)" -ne 0 ]; then sudo="sudo"; fi

echo "==> Installing Debian packages"
$sudo apt-get update
$sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv git

bits="$(python3 -c 'import struct; print(struct.calcsize("P") * 8)')"
if [ "$bits" != "64" ]; then
  echo "Python is ${bits}-bit; a 64-bit Python is required." >&2
  exit 1
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "Python 3.10 or newer is required; this Debian ships $(python3 --version)." >&2
  exit 1
}

echo "==> Creating virtualenv at $venv"
python3 -m venv "$venv"
# shellcheck disable=SC1091
. "$venv/bin/activate"
python -m pip install --upgrade pip

extras=""
if [ "$want_service" -eq 1 ]; then extras="[service]"; fi
echo "==> pip install -e .${extras}"
python -m pip install -e "${repo_root}${extras}"

echo "==> Offline smoke test"
WARNETECH_OFFLINE=1 warnetech ask "hello from debian"
warnetech doctor || true

cat <<MSG

Done. In Termux, enter the guest with:
    proot-distro login debian --bind "\$PWD:/root/warnetech-core"
then:  . "$venv/bin/activate"
Local models: point OLLAMA_HOST at a machine you can reach, or use WARNETECH_OFFLINE=1.
Cloud providers: put the keys in .env (see .env.example).
MSG
