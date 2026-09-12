"""Single-video metadata comes from yt-dlp, not the Data API.

The API charges every lookup against a project-wide daily quota that the
scheduled collect runs already spend, so on-demand ingest used to fail with
quotaExceeded over a lookup worth one unit. yt-dlp reads the watch page: no
token, no quota. The dict it returns must be indistinguishable from
youtube_client.get_video_by_id()'s, or the store gets a different shape
depending on which path answered.
"""
import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, REPO)

import ytdlp_meta


def _info(**overrides):
    info = {
        "id": "abc123",
        "title": "Ein Video",
        "duration": 3723.0,
        "timestamp": 1757000000,
        "channel_id": "UC" + "x" * 22,
        "channel": "Ein Kanal",
    }
    info.update(overrides)
    return info


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_run(info=None, returncode=0, record=None):
    def run(cmd, **kwargs):
        if record is not None:
            record.append(cmd)
        payload = json.dumps(info) if info is not None else ""
        return _Completed(returncode=returncode, stdout=payload)
    return run


def test_metadata_has_the_same_shape_as_the_api_path(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(_info()))
    meta = ytdlp_meta.get_video_metadata("abc123")
    assert set(meta) == {
        "video_id",
        "title",
        "published_at",
        "thumbnail_url",
        "duration",
        "channel_id",
        "channel_title",
    }
    assert meta["video_id"] == "abc123"
    assert meta["title"] == "Ein Video"
    assert meta["channel_id"] == "UC" + "x" * 22
    assert meta["channel_title"] == "Ein Kanal"


def test_duration_is_iso_8601_because_that_is_what_the_store_parses(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(_info(duration=3723.0)))
    assert ytdlp_meta.get_video_metadata("abc123")["duration"] == "PT1H2M3S"

    monkeypatch.setattr(subprocess, "run", _fake_run(_info(duration=95)))
    assert ytdlp_meta.get_video_metadata("abc123")["duration"] == "PT1M35S"

    monkeypatch.setattr(subprocess, "run", _fake_run(_info(duration=0)))
    assert ytdlp_meta.get_video_metadata("abc123")["duration"] == "PT0S"


def test_a_live_stream_without_duration_keeps_the_field_empty(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(_info(duration=None)))
    assert ytdlp_meta.get_video_metadata("abc123")["duration"] is None


def test_published_at_prefers_the_exact_timestamp(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(_info(timestamp=1757000000)))
    assert ytdlp_meta.get_video_metadata("abc123")["published_at"] == "2025-09-04T15:33:20Z"


def test_release_timestamp_wins_over_the_upload_timestamp(monkeypatch):
    info = _info(timestamp=1757000000, release_timestamp=1757100000)
    monkeypatch.setattr(subprocess, "run", _fake_run(info))
    assert ytdlp_meta.get_video_metadata("abc123")["published_at"] == "2025-09-05T19:20:00Z"


def test_upload_date_is_the_fallback_when_no_timestamp_is_given(monkeypatch):
    info = _info(upload_date="20260906")
    info.pop("timestamp")
    monkeypatch.setattr(subprocess, "run", _fake_run(info))
    assert ytdlp_meta.get_video_metadata("abc123")["published_at"] == "2026-09-06T00:00:00Z"


def test_thumbnail_is_the_jpeg_url_the_rest_of_the_pipeline_expects(monkeypatch):
    # yt-dlp reports a .webp URL with a query string; ebook.collect_thumbnails()
    # and the export cards assume the API's plain mqdefault.jpg.
    monkeypatch.setattr(subprocess, "run", _fake_run(_info(thumbnail="https://i.ytimg.com/vi_webp/abc123/maxres.webp?x=1")))
    meta = ytdlp_meta.get_video_metadata("abc123")
    assert meta["thumbnail_url"] == "https://i.ytimg.com/vi/abc123/mqdefault.jpg"


def test_channel_title_falls_back_to_uploader(monkeypatch):
    info = _info(uploader="Der Uploader")
    info.pop("channel")
    monkeypatch.setattr(subprocess, "run", _fake_run(info))
    assert ytdlp_meta.get_video_metadata("abc123")["channel_title"] == "Der Uploader"


def test_a_failing_yt_dlp_returns_none_instead_of_raising(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(None, returncode=1))
    assert ytdlp_meta.get_video_metadata("abc123") is None


def test_unparseable_output_returns_none(monkeypatch):
    def run(cmd, **kwargs):
        return _Completed(returncode=0, stdout="not json")
    monkeypatch.setattr(subprocess, "run", run)
    assert ytdlp_meta.get_video_metadata("abc123") is None


def test_a_timeout_returns_none(monkeypatch):
    def run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(subprocess, "run", run)
    assert ytdlp_meta.get_video_metadata("abc123") is None


def test_a_missing_channel_id_is_treated_as_a_failure(monkeypatch):
    info = _info()
    info.pop("channel_id")
    monkeypatch.setattr(subprocess, "run", _fake_run(info))
    assert ytdlp_meta.get_video_metadata("abc123") is None


def test_yt_dlp_runs_through_the_current_interpreter_and_a_full_url(monkeypatch):
    # Cron has no .venv/bin on PATH, so a bare "yt-dlp" would not resolve; and a
    # leading-dash video ID as a bare argument is read as a flag.
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(_info(), record=calls))
    ytdlp_meta.get_video_metadata("-7ajktD8pOo")
    cmd = calls[0]
    assert cmd[:3] == [sys.executable, "-m", "yt_dlp"]
    assert cmd[-1] == "https://www.youtube.com/watch?v=-7ajktD8pOo"


def test_a_direct_failure_is_retried_through_the_proxy(monkeypatch):
    # Same reason transcripts.py retries: the server's own IP is the one YouTube
    # blocks. Direct first so a working IP never pays for the proxy.
    calls = []
    monkeypatch.setenv("WEBSHARE_PROXY_URL", "http://user:pw@proxy.example:80")

    def run(cmd, **kwargs):
        calls.append(cmd)
        if "--proxy" not in cmd:
            return _Completed(returncode=1, stdout="")
        return _Completed(returncode=0, stdout=json.dumps(_info()))

    monkeypatch.setattr(subprocess, "run", run)
    assert ytdlp_meta.get_video_metadata("abc123") is not None
    assert len(calls) == 2
    assert "--proxy" not in calls[0]
    assert calls[1][calls[1].index("--proxy") + 1] == "http://user:pw@proxy.example:80"


def test_no_proxy_skips_the_retry(monkeypatch):
    calls = []
    monkeypatch.setenv("WEBSHARE_PROXY_URL", "http://user:pw@proxy.example:80")
    monkeypatch.setattr(subprocess, "run", _fake_run(None, returncode=1, record=calls))
    assert ytdlp_meta.get_video_metadata("abc123", no_proxy=True) is None
    assert len(calls) == 1
