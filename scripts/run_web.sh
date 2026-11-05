#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"
mkdir -p data/logs
exec "$PROJECT_ROOT/.venv/bin/backend-scout" web serve --tracker production \
  >> data/logs/web-server.log 2>&1
