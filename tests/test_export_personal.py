"""A per-user export: unread, recently-added read, and bookmarked videos.

The filtered page is the one people open every day, so the full archive is
written beside it (<name>.full.html) and linked from the header -- and share
links point at that full archive, because a link into a filtered page is a
link that can dead-end for whoever receives it.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import export
from export_harness import extract_script, node_available, render_export, run_node, video

NOW = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)


def _v(vid, collected_at, published_at="2020-01-01T00:00:00Z"):
    return {"video_id": vid, "published_at": published_at, "collected_at": collected_at}


def _iso(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat()


def _kept(videos, read=(), bookmarked=(), days=30):
    picked = export.filter_personal(videos, set(read), set(bookmarked), now=NOW, days=days)
    return [v["video_id"] for v in picked]


# ── the selection rule ────────────────────────────────────────────────────────

def test_unread_videos_are_kept_however_old():
    assert _kept([_v("v1", _iso(400))]) == ["v1"]


def test_read_and_long_since_added_is_the_only_thing_dropped():
    assert _kept([_v("v1", _iso(40))], read=["v1"]) == []


def test_read_but_added_within_the_window_is_kept():
    assert _kept([_v("v1", _iso(10))], read=["v1"]) == ["v1"]


def test_a_bookmark_keeps_a_read_and_old_video():
    assert _kept([_v("v1", _iso(400))], read=["v1"], bookmarked=["v1"]) == ["v1"]


def test_the_window_is_the_arrival_date_not_the_publish_date():
    """Collected yesterday, published years ago -- that is the backfill case
    the 'date added' sort exists for, and it must survive the filter."""
    old = _v("v1", _iso(1), published_at="2019-05-01T00:00:00Z")
    assert _kept([old], read=["v1"]) == ["v1"]


def test_rows_without_collected_at_fall_back_to_the_publish_date():
    fresh = {"video_id": "v1", "published_at": _iso(2), "collected_at": None}
    stale = {"video_id": "v2", "published_at": _iso(200), "collected_at": None}
    assert _kept([fresh, stale], read=["v1", "v2"]) == ["v1"]


def test_an_undatable_row_is_kept_rather_than_silently_dropped():
    assert _kept([{"video_id": "v1", "published_at": "", "collected_at": None}], read=["v1"]) == ["v1"]


def test_the_window_length_is_configurable():
    assert _kept([_v("v1", _iso(40))], read=["v1"], days=60) == ["v1"]


def test_order_is_preserved():
    vids = [_v("v1", _iso(1)), _v("v2", _iso(2)), _v("v3", _iso(3))]
    assert _kept(vids) == ["v1", "v2", "v3"]


# ── where the full archive lands ──────────────────────────────────────────────

@pytest.mark.parametrize("output,expected", [
    ("yt.html", "yt.full.html"),
    ("/srv/www/yt.html", "/srv/www/yt.full.html"),
    ("export_2026-09-18_12-00.html", "export_2026-09-18_12-00.full.html"),
    ("archive", "archive.full.html"),
])
def test_the_full_archive_is_written_beside_the_filtered_page(output, expected):
    assert export.full_sidecar_path(output) == expected


# ── the link to it ────────────────────────────────────────────────────────────

def test_the_header_links_to_the_full_archive():
    html = render_export([video("v1", "2026-01-01T00:00:00Z")], full_url="yt.full.html")
    assert 'id="full-link"' in html
    assert 'href="yt.full.html"' in html
    assert "Vollst" in html  # "Vollständiges Archiv"


def test_an_ordinary_export_carries_no_such_link():
    html = render_export([video("v1", "2026-01-01T00:00:00Z")])
    assert 'id="full-link"' not in html


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_share_links_point_at_the_full_archive():
    script = extract_script(render_export([video("v1", "2026-01-01T00:00:00Z")],
                                          full_url="yt.full.html"))
    out = run_node(script, "console.log(singleVideoUrl('abc')); process.exit(0);")
    assert out.strip() == "https://example.com/yt.full.html?v=abc"


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_without_a_full_archive_share_links_stay_on_this_page():
    script = extract_script(render_export([video("v1", "2026-01-01T00:00:00Z")]))
    out = run_node(script, "console.log(singleVideoUrl('abc')); process.exit(0);")
    assert out.strip() == "https://example.com/x.html?v=abc"


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_a_video_missing_from_the_filtered_page_is_offered_in_the_full_archive():
    """The filtered page is missing most of the archive by design, so 'not
    here' has an obvious next step that a full export does not have."""
    script = ("location.search = '?v=zzz';\n"
              + extract_script(render_export([video("v1", "2026-01-01T00:00:00Z")],
                                             full_url="yt.full.html")))
    snippet = """
    setTimeout(function () {
      console.log(document.getElementById('grid').innerHTML);
      process.exit(0);
    }, 300);
    """
    out = run_node(script, snippet)
    assert "yt.full.html?v=zzz" in out


def test_both_languages_carry_the_new_strings():
    html = render_export([video("v1", "2026-01-01T00:00:00Z")], full_url="yt.full.html")
    for key in ("fullArchive", "singleTryFull"):
        assert html.count(key + ":") == 2, f"{key} missing from one of the JS I18N dicts"
