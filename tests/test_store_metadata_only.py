"""Tests for store.py metadata-only listing (skip reading transcript files)."""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import store


def _store_env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "videos.db")
    monkeypatch.setattr(store, "TRANSCRIPTS_DIR", tmp_path / "transcripts")
    monkeypatch.setattr(store, "SUMMARIES_DIR", tmp_path / "summaries")


def test_metadata_only_listing_skips_transcript_files(tmp_path, monkeypatch):
    _store_env(tmp_path, monkeypatch)
    store.add_video({
        "video_id": "v1", "channel_id": "UC1", "channel_title": "Chan",
        "title": "T", "published_at": "2026-01-01T00:00:00Z",
        "thumbnail_url": "https://i.ytimg.com/vi/v1/hq.jpg",
        "transcript": "hello transcript", "summary": "<p>sum</p>", "tags": [],
        "collected_at": datetime.now(tz=timezone.utc).isoformat(),
    })

    full = store.get_all_videos()
    assert full[0]["transcript"] == "hello transcript"

    lean = store.get_all_videos(with_transcripts=False)
    assert lean[0]["transcript"] is None
    assert lean[0]["summary"] == "<p>sum</p>"        # summary is still needed
    assert lean[0]["title"] == "T"


def test_metadata_only_get_videos_since_skips_transcript_files(tmp_path, monkeypatch):
    _store_env(tmp_path, monkeypatch)
    store.add_video({
        "video_id": "v1", "channel_id": "UC1", "channel_title": "Chan",
        "title": "T", "published_at": "2026-01-01T00:00:00+00:00",
        "thumbnail_url": "https://i.ytimg.com/vi/v1/hq.jpg",
        "transcript": "hello transcript", "summary": "<p>sum</p>", "tags": [],
        "collected_at": datetime.now(tz=timezone.utc).isoformat(),
    })
    since = datetime(2025, 1, 1, tzinfo=timezone.utc)

    full = store.get_videos_since(since)
    assert full[0]["transcript"] == "hello transcript"

    lean = store.get_videos_since(since, with_transcripts=False)
    assert lean[0]["transcript"] is None
    assert lean[0]["summary"] == "<p>sum</p>"
    assert lean[0]["title"] == "T"
