#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
OUTPUT="$REPO/summary_$(date +%Y-%m-%d_%H-%M).html"
TO="alain@parkautomat.net"

cd "$REPO"

python3 summarize.py --auth --hours 12 --output "$OUTPUT"

mutt -s "YouTube Summary $(date +%Y-%m-%d %H:%M)" \
     -e "set content_type=text/html" \
     -- "$TO" < "$OUTPUT"

# Keep only the last 7 days of files
find "$REPO" -maxdepth 1 -name "summary_*_*-*.html" -mtime +7 -delete
