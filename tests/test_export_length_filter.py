"""The controls bar can narrow the archive to short videos.

Durations reach the browser only as the display string the card shows
("7:12", "1:02:03"), so the filter has to read them back -- and 3.8 % of the
store has no duration at all, which must not quietly pass a "under 5 minutes"
filter.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from export_harness import extract_script, node_available, render_export, run_node, video

pytestmark = pytest.mark.skipif(not node_available(), reason="node not installed")

DURATIONS = {
    "short": "4:59",
    "five": "5:00",
    "medium": "19:30",
    "long": "1:02:03",
    "unknown": "",
}


def _script():
    videos = [
        video(vid, "2026-01-%02dT00:00:00Z" % (i + 1), duration=d)
        for i, (vid, d) in enumerate(DURATIONS.items())
    ]
    return extract_script(render_export(videos))


def _visible(max_minutes, extra=""):
    snippet = """
    setTimeout(function () {
      document.getElementById('length-filter').value = %s;
      %s
      applyFiltersAndSort(1);
      console.log(JSON.stringify(filtered.map(function (v) { return v.video_id; })));
      process.exit(0);
    }, 300);
    """ % (json.dumps(str(max_minutes)), extra)
    return set(json.loads(run_node(_script(), snippet)))


def test_all_videos_are_shown_by_default():
    assert _visible("") == set(DURATIONS)


def test_under_five_minutes_keeps_only_the_short_one():
    assert _visible(5) == {"short"}


def test_the_threshold_is_strict_so_an_exact_five_minutes_is_too_long():
    assert "five" not in _visible(5)
    assert "five" in _visible(10)


def test_the_hour_form_is_parsed_not_read_as_one_minute():
    """'1:02:03' must not be mistaken for 1 minute 2 seconds."""
    assert "long" not in _visible(30)
    assert "long" in _visible(90)


def test_a_video_without_a_duration_never_passes_a_length_filter():
    assert "unknown" not in _visible(60)
    assert "unknown" in _visible("")


def test_the_length_filter_composes_with_the_other_filters():
    visible = _visible(60, extra="document.getElementById('search').value = 'medium';")
    assert visible == {"medium"}


def test_both_languages_label_the_control():
    html = render_export([video("v1", "2026-01-01T00:00:00Z")])
    assert 'id="length-filter"' in html
    assert html.count("labelLength:") == 2
    assert html.count("lengthUnder:") == 2
