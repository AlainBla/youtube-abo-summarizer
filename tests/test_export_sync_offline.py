"""Read/bookmark changes made while logged out must reach the server later.

syncToggle() used to return before stamping a local timestamp when there was
no session, and applyServerState() only pushes entries that carry one -- so a
reading session spent logged out stayed on that one device for good, and a
server entry for the same video overwrote it on the next login.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from export_harness import extract_script, node_available, render_export, run_node, video

SYNC = "https://sync.example.com"

pytestmark = pytest.mark.skipif(not node_available(), reason="node not installed")

# A fake sync server: GET returns `serverState`, every POST is recorded and
# answered 200. Installed after the page script so it replaces the stub fetch.
FAKE_SERVER = """
var serverState = {read: {}, bookmark: {}};
var posts = [];
globalThis.fetch = function (url, opts) {
  if (opts && opts.method === 'POST') {
    posts.push(JSON.parse(opts.body));
    return Promise.resolve({status: 200, ok: true,
      json: function () { return Promise.resolve(serverState); }});
  }
  return Promise.resolve({status: 200, ok: true,
    json: function () { return Promise.resolve(serverState); }});
};
function settle() { return new Promise(function (r) { setTimeout(r, 10); }); }
function done(obj) { console.log(JSON.stringify(obj)); process.exit(0); }
"""


def _run(snippet: str) -> dict:
    script = extract_script(render_export([video("v1", "2026-01-01T00:00:00Z")], sync_url=SYNC))
    out = run_node(script, FAKE_SERVER + snippet)
    return json.loads(out.strip().splitlines()[-1])


def test_logged_out_toggle_is_stamped_and_not_sent():
    data = _run("""
    window.toggleRead('vidA');
    settle().then(function () {
      done({ts: JSON.parse(localStorage.getItem('yt_read_ts') || '{}'), posts: posts});
    });
    """)
    assert "vidA" in data["ts"]
    assert data["posts"] == []


def test_first_merge_after_login_pushes_logged_out_changes():
    data = _run("""
    window.toggleRead('vidA');
    window.toggleBookmark('vidB');
    localStorage.setItem('yt_sync_token', 'tok');
    applyServerState({read: {}, bookmark: {}}).then(function (n) {
      done({n: n, posts: posts});
    });
    """)
    assert data["n"] == 2
    assert data["posts"][0]["read"]["vidA"]["value"] == 1
    assert data["posts"][0]["bookmark"]["vidB"]["value"] == 1


def test_logged_out_change_beats_an_older_server_entry():
    data = _run("""
    window.toggleRead('vidA');
    localStorage.setItem('yt_sync_token', 'tok');
    applyServerState({read: {vidA: {value: 0, ts: '2020-01-01T00:00:00Z'}}, bookmark: {}})
      .then(function () { done({read: readSet.has('vidA'), posts: posts}); });
    """)
    assert data["read"] is True
    assert data["posts"][0]["read"]["vidA"]["value"] == 1


def test_large_push_is_split_below_the_server_limit():
    data = _run("""
    localStorage.setItem('yt_sync_token', 'tok');
    var ts = {};
    for (var i = 0; i < 1200; i++) { readSet.add('v' + i); ts['v' + i] = '2026-01-01T00:00:00Z'; }
    localStorage.setItem('yt_read_ts', JSON.stringify(ts));
    applyServerState({read: {}, bookmark: {}}).then(function (n) {
      done({n: n, sizes: posts.map(function (p) {
        return Object.keys(p.read).length + Object.keys(p.bookmark).length;
      })});
    });
    """)
    assert data["n"] == 1200
    assert sum(data["sizes"]) == 1200
    assert max(data["sizes"]) <= 2000


def test_full_resync_uploads_untracked_marks():
    # Marks set before the fix -- in readSet, but with no timestamp -- are
    # invisible to a normal merge; the manual resync stamps and sends them.
    data = _run("""
    localStorage.setItem('yt_sync_token', 'tok');
    readSet.add('old1');
    bookmarkSet.add('old2');
    serverState = {read: {known: {value: 1, ts: '2026-01-01T00:00:00Z'}}, bookmark: {}};
    applyServerState(serverState).then(function (before) {
      posts = [];
      return syncFullResync().then(function (n) {
        done({before: before, n: n, posts: posts,
              status: document.getElementById('sync-status').textContent});
      });
    });
    """)
    assert data["before"] == 0
    assert data["n"] == 2
    assert data["posts"][0]["read"]["old1"]["value"] == 1
    assert data["posts"][0]["bookmark"]["old2"]["value"] == 1
    assert "2" in data["status"]


def test_full_resync_without_session_does_nothing():
    data = _run("""
    readSet.add('old1');
    syncFullResync().then(function (n) { done({n: n, posts: posts}); });
    """)
    assert data["n"] is None
    assert data["posts"] == []
