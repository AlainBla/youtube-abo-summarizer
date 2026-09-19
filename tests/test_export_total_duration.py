"""The results count names the runtime of what is currently shown.

Durations reach the browser only as the display string the card shows
("7:12", "1:02:03"), the same source the length filter reads back, and ~4 % of
the store carries none at all -- those contribute nothing, so the total is a
lower bound, never a wrong number.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import renderer

from export_harness import extract_script, node_available, render_export, run_node, video

# Node runs the page's half of every pairing below, so the whole module needs it.
pytestmark = pytest.mark.skipif(not node_available(), reason="node not installed")


def _script(videos=None):
    videos = videos or [video("v1", "2026-01-01T00:00:00Z")]
    return extract_script(render_export(videos))


def _format(seconds):
    snippet = """
    console.log(JSON.stringify(%s.map(formatTotalDuration)));
    process.exit(0);
    """ % json.dumps(seconds)
    return json.loads(run_node(_script(), snippet))


def test_dd_hh_mm_padding_and_rounding_down_to_the_minute():
    assert _format([0, 59, 60, 3600, 90061, 86400]) == [
        "00:00:00",
        "00:00:00",
        "00:00:01",
        "00:01:00",
        "01:01:01",
        "01:00:00",
    ]


def test_days_are_not_capped_at_two_digits():
    assert _format([100 * 86400 + 3660]) == ["100:01:01"]


def test_the_sum_skips_videos_without_a_duration():
    videos = [
        video("a", "2026-01-01T00:00:00Z", duration="1:02:03"),
        video("b", "2026-01-02T00:00:00Z", duration="7:12"),
        video("c", "2026-01-03T00:00:00Z", duration=""),
    ]
    snippet = """
    console.log(JSON.stringify(totalDurationSeconds(%s)));
    process.exit(0);
    """ % json.dumps([{"duration": v["duration"]} for v in videos])
    assert json.loads(run_node(_script(videos), snippet)) == 3723 + 432


def test_the_count_line_carries_the_total_and_follows_the_filters():
    videos = [
        video("a", "2026-01-01T00:00:00Z", duration="1:02:03", title="Alpha"),
        video("b", "2026-01-02T00:00:00Z", duration="7:12", title="Beta"),
    ]
    snippet = """
    setTimeout(function () {
      var out = [];
      out.push(document.getElementById('results-count').textContent);
      document.getElementById('search').value = 'Beta';
      applyFiltersAndSort(1);
      out.push(document.getElementById('results-count').textContent);
      console.log(JSON.stringify(out));
      process.exit(0);
    }, 300);
    """
    both, one = json.loads(run_node(_script(videos), snippet))
    assert both == "2 Videos · 00:01:09"
    assert one == "1 Video · 00:00:07"


def test_a_selection_with_no_known_runtime_shows_no_separator():
    """An empty result, or one made only of duration-less videos, has nothing
    to report -- "00:00:00" there would read as a measured zero."""
    videos = [
        video("a", "2026-01-01T00:00:00Z", duration="", title="Alpha"),
        video("b", "2026-01-02T00:00:00Z", duration="10:00", title="Beta"),
    ]
    snippet = """
    setTimeout(function () {
      var out = [];
      document.getElementById('search').value = 'Alpha';
      applyFiltersAndSort(1);
      out.push(document.getElementById('results-count').textContent);
      document.getElementById('search').value = 'nothing matches this';
      applyFiltersAndSort(1);
      out.push(document.getElementById('results-count').textContent);
      console.log(JSON.stringify(out));
      process.exit(0);
    }, 300);
    """
    unknown, none = json.loads(run_node(_script(videos), snippet))
    assert unknown == "1 Video"
    assert none == "0 Videos"


def test_both_languages_take_the_duration():
    html = render_export([video("v1", "2026-01-01T00:00:00Z")])
    assert html.count("results: function(n, dur)") == 2


# --- The header, which states the whole archive rather than the selection ----


@pytest.mark.parametrize(
    "display, secs",
    [
        ("7:12", 432),
        ("1:02:03", 3723),
        ("0:59", 59),
        ("", None),
        (None, None),
        ("PT1H2M3S", None),  # the store's raw form never reaches the page
        ("nonsense", None),
    ],
)
def test_the_server_reads_the_same_display_string_the_page_does(display, secs):
    assert renderer._duration_seconds(display) == secs


def test_the_server_and_the_page_format_a_total_alike():
    """Both halves print the header line, so they must agree to the character."""
    cases = [0, 59, 60, 3600, 86400, 90061, 100 * 86400 + 3660]
    assert [renderer._format_total_duration(c) for c in cases] == _format(cases)


def test_the_header_label_is_empty_when_nothing_carries_a_duration():
    assert renderer._total_duration_label([{"duration": ""}, {"duration": None}]) == ""
    assert renderer._total_duration_label([]) == ""
    assert renderer._total_duration_label([{"duration": "7:12"}, {"duration": ""}]) == "00:00:07"


def test_the_pre_rendered_header_carries_the_archive_runtime():
    html = render_export([
        video("a", "2026-01-01T00:00:00Z", duration="1:02:03"),
        video("b", "2026-01-02T00:00:00Z", duration="7:12"),
    ])
    assert "2 Videos &middot; 00:01:09</p>" in html
    assert "const TOTAL_DURATION = '00:01:09';" in html


def test_an_archive_without_any_duration_keeps_the_header_as_it_was():
    html = render_export([video("a", "2026-01-01T00:00:00Z", duration="")])
    assert "1 Video</p>" in html
    assert "const TOTAL_DURATION = '';" in html


def test_the_header_survives_a_language_switch_and_ignores_the_filters():
    videos = [
        video("a", "2026-01-01T00:00:00Z", duration="1:02:03", title="Alpha"),
        video("b", "2026-01-02T00:00:00Z", duration="7:12", title="Beta"),
    ]
    snippet = """
    setTimeout(function () {
      var out = [];
      out.push(document.getElementById('page-meta').textContent);
      applyLang('en');
      out.push(document.getElementById('page-meta').textContent);
      document.getElementById('search').value = 'Beta';
      applyFiltersAndSort(1);
      out.push(document.getElementById('page-meta').textContent);
      console.log(JSON.stringify(out));
      process.exit(0);
    }, 300);
    """
    de, en, filtered = json.loads(run_node(_script(videos), snippet))
    assert de.endswith("2 Videos \u00b7 00:01:09")
    assert en.endswith("2 videos \u00b7 00:01:09")
    # The header states the archive; only #results-count follows the filters.
    assert filtered == en
