"""?v=ID shows exactly one video out of the archive.

The archive is large, so the shared video is almost never among the
pre-rendered cards and its summary usually sits in a chunk that has not been
decoded when the page paints. The view therefore has three distinct states --
loading, found, not in archive -- and the "not in archive" verdict may only be
reached against the fully decoded index.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import renderer
from export_harness import extract_script, node_available, render_export, run_node, video


def _archive(n: int = 60) -> list[dict]:
    """More videos than fit on the pre-rendered first page and into one chunk."""
    return [video("v%03d" % i, "2026-01-01T00:%02d:00Z" % i) for i in range(n)]


def _script(videos=None) -> str:
    return extract_script(render_export(videos or _archive()))


def test_share_button_is_pre_rendered_on_the_first_page():
    html = render_export([video("v1", "2026-01-01T00:00:00Z")])
    assert "shareVideo('v1', this)" in html
    assert "Link kopieren" in html


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_the_loading_note_replaces_the_pre_rendered_cards_before_any_decode():
    """The note must be in place before bootstrap() -- i.e. from the parse-time
    hook, not after the index is decoded."""
    script = "location.search = '?v=v005';\n" + _script()
    snippet = """
    console.log(JSON.stringify({
      beforeBootstrap: document.getElementById('grid').innerHTML,
      barVisible: document.getElementById('single-bar').style.display !== 'none',
      controlsHidden: document.querySelector('.controls-bar').style.display === 'none'
    }));
    process.exit(0);
    """
    out = json.loads(run_node(script, snippet))
    assert "v005" in out["beforeBootstrap"]
    assert "wird geladen" in out["beforeBootstrap"]
    assert "video-card" not in out["beforeBootstrap"]
    assert out["barVisible"] and out["controlsHidden"]


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_a_video_from_a_later_chunk_is_rendered_alone():
    script = "location.search = '?v=v003';\n" + _script()
    snippet = """
    bootstrap().then(function () {
      // Its summary lives outside chunk 0, so renderPage() re-enters itself
      // once ensureChunks() resolves. That takes an unknown number of ticks
      // (gunzip is async), so wait for the card instead of for one timeout --
      // a fixed setTimeout(0) here made this test flaky.
      return new Promise(function (resolve, reject) {
        var ticks = 0;
        (function poll() {
          if (document.getElementById('grid').innerHTML.indexOf('data-video-id="v003"') !== -1) return resolve();
          if (++ticks > 200) return reject(new Error('card never appeared'));
          setTimeout(poll, 5);
        })();
      });
    }).then(function () {
      var grid = document.getElementById('grid').innerHTML;
      console.log(JSON.stringify({
        cards: (grid.match(/class="video-card/g) || []).length,
        hasWanted: grid.indexOf('data-video-id="v003"') !== -1,
        hasSummary: grid.indexOf('Summary of v003') !== -1,
        chunkOfWanted: VIDEOS.filter(function (v) { return v.video_id === 'v003'; })[0]._c
      }));
      process.exit(0);
    });
    """
    out = json.loads(run_node(script, snippet))
    assert out["cards"] == 1
    assert out["hasWanted"]
    assert out["hasSummary"]
    assert out["chunkOfWanted"] > 0, "test archive too small to exercise a later chunk"


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_an_unknown_id_reports_not_in_archive_rather_than_no_videos():
    script = "location.search = '?v=nosuchid';\n" + _script()
    snippet = """
    bootstrap().then(function () {
      var grid = document.getElementById('grid').innerHTML;
      console.log(JSON.stringify({
        grid: grid,
        notFound: grid.indexOf('nicht in diesem Archiv') !== -1,
        noVideos: grid.indexOf('Keine Videos gefunden') !== -1
      }));
      process.exit(0);
    });
    """
    out = json.loads(run_node(script, snippet))
    assert out["notFound"], out["grid"]
    assert not out["noVideos"]


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_show_all_restores_the_archive_and_cleans_the_url():
    script = "location.search = '?v=v005';\n" + _script()
    snippet = """
    var replaced = [];
    history.replaceState = function (a, b, url) { replaced.push(url); };
    bootstrap().then(function () {
      clearSingleVideo();
      return new Promise(function (r) { setTimeout(r, 0); });
    }).then(function () {
      var grid = document.getElementById('grid').innerHTML;
      console.log(JSON.stringify({
        cards: (grid.match(/class="video-card/g) || []).length,
        replaced: replaced,
        barHidden: document.getElementById('single-bar').style.display === 'none',
        controlsVisible: document.querySelector('.controls-bar').style.display !== 'none'
      }));
      process.exit(0);
    });
    """
    out = json.loads(run_node(script, snippet))
    assert out["cards"] == renderer.EXPORT_FIRST_PAGE, "full archive should paginate again"
    assert out["replaced"] and "v=" not in out["replaced"][-1]
    assert out["barHidden"] and out["controlsVisible"]


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_share_copies_a_url_that_reproduces_the_view():
    script = _script([video("v1", "2026-01-01T00:00:00Z")])
    snippet = """
    var copied = [];
    navigator.clipboard = {writeText: function (t) { copied.push(t); return Promise.resolve(); }};
    var btn = document.getElementById('share-btn-probe');
    btn.textContent = 'Link kopieren';
    shareVideo('v1', btn);
    setTimeout(function () {
      console.log(JSON.stringify({copied: copied, label: btn.textContent}));
      process.exit(0);
    }, 0);
    """
    out = json.loads(run_node(script, snippet))
    assert out["copied"] == ["https://example.com/x.html?v=v1"]
    assert out["label"] == "Kopiert"


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_without_the_parameter_nothing_changes():
    script = _script()
    snippet = """
    bootstrap().then(function () {
      var grid = document.getElementById('grid').innerHTML;
      console.log(JSON.stringify({
        cards: (grid.match(/class="video-card/g) || []).length,
        // The stub does not parse the markup's style="display:none", so
        // assert on what the code did: it must never have shown the bar.
        barDisplay: String(document.getElementById('single-bar').style.display),
        single: singleVideoId
      }));
      process.exit(0);
    });
    """
    out = json.loads(run_node(script, snippet))
    assert out["cards"] == renderer.EXPORT_FIRST_PAGE
    # "" is what setSingleChrome(true) writes; anything else means it never ran.
    assert out["barDisplay"] != "", "the single-video bar must stay hidden"
    assert out["single"] is None
