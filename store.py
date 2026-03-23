"""Persistent video store backed by SQLite + individual files.

Layout:
  data/
    videos.db               — SQLite: video metadata and status
    transcripts/<id>.<lang>.txt  — raw transcript (original language)
    transcripts/<id>.de.txt      — manual German transcript (when available)
    transcripts/<id>.en.txt      — manual English transcript (when available)
    transcripts/<id>.txt         — legacy format (still recognised)
    summaries/<id>.html     — HTML-fragment summary (one file per video)
"""

import json
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
    summary_model   TEXT,
    transcript_error TEXT,
    collected_at    TEXT NOT NULL
);
"""

_MIGRATIONS = [
    ("duration",         "ALTER TABLE videos ADD COLUMN duration TEXT"),
    ("summary_model",    "ALTER TABLE videos ADD COLUMN summary_model TEXT"),
    ("tags",             "ALTER TABLE videos ADD COLUMN tags TEXT"),
    ("transcript_lang",  "ALTER TABLE videos ADD COLUMN transcript_lang TEXT"),
]


def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    TRANSCRIPTS_DIR.mkdir(exist_ok=True)
    SUMMARIES_DIR.mkdir(exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute(_SCHEMA)
    cols = {row[1] for row in c.execute("PRAGMA table_info(videos)")}
    for col, stmt in _MIGRATIONS:
        if col not in cols:
            c.execute(stmt)
    c.commit()
    return c


def _resolve_transcript_path(video_id: str, transcript_lang: str | None) -> Path | None:
    """Return the path to the original-language transcript file, or None if not found.

    Checks <id>.<lang>.txt first, then falls back to legacy <id>.txt.
    """
    if transcript_lang:
        p = TRANSCRIPTS_DIR / f"{video_id}.{transcript_lang}.txt"
        if p.exists():
            return p
    legacy = TRANSCRIPTS_DIR / f"{video_id}.txt"
    return legacy if legacy.exists() else None


def get_llm_transcript_path(video_id: str) -> Path | None:
    """Return the best transcript path for LLM input.

    Priority: <id>.de.txt → <id>.en.txt → <id>.<transcript_lang>.txt → <id>.txt.
    Returns None if no transcript file exists.
    """
    row = _conn().execute(
        "SELECT transcript_lang FROM videos WHERE video_id = ?", (video_id,)
    ).fetchone()
    transcript_lang = row["transcript_lang"] if row else None

    for lang in ["de", "en"]:
        p = TRANSCRIPTS_DIR / f"{video_id}.{lang}.txt"
        if p.exists():
            return p
    return _resolve_transcript_path(video_id, transcript_lang)


def get_video(video_id: str) -> dict | None:
    """Return stored video metadata with has_transcript and has_summary flags, or None if not found."""
    row = _conn().execute(
        "SELECT * FROM videos WHERE video_id = ?", (video_id,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    t_path = _resolve_transcript_path(video_id, d.get("transcript_lang"))
    d["has_transcript"] = t_path is not None
    d["has_manual_transcript"] = any(
        (TRANSCRIPTS_DIR / f"{video_id}.{lang}.txt").exists()
        for lang in ["de", "en"]
    )
    d["has_summary"] = (SUMMARIES_DIR / f"{video_id}.html").exists()
    d["tags"] = json.loads(d["tags"]) if d.get("tags") else []
    return d


def add_video(entry: dict) -> bool:
    """Insert one video entry. Returns True if inserted, False if already present.

    entry keys:
        video_id, channel_id, channel_title, title, published_at (ISO str),
        thumbnail_url, duration (ISO 8601 str|None), summary_model (str|None),
        transcript (str|None), summary (str|None), transcript_error (str|None),
        tags (list[str]|None), collected_at (ISO str).
    """
    tags = entry.get("tags")
    tags_json = json.dumps(tags) if tags else None
    with _conn() as c:
        try:
            c.execute(
                """INSERT INTO videos
                   (video_id, channel_id, channel_title, title, published_at,
                    thumbnail_url, duration, summary_model, transcript_error, tags,
                    transcript_lang, collected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry["video_id"], entry["channel_id"], entry["channel_title"],
                    entry["title"], entry["published_at"], entry["thumbnail_url"],
                    entry.get("duration"), entry.get("summary_model"),
                    entry.get("transcript_error"), tags_json,
                    entry.get("transcript_lang"), entry["collected_at"],
                ),
            )
        except sqlite3.IntegrityError:
            return False  # duplicate video_id

    if entry.get("transcript"):
        lang = entry.get("transcript_lang")
        fname = f"{entry['video_id']}.{lang}.txt" if lang else f"{entry['video_id']}.txt"
        (TRANSCRIPTS_DIR / fname).write_text(entry["transcript"], encoding="utf-8")
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
    summary_model: str | None = None,
    tags: list[str] | None = None,
    transcript_lang: str | None = None,
) -> None:
    """Update transcript_error, summary_model, tags, and transcript_lang in DB; write transcript/summary files if provided.

    When transcript_lang is None, the transcript is written to the legacy <id>.txt path.
    Callers that have a lang_code should always pass transcript_lang= to ensure the
    lang-suffixed file is written and _resolve_transcript_path returns fresh content.
    """
    tags_json = json.dumps(tags) if tags else None
    with _conn() as c:
        c.execute(
            """UPDATE videos
               SET transcript_error = ?, summary_model = ?, tags = ?,
                   transcript_lang = COALESCE(?, transcript_lang)
               WHERE video_id = ?""",
            (transcript_error, summary_model, tags_json, transcript_lang, video_id),
        )
    if transcript is not None:
        lang = transcript_lang
        fname = f"{video_id}.{lang}.txt" if lang else f"{video_id}.txt"
        (TRANSCRIPTS_DIR / fname).write_text(transcript, encoding="utf-8")
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
        t_path = _resolve_transcript_path(d["video_id"], d.get("transcript_lang"))
        s_path = SUMMARIES_DIR / f"{d['video_id']}.html"
        d["transcript"] = t_path.read_text(encoding="utf-8") if t_path else None
        d["summary"] = s_path.read_text(encoding="utf-8") if s_path.exists() else None
        d["tags"] = json.loads(d["tags"]) if d.get("tags") else []
        result.append(d)
    return result


def get_all_videos() -> list[dict]:
    """Return all stored videos, newest first.

    Loads transcript and summary from their files; both may be None.
    """
    rows = _conn().execute(
        "SELECT * FROM videos ORDER BY published_at DESC"
    ).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        t_path = _resolve_transcript_path(d["video_id"], d.get("transcript_lang"))
        s_path = SUMMARIES_DIR / f"{d['video_id']}.html"
        d["transcript"] = t_path.read_text(encoding="utf-8") if t_path else None
        d["summary"] = s_path.read_text(encoding="utf-8") if s_path.exists() else None
        d["tags"] = json.loads(d["tags"]) if d.get("tags") else []
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
            for p in TRANSCRIPTS_DIR.glob(f"{vid_id}.*.txt"):
                p.unlink(missing_ok=True)
            (TRANSCRIPTS_DIR / f"{vid_id}.txt").unlink(missing_ok=True)
            (SUMMARIES_DIR / f"{vid_id}.html").unlink(missing_ok=True)
        c.execute("DELETE FROM videos WHERE published_at < ?", (cutoff,))
    return len(video_ids)
