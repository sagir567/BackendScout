#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"
mkdir -p data/logs

/opt/homebrew/bin/uv --cache-dir .uv-cache run --no-editable backend-scout collect run \
  --tracker production --write-notion --send-digest \
  >> data/logs/daily-collection.log 2>&1
