"""The retry button on cards without a summary.

A reader who may ingest gets "Erneut versuchen" on a card whose video has no
summary, once the video is a day old. The click goes through doIngest() -- the
Ingest field's own path -- so the worker fetches the transcript again and
falls back to Whisper.
"""
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from export_harness import extract_script, node_available, render_export, run_node, video

SYNC = "https://sync.example.com"
needs_node = pytest.mark.skipif(not node_available(), reason="node not installed")


def _no_summary(vid, published="2026-01-01T00:00:00Z", **kw):
    return video(vid, published, summary=None, transcript_error="unavailable", **kw)


def _normalize(html):
    return re.sub(r">\s+<", "><", html).strip()


def test_only_cards_without_a_summary_carry_the_button_and_only_with_sync():
    videos = [video("withsumm001", "2026-01-02T00:00:00Z"), _no_summary("nosumm00001")]
    html = render_export(videos, sync_url=SYNC)
    grid = re.search(r'<div id="grid">(.*?)</div>\s*<!-- /grid -->', html, re.S).group(1)
    cards = re.findall(r"<article .*?</article>", grid, re.S)
    assert "retry-btn" not in cards[0]
    assert 'onclick="retryVideo(\'nosumm00001\', this)" hidden>Erneut versuchen</button>' in cards[1]
    assert 'data-published="2026-01-01T00:00:00Z"' in cards[1]

    plain = render_export(videos)
    assert 'class="retry-btn"' not in plain


@needs_node
def test_pre_rendered_retry_button_matches_buildCard():
    videos = [video("v1", "2026-03-01T00:00:00Z"),
              _no_summary("v2", "2026-02-01T00:00:00Z"),
              video("v3", "2026-01-01T00:00:00Z", summary=None, transcript_error="country_blocked")]
    html = render_export(videos, sync_url=SYNC)
    built = json.loads(run_node(extract_script(html), """
      bootstrap().then(function () { console.log(JSON.stringify(VIDEOS.map(buildCard))); });
    """).strip().splitlines()[-1])
    grid = re.search(r'<div id="grid">(.*?)</div>\s*<!-- /grid -->', html, re.S).group(1)
    pre = re.findall(r"<article .*?</article>", grid, re.S)
    assert len(pre) == len(built) == 3
    for a, b in zip(pre, built):
        assert _normalize(a) == _normalize(b)


@needs_node
def test_retry_is_due_one_day_after_publication():
    html = render_export([_no_summary("v1")], sync_url=SYNC)
    out = json.loads(run_node(extract_script(html), """
      var t = Date.parse('2026-01-01T00:00:00Z');
      console.log(JSON.stringify([
        retryDue('2026-01-01T00:00:00Z', t + 23 * 3600e3),
        retryDue('2026-01-01T00:00:00Z', t + 24 * 3600e3),
        retryDue('2026-01-01T00:00:00Z', t + 30 * 86400e3),
        retryDue('', t),
        retryDue('kaputt', t)
      ]));
      process.exit(0);
    """))
    assert out == [False, True, True, False, False]


@needs_node
def test_the_click_goes_through_the_ingest_path_and_marks_the_button_queued():
    html = render_export([_no_summary("abcdefgh123")], sync_url=SYNC)
    out = json.loads(run_node(extract_script(html), """
      var posted = [];
      globalThis.fetch = function (url, opts) {
        if (String(url).indexOf('/api/ingest') !== -1) posted.push(JSON.parse(opts.body).video_id);
        return Promise.resolve({status: 202, json: function () { return Promise.resolve({}); }});
      };
      setTimeout(function () {
        var btn = {disabled: false};
        retryVideo('abcdefgh123', btn);
        setTimeout(function () {
          var ds = document.getElementById('sync-ingest').dataset;
          console.log(JSON.stringify({posted: posted, result: ds.ingestResult,
                                      queued: retryQueuedIds.has('abcdefgh123'),
                                      disabled: btn.disabled}));
          process.exit(0);
        }, 300);
      }, 300);
    """))
    assert out == {"posted": ["abcdefgh123"], "result": "queued", "queued": True, "disabled": True}


@needs_node
def test_both_languages_carry_the_retry_strings():
    html = render_export([_no_summary("v1")], sync_url=SYNC)
    for key in ("retryBtn", "retryQueued"):
        assert html.count(key + ":") == 2, key
