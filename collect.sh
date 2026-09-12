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
# Channel list used when OAuth is unusable. Gitignored, so a fresh clone does
# not have it -- put it on the host by hand (see README).
CHANNELS_FILE="${CHANNELS_FILE:-$REPO/channels.txt}"
# Upper bound per run. A run that outlives it is killed, so a stuck process
# cannot pile up every 30 minutes. Raise it while a backlog is being worked off.
COLLECT_TIMEOUT="${COLLECT_TIMEOUT:-30m}"
EXIT_NEW_VIDEOS=10
# collect.py signals "these credentials cannot work" with 11; the run is then
# repeated from the channel list, which needs neither token nor quota.
EXIT_AUTH_FAILED=11
# `timeout` reports 124 when TERM ended the run, and 128+9 = 137 when the
# process ignored TERM and -k had to escalate to KILL. Both mean "killed".
EXIT_TIMEOUT=124
EXIT_KILLED=137

cd "$REPO"
source .venv/bin/activate

rc=0
if [ -f "$REPO/token.pickle" ]; then
    timeout -k 30s "$COLLECT_TIMEOUT" python3 collect.py --auth --hours 4 >> "$REPO/cron.log" 2>&1 || rc=$?
    if [ "$rc" -eq "$EXIT_TIMEOUT" ] || [ "$rc" -eq "$EXIT_KILLED" ]; then
        # Nothing sane takes this long -- most likely the OAuth flow waiting for
        # a browser nobody will open. Treat it like unusable credentials.
        echo "[$(date -Iseconds)] ERROR: the subscription run exceeded $COLLECT_TIMEOUT and was killed (exit $rc)." >> "$REPO/cron.log"
        rc=$EXIT_AUTH_FAILED
    fi
else
    echo "[$(date -Iseconds)] WARNING: token.pickle is missing -- skipping OAuth." >> "$REPO/cron.log"
    rc=$EXIT_AUTH_FAILED
fi

if [ "$rc" -eq "$EXIT_AUTH_FAILED" ]; then
    if [ -f "$CHANNELS_FILE" ]; then
        echo "[$(date -Iseconds)] WARNING: OAuth unusable -- collecting from $CHANNELS_FILE instead. Renew the token; new subscriptions are invisible to this list." >> "$REPO/cron.log"
        rc=0
        timeout -k 30s "$COLLECT_TIMEOUT" python3 collect.py --file "$CHANNELS_FILE" --hours 4 >> "$REPO/cron.log" 2>&1 || rc=$?
        if [ "$rc" -eq "$EXIT_TIMEOUT" ] || [ "$rc" -eq "$EXIT_KILLED" ]; then
            echo "[$(date -Iseconds)] ERROR: the fallback run exceeded $COLLECT_TIMEOUT and was killed (exit $rc)." >> "$REPO/cron.log"
        fi
    else
        echo "[$(date -Iseconds)] ERROR: OAuth unusable and $CHANNELS_FILE is missing -- nothing was collected." >> "$REPO/cron.log"
    fi
fi

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
