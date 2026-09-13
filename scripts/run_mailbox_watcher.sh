#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"
mkdir -p data/logs data/mailbox

LOCK_DIR="data/mailbox/watcher.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT

/opt/homebrew/bin/uv --cache-dir .uv-cache run --no-editable backend-scout gmail watch-once \
  --tracker production --write-notion --send-digest \
  >> data/logs/mailbox-watcher.log 2>&1
