#!/usr/bin/env bash
# Render and send a 12-hour digest from the video store.
# Assumes collect.sh (or collect.py) is running separately on a frequent schedule.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
OUTPUT="$REPO/summary_$(date +%Y-%m-%d_%H-%M).html"
TO="alain@parkautomat.net"

cd "$REPO"
source .venv/bin/activate

python3 report.py --hours 12 --output "$OUTPUT" --skip-empty --send-to "$TO"

# Keep only the last 7 days of files
find "$REPO" -maxdepth 1 -name "summary_*_*-*.html" -mtime +7 -delete
