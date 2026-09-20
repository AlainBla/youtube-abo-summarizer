#!/usr/bin/env python3
"""A running record of how big the personal export was, run after run.

Every ``export.py --user`` run appends one line to ``data/export_stats.jsonl``
naming how many videos survived the personal filter. Read back, those lines
answer the only question the number in the header cannot: is the backlog
growing or shrinking? The archive shows it as a tooltip on its video count --
today, seven days ago, thirty, ninety.

A line is only comparable to lines produced under the same rules, so each one
carries its own fingerprint: the user, ``--read-days`` and the time window. A
run with a different ``--read-days`` starts a separate series rather than
bending the old one -- changing the filter must not look like the backlog
moved. Older series stay in the file; they are simply not what the current
page asks for.

Deliberately JSON Lines and not a table in ``videos.db``: the file is appended
to from a cron job, read by eye when something looks odd, and survives a
half-written line (a killed write) without a migration -- ``load()`` skips
what it cannot parse instead of failing the export that follows.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import store

STATS_PATH = store.DATA_DIR / "export_stats.jsonl"

# The tooltip's columns, in days. 90 is spoken of as "3 Monate" in the UI.
BACKLOG_WINDOWS = (7, 30, 90)


def window_label(all_videos: bool, hours: int | None) -> str:
    """The time window as one comparable string ("all" or "hours:48").

    Part of the fingerprint: the same store yields a different count under
    ``--all`` than under ``--hours 48``, and plotting the two as one line
    would invent a drop that never happened.
    """
    if all_videos or hours is None:
        return "all"
    return f"hours:{hours}"


def fingerprint(user: str, read_days: int, window: str) -> dict:
    """What makes two records comparable. Everything else is measurement."""
    return {"user": user, "read_days": read_days, "window": window}


def record(entry: dict, path: Path | None = None) -> bool:
    """Append one measurement. Never raises -- an export must not fail on it.

    The write is the least important thing this process does: the statistic is
    a convenience, the archive is the product. A full disk or a read-only
    data/ therefore costs a warning on stderr, not the export.

    `path` defaults to STATS_PATH at call time, not at import time, so a test
    can point the module somewhere else.
    """
    path = path or STATS_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except OSError as e:
        print(f"Warning: could not write {path}: {e}", file=sys.stderr)
        return False


def load(path: Path | None = None) -> list[dict]:
    """Every readable record, oldest first. A missing file means no history.

    A line that does not parse is skipped rather than fatal: a run killed
    mid-write leaves a partial line behind, and one truncated measurement must
    not cost the whole history.
    """
    path = path or STATS_PATH
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    entries = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("ts"):
            entries.append(obj)
    return entries


def series(entries: list[dict], fp: dict) -> list[dict]:
    """The records that share `fp`, oldest first.

    Sorted here rather than trusted in file order, because two exports can
    finish out of order (the personal pair writes twice) and a lookup that
    walks backwards has to walk a sorted list.
    """
    matching = [
        e for e in entries
        if all(e.get(key) == value for key, value in fp.items())
    ]
    return sorted(matching, key=lambda e: e.get("ts") or "")


def _parse_ts(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _count_at(entries: list[dict], moment: datetime) -> int | None:
    """The last count recorded at or before `moment`, or None.

    At-or-before, never the nearest record: a history that starts after the
    asked-for day has no answer for it, and the honest answer is "no data"
    (the tooltip shows a dash). Reaching forward for the earliest record
    instead would report today's backlog as if it were last month's.
    """
    best = None
    for entry in entries:
        ts = _parse_ts(entry.get("ts"))
        if ts is None or ts > moment:
            continue
        count = entry.get("personal_count")
        if isinstance(count, int):
            best = count
    return best


def backlog(entries: list[dict], now: datetime, windows=BACKLOG_WINDOWS) -> dict | None:
    """The tooltip's numbers: the current count and one per window.

    `entries` is one series (see series()). Returns None when the series is
    empty -- a first-ever run has nothing to show and the page then renders no
    tooltip at all, rather than a row of dashes.
    """
    ordered = sorted(entries, key=lambda e: e.get("ts") or "")
    current = _count_at(ordered, now)
    if current is None:
        return None
    result = {"now": current}
    for days in windows:
        result[f"d{days}"] = _count_at(ordered, now - timedelta(days=days))
    return result
