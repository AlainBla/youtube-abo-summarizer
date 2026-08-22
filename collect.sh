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

# Host-specific settings (paths, sync URL, digest recipient) live in cron.env,
# which is gitignored -- this repository is public. See cron.env.example.
# Anything already set in the environment can be preserved there with the
# ${VAR:-default} form; a plain assignment in cron.env wins over the caller.
if [ -f "$REPO/cron.env" ]; then
    # shellcheck source=/dev/null
    . "$REPO/cron.env"
fi

EXPORT_OUTPUT="${EXPORT_OUTPUT:-$REPO/yt.html}"
SYNC_URL="${SYNC_URL:-}"
EXIT_NEW_VIDEOS=10

cd "$REPO"
source .venv/bin/activate

rc=0
python3 collect.py --auth --hours 4 >> "$REPO/cron.log" 2>&1 || rc=$?

if [ "$rc" -eq "$EXIT_NEW_VIDEOS" ]; then
    sync_args=()
    if [ -n "$SYNC_URL" ]; then
        sync_args=(--sync-url "$SYNC_URL")
    fi
    if [ -z "$SYNC_URL" ]; then
        # Losing this is silent and total: the export drops the whole sync UI
        # (login, account display, ingest button) instead of failing.
        echo "[$(date -Iseconds)] WARNING: SYNC_URL is unset -- exporting without sync support. Set it in cron.env." >> "$REPO/cron.log"
    fi

    python3 export.py --all ${sync_args[@]+"${sync_args[@]}"} --output "$EXPORT_OUTPUT" >> "$REPO/cron.log" 2>&1
elif [ "$rc" -ne 0 ]; then
    exit "$rc"
fi
