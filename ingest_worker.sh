#!/bin/bash
# Processes the ingest queue: reads queued video IDs and runs collect.py for each.
# On success, regenerates the export archive.
# Schedule frequently, e.g. every minute: * * * * * /path/to/ingest_worker.sh
#
# Queue line format: "<video_id>" or "<video_id>|<attempts>".
# On collect.py failure (e.g. upstream 429, network blip), the entry is
# re-appended to the live queue with attempts+1, up to MAX_RETRIES, so videos
# are never silently dropped on transient errors.

QUEUE="${INGEST_QUEUE:-/home/alain/repos/youtube-abo-summarizer/data/ingest_queue.txt}"
REPO="/home/alain/repos/youtube-abo-summarizer"
COLLECT="$REPO/collect.py"
EXPORT="$REPO/export.py"
PYTHON="$REPO/.venv/bin/python3"
LOG="$REPO/data/ingest_worker.log"
EXPORT_OUTPUT="$REPO/full_archive.html"
SYNC_URL="https://imap.parkautomat.net/sync/"
MAX_RETRIES=100

if [ ! -s "$QUEUE" ]; then
    exit 0
fi

# Atomically claim the queue. New entries written by the sync server after
# this rename land in a fresh QUEUE file and will be picked up next tick.
TMPFILE=$(mktemp)
mv "$QUEUE" "$TMPFILE"

success=0
while IFS= read -r line; do
    [ -z "$line" ] && continue
    video_id="${line%%|*}"
    attempts="${line#*|}"
    if [ "$attempts" = "$line" ]; then
        attempts=0
    fi

    echo "[$(date -Iseconds)] ingest $video_id (attempt $((attempts + 1)))" >> "$LOG"
    if "$PYTHON" "$COLLECT" --video "$video_id" >> "$LOG" 2>&1; then
        success=1
        echo "[$(date -Iseconds)] done $video_id (ok)" >> "$LOG"
    else
        rc=$?
        next=$((attempts + 1))
        if [ "$next" -lt "$MAX_RETRIES" ]; then
            # Re-append to the live queue (append is safe; sync server also appends).
            echo "${video_id}|${next}" >> "$QUEUE"
            echo "[$(date -Iseconds)] fail $video_id (exit $rc) — requeued as attempt $((next + 1))/$MAX_RETRIES" >> "$LOG"
        else
            echo "[$(date -Iseconds)] fail $video_id (exit $rc) — giving up after $MAX_RETRIES attempts" >> "$LOG"
        fi
    fi
done < "$TMPFILE"

rm -f "$TMPFILE"

if [ "$success" -eq 1 ]; then
    echo "[$(date -Iseconds)] re-export" >> "$LOG"
    cd "$REPO" && "$PYTHON" "$EXPORT" --all --sync-url "$SYNC_URL" --output "$EXPORT_OUTPUT" >> "$LOG" 2>&1
fi
