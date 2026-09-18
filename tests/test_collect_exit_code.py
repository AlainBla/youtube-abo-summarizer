"""collect.py signals "new videos landed" through its exit code.

Cron chains the export onto that signal, so an export -- and with it the
"new videos" banner in the archive -- only happens when something was
actually added. Importing collect.py pulls in the full runtime stack
(googleapiclient, openai, pydantic); where that is unavailable the module
tests skip and the shell-wiring tests below still run.
"""
import os
import re
import sys
from datetime import datetime, timezone

import pytest

REPO = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, REPO)

collect = pytest.importorskip("collect", reason="collect.py runtime deps unavailable")

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)

STORED = {
    "video_id": "abc123",
    "title": "Schon da",
    "published_at": "2026-09-06T10:00:00Z",
    "thumbnail_url": "https://i.ytimg.com/vi/abc123/mqdefault.jpg",
    "duration": "PT10M",
    "channel_id": "UC" + "x" * 22,
    "channel_title": "Ein Kanal",
    "transcript_lang": "de",
    "transcript_error": None,
    "has_transcript": True,
    "has_summary": False,
}


def test_nothing_added_exits_zero():
    assert collect._exit_code(0) == 0
    assert collect._exit_code(0, 0) == 0


def test_added_videos_exit_with_the_new_videos_code():
    assert collect._exit_code(1) == collect.EXIT_NEW_VIDEOS
    assert collect._exit_code(42) == collect.EXIT_NEW_VIDEOS


def test_summarized_existing_videos_also_exit_with_the_new_videos_code():
    """A summary filled in for an already-stored video is an archive change
    too, even though store.add_video() was never called for it -- added
    stays 0 and only the summarized count carries the signal."""
    assert collect._exit_code(0, 1) == collect.EXIT_NEW_VIDEOS
    assert collect._exit_code(0, 7) == collect.EXIT_NEW_VIDEOS


def test_either_count_alone_is_enough():
    assert collect._exit_code(1, 0) == collect.EXIT_NEW_VIDEOS
    assert collect._exit_code(1, 1) == collect.EXIT_NEW_VIDEOS


def test_the_signal_code_is_distinct_from_success_and_generic_failure():
    assert collect.EXIT_NEW_VIDEOS not in (0, 1)


# ── _process_single_video reports the second half of the signal ──
#
# "A summary was written for a video the store already had" never reaches
# store.add_video(), so it cannot ride on an "added" flag. These tests
# exercise that path directly: an edit that quietly drops the second half of
# the return value fails here, instead of only losing an exit code that
# nothing else asserts on.

def test_a_summary_filled_in_for_an_existing_video_is_reported_as_summarized(monkeypatch, tmp_path):
    transcript_path = tmp_path / "abc123.de.txt"
    transcript_path.write_text("Ein Transkript.", encoding="utf-8")

    monkeypatch.setattr(collect.store, "get_video", lambda vid: dict(STORED))
    monkeypatch.setattr(collect.store, "get_llm_transcript_path", lambda vid: transcript_path)
    monkeypatch.setattr(collect.store, "update_video_with_summary", lambda *a, **k: None)
    monkeypatch.setattr(
        collect.openrouter, "summarize_video",
        lambda vid, title, transcript, model, channel=None: ("<p>Zusammenfassung</p>", ["Tag1"]),
    )
    monkeypatch.setattr(
        collect.ytdlp_meta, "get_video_metadata",
        lambda vid, no_proxy=False: pytest.fail("metadata fetched though the store has it"),
    )
    monkeypatch.setattr(collect, "get_video_by_id", lambda *a, **k: pytest.fail("API called"))

    added, summarized = collect._process_single_video(
        lambda: pytest.fail("API service built"), "abc123", "model", NOW)

    assert added is False
    assert summarized is True


def test_a_rejected_summary_is_not_counted_as_a_change(monkeypatch, tmp_path):
    transcript_path = tmp_path / "abc123.de.txt"
    transcript_path.write_text("Ein Transkript.", encoding="utf-8")

    def reject(vid, title, transcript, model, channel=None):
        raise collect.openrouter.SummaryRejected("hit the output cap")

    monkeypatch.setattr(collect.store, "get_video", lambda vid: dict(STORED))
    monkeypatch.setattr(collect.store, "get_llm_transcript_path", lambda vid: transcript_path)
    monkeypatch.setattr(collect.store, "update_video_with_summary", lambda *a, **k: None)
    monkeypatch.setattr(collect.openrouter, "summarize_video", reject)

    added, summarized = collect._process_single_video(
        lambda: pytest.fail("API service built"), "abc123", "model", NOW)

    assert added is False
    assert summarized is False


def test_a_video_already_complete_in_the_store_signals_no_change(monkeypatch):
    """The ingest queue re-offers IDs that were collected long ago. Looking one
    up must not claim the archive changed -- that would re-export on every
    queue tick and keep the update banner permanently lit."""
    monkeypatch.setattr(
        collect.store, "get_video", lambda vid: dict(STORED, has_summary=True))
    monkeypatch.setattr(
        collect.openrouter, "summarize_video",
        lambda *a, **k: pytest.fail("summarized a video that already has a summary"),
    )

    assert collect._process_single_video(
        lambda: pytest.fail("API service built"), "abc123", "model", NOW) == (False, False)
