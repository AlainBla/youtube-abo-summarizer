#!/usr/bin/env bash
# Run the collection phase: fetch new videos, transcripts, and summaries.
# Schedule this frequently (e.g. every hour or every 15 minutes via cron).
#
# When the run actually added videos (collect.py exits with EXIT_NEW_VIDEOS,
# 10), the export archive is regenerated right away so the new videos show up
# without waiting for anything else -- and so the archive's update banner only
# fires when there is something new to announce.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
EXPORT_OUTPUT="${EXPORT_OUTPUT:-$REPO/yt.html}"
SYNC_URL="${SYNC_URL:-https://imap.parkautomat.net/sync/}"
EXIT_NEW_VIDEOS=10

cd "$REPO"
source .venv/bin/activate

rc=0
python3 collect.py --auth --hours 4 >> "$REPO/cron.log" 2>&1 || rc=$?

if [ "$rc" -eq "$EXIT_NEW_VIDEOS" ]; then
    python3 export.py --all --sync-url "$SYNC_URL" --output "$EXPORT_OUTPUT" >> "$REPO/cron.log" 2>&1
elif [ "$rc" -ne 0 ]; then
    exit "$rc"
fi
