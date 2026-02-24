#!/usr/bin/env bash
# Run the collection phase: fetch new videos, transcripts, and summaries.
# Schedule this frequently (e.g. every hour or every 15 minutes via cron).
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"

cd "$REPO"
source .venv/bin/activate

python3 collect.py --auth --hours 4 >> "$REPO/cron.log" 2>&1
