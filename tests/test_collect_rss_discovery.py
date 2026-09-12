"""collect.py discovers new videos through the RSS feed before the API.

The feed costs nothing and needs no token, so the API service must stay
unbuilt unless a channel actually falls back to it. What the feed does not
carry is the duration -- and the shorts filter runs before transcript and LLM
work, so it has to be filled in first, from yt-dlp, also for free.
"""
import os
import sys
from datetime import datetime, timezone

import pytest

REPO = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, REPO)

collect = pytest.importorskip("collect", reason="collect.py runtime deps unavailable")

SINCE = datetime(2026, 9, 1, tzinfo=timezone.utc)
CHANNEL = "UC" + "x" * 22


def _feed_video(video_id="vid1"):
    return {
        "video_id": video_id,
        "title": "Aus dem Feed",
        "published_at": "2026-09-05T10:00:00Z",
        "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
    }


@pytest.fixture(autouse=True)
def _empty_store(monkeypatch):
    """Nothing is in the store unless a test says so (never touch data/videos.db)."""
    monkeypatch.setattr(collect.store, "get_video", lambda vid: None)


def _exploding_service():
    def get():
        raise AssertionError("the API service must not be built when the feed answered")
    return get


def test_the_feed_answers_and_the_api_is_never_built(monkeypatch):
    monkeypatch.setattr(collect.feeds, "get_new_videos_rss", lambda *a, **k: [_feed_video()])
    monkeypatch.setattr(collect.ytdlp_meta, "get_video_metadata", lambda vid, no_proxy=False: {"duration": "PT12M"})
    monkeypatch.setattr(collect, "get_new_videos", lambda *a, **k: pytest.fail("API called"))

    videos = collect._discover_videos(CHANNEL, SINCE, _exploding_service())
    assert [v["video_id"] for v in videos] == ["vid1"]
    assert videos[0]["duration"] == "PT12M"


def test_nothing_new_in_the_feed_still_skips_the_api(monkeypatch):
    # An empty list is an answer, not a failure -- the usual case on a
    # half-hourly schedule, and the whole quota saving lives here.
    monkeypatch.setattr(collect.feeds, "get_new_videos_rss", lambda *a, **k: [])
    monkeypatch.setattr(collect, "get_new_videos", lambda *a, **k: pytest.fail("API called"))
    assert collect._discover_videos(CHANNEL, SINCE, _exploding_service()) == []


def test_a_feed_that_cannot_answer_falls_back_to_the_api(monkeypatch):
    built = []
    monkeypatch.setattr(collect.feeds, "get_new_videos_rss", lambda *a, **k: None)
    monkeypatch.setattr(collect, "get_new_videos", lambda service, cid, since: [dict(_feed_video("apivid"))])
    monkeypatch.setattr(collect, "get_video_durations", lambda service, ids: {"apivid": "PT5M"})

    def get_service():
        built.append(True)
        return object()

    videos = collect._discover_videos(CHANNEL, SINCE, get_service)
    assert [v["video_id"] for v in videos] == ["apivid"]
    assert videos[0]["duration"] == "PT5M"
    assert built == [True]


def test_no_rss_forces_the_api_path(monkeypatch):
    monkeypatch.setattr(collect.feeds, "get_new_videos_rss", lambda *a, **k: pytest.fail("feed used"))
    monkeypatch.setattr(collect, "get_new_videos", lambda service, cid, since: [])
    assert collect._discover_videos(CHANNEL, SINCE, lambda: object(), use_rss=False) == []


def test_a_video_yt_dlp_cannot_read_gets_its_duration_from_the_api(monkeypatch):
    # _is_short(None) is False, so a missing duration lets every short through
    # into transcript and LLM work. One batched videos().list (1 unit per 50) is
    # the backstop for a run whose yt-dlp is broken or blocked.
    built = []
    monkeypatch.setattr(collect.feeds, "get_new_videos_rss", lambda *a, **k: [_feed_video()])
    monkeypatch.setattr(collect.ytdlp_meta, "get_video_metadata", lambda vid, no_proxy=False: None)
    monkeypatch.setattr(collect, "get_video_durations", lambda service, ids: {"vid1": "PT0M42S"})

    def get_service():
        built.append(True)
        return object()

    videos = collect._discover_videos(CHANNEL, SINCE, get_service)
    assert videos[0]["duration"] == "PT0M42S"
    assert collect._is_short(videos[0]["duration"]) is True
    assert built == [True]


def test_the_duration_backfill_is_batched_and_only_for_the_misses(monkeypatch):
    asked = []
    monkeypatch.setattr(
        collect.feeds, "get_new_videos_rss",
        lambda *a, **k: [_feed_video("vid1"), _feed_video("vid2")],
    )
    monkeypatch.setattr(
        collect.ytdlp_meta, "get_video_metadata",
        lambda vid, no_proxy=False: {"duration": "PT9M"} if vid == "vid1" else None,
    )

    def durations(service, ids):
        asked.append(list(ids))
        return {"vid2": "PT1M"}

    monkeypatch.setattr(collect, "get_video_durations", durations)
    videos = collect._discover_videos(CHANNEL, SINCE, lambda: object())
    assert [v["duration"] for v in videos] == ["PT9M", "PT1M"]
    assert asked == [["vid2"]]


def test_without_credentials_the_duration_simply_stays_unknown(monkeypatch):
    # A --file run over channel IDs needs no API at all; a yt-dlp miss there must
    # degrade, not crash.
    monkeypatch.setattr(collect.feeds, "get_new_videos_rss", lambda *a, **k: [_feed_video()])
    monkeypatch.setattr(collect.ytdlp_meta, "get_video_metadata", lambda vid, no_proxy=False: None)

    def get_service():
        raise FileNotFoundError("client_secrets.json")

    videos = collect._discover_videos(CHANNEL, SINCE, get_service)
    assert videos[0]["duration"] is None


def test_a_video_already_in_the_store_costs_no_lookup_at_all(monkeypatch):
    # collect.sh looks back four hours every thirty minutes, so the feed re-lists
    # the same video for about eight runs. Its duration is already on disk.
    monkeypatch.setattr(collect.feeds, "get_new_videos_rss", lambda *a, **k: [_feed_video()])
    monkeypatch.setattr(collect.store, "get_video", lambda vid: {"duration": "PT7M"})
    monkeypatch.setattr(
        collect.ytdlp_meta, "get_video_metadata",
        lambda vid, no_proxy=False: pytest.fail("yt-dlp called for a stored video"),
    )

    videos = collect._discover_videos(CHANNEL, SINCE, _exploding_service())
    assert videos[0]["duration"] == "PT7M"


def test_yt_dlp_metadata_overrides_the_feed_where_it_is_richer(monkeypatch):
    monkeypatch.setattr(collect.feeds, "get_new_videos_rss", lambda *a, **k: [_feed_video()])
    monkeypatch.setattr(
        collect.ytdlp_meta,
        "get_video_metadata",
        lambda vid, no_proxy=False: {
            "duration": "PT1M",
            "title": "Titel von yt-dlp",
            "published_at": "2026-09-05T11:22:33Z",
        },
    )
    video = collect._discover_videos(CHANNEL, SINCE, _exploding_service())[0]
    assert video["title"] == "Titel von yt-dlp"
    assert video["published_at"] == "2026-09-05T11:22:33Z"


def test_a_channel_id_is_resolved_from_the_feed_without_the_api(monkeypatch):
    monkeypatch.setattr(collect.feeds, "get_channel", lambda cid, no_proxy=False: {"channel_id": cid, "title": "Feed-Kanal"})
    monkeypatch.setattr(collect, "resolve_channel_id", lambda *a, **k: pytest.fail("API called"))

    assert collect._resolve_identifiers([CHANNEL], _exploding_service()) == [
        {"channel_id": CHANNEL, "title": "Feed-Kanal"}
    ]


def test_a_handle_still_goes_through_the_api(monkeypatch):
    monkeypatch.setattr(collect.feeds, "get_channel", lambda *a, **k: pytest.fail("feed used for a handle"))
    monkeypatch.setattr(collect, "resolve_channel_id", lambda service, ident: {"channel_id": CHANNEL, "title": ident})

    assert collect._resolve_identifiers(["@einkanal"], lambda: object()) == [
        {"channel_id": CHANNEL, "title": "@einkanal"}
    ]


def test_a_channel_id_the_feed_cannot_resolve_falls_back_to_the_api(monkeypatch):
    monkeypatch.setattr(collect.feeds, "get_channel", lambda cid, no_proxy=False: None)
    monkeypatch.setattr(collect, "resolve_channel_id", lambda service, ident: {"channel_id": ident, "title": "Via API"})

    assert collect._resolve_identifiers([CHANNEL], lambda: object()) == [
        {"channel_id": CHANNEL, "title": "Via API"}
    ]
