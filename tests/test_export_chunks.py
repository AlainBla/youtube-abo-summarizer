"""Tests for the export data split (index + summary chunks)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from renderer import _split_export_data, _summary_preview, _esc_html


def _video(vid, published, summary="<p>one</p><p>two</p>"):
    return {
        "video_id": vid,
        "channel_id": "UC1",
        "channel_title": "Channel",
        "title": "Title " + vid,
        "published_at": published,
        "published_at_display": "January 01, 2026",
        "duration": "10:00",
        "thumbnail_url": "https://i.ytimg.com/vi/%s/hq.jpg" % vid,
        "summary": summary,
        "summary_model": None,
        "transcript_error": None,
        "tags": ["Tag"],
    }


def test_index_is_sorted_newest_first():
    videos = [
        _video("a", "2026-01-01T00:00:00Z"),
        _video("c", "2026-03-01T00:00:00Z"),
        _video("b", "2026-02-01T00:00:00Z"),
    ]
    index, _ = _split_export_data(videos)
    assert [v["video_id"] for v in index] == ["c", "b", "a"]


def test_ties_break_on_video_id_descending():
    videos = [_video("a", "2026-01-01T00:00:00Z"), _video("b", "2026-01-01T00:00:00Z")]
    index, _ = _split_export_data(videos)
    assert [v["video_id"] for v in index] == ["b", "a"]


def test_index_carries_no_summary():
    index, _ = _split_export_data([_video("a", "2026-01-01T00:00:00Z")])
    assert "summary" not in index[0]
    assert index[0]["title"] == "Title a"


def test_chunks_follow_index_order_and_size():
    videos = [_video("v%03d" % i, "2026-01-%02dT00:00:00Z" % (i + 1)) for i in range(7)]
    index, chunks = _split_export_data(videos, chunk_size=3)
    assert [len(c) for c in chunks] == [3, 3, 1]
    for pos, entry in enumerate(index):
        assert entry["video_id"] in chunks[pos // 3]


def test_every_summary_appears_exactly_once():
    videos = [_video("v%d" % i, "2026-01-01T00:00:00Z") for i in range(5)]
    _, chunks = _split_export_data(videos, chunk_size=2)
    seen = [vid for c in chunks for vid in c]
    assert sorted(seen) == sorted(v["video_id"] for v in videos)


def test_video_without_summary_is_absent_from_chunk_but_present_in_index():
    index, chunks = _split_export_data([_video("a", "2026-01-01T00:00:00Z", summary=None)])
    assert [v["video_id"] for v in index] == ["a"]
    assert chunks == [{}]


def test_empty_input_yields_no_chunks():
    assert _split_export_data([]) == ([], [])


def test_summaries_are_sanitized():
    videos = [_video("a", "2026-01-01T00:00:00Z", summary="<p>hi</p><script>evil()</script>")]
    _, chunks = _split_export_data(videos)
    assert "script" not in chunks[0]["a"]


def test_summary_preview_splits_at_first_paragraph():
    preview, rest = _summary_preview("<p>one</p><p>two</p>")
    assert preview == "<p>one</p>"
    assert rest == "<p>two</p>"


def test_summary_preview_without_paragraph_end_has_no_rest():
    preview, rest = _summary_preview("<h3>only</h3>")
    assert preview == "<h3>only</h3>"
    assert rest == ""


def test_esc_html_matches_js_escaping():
    # Mirrors escHtml() in export.html.j2: & < > " and nothing else.
    assert _esc_html('a&b<c>d"e\'f') == "a&amp;b&lt;c&gt;d&quot;e'f"
    assert _esc_html(None) == ""


import json

import pytest

from export_harness import extract_script, node_available, render_export, run_node, video


def test_compressed_export_embeds_index_and_chunk_blobs():
    videos = [_video("v%03d" % i, "2026-01-01T00:%02d:00Z" % i) for i in range(7)]
    html = render_export(videos)
    assert "const INDEX_B64" in html
    assert "const SUM_B64" in html
    assert "const DATA_B64" not in html


def test_ui_code_is_parsed_before_the_data_blob():
    # Otherwise the pre-rendered cards are clickable before their onclick
    # handlers exist, and every click is a ReferenceError.
    html = render_export([_video("v1", "2026-01-01T00:00:00Z")])
    assert html.index("function buildCard") < html.index("const INDEX_B64")


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_browser_decodes_index_and_only_the_needed_chunk():
    # Use export_harness.video() (not the local _video()) because its default
    # summary embeds the video id ("Summary of v119"), which the last
    # assertion below checks for. %03d (not %02d) keeps the minute field a
    # fixed 3-digit width so its lexical (string) order matches numeric order
    # all the way to i=119 -- _split_export_data sorts on the raw
    # published_at string.
    videos = [video("v%03d" % i, "2026-01-01T00:%03d:00Z" % i) for i in range(120)]
    html = render_export(videos)
    script = extract_script(html)
    out = run_node(script, """
      bootstrap().then(function () {
        console.log(JSON.stringify({
          videos: VIDEOS.length,
          firstId: VIDEOS[0].video_id,
          firstChunk: VIDEOS[0]._c,
          lastChunk: VIDEOS[VIDEOS.length - 1]._c,
          chunkCount: SUM_B64.length,
          decoded: CHUNKS.filter(Boolean).length,
          summary: getSummary(VIDEOS[0]),
        }));
      });
    """)
    data = json.loads(out.strip().splitlines()[-1])
    assert data["videos"] == 120
    assert data["firstId"] == "v119"          # newest first
    assert data["firstChunk"] == 0
    assert data["lastChunk"] == 2             # 120 videos / 50 per chunk
    assert data["chunkCount"] == 3
    assert data["decoded"] == 1               # only chunk 0 decoded for page 1
    assert "Summary of v119" in data["summary"]
