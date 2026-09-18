#!/usr/bin/env python3
"""Read the sync server's per-user state, read-only.

The sync database keeps read and bookmark flags in one `video_state` table,
distinguished by its `type` column. Both the ebook CLI (read state) and the
personal export (read *and* bookmark state) need them, so the query lives
here once instead of twice.
"""

import os
import sqlite3
import sys

STATE_TYPES = ("read", "bookmark")
# __file__-relative: cron runs these CLIs from outside the repo root, where a
# cwd-relative default would point at nothing.
DEFAULT_SYNC_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync-server", "sync.db")


def load_state_ids(sync_db: str, email: str, kind: str) -> set[str]:
    """Video IDs this user has flagged with `kind` ('read' or 'bookmark').

    An unknown email is an error rather than an empty set -- a typo would
    otherwise silently produce an export in which nothing is marked.
    """
    if kind not in STATE_TYPES:
        raise ValueError(f"unknown state type {kind!r}; expected one of {STATE_TYPES}")
    if not os.path.exists(sync_db):
        sys.exit(f"Error: sync database not found: {sync_db}")
    db = sqlite3.connect(f"file:{sync_db}?mode=ro", uri=True)
    try:
        row = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            sys.exit(f"Error: no sync user with email '{email}'.")
        rows = db.execute(
            "SELECT video_id FROM video_state WHERE user_id = ? AND type = ? AND value = 1",
            (row[0], kind),
        ).fetchall()
    finally:
        db.close()
    return {r[0] for r in rows}
