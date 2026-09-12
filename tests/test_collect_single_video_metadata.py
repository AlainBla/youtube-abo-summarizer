"""The single-video path must not need the API -- that is the whole point.

Ingest runs on demand while the scheduled collect runs have already spent the
day's quota, so a lookup that costs one unit still fails. yt-dlp answers first;
the API is the fallback, and building its service is deferred until that
fallback is actually taken (an expired token would otherwise open an
interactive OAuth flow and hang the cron worker forever).
"""
import os
import sys
from datetime import datetime, timezone

import pytest

REPO = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, REPO)

collect = pytest.importorskip("collect", reason="collect.py runtime deps unavailable")


NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)

META = {
    "video_id": "abc123",
    "title": "Ein Video",
    "published_at": "2026-09-06T10:00:00Z",
    "thumbnail_url": "https://i.ytimg.com/vi/abc123/mqdefault.jpg",
    "duration": "PT10M",
    "channel_id": "UC" + "x" * 22,
    "channel_title": "Ein Kanal",
}


def _exploding_service():
    def get():
        raise AssertionError("the API service must not be built when yt-dlp answered")
    return get


def test_yt_dlp_answers_and_the_api_is_never_touched(monkeypatch):
    monkeypatch.setattr(collect.ytdlp_meta, "get_video_metadata", lambda vid, no_proxy=False: META)
    monkeypatch.setattr(collect, "get_video_by_id", lambda *a, **k: pytest.fail("API called"))

    assert collect._fetch_video_metadata("abc123", _exploding_service()) == META


def test_the_api_is_the_fallback_when_yt_dlp_comes_back_empty(monkeypatch):
    built = []
    monkeypatch.setattr(collect.ytdlp_meta, "get_video_metadata", lambda vid, no_proxy=False: None)
    monkeypatch.setattr(collect, "get_video_by_id", lambda service, vid: dict(META, title="via API"))

    def get_service():
        built.append(True)
        return object()

    meta = collect._fetch_video_metadata("abc123", get_service)
    assert meta["title"] == "via API"
    assert built == [True]


def test_no_proxy_is_passed_through_to_yt_dlp(monkeypatch):
    seen = {}

    def fake(vid, no_proxy=False):
        seen["no_proxy"] = no_proxy
        return META

    monkeypatch.setattr(collect.ytdlp_meta, "get_video_metadata", fake)
    collect._fetch_video_metadata("abc123", _exploding_service(), no_proxy=True)
    assert seen["no_proxy"] is True


def test_both_paths_failing_yields_none(monkeypatch):
    monkeypatch.setattr(collect.ytdlp_meta, "get_video_metadata", lambda vid, no_proxy=False: None)
    monkeypatch.setattr(collect, "get_video_by_id", lambda service, vid: None)
    assert collect._fetch_video_metadata("abc123", lambda: object()) is None


def test_an_unusable_api_service_is_not_fatal(monkeypatch):
    # No token, no client_secrets: build_service() raises. yt-dlp already
    # failed, so the ingest fails -- but as a return value, not a traceback.
    monkeypatch.setattr(collect.ytdlp_meta, "get_video_metadata", lambda vid, no_proxy=False: None)

    def get_service():
        raise FileNotFoundError("client_secrets.json")

    assert collect._fetch_video_metadata("abc123", get_service) is None


def test_the_service_is_built_once_for_a_batch_of_videos(monkeypatch):
    calls = []
    monkeypatch.setattr(collect, "build_service", lambda: calls.append(True) or "svc")

    get = collect._lazy_service()
    assert get() == "svc"
    assert get() == "svc"
    assert len(calls) == 1


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
    "has_summary": True,
}


def test_a_complete_video_is_recognised_before_any_metadata_is_fetched(monkeypatch):
    # The queue re-offers IDs that are long since collected. Fetching metadata
    # first meant a yt-dlp run and, on a blocked IP, a proxy retry -- seconds of
    # work per video, only to discover the store already has everything.
    monkeypatch.setattr(collect.store, "get_video", lambda vid: dict(STORED))
    monkeypatch.setattr(
        collect.ytdlp_meta, "get_video_metadata",
        lambda vid, no_proxy=False: pytest.fail("metadata fetched for a video already in the store"),
    )
    monkeypatch.setattr(collect, "get_video_by_id", lambda *a, **k: pytest.fail("API called"))

    assert collect._process_single_video(_exploding_service(), "abc123", "model", NOW) is False


def test_an_incomplete_entry_reuses_the_stored_metadata(monkeypatch):
    # Only the summary is missing: the transcript is on disk and every metadata
    # field the store needs is already a column. Nothing to fetch.
    stored = dict(STORED, has_summary=False)
    monkeypatch.setattr(collect.store, "get_video", lambda vid: dict(stored))
    monkeypatch.setattr(collect.store, "get_llm_transcript_path", lambda vid: None)
    monkeypatch.setattr(
        collect.ytdlp_meta, "get_video_metadata",
        lambda vid, no_proxy=False: pytest.fail("metadata fetched though the store has it"),
    )
    monkeypatch.setattr(collect, "get_video_by_id", lambda *a, **k: pytest.fail("API called"))
    monkeypatch.setattr(collect.store, "update_video_with_summary", lambda *a, **k: None)

    assert collect._process_single_video(_exploding_service(), "abc123", "model", NOW) is False


def test_a_video_the_store_does_not_know_is_still_fetched(monkeypatch):
    fetched = []
    monkeypatch.setattr(collect.store, "get_video", lambda vid: None)

    def meta(vid, no_proxy=False):
        fetched.append(vid)
        return dict(META)

    monkeypatch.setattr(collect.ytdlp_meta, "get_video_metadata", meta)
    monkeypatch.setattr(collect.tr, "get_transcript", lambda vid: (None, None, "unavailable"))
    monkeypatch.setattr(collect.time, "sleep", lambda s: None)
    monkeypatch.setattr(collect.store, "add_video", lambda entry: True)

    assert collect._process_single_video(_exploding_service(), "abc123", "model", NOW) is True
    assert fetched == ["abc123"]
