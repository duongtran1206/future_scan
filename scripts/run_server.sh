#!/usr/bin/env bash
set -euo pipefail

if [ ! -d ".venv" ]; then
  echo "Missing .venv. Run scripts/install_ubuntu.sh first."
  exit 1
fi

if [ ! -f ".env" ]; then
  echo "Missing .env. Copy .env.example to .env and fill Telegram values."
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

source .venv/bin/activate

INTERVAL_MINUTES="${INTERVAL_MINUTES:-10}"
MAX_COINS="${MAX_COINS:-0}"

python main.py --interval-minutes "${INTERVAL_MINUTES}" --max-coins "${MAX_COINS}"
