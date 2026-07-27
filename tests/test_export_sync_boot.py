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
