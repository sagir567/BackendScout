#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"
mkdir -p data/logs

exec /opt/homebrew/bin/uv run --no-editable backend-scout telegram listen --tracker production --timeout-seconds 30 \
  >> data/logs/telegram-listener.log 2>&1
