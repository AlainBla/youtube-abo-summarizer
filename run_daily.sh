#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
OUTPUT="$REPO/summary_$(date +%Y-%m-%d).html"
TO="alain@parkautomat.net"

cd "$REPO"

python3 summarize.py --auth --output "$OUTPUT"

mutt -s "YouTube Summary $(date +%Y-%m-%d)" \
     -e "set content_type=text/html" \
     -- "$TO" < "$OUTPUT"

# Keep only the last 7 daily files
find "$REPO" -maxdepth 1 -name "summary_*.html" -mtime +7 -delete
