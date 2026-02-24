"""Persistent video store backed by SQLite + individual files.

Layout:
  data/
    videos.db               — SQLite: video metadata and status
    transcripts/<id>.txt    — raw transcript text (one file per video)
    summaries/<id>.html     — HTML-fragment summary (one file per video)
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "videos.db"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
SUMMARIES_DIR = DATA_DIR / "summaries"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    video_id        TEXT PRIMARY KEY,
    channel_id      TEXT NOT NULL,
    channel_title   TEXT NOT NULL,
    title           TEXT NOT NULL,
    published_at    TEXT NOT NULL,
    thumbnail_url   TEXT NOT NULL,
    duration        TEXT,
    transcript_error TEXT,
    collected_at    TEXT NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    TRANSCRIPTS_DIR.mkdir(exist_ok=True)
    SUMMARIES_DIR.mkdir(exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute(_SCHEMA)
    # Migration: add duration column to existing databases
    cols = {row[1] for row in c.execute("PRAGMA table_info(videos)")}
    if "duration" not in cols:
        c.execute("ALTER TABLE videos ADD COLUMN duration TEXT")
    c.commit()
    return c


def add_video(entry: dict) -> bool:
    """Insert one video entry. Returns True if inserted, False if already present.

    entry keys:
        video_id, channel_id, channel_title, title, published_at (ISO str),
        thumbnail_url, duration (ISO 8601 str|None), transcript (str|None),
        summary (str|None), transcript_error (str|None), collected_at (ISO str).
    """
    with _conn() as c:
        try:
            c.execute(
                """INSERT INTO videos
                   (video_id, channel_id, channel_title, title, published_at,
                    thumbnail_url, duration, transcript_error, collected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry["video_id"], entry["channel_id"], entry["channel_title"],
                    entry["title"], entry["published_at"], entry["thumbnail_url"],
                    entry.get("duration"), entry.get("transcript_error"), entry["collected_at"],
                ),
            )
        except sqlite3.IntegrityError:
            return False  # duplicate video_id

    if entry.get("transcript"):
        (TRANSCRIPTS_DIR / f"{entry['video_id']}.txt").write_text(
            entry["transcript"], encoding="utf-8"
        )
    if entry.get("summary"):
        (SUMMARIES_DIR / f"{entry['video_id']}.html").write_text(
            entry["summary"], encoding="utf-8"
        )
    return True


def update_video_with_summary(
    video_id: str,
    transcript: str | None,
    summary: str | None,
    transcript_error: str | None,
) -> None:
    """Update transcript_error in DB and write transcript/summary files if provided."""
    with _conn() as c:
        c.execute(
            "UPDATE videos SET transcript_error = ? WHERE video_id = ?",
            (transcript_error, video_id),
        )
    if transcript is not None:
        (TRANSCRIPTS_DIR / f"{video_id}.txt").write_text(transcript, encoding="utf-8")
    if summary is not None:
        (SUMMARIES_DIR / f"{video_id}.html").write_text(summary, encoding="utf-8")


def get_videos_since(since: datetime) -> list[dict]:
    """Return all stored videos published at or after `since`, newest first.

    Loads transcript and summary from their files; both may be None.
    """
    rows = _conn().execute(
        "SELECT * FROM videos WHERE published_at >= ? ORDER BY published_at DESC",
        (since.astimezone(timezone.utc).isoformat(),),
    ).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        t_path = TRANSCRIPTS_DIR / f"{d['video_id']}.txt"
        s_path = SUMMARIES_DIR / f"{d['video_id']}.html"
        d["transcript"] = t_path.read_text(encoding="utf-8") if t_path.exists() else None
        d["summary"] = s_path.read_text(encoding="utf-8") if s_path.exists() else None
        result.append(d)
    return result


def prune_older_than(days: int = 7) -> int:
    """Delete entries and their files older than `days` days by published_at.

    Returns the number of entries removed.
    """
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()
    with _conn() as c:
        rows = c.execute(
            "SELECT video_id FROM videos WHERE published_at < ?", (cutoff,)
        ).fetchall()
        video_ids = [r["video_id"] for r in rows]
        for vid_id in video_ids:
            for path in (TRANSCRIPTS_DIR / f"{vid_id}.txt", SUMMARIES_DIR / f"{vid_id}.html"):
                if path.exists():
                    path.unlink()
        c.execute("DELETE FROM videos WHERE published_at < ?", (cutoff,))
    return len(video_ids)
