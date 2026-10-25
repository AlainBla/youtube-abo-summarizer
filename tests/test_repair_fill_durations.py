"""Tests for repair.py --fill-durations."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

import repair


@pytest.fixture
def written(monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(repair.store, "update_duration", lambda vid, d: calls.append((vid, d)))
    return calls


def _ytdlp(durations):
    def get(vid, no_proxy=False):
        if vid not in durations:
            return None
        return {"video_id": vid, "duration": durations[vid]}
    return get


def _no_api():
    raise AssertionError("the API must not be asked when yt-dlp answered everything")


def _entry(vid, duration=None):
    return {"video_id": vid, "title": f"Video {vid}", "duration": duration}


def test_yt_dlp_fills_what_is_missing_and_leaves_known_durations_alone(monkeypatch, written):
    monkeypatch.setattr(repair.ytdlp_meta, "get_video_metadata", _ytdlp({"a": "PT1H2M3S"}))
    entries = [_entry("a"), _entry("b", "PT5M")]
    assert repair.fill_durations(entries, dry_run=False, get_service=_no_api) == 1
    assert written == [("a", "PT1H2M3S")]


def test_what_yt_dlp_cannot_read_goes_to_the_api_in_one_batch(monkeypatch, written):
    monkeypatch.setattr(repair.ytdlp_meta, "get_video_metadata", _ytdlp({}))
    asked = []

    def durations(service, ids):
        asked.append(ids)
        return {"a": "PT7M", "b": "PT9M"}

    import youtube_client
    monkeypatch.setattr(youtube_client, "get_video_durations", durations)
    n = repair.fill_durations([_entry("a"), _entry("b")], dry_run=False, get_service=lambda: object())
    assert n == 2
    assert asked == [["a", "b"]]
    assert written == [("a", "PT7M"), ("b", "PT9M")]


def test_a_stream_still_live_is_not_stored_as_zero(monkeypatch, written):
    # The API answers "P0D" for a running stream; stored, it would read as a short.
    monkeypatch.setattr(repair.ytdlp_meta, "get_video_metadata", _ytdlp({"live": None}))
    import youtube_client
    monkeypatch.setattr(youtube_client, "get_video_durations", lambda s, ids: {"live": "P0D"})
    assert repair.fill_durations([_entry("live")], dry_run=False, get_service=lambda: object()) == 0
    assert written == []


def test_no_usable_token_leaves_the_rest_without_failing(monkeypatch, written):
    monkeypatch.setattr(repair.ytdlp_meta, "get_video_metadata", _ytdlp({"a": "PT3M"}))
    n = repair.fill_durations([_entry("a"), _entry("b")], dry_run=False, get_service=lambda: None)
    assert n == 1
    assert written == [("a", "PT3M")]


def test_dry_run_writes_nothing_but_counts(monkeypatch, written):
    monkeypatch.setattr(repair.ytdlp_meta, "get_video_metadata", _ytdlp({"a": "PT3M"}))
    assert repair.fill_durations([_entry("a")], dry_run=True, get_service=_no_api) == 1
    assert written == []


@pytest.mark.parametrize("value,expected", [
    ("PT1H", "PT1H"), ("PT0M42S", "PT0M42S"), ("P0D", None), ("PT0S", None), ("", None), (None, None),
])
def test_known_duration(value, expected):
    assert repair._known_duration(value) == expected
