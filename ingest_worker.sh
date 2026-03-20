#!/bin/bash
# Processes the ingest queue: reads queued video IDs and runs collect.py for each.
# Schedule frequently, e.g. every minute: * * * * * /path/to/ingest_worker.sh

QUEUE="${INGEST_QUEUE:-/home/alain/repos/youtube-abo-summarizer/data/ingest_queue.txt}"
COLLECT="/home/alain/repos/youtube-abo-summarizer/collect.py"
PYTHON="/home/alain/repos/youtube-abo-summarizer/.venv/bin/python"
LOG="/home/alain/repos/youtube-abo-summarizer/data/ingest_worker.log"

if [ ! -s "$QUEUE" ]; then
    exit 0
fi

# Atomically claim the queue
TMPFILE=$(mktemp)
mv "$QUEUE" "$TMPFILE"

while IFS= read -r video_id; do
    [ -z "$video_id" ] && continue
    echo "[$(date -Iseconds)] ingest $video_id" >> "$LOG"
    "$PYTHON" "$COLLECT" --video "$video_id" >> "$LOG" 2>&1
    echo "[$(date -Iseconds)] done $video_id (exit $?)" >> "$LOG"
done < "$TMPFILE"

rm -f "$TMPFILE"
