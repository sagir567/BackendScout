#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"
mkdir -p data/logs data/tasks

LOCK_DIR="data/tasks/worker.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT

/opt/homebrew/bin/uv --cache-dir .uv-cache run --no-editable backend-scout tasks worker-once \
  >> data/logs/task-worker.log 2>&1
