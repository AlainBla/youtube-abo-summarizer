"""The pre-rendered first page must match what buildCard() produces."""
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import renderer
from export_harness import extract_script, node_available, render_export, run_node, video


def _normalize(html: str) -> str:
    """Collapse whitespace between tags so formatting differences do not matter."""
    return re.sub(r">\s+<", "><", html).strip()


def test_first_page_cards_are_in_the_html_before_the_data_blob():
    n = renderer.EXPORT_FIRST_PAGE
    videos = [video("v%03d" % i, "2026-01-01T00:%02d:00Z" % i) for i in range(30)]
    html = render_export(videos)
    first_card = html.index('data-video-id="v029"')
    blob = html.index("const INDEX_B64")
    assert first_card < blob, "cards must precede the data blob so they paint first"
    # exactly one page of cards is pre-rendered
    assert html.count('class="video-card') == n
    boundary_id = "v%03d" % (30 - n)
    beyond_id = "v%03d" % (30 - n - 1)
    assert 'data-video-id="%s"' % boundary_id in html      # Nth newest
    assert 'data-video-id="%s"' % beyond_id not in html    # (N+1)th is not pre-rendered


def test_first_page_cards_precede_data_obj_when_uncompressed():
    # The compressed-path ordering check above only exercises "const INDEX_B64",
    # which does not exist when compress=False -- the uncompressed path has its
    # own blob (DATA_OBJ) and needs the same "cards paint before the blob"
    # guarantee verified independently.
    videos = [video("v%03d" % i, "2026-01-01T00:%02d:00Z" % i) for i in range(5)]
    html = render_export(videos, compress=False)
    first_card = html.index('data-video-id="v004"')
    blob = html.index("const DATA_OBJ")
    assert first_card < blob, "cards must precede the data blob so they paint first"


def test_pre_rendered_card_shows_summary_preview_and_toggle():
    html = render_export([video("v1", "2026-01-01T00:00:00Z")])
    assert "Summary of v1" in html
    assert "summary-details" in html


def test_page_size_is_rendered_from_the_pre_render_count():
    html = render_export([video("v1", "2026-01-01T00:00:00Z")])
    assert "const PAGE_SIZE = %d;" % renderer.EXPORT_FIRST_PAGE in html


def test_page_size_and_prerender_count_follow_a_patched_export_first_page(monkeypatch):
    # The two assertions above both use renderer.EXPORT_FIRST_PAGE's *current*
    # value, so they would pass unchanged even if PAGE_SIZE were hardcoded
    # back to a literal 20 (it happens to equal EXPORT_FIRST_PAGE today). Patch
    # the constant to a value that isn't 20 and prove the pre-rendered card
    # count, the rendered PAGE_SIZE constant, and the pre-render/omit boundary
    # all move together -- this is what "EXPORT_FIRST_PAGE is the single
    # source" actually means.
    monkeypatch.setattr(renderer, "EXPORT_FIRST_PAGE", 7)
    videos = [video("v%03d" % i, "2026-01-01T00:%02d:00Z" % i) for i in range(15)]
    html = render_export(videos)
    assert html.count('class="video-card') == 7
    assert "const PAGE_SIZE = 7;" in html
    assert 'data-video-id="v008"' in html      # 7th newest (15 - 7)
    assert 'data-video-id="v007"' not in html  # 8th newest is not pre-rendered


def test_filter_controls_do_not_restore_stale_values_on_reload():
    # A restored <select> value would contradict the static grid until
    # bootstrap() runs, and a toggle in that window blanks the grid.
    html = render_export([video("v1", "2026-01-01T00:00:00Z")])
    for control in ('id="channel-filter"', 'id="tag-filter"', 'id="read-filter"',
                    'id="bookmark-filter"', 'id="sort"', 'id="date-filter"'):
        tag_start = html.index(control)
        tag = html[html.rindex("<", 0, tag_start):html.index(">", tag_start)]
        assert 'autocomplete="off"' in tag, control


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_pre_rendered_markup_matches_buildCard_output():
    videos = [
        video("v1", "2026-03-01T00:00:00Z"),
        video("v2", "2026-02-01T00:00:00Z", tags=[], duration="", summary=None,
              transcript_error="country_blocked"),
        video("v3", "2026-01-01T00:00:00Z", title='Quote " & <tag>'),
    ]
    html = render_export(videos)
    script = extract_script(html)
    js_cards = run_node(script, """
      bootstrap().then(function () {
        console.log(JSON.stringify(VIDEOS.map(buildCard)));
      });
    """)
    built = json.loads(js_cards.strip().splitlines()[-1])

    grid = re.search(r'<div id="grid">(.*?)</div>\s*<!-- /grid -->', html, re.S)
    assert grid, "grid marker not found"
    pre_rendered = re.findall(r"<article .*?</article>", grid.group(1), re.S)
    assert len(pre_rendered) == len(built) == 3

    for pre, js in zip(pre_rendered, built):
        assert _normalize(pre) == _normalize(js)


def test_hydration_script_runs_before_the_data_blob():
    html = render_export([video("v1", "2026-01-01T00:00:00Z")])
    hydrate = html.index("yt_read")
    blob = html.index("const INDEX_B64")
    assert hydrate < blob, "read/bookmark hydration must run before the data blob"
    assert "is-read" in html


def test_hydration_script_runs_immediately_after_the_grid():
    # The assertions in test_hydration_script_runs_before_the_data_blob above
    # both pass today for the wrong reason: 'yt_read' already appears inside
    # the (much later) main UI script as `const COOKIE_READ = 'yt_read'`, and
    # 'is-read' already appears in the CSS and in buildCard(). Neither
    # assertion proves a *new* script exists right after the grid marker.
    # This test pins the hydration script to its own <script> block, sitting
    # strictly between "<!-- /grid -->" and the main UI script.
    html = render_export([video("v1", "2026-01-01T00:00:00Z")])
    grid_end = html.index("<!-- /grid -->")
    ui_script = html.index("const CHUNK_SIZE")
    assert grid_end < ui_script

    after_grid = html[grid_end:ui_script]
    match = re.search(r"<script>(.*?)</script>", after_grid, re.S)
    assert match, "no dedicated script between the grid marker and the main UI script"
    hydration_source = match.group(1)

    assert "yt_read" in hydration_source
    assert "yt_bookmark" in hydration_source
    assert "#grid .video-card" in hydration_source
    assert "is-read" in hydration_source
    assert "is-active" in hydration_source


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_hydration_script_paints_read_and_bookmark_state_on_the_right_cards():
    # export_harness.run_node()/extract_script() are designed for the main UI
    # script and rely on dom_stub.js, whose document.querySelectorAll() always
    # returns [] and whose document.querySelector() always returns a generic
    # placeholder element -- neither is selector-aware, so they cannot express
    # "find the card whose data-video-id matches and check which classes
    # landed on it". That harness is therefore not used here. Instead this
    # test extracts the real hydration <script> from the real rendered HTML,
    # parses the real pre-rendered <article> markup for the two cards with a
    # small purpose-built (not-faked) DOM that implements only the handful of
    # operations the hydration IIFE actually performs, seeds localStorage with
    # one read id and one bookmark id, executes the extracted script, and
    # reads back which classes actually landed on which card/button.
    html = render_export([
        video("v1", "2026-01-01T00:00:00Z"),
        video("v2", "2026-01-02T00:00:00Z"),
    ])
    grid_end = html.index("<!-- /grid -->")
    ui_script = html.index("const CHUNK_SIZE")
    after_grid = html[grid_end:ui_script]
    match = re.search(r"<script>(.*?)</script>", after_grid, re.S)
    assert match, "no dedicated script between the grid marker and the main UI script"
    hydration_source = match.group(1)

    grid = re.search(r'<div id="grid">(.*?)</div>\s*<!-- /grid -->', html, re.S)
    assert grid, "grid marker not found"
    articles = re.findall(r"<article .*?</article>", grid.group(1), re.S)
    assert len(articles) == 2

    node_script = """
'use strict';
const ARTICLES = %s;

const __localStore = { yt_read: JSON.stringify(['v1']), yt_bookmark: JSON.stringify(['v2']) };
globalThis.localStorage = {
  getItem: function (k) { return k in __localStore ? __localStore[k] : null; },
};

function makeButton() {
  var classes = new Set();
  return { classList: { add: function (c) { classes.add(c); } }, _classes: classes };
}

function makeCard(html) {
  var id = html.match(/data-video-id="([^"]*)"/)[1];
  var classes = new Set(html.match(/^<article class="([^"]*)"/)[1].split(/\\s+/).filter(Boolean));
  var readBtn = /class="read-btn"/.test(html) ? makeButton() : null;
  var bookmarkBtn = /class="bookmark-btn"/.test(html) ? makeButton() : null;
  return {
    getAttribute: function (name) { return name === 'data-video-id' ? id : null; },
    classList: { add: function (c) { classes.add(c); } },
    querySelector: function (sel) {
      if (sel === '.read-btn') return readBtn;
      if (sel === '.bookmark-btn') return bookmarkBtn;
      return null;
    },
    _classes: classes,
    _readBtn: readBtn,
    _bookmarkBtn: bookmarkBtn,
  };
}

const CARDS = ARTICLES.map(makeCard);
globalThis.document = {
  querySelectorAll: function (sel) { return sel === '#grid .video-card' ? CARDS : []; },
};

%s

console.log(JSON.stringify(CARDS.map(function (c) {
  return {
    id: c.getAttribute('data-video-id'),
    classes: Array.from(c._classes),
    readActive: c._readBtn ? Array.from(c._readBtn._classes) : null,
    bookmarkActive: c._bookmarkBtn ? Array.from(c._bookmarkBtn._classes) : null,
  };
})));
""" % (json.dumps(articles), hydration_source)

    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "run.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(node_script)
        proc = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, "node failed:\n" + proc.stderr[:2000]
        result = json.loads(proc.stdout.strip().splitlines()[-1])

    by_id = {r["id"]: r for r in result}

    assert "is-read" in by_id["v1"]["classes"]
    assert "is-bookmarked" not in by_id["v1"]["classes"]
    assert "is-active" in by_id["v1"]["readActive"]
    assert "is-active" not in by_id["v1"]["bookmarkActive"]

    assert "is-bookmarked" in by_id["v2"]["classes"]
    assert "is-read" not in by_id["v2"]["classes"]
    assert "is-active" in by_id["v2"]["bookmarkActive"]
    assert "is-active" not in by_id["v2"]["readActive"]
