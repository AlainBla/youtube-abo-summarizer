"""The archive's login-code entry: shown after "Link senden", and a valid code
stores the session right in this page -- the point of it in an installed web
app, where the mailed link would open (and log in) the browser instead."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from export_harness import extract_script, node_available, render_export, run_node, video

SYNC = "https://sync.example.com"

pytestmark = pytest.mark.skipif(not node_available(), reason="node not installed")

FAKE = """
var calls = [];
var verifyStatus = 200;
globalThis.fetch = function (url, opts) {
  calls.push({url: url, body: opts && opts.body ? JSON.parse(opts.body) : null});
  if (url.indexOf('/auth/verify-code') !== -1) {
    return Promise.resolve({status: verifyStatus, ok: verifyStatus === 200,
      json: function () { return Promise.resolve({session: 'tok-from-code'}); }});
  }
  return Promise.resolve({status: 200, ok: true,
    json: function () { return Promise.resolve({email: 'me@example.com', read: {}, bookmark: {}}); }});
};
function settle() { return new Promise(function (r) { setTimeout(r, 20); }); }
function done(o) { console.log(JSON.stringify(o)); process.exit(0); }
document.getElementById('sync-email').value = 'Me@Example.com';
"""


def _run(snippet):
    script = extract_script(render_export([video("v1", "2026-01-01T00:00:00Z")], sync_url=SYNC))
    return json.loads(run_node(script, FAKE + snippet).strip().splitlines()[-1])


def test_code_row_appears_after_sending_link():
    data = _run("""
    var row = document.getElementById('sync-code-row');
    row.style.display = 'none';
    var statuses = [];
    var orig = updateSyncStatus;
    updateSyncStatus = function (m) { statuses.push(m); orig(m); };
    syncRequestLink();
    settle().then(function () { done({display: row.style.display, statuses: statuses}); });
    """)
    assert data["display"] == ""
    assert any("Code eingeben" in m for m in data["statuses"])


def test_valid_code_stores_session_and_starts_sync():
    data = _run("""
    document.getElementById('sync-code').value = '123 456';
    syncVerifyCode();
    settle().then(function () {
      done({token: localStorage.getItem('yt_sync_token'),
            verify: calls.filter(function (c) { return c.url.indexOf('verify-code') !== -1; })[0].body,
            whoami: calls.some(function (c) { return c.url.indexOf('/api/whoami') !== -1; })});
    });
    """)
    assert data["token"] == "tok-from-code"
    assert data["verify"] == {"email": "Me@Example.com", "code": "123456"}
    assert data["whoami"] is True


@pytest.mark.parametrize("status,expected", [(400, "Code falsch oder abgelaufen"),
                                             (429, "Zu viele Versuche")])
def test_rejected_code_reports_why(status, expected):
    data = _run(f"""
    verifyStatus = {status};
    document.getElementById('sync-code').value = '123456';
    syncVerifyCode();
    settle().then(function () {{
      done({{token: localStorage.getItem('yt_sync_token'), status: document.getElementById('sync-status').textContent}});
    }});
    """)
    assert data["token"] is None
    assert expected in data["status"]


def test_malformed_code_never_reaches_server():
    data = _run("""
    document.getElementById('sync-code').value = '12a';
    syncVerifyCode();
    settle().then(function () { done({n: calls.filter(function (c) { return c.url.indexOf('verify-code') !== -1; }).length}); });
    """)
    assert data["n"] == 0
