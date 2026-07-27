"""The sync requests must start before the data blob is parsed."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from export_harness import extract_script, node_available, render_export, run_node, video

SYNC = "https://sync.example.com"


def test_sync_requests_are_fired_before_the_data_blob():
    html = render_export([video("v1", "2026-01-01T00:00:00Z")], sync_url=SYNC)
    boot = html.index("__syncBoot")
    blob = html.index("const INDEX_B64")
    assert boot < blob


def test_whoami_and_state_are_requested_in_parallel():
    html = render_export([video("v1", "2026-01-01T00:00:00Z")], sync_url=SYNC)
    early = html[: html.index("const INDEX_B64")]
    assert "/api/whoami" in early
    assert "/api/state" in early


def test_sync_url_is_declared_exactly_once():
    html = render_export([video("v1", "2026-01-01T00:00:00Z")], sync_url=SYNC)
    assert html.count("const SYNC_URL") == 1


def test_no_sync_boot_without_sync_url():
    html = render_export([video("v1", "2026-01-01T00:00:00Z")])
    assert "__syncBoot" not in html


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_sync_requests_start_before_bootstrap_and_apply_without_empty_render():
    """Proves the parallel start and the fixed race in one Node run.

    A custom fetch stub records every call url into `fetchLog`. It is
    installed *before* the extracted script text, so when the early inline
    script's IIFE runs (synchronously, as soon as the script is parsed) its
    two fetch() calls land in the log immediately -- before any of the
    document's later code (bootstrap included) executes at all. That proves
    the requests start in parallel and ahead of the data-blob decode.

    A spy on applyFiltersAndSort records VIDEOS.length on every call. Since
    initSync() now runs at the end of bootstrap() (after VIDEOS is
    populated), every recorded call must see the final video count -- never
    0 -- which is exactly the race the brief asked to close: the old code
    fired initSync() independently of bootstrap()'s VIDEOS assignment, so a
    fast-resolving fetch chain (as here) could call applyServerState /
    applyFiltersAndSort while VIDEOS was still []."""
    videos = [
        video("v1", "2026-01-01T00:00:00Z"),
        video("v2", "2026-01-01T00:01:00Z"),
    ]
    html = render_export(videos, sync_url=SYNC)
    real_script = extract_script(html)

    setup = """
    localStorage.setItem('yt_sync_token', 'tok123');
    var fetchLog = [];
    globalThis.fetch = function (url, opts) {
      fetchLog.push(url);
      if (url.indexOf('/api/whoami') !== -1) {
        return Promise.resolve({
          status: 200,
          json: function () { return Promise.resolve({email: 'a@b.com', can_ingest: false}); }
        });
      }
      if (url.indexOf('/api/state') !== -1) {
        return Promise.resolve({
          status: 200,
          json: function () { return Promise.resolve({read: {}, bookmark: {}}); }
        });
      }
      return Promise.reject(new Error('unexpected fetch: ' + url));
    };
    """
    script = setup + "\n" + real_script + "\n" + "var earlyFetchLog = fetchLog.slice();"

    snippet = """
    var applyCalls = [];
    var _origApply = applyFiltersAndSort;
    applyFiltersAndSort = function (page) {
      applyCalls.push(VIDEOS.length);
      return _origApply(page);
    };

    bootstrap().then(function () {
      // Let initSync's fetch/.then chain (queued microtasks) settle too.
      return new Promise(function (resolve) { setTimeout(resolve, 0); });
    }).then(function () {
      console.log(JSON.stringify({
        earlyFetchLog: earlyFetchLog,
        finalFetchLog: fetchLog,
        applyCalls: applyCalls,
        videos: VIDEOS.length,
      }));
      // The real script's setInterval(pullServerState, ...) would otherwise
      // keep this process alive for 5 minutes; the assertions are already
      // captured above, so exit explicitly.
      process.exit(0);
    });
    """

    out = run_node(script, snippet)
    data = json.loads(out.strip().splitlines()[-1])

    # Both requests were already issued -- by the early inline script, at
    # script-parse time -- before any later code (including bootstrap) ran.
    assert data["earlyFetchLog"] == [SYNC + "/api/whoami", SYNC + "/api/state"]

    # initSync() must reuse window.__syncBoot from the early script rather
    # than re-issuing the requests.
    assert data["finalFetchLog"] == data["earlyFetchLog"]

    # applyFiltersAndSort ran (once via applyLang, once via initSync's
    # applyServerState) and never saw an empty VIDEOS array.
    assert data["applyCalls"], "expected applyFiltersAndSort to have run"
    assert all(n == data["videos"] for n in data["applyCalls"])


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_pull_server_state_before_bootstrap_merges_without_wiping_prerendered_grid():
    """Coordinator finding 1: visibilitychange/focus/setInterval wire up
    pullServerState() at parse time -- well before bootstrap()'s index/chunk-0
    decode resolves. Without the `dataReady` guard, pullServerState's
    applyFiltersAndSort(currentPage) call runs VIDEOS.filter(...) against an
    empty VIDEOS and overwrites the pre-rendered first page with the
    "no videos" placeholder on a mere tab-focus event. The server-state merge
    (readSet/bookmarkSet + localStorage) must still happen regardless."""
    videos = [video("v1", "2026-01-01T00:00:00Z")]
    html = render_export(videos, sync_url=SYNC)
    real_script = extract_script(html)

    setup = """
    // Prevent the script's own requestAnimationFrame(bootstrap) tail call
    // from running -- this test fires pullServerState() on its own, strictly
    // before bootstrap() ever starts, to isolate the race.
    globalThis.requestAnimationFrame = function () {};
    localStorage.setItem('yt_sync_token', 'tok123');
    globalThis.fetch = function (url, opts) {
      if (url.indexOf('/api/state') !== -1) {
        return Promise.resolve({
          status: 200,
          json: function () {
            return Promise.resolve({
              read: {v1: {value: 1, ts: '2026-01-01T00:00:00Z'}},
              bookmark: {}
            });
          }
        });
      }
      return Promise.reject(new Error('unexpected fetch: ' + url));
    };
    """
    script = setup + "\n" + real_script

    snippet = """
    // Simulate the pre-rendered first page already sitting in the DOM.
    var grid = document.getElementById('grid');
    grid.innerHTML = 'PRERENDERED-MARKER';

    // Fired the way visibilitychange/focus/setInterval would: bootstrap()
    // has never run, VIDEOS is still [], dataReady is still false.
    pullServerState();

    setTimeout(function () {
      console.log(JSON.stringify({
        gridHtml: grid.innerHTML,
        readMerged: JSON.parse(localStorage.getItem('yt_read') || '[]'),
        videos: VIDEOS.length,
        dataReady: dataReady,
      }));
      process.exit(0);
    }, 20);
    """

    out = run_node(script, snippet)
    data = json.loads(out.strip().splitlines()[-1])

    assert data["videos"] == 0, "bootstrap() must not have run in this test"
    assert data["dataReady"] is False
    assert data["gridHtml"] == "PRERENDERED-MARKER", (
        "pullServerState() must not re-render while VIDEOS is still empty"
    )
    assert data["readMerged"] == ["v1"], (
        "server state must still be merged into localStorage even though "
        "the render was skipped"
    )


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_bootstrap_establishes_login_state_when_decompression_unsupported():
    """Coordinator finding 2: when DecompressionStream is unavailable,
    bootstrap()'s catch branch used to return before ever calling initSync(),
    so a returning logged-in user saw the logged-out login form instead of
    their real status. The fix calls initSync() from the catch branch too;
    since `dataReady` never flips true on this path, the eventual
    applyServerState()/applyFiltersAndSort() call is skipped and the
    "needs a modern browser" message stays exactly as rendered."""
    videos = [video("v1", "2026-01-01T00:00:00Z")]
    html = render_export(videos, sync_url=SYNC)
    real_script = extract_script(html)

    setup = """
    delete globalThis.DecompressionStream;
    // This test drives bootstrap() itself exactly once; suppress the
    // script's own auto-triggered call so results aren't a mix of two runs.
    globalThis.requestAnimationFrame = function () {};
    localStorage.setItem('yt_sync_token', 'tok123');
    globalThis.fetch = function (url, opts) {
      if (url.indexOf('/api/whoami') !== -1) {
        return Promise.resolve({
          status: 200,
          json: function () { return Promise.resolve({email: 'a@b.com', can_ingest: false}); }
        });
      }
      if (url.indexOf('/api/state') !== -1) {
        return Promise.resolve({
          status: 200,
          json: function () { return Promise.resolve({read: {}, bookmark: {}}); }
        });
      }
      return Promise.reject(new Error('unexpected fetch: ' + url));
    };
    """
    script = setup + "\n" + real_script

    snippet = """
    var loggedInCalls = [];
    var _origShowLoggedIn = showSyncLoggedIn;
    showSyncLoggedIn = function (email) {
      loggedInCalls.push(email);
      return _origShowLoggedIn(email);
    };

    bootstrap().then(function () {
      return new Promise(function (resolve) { setTimeout(resolve, 20); });
    }).then(function () {
      console.log(JSON.stringify({
        gridHtml: document.getElementById('grid').innerHTML,
        loggedInCalls: loggedInCalls,
        videos: VIDEOS.length,
        dataReady: dataReady,
      }));
      process.exit(0);
    });
    """

    out = run_node(script, snippet)
    data = json.loads(out.strip().splitlines()[-1])

    assert "needs a modern browser" in data["gridHtml"], (
        "the fallback message must survive -- no later render may overwrite it"
    )
    assert data["videos"] == 0
    assert data["dataReady"] is False
    assert data["loggedInCalls"] == ["a@b.com"], (
        "initSync() must still run on the unsupported-browser path and "
        "reflect the visitor's real login state"
    )


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_dataready_flips_true_on_the_uncompressed_path_too():
    """`dataReady = true` sits after the {% if compressed %}/{% else %}
    block, so both branches share the same assignment -- but only the
    compressed path is exercised by the tests above. Render with
    compress=False (the --no-compress output) and confirm dataReady still
    flips once VIDEOS is populated there."""
    videos = [video("v1", "2026-01-01T00:00:00Z")]
    html = render_export(videos, sync_url=SYNC, compress=False)
    real_script = extract_script(html)

    setup = "globalThis.requestAnimationFrame = function () {};\n"
    script = setup + real_script

    snippet = """
    bootstrap().then(function () {
      console.log(JSON.stringify({videos: VIDEOS.length, dataReady: dataReady}));
      process.exit(0);
    });
    """

    out = run_node(script, snippet)
    data = json.loads(out.strip().splitlines()[-1])

    assert data["videos"] == 1
    assert data["dataReady"] is True
