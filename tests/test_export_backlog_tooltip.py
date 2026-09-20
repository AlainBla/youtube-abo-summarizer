"""The personal archive offers its history as a tooltip on the video count.

Three halves have to agree: export.py writes the record and reads the series
back, renderer.py embeds the result as `const BACKLOG`, and the page turns it
into the title attribute. The wording itself comes out of a pure function, so
every branch is pinned in Node without a DOM.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import renderer
from export_harness import extract_script, node_available, render_export, run_node, video

BACKLOG = {"now": 389, "d7": 412, "d30": 350, "d90": None}


def _script(backlog=BACKLOG):
    return extract_script(render_export([video("v1", "2026-01-01T00:00:00Z")],
                                        backlog=backlog))


def _tooltip(lang, backlog=BACKLOG):
    snippet = "console.log(JSON.stringify(backlogTooltipText(%s, I18N['%s'])));" % (
        json.dumps(backlog), lang)
    return json.loads(run_node(_script(backlog), snippet).strip().splitlines()[-1])


# ── what the renderer embeds ─────────────────────────────────────────────────

def test_a_personal_export_embeds_its_history(tmp_path):
    out = str(tmp_path / "yt.html")
    renderer.render_export_html([video("v1", "2026-01-01T00:00:00Z")], out, backlog=BACKLOG)
    with open(out, encoding="utf-8") as f:
        html = f.read()
    assert "const BACKLOG = " in html
    assert json.dumps(BACKLOG) in html
    # The dotted underline is the only hint that there is anything to hover.
    assert '<span id="video-count" class="has-backlog">' in html


def test_a_plain_export_embeds_none_and_marks_nothing(tmp_path):
    out = str(tmp_path / "export.html")
    renderer.render_export_html([video("v1", "2026-01-01T00:00:00Z")], out)
    with open(out, encoding="utf-8") as f:
        html = f.read()
    assert "const BACKLOG = null;" in html
    # The class is what marks the count as hoverable; the CSS rule for it is
    # in every export, the class on the element is not.
    assert '<span id="video-count">' in html


# ── the wording ──────────────────────────────────────────────────────────────

@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_the_german_tooltip_names_every_window_and_its_delta():
    assert _tooltip("de") == (
        "Bestand heute: 389\n"
        "vor 7 Tagen: 412 (−23)\n"
        "vor 30 Tagen: 350 (+39)\n"
        "vor 3 Monaten: —"
    )


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_the_english_tooltip_reads_the_same_way():
    assert _tooltip("en") == (
        "Backlog today: 389\n"
        "7 days ago: 412 (−23)\n"
        "30 days ago: 350 (+39)\n"
        "3 months ago: —"
    )


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_an_unchanged_count_is_neither_a_gain_nor_a_loss():
    flat = {"now": 389, "d7": 389, "d30": None, "d90": None}
    assert _tooltip("de", flat).splitlines()[1] == "vor 7 Tagen: 389 (±0)"


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_no_history_yields_no_text_at_all():
    assert _tooltip("de", None) == ""


# ── what lands on the element ────────────────────────────────────────────────

@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_the_title_is_set_before_the_data_blob_is_decoded():
    # The count is pre-rendered; a tooltip that waits for bootstrap() would be
    # dead for the seconds a large archive spends decoding.
    snippet = """
    console.log(JSON.stringify(document.getElementById('video-count').getAttribute('title')));
    process.exit(0);
    """
    out = json.loads(run_node(_script(), snippet).strip().splitlines()[-1])
    assert out.startswith("Bestand heute: 389")


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_a_language_switch_rewrites_the_tooltip_and_keeps_the_count():
    snippet = """
    setTimeout(function () {
      applyLang('en');
      console.log(JSON.stringify({
        title: document.getElementById('video-count').getAttribute('title'),
        count: document.getElementById('video-count').textContent
      }));
      process.exit(0);
    }, 300);
    """
    out = json.loads(run_node(_script(), snippet))
    assert out["title"].startswith("Backlog today: 389")
    assert out["count"] == "1 video"


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_an_export_without_history_sets_no_title():
    snippet = """
    setTimeout(function () {
      applyLang('en');
      console.log(JSON.stringify(
        document.getElementById('video-count').getAttribute('title')));
      process.exit(0);
    }, 300);
    """
    out = json.loads(run_node(_script(None), snippet))
    assert out is None


# ── the panel: what a phone gets instead of a hover ──────────────────────────

@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_tapping_the_count_opens_a_panel_with_the_same_text():
    # A phone has no hover, so the title attribute alone would hide the whole
    # feature from every touch device.
    snippet = """
    var panel = document.getElementById('backlog-panel');
    var out = {before: panel.hidden};
    toggleBacklogPanel();
    out.open = panel.hidden;
    out.text = panel.textContent;
    out.expanded = document.getElementById('video-count').getAttribute('aria-expanded');
    toggleBacklogPanel();
    out.closedAgain = panel.hidden;
    console.log(JSON.stringify(out));
    process.exit(0);
    """
    out = json.loads(run_node(_script(), snippet).strip().splitlines()[-1])
    assert out["before"] is True
    assert out["open"] is False
    assert out["closedAgain"] is True
    assert out["expanded"] == "true"
    assert out["text"] == (
        "Bestand heute: 389\n"
        "vor 7 Tagen: 412 (\u221223)\n"
        "vor 30 Tagen: 350 (+39)\n"
        "vor 3 Monaten: \u2014"
    )


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_the_count_announces_itself_as_a_button_for_assistive_tech():
    snippet = """
    var el = document.getElementById('video-count');
    console.log(JSON.stringify({
      role: el.getAttribute('role'),
      tabindex: el.getAttribute('tabindex'),
      controls: el.getAttribute('aria-controls'),
      expanded: el.getAttribute('aria-expanded')
    }));
    process.exit(0);
    """
    out = json.loads(run_node(_script(), snippet).strip().splitlines()[-1])
    assert out == {"role": "button", "tabindex": "0",
                   "controls": "backlog-panel", "expanded": "false"}


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_an_open_panel_follows_a_language_switch():
    snippet = """
    setTimeout(function () {
      toggleBacklogPanel();
      applyLang('en');
      console.log(JSON.stringify(document.getElementById('backlog-panel').textContent));
      process.exit(0);
    }, 300);
    """
    out = json.loads(run_node(_script(), snippet))
    assert out.startswith("Backlog today: 389")


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_an_export_without_history_has_no_panel_to_open():
    snippet = """
    toggleBacklogPanel();
    console.log(JSON.stringify(document.getElementById('backlog-panel').hidden));
    process.exit(0);
    """
    out = json.loads(run_node(_script(None), snippet).strip().splitlines()[-1])
    assert out is True


# ── export.py: record first, then read the series back ───────────────────────

export_stats = pytest.importorskip("export_stats")
export = pytest.importorskip("export", reason="export.py runtime deps unavailable")

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def stats_file(tmp_path, monkeypatch):
    path = tmp_path / "export_stats.jsonl"
    monkeypatch.setattr(export_stats, "STATS_PATH", path)
    return path


def test_the_first_run_records_itself_and_reports_only_today(stats_file):
    out = export.record_backlog("me@example.com", 30, True, None,
                                personal_count=389, total_count=5000, now=NOW)
    assert out == {"now": 389, "d7": None, "d30": None, "d90": None}
    assert len(export_stats.load(stats_file)) == 1


def test_a_later_run_sees_the_earlier_one(stats_file):
    export.record_backlog("me@example.com", 30, True, None,
                          personal_count=412, total_count=4800, now=NOW - timedelta(days=8))
    out = export.record_backlog("me@example.com", 30, True, None,
                                personal_count=389, total_count=5000, now=NOW)
    assert out["now"] == 389 and out["d7"] == 412


def test_changing_read_days_starts_a_new_series_instead_of_bending_the_old(stats_file):
    export.record_backlog("me@example.com", 30, True, None,
                          personal_count=412, total_count=4800, now=NOW - timedelta(days=8))
    # The same store, a stricter --read-days: a much smaller personal export,
    # which must not read as "the backlog collapsed".
    out = export.record_backlog("me@example.com", 7, True, None,
                                personal_count=120, total_count=5000, now=NOW)
    assert out == {"now": 120, "d7": None, "d30": None, "d90": None}
    # ... and the old series is still there, untouched, for the day the old
    # setting comes back.
    assert len(export_stats.load(stats_file)) == 2


def test_the_record_carries_the_whole_archive_size_too(stats_file):
    export.record_backlog("me@example.com", 30, False, 48,
                          personal_count=12, total_count=5000, now=NOW)
    entry = export_stats.load(stats_file)[0]
    assert entry["total_count"] == 5000
    assert entry["window"] == "hours:48"
    assert entry["personal_count"] == 12
