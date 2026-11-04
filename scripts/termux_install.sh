#!/usr/bin/env bash
# Install warnetech-core in Termux on a 64-bit Android device (arm64-v8a).
#
# Run from the repository root, inside Termux (the F-Droid / GitHub build, not the
# discontinued Play Store one):
#
#     bash scripts/termux_install.sh            # core library + CLI
#     bash scripts/termux_install.sh --service  # also the FastAPI service extra
#
# Nothing here needs root. Everything is installed into Termux's own prefix and a
# virtualenv in $HOME. Run `warnetech doctor` afterwards to see what was found.
#
# STATUS: written against Termux's documented package names, and checked statically
# (bash -n, strict mode). It has NOT been run on a real Android device by the author.
set -euo pipefail

want_service=0
for arg in "$@"; do
  case "$arg" in
    --service) want_service=1 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ -z "${TERMUX_VERSION:-}" ] && [ ! -d "${PREFIX:-/nonexistent}/bin" ]; then
  echo "This script is for Termux on Android. Use scripts/bootstrap_repo.sh elsewhere." >&2
  exit 1
fi

arch="$(uname -m)"
case "$arch" in
  aarch64|arm64|x86_64) ;;
  *)
    echo "Unsupported architecture '$arch': a 64-bit device (arm64-v8a or x86_64) is required." >&2
    echo "On 32-bit ARM (armv7l) pydantic-core has no wheels and models will not fit in memory." >&2
    exit 1
    ;;
esac

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv="${WARNETECH_VENV:-$HOME/.venvs/warnetech}"

echo "==> Installing build prerequisites (pkg)"
pkg update -y
# pydantic-core is Rust: with no Android wheel on PyPI it is compiled from source, which
# needs rust, clang, make and the libffi/openssl headers. This is the slow step.
pkg install -y python rust clang make pkg-config libffi openssl git

bits="$(python -c 'import struct; print(struct.calcsize("P") * 8)')"
if [ "$bits" != "64" ]; then
  echo "Termux's python is ${bits}-bit; a 64-bit Python is required." >&2
  exit 1
fi

echo "==> Creating virtualenv at $venv"
python -m venv "$venv"
# shellcheck disable=SC1091
. "$venv/bin/activate"
python -m pip install --upgrade pip

# Termux exports ANDROID_API_LEVEL for native builds; maturin (pydantic-core) reads it.
export ANDROID_API_LEVEL="${ANDROID_API_LEVEL:-24}"

extras=""
if [ "$want_service" -eq 1 ]; then extras="[service]"; fi
echo "==> pip install -e .${extras}  (compiles pydantic-core; expect several minutes)"
python -m pip install -e "${repo_root}${extras}"

echo "==> Offline smoke test"
WARNETECH_OFFLINE=1 warnetech ask "hello from android"
warnetech doctor || true

cat <<MSG

Done. Activate later with:  . "$venv/bin/activate"
Run a model locally by pointing at an Ollama server you can reach (OLLAMA_HOST), or use
the echo provider with WARNETECH_OFFLINE=1. Ollama itself is not packaged for Termux here.
MSG
