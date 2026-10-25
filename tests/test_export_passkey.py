"""The archive's passkey buttons hand off to the sync server's own pages.

WebAuthn binds a credential to the origin that created it, so neither a
file:// archive nor one on another host can run the ceremony itself; the
buttons navigate to the sync server instead, with the same redirect URI the
magic link uses. The manage page needs this page's session, which travels in
the fragment so it never reaches the server log.
"""
import json
import os
import sys
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from export_harness import extract_script, node_available, render_export, run_node, video

SYNC = "https://sync.example.com"


def _html(**kw):
    return render_export([video("v1", "2026-01-01T00:00:00Z")], **kw)


def test_buttons_present_with_sync_url():
    html = _html(sync_url=SYNC)
    assert 'id="sync-passkey-btn"' in html
    assert 'id="sync-passkeys-btn"' in html


def test_buttons_absent_without_sync_url():
    html = _html()
    assert "sync-passkey-btn" not in html
    assert "function syncPasskeyLogin" not in html


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_login_and_manage_targets():
    script = extract_script(_html(sync_url=SYNC))
    out = run_node(script, """
    localStorage.setItem('yt_sync_token', 'tok/1');
    syncPasskeyLogin();
    var login = window.location.href;
    syncManagePasskeys();
    console.log(JSON.stringify({login: login, manage: window.location.href,
                                redirect: loginRedirectUri()}));
    """)
    data = json.loads(out.strip().splitlines()[-1])

    login = urlparse(data["login"])
    assert f"{login.scheme}://{login.netloc}{login.path}" == SYNC + "/auth/passkey"
    assert parse_qs(login.query)["redirect_uri"] == [data["redirect"]]
    assert login.fragment == ""

    manage = urlparse(data["manage"])
    assert manage.path == "/auth/passkey/manage"
    assert parse_qs(manage.query)["redirect_uri"] == [data["redirect"]]
    assert manage.fragment == "session=tok%2F1"
