"""Channel discovery via the public RSS feed costs no API quota.

playlistItems.list was the last per-channel, per-run quota unit; the feed at
youtube.com/feeds/videos.xml carries the same newest-first list for free. It
holds only ~15 entries though, so the module must say "I cannot answer this"
(None) rather than hand back a list that may be missing videos -- the caller
then pays for the API for that one channel.
"""
import os
import sys
import urllib.error
from datetime import datetime, timezone

import pytest

REPO = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, REPO)

import feeds

CHANNEL = "UC" + "x" * 22


def _entry(video_id, published, title="Ein Video", updated=None):
    return f"""
 <entry>
  <id>yt:video:{video_id}</id>
  <yt:videoId>{video_id}</yt:videoId>
  <yt:channelId>{CHANNEL}</yt:channelId>
  <title>{title}</title>
  <author><name>Ein Kanal</name></author>
  <published>{published}</published>
  <updated>{updated or published}</updated>
  <media:group>
   <media:thumbnail url="https://i2.ytimg.com/vi/{video_id}/hqdefault.jpg" width="480" height="360"/>
  </media:group>
 </entry>"""


def _feed(entries, channel_title="Ein Kanal"):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" '
        'xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">\n'
        f" <yt:channelId>{CHANNEL[2:]}</yt:channelId>\n"
        f" <title>{channel_title}</title>\n"
        f" <author><name>{channel_title}</name></author>\n"
        + "".join(entries)
        + "\n</feed>"
    ).encode("utf-8")


def _serve(payload, record=None):
    def fetch(url, proxy=None, timeout=None):
        if record is not None:
            record.append((url, proxy))
        if isinstance(payload, Exception):
            raise payload
        return payload
    return fetch


SINCE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def test_new_entries_come_back_in_the_shape_the_api_path_returns(monkeypatch):
    xml = _feed([
        _entry("newvid", "2026-09-05T10:00:00+00:00", title="Neu"),
        _entry("oldvid", "2026-08-20T10:00:00+00:00", title="Alt"),
    ])
    monkeypatch.setattr(feeds, "_fetch", _serve(xml))

    videos = feeds.get_new_videos_rss(CHANNEL, SINCE)
    assert [v["video_id"] for v in videos] == ["newvid"]
    assert set(videos[0]) == {"video_id", "title", "published_at", "thumbnail_url"}
    assert videos[0]["title"] == "Neu"


def test_published_at_is_normalised_to_the_api_s_z_form(monkeypatch):
    monkeypatch.setattr(feeds, "_fetch", _serve(_feed([
        _entry("newvid", "2026-09-05T10:00:00+00:00"),
        _entry("oldvid", "2026-08-20T10:00:00+00:00"),
    ])))
    assert feeds.get_new_videos_rss(CHANNEL, SINCE)[0]["published_at"] == "2026-09-05T10:00:00Z"


def test_the_thumbnail_matches_what_the_rest_of_the_store_holds(monkeypatch):
    # The feed offers hqdefault; the API path and ytdlp_meta both store mqdefault.
    monkeypatch.setattr(feeds, "_fetch", _serve(_feed([
        _entry("newvid", "2026-09-05T10:00:00+00:00"),
        _entry("oldvid", "2026-08-20T10:00:00+00:00"),
    ])))
    thumb = feeds.get_new_videos_rss(CHANNEL, SINCE)[0]["thumbnail_url"]
    assert thumb == "https://i.ytimg.com/vi/newvid/mqdefault.jpg"


def test_nothing_new_is_an_empty_list_not_a_fallback(monkeypatch):
    # Distinct from None: the feed answered, there is simply nothing to collect.
    monkeypatch.setattr(feeds, "_fetch", _serve(_feed([
        _entry("oldvid", "2026-08-20T10:00:00+00:00"),
    ])))
    assert feeds.get_new_videos_rss(CHANNEL, SINCE) == []


def test_an_edit_does_not_resurrect_an_old_video(monkeypatch):
    # <updated> moves when a title or description is edited; <published> is the
    # publish time and the only field the window may be measured against.
    monkeypatch.setattr(feeds, "_fetch", _serve(_feed([
        _entry("oldvid", "2026-08-20T10:00:00+00:00", updated="2026-09-09T10:00:00+00:00"),
    ])))
    assert feeds.get_new_videos_rss(CHANNEL, SINCE) == []


def test_a_feed_whose_entries_are_all_new_is_refused_as_possibly_truncated(monkeypatch):
    # The feed carries ~15 entries. If the oldest is still inside the window,
    # older-but-newer-than-since videos may have been cut off -- the caller must
    # pay for the API rather than silently miss them.
    entries = [_entry(f"vid{i}", "2026-09-05T10:00:00+00:00") for i in range(15)]
    monkeypatch.setattr(feeds, "_fetch", _serve(_feed(entries)))
    assert feeds.get_new_videos_rss(CHANNEL, SINCE) is None


def test_an_empty_feed_is_refused_rather_than_read_as_no_new_videos(monkeypatch):
    monkeypatch.setattr(feeds, "_fetch", _serve(_feed([])))
    assert feeds.get_new_videos_rss(CHANNEL, SINCE) is None


def test_malformed_xml_falls_back(monkeypatch):
    monkeypatch.setattr(feeds, "_fetch", _serve(b"<feed><entry>"))
    assert feeds.get_new_videos_rss(CHANNEL, SINCE) is None


def test_an_http_error_falls_back(monkeypatch):
    err = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
    monkeypatch.setattr(feeds, "_fetch", _serve(err))
    assert feeds.get_new_videos_rss(CHANNEL, SINCE) is None


def test_an_entry_without_a_video_id_is_skipped_not_fatal(monkeypatch):
    broken = """
 <entry><title>kaputt</title><published>2026-09-05T10:00:00+00:00</published></entry>"""
    monkeypatch.setattr(feeds, "_fetch", _serve(_feed([
        broken,
        _entry("newvid", "2026-09-05T10:00:00+00:00"),
        _entry("oldvid", "2026-08-20T10:00:00+00:00"),
    ])))
    assert [v["video_id"] for v in feeds.get_new_videos_rss(CHANNEL, SINCE)] == ["newvid"]


def test_the_channel_title_can_be_read_without_touching_the_api(monkeypatch):
    monkeypatch.setattr(feeds, "_fetch", _serve(_feed([], channel_title="Küppersbusch TV")))
    assert feeds.get_channel(CHANNEL) == {"channel_id": CHANNEL, "title": "Küppersbusch TV"}


def test_a_channel_lookup_that_fails_returns_none(monkeypatch):
    monkeypatch.setattr(feeds, "_fetch", _serve(urllib.error.URLError("boom")))
    assert feeds.get_channel(CHANNEL) is None


def test_a_failed_fetch_is_retried_through_the_proxy(monkeypatch):
    calls = []
    monkeypatch.setenv("WEBSHARE_PROXY_URL", "http://user:pw@proxy.example:80")
    xml = _feed([
        _entry("newvid", "2026-09-05T10:00:00+00:00"),
        _entry("oldvid", "2026-08-20T10:00:00+00:00"),
    ])

    def fetch(url, proxy=None, timeout=None):
        calls.append(proxy)
        if proxy is None:
            raise urllib.error.URLError("blocked")
        return xml

    monkeypatch.setattr(feeds, "_fetch", fetch)
    assert feeds.get_new_videos_rss(CHANNEL, SINCE) is not None
    assert calls == [None, "http://user:pw@proxy.example:80"]


def test_no_proxy_skips_the_retry(monkeypatch):
    calls = []
    monkeypatch.setenv("WEBSHARE_PROXY_URL", "http://user:pw@proxy.example:80")
    monkeypatch.setattr(feeds, "_fetch", _serve(urllib.error.URLError("blocked"), record=calls))
    assert feeds.get_new_videos_rss(CHANNEL, SINCE, no_proxy=True) is None
    assert len(calls) == 1


def test_the_feed_url_is_the_channel_feed(monkeypatch):
    calls = []
    monkeypatch.setattr(feeds, "_fetch", _serve(_feed([]), record=calls))
    feeds.get_channel(CHANNEL)
    assert calls[0][0] == f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}"
