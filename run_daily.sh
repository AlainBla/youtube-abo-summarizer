#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
OUTPUT="$REPO/summary_$(date +%Y-%m-%d).html"
TO="alain@parkautomat.net"

cd "$REPO"

source .venv/bin/activate

python3 summarize.py --auth --output "$OUTPUT" --skip-empty

python3 "$REPO/send_mail.py" "YouTube Summary $(date '+%Y-%m-%d %H:%M')" "$TO" "$OUTPUT"

# Keep only the last 7 daily files
find "$REPO" -maxdepth 1 -name "summary_*.html" -mtime +7 -delete
