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
import re

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


def test_data_blob_script_is_the_last_element_before_body_close():
    # The whole point of the two-script split is that the browser can finish
    # parsing/executing the UI code before it has to tokenize the multi-MB
    # base64 payload. If a future edit moves other markup or script after the
    # data-blob script, that guarantee silently breaks. Nothing but whitespace
    # and the closing tags may follow the data script's </script>.
    html = render_export([_video("v1", "2026-01-01T00:00:00Z")])
    tail = html[html.rindex("</script>") + len("</script>"):]
    assert re.fullmatch(r"\s*</body>\s*</html>\s*", tail), tail


def test_bootstrap_is_scheduled_via_raf_not_called_synchronously():
    # A bare, synchronous `bootstrap();` call would run (and could read
    # INDEX_B64/SUM_B64) before the data-blob script has even finished
    # parsing when scheduled from elsewhere -- it must only ever be invoked
    # from inside the requestAnimationFrame callback in the trailing script.
    html = render_export([_video("v1", "2026-01-01T00:00:00Z")])
    assert "requestAnimationFrame(function () { bootstrap(); });" in html
    calls = [m.start() for m in re.finditer(r"\bbootstrap\(\);", html)]
    assert len(calls) == 1, "expected exactly one bootstrap() call site"
    call_pos = calls[0]
    prefix = "function () { "
    assert html[call_pos - len(prefix):call_pos] == prefix


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


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_search_waits_for_every_chunk_then_matches_summary_text():
    videos = [_video("v%03d" % i, "2026-01-01T00:%02d:00Z" % i) for i in range(120)]
    videos[0]["summary"] = "<p>needle in the oldest video</p>"
    html = render_export(videos)
    script = extract_script(html)
    out = run_node(script, """
      bootstrap().then(function () {
        document.getElementById('search').value = 'needle';
        applyFiltersAndSort();
        return ensureAllChunks().then(function () {
          applyFiltersAndSort();
          console.log(JSON.stringify({
            decoded: CHUNKS.filter(Boolean).length,
            hits: filtered.map(function (v) { return v.video_id; }),
          }));
        });
      });
    """)
    data = json.loads(out.strip().splitlines()[-1])
    assert data["decoded"] == 3       # every chunk decoded for the search
    assert data["hits"] == ["v000"]


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_search_before_prefetch_runs_still_finds_late_chunk_match_and_stays_stable():
    # Pins the regression parked during Task 2: getSearchText() caches
    # v.search_text on first call. A search issued before every chunk was
    # decoded used to see an empty summary for videos whose chunk wasn't
    # loaded yet, bake that into search_text as title-only text, and never
    # re-derive it -- so the match was lost forever even once the chunk
    # loaded. The applyFiltersAndSort() gate added in this task must make
    # that path unreachable by deferring the whole search until every chunk
    # is ready, so getSearchText() only ever runs once summary text is
    # actually available.
    #
    # Use export_harness.video() (not the local _video()) for the same
    # %03d-width reasoning as test_browser_decodes_index_and_only_the_needed_chunk
    # above -- lexical order of published_at must match numeric order.
    videos = [video("v%03d" % i, "2026-01-01T00:%03d:00Z" % i) for i in range(120)]
    videos[0]["summary"] = "<p>zzzneedlezzz appears only in the oldest video</p>"
    html = render_export(videos)
    script = extract_script(html)
    out = run_node(script, """
      function waitUntil(pred) {
        return new Promise(function (resolve) {
          (function poll() {
            if (pred()) return resolve();
            setTimeout(poll, 5);
          })();
        });
      }
      bootstrap().then(function () {
        // Issue the search immediately: only chunk 0 is decoded so far, and
        // prefetchChunks()'s idle callback (a setTimeout macrotask) has not
        // fired yet. This exercises the app's own recovery path, not a
        // test-forced ensureAllChunks() call.
        document.getElementById('search').value = 'zzzneedlezzz';
        applyFiltersAndSort();
        return waitUntil(allChunksReady).then(function () {
          applyFiltersAndSort();
          var firstHits = filtered.map(function (v) { return v.video_id; });
          // Repeat the identical search. If getSearchText() had poisoned its
          // cache during the earlier partial-load attempt, this would now
          // return stale (title-only) search text and lose the match.
          applyFiltersAndSort();
          var secondHits = filtered.map(function (v) { return v.video_id; });
          console.log(JSON.stringify({firstHits: firstHits, secondHits: secondHits}));
        });
      });
    """)
    data = json.loads(out.strip().splitlines()[-1])
    assert data["firstHits"] == ["v000"]
    assert data["secondHits"] == ["v000"]


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_chunk_decode_failure_clears_the_promise_cache_for_retry():
    """Coordinator finding 2: ensureChunk() caches the in-flight promise in
    CHUNK_PROMISES so concurrent callers share one decode. If a decode
    rejects (corrupted/truncated base64, a transient browser hiccup, etc.)
    and that rejected promise stays cached, every later caller -- a retried
    page render, another filter change -- would replay the same rejection
    forever, even after the underlying data would decode fine. The catch in
    ensureChunk() must clear CHUNK_PROMISES[k] before rethrowing so a
    subsequent call starts a fresh decode instead."""
    videos = [video("v%03d" % i, "2026-01-01T00:%03d:00Z" % i) for i in range(60)]
    html = render_export(videos)
    script = extract_script(html)
    out = run_node(script, """
      bootstrap().then(function () {
        var goodB64 = SUM_B64[1];
        // Valid base64 that decodes to plain text, not a gzip stream --
        // DecompressionStream rejects on it.
        SUM_B64[1] = 'bm90LWEtdmFsaWQtZ3ppcA==';
        return ensureChunk(1).then(function () {
          throw new Error('expected the corrupted chunk to reject');
        }, function (firstErr) {
          // Simulate the data becoming available again (e.g. a retried
          // fetch) and confirm the retry is not poisoned by the cached
          // rejection.
          SUM_B64[1] = goodB64;
          return ensureChunk(1);
        });
      }).then(function (map) {
        console.log(JSON.stringify({retried: true, hasChunk: !!map, count: Object.keys(map).length}));
      }).catch(function (err) {
        console.log(JSON.stringify({retried: false, error: String(err)}));
      });
    """)
    data = json.loads(out.strip().splitlines()[-1])
    assert data["retried"] is True, (
        "retrying ensureChunk() after a failed decode must not replay the "
        "cached rejection: " + str(data)
    )
    assert data["hasChunk"] is True
    assert data["count"] == 10  # chunk 1 covers videos 50-59


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_render_page_surfaces_chunk_decode_failure_instead_of_hanging():
    """Coordinator finding 2: renderPage's ensureChunks(pending).then(...)
    had no rejection handler. A chunk that fails to decode after a
    successful index decode used to leave "results-count" stuck on the
    loading text forever, with no visible sign anything went wrong. The
    added .catch() must surface the failure there instead."""
    videos = [video("v%03d" % i, "2026-01-01T00:%03d:00Z" % i) for i in range(60)]
    html = render_export(videos)
    script = extract_script(html)
    out = run_node(script, """
      bootstrap().then(function () {
        // Corrupt the chunk that page 3 (indices 40-59) needs beyond
        // chunk 0 -- page 1/2 are chunk 0 only (indices 0-39), page 3
        // reaches into chunk 1 (40-49 still chunk 0, 50-59 chunk 1)... use
        // page 3 with PAGE_SIZE 20: slice is [40,59], spanning both chunks.
        SUM_B64[1] = 'bm90LWEtdmFsaWQtZ3ppcA==';
        renderPage(3);
        var immediateText = document.getElementById('results-count').textContent;
        return new Promise(function (resolve) {
          setTimeout(function () {
            resolve({
              immediateText: immediateText,
              settledText: document.getElementById('results-count').textContent,
            });
          }, 20);
        });
      }).then(function (result) {
        console.log(JSON.stringify(result));
      });
    """)
    data = json.loads(out.strip().splitlines()[-1])
    # dom_stub.js sets navigator.languages = ['de'], so detectLang() resolves
    # to German regardless of the archive's embedded default language.
    loading_text = "Daten werden geladen…"
    assert data["immediateText"] == loading_text
    # Pin the exact showChunkLoadError() text, not just "changed" -- a
    # successful render would also change this text away from the loading
    # message, so a mere inequality would not actually prove the .catch() ran.
    assert data["settledText"] == "Error loading video data."


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_search_surfaces_chunk_decode_failure_instead_of_hanging():
    """Coordinator finding 2, search path: a full-text search waits for every
    chunk via ensureAllChunks() (search demands complete coverage, since a
    partial set could silently drop hits) and that call also had no rejection
    handler. This is the call site most likely to actually hit a bad chunk,
    since it is the one that always needs *all* of them, not just the page
    currently on screen."""
    videos = [video("v%03d" % i, "2026-01-01T00:%03d:00Z" % i) for i in range(60)]
    html = render_export(videos)
    script = extract_script(html)
    out = run_node(script, """
      bootstrap().then(function () {
        // Corrupt a chunk beyond the one already decoded for page 1, before
        // prefetchChunks()'s idle callback (a setTimeout macrotask) gets a
        // chance to decode it first.
        SUM_B64[1] = 'bm90LWEtdmFsaWQtZ3ppcA==';
        document.getElementById('search').value = 'needle';
        applyFiltersAndSort();
        var immediateText = document.getElementById('results-count').textContent;
        return new Promise(function (resolve) {
          setTimeout(function () {
            resolve({
              immediateText: immediateText,
              settledText: document.getElementById('results-count').textContent,
            });
          }, 20);
        });
      }).then(function (result) {
        console.log(JSON.stringify(result));
      });
    """)
    data = json.loads(out.strip().splitlines()[-1])
    loading_text = "Daten werden geladen…"
    assert data["immediateText"] == loading_text
    assert data["settledText"] == "Error loading video data."


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_bootstrap_resolves_on_empty_archive_without_throwing():
    # With zero videos, chunks == [] so SUM_B64 == []. The chunk-0 decode
    # must be skipped (there is nothing to decode), not attempted against a
    # missing SUM_B64[0] -- that used to throw inside the unawaited
    # requestAnimationFrame callback (an unhandled rejection: no cards, no
    # error message, populateChannelFilter/populateTagFilter/applyLang never
    # ran).
    html = render_export([])
    script = extract_script(html)
    out = run_node(script, """
      bootstrap().then(function () {
        console.log(JSON.stringify({videos: VIDEOS.length}));
      });
    """)
    data = json.loads(out.strip().splitlines()[-1])
    assert data["videos"] == 0
