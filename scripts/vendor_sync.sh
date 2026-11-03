#!/usr/bin/env bash
# Update one vendored repository from its upstream with `git subtree pull`.
#
# Usage: scripts/vendor_sync.sh <claude-code-assist|cli-ai-command-center-prd>
#
# Needs network access and a clean working tree. After it succeeds, copy the new
# `git-subtree-split` commit it prints into vendor/vendor.json.
set -euo pipefail

name="${1:?usage: vendor_sync.sh <claude-code-assist|cli-ai-command-center-prd>}"

case "$name" in
  claude-code-assist)
    prefix="vendor/claude-code-assist"
    url="https://github.com/warnetech/claude-code-assist"
    ref="claude/llm-integration-repo-1aicun"
    ;;
  cli-ai-command-center-prd)
    prefix="vendor/cli-ai-command-center-prd"
    url="https://github.com/warnetech/CLI-AI-Command-Center-PRD"
    ref="main"
    ;;
  *)
    echo "unknown vendored repository: $name" >&2
    exit 2
    ;;
esac

cd "$(git rev-parse --show-toplevel)" || exit 1

if [ -n "$(git status --porcelain)" ]; then
  echo "working tree is not clean; commit or stash first" >&2
  exit 1
fi

git subtree pull --prefix="$prefix" "$url" "$ref" --squash \
  -m "chore(vendor): update $name"

split="$(git log -10 --format=%B | grep -m1 "^git-subtree-split:" || true)"
echo "updated $name. Set \"commit\" for it in vendor/vendor.json to the value below."
echo "${split:-git-subtree-split not found in recent history}"
