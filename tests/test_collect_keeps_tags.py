"""A stored video that has its summary but lost its transcript gets the
transcript back without losing its tags.

update_video_with_summary() writes tags unconditionally; collect.py kept the
stored summary_model on this path but passed tags=None, so one video whose
transcript was re-fetched by the scheduled run lost its tags.
"""
import os
import sys
from datetime import datetime, timezone

import pytest

REPO = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, REPO)

collect = pytest.importorskip("collect", reason="collect.py runtime deps unavailable")

EXISTING = {
    "video_id": "abc123", "title": "Ein Video", "channel_id": "UC" + "x" * 22,
    "channel_title": "Ein Kanal", "duration": "PT10M", "published_at": "2026-09-06T10:00:00Z",
    "has_transcript": False, "has_summary": True, "transcript_lang": "ar",
    "transcript_error": None, "summary_model": "model-x", "tags": ["Gaming", "Spieletest"],
}


def test_refetched_transcript_keeps_stored_tags(monkeypatch):
    calls = []
    monkeypatch.setattr(collect.store, "get_video", lambda vid: dict(EXISTING))
    monkeypatch.setattr(collect.tr, "get_transcript", lambda vid: ("text", "en", None))
    monkeypatch.setattr(collect.store, "update_video_with_summary",
                        lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(collect.openrouter, "summarize_video",
                        lambda *a, **kw: pytest.fail("a stored summary must not be redone"))

    added, summarized = collect._process_single_video(
        lambda: None, "abc123", "m", datetime(2026, 9, 12, tzinfo=timezone.utc))

    (args, kw), = calls
    assert (added, summarized) == (False, False)
    assert args[1] == "text" and kw["transcript_lang"] == "en"
    assert args[4] == "model-x"
    assert kw["tags"] == ["Gaming", "Spieletest"]
