#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"
mkdir -p data/logs data/runtime

exec /opt/homebrew/bin/uv --cache-dir .uv-cache run --no-editable backend-scout tasks worker \
  --lane browser >> data/logs/browser-worker.log 2>&1
