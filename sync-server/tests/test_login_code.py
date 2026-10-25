"""The mailed six-digit login code: the way in for a device where the link
opens in the wrong place (an installed web app on iOS has its own storage)."""
import sqlite3

import pytest
import sync_server

EMAIL = "user@example.com"


def _request(client, email=EMAIL):
    return client.post("/auth/request-link",
                       json={"email": email, "redirect_uri": "http://testserver/yt.html"})


def _verify(client, code, email=EMAIL):
    return client.post("/auth/verify-code", json={"email": email, "code": code})


def _wrong(code):
    return f"{(int(code) + 1) % 10**6:06d}"


def test_mail_carries_link_and_code(client, mock_smtp):
    _request(client)
    sent = mock_smtp[0]
    assert "/auth/verify" in sent["link"]
    assert len(sent["code"]) == 6 and sent["code"].isdigit()


def test_mail_body_shows_the_code(monkeypatch):
    captured = {}

    class FakeSMTP:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, *a): pass
        def send_message(self, msg): captured["body"] = msg.get_content()

    monkeypatch.setattr(sync_server.smtplib, "SMTP", FakeSMTP)
    sync_server._send_magic_link(EMAIL, "http://x/link", "123456")
    assert "http://x/link" in captured["body"]
    assert "123 456" in captured["body"]


def test_code_logs_in(client, mock_smtp):
    _request(client)
    r = _verify(client, mock_smtp[0]["code"])
    assert r.status_code == 200
    token = r.get_json()["session"]
    who = client.get("/api/whoami", headers={"Authorization": f"Bearer {token}"}).get_json()
    assert who["email"] == EMAIL


def test_code_with_spaces_and_mixed_case_email(client, mock_smtp):
    _request(client)
    code = mock_smtp[0]["code"]
    assert _verify(client, f"{code[:3]} {code[3:]}", email=" User@Example.com ").status_code == 200


def test_code_is_single_use(client, mock_smtp):
    _request(client)
    code = mock_smtp[0]["code"]
    assert _verify(client, code).status_code == 200
    assert _verify(client, code).status_code == 400


def test_new_request_replaces_old_code(client, mock_smtp):
    _request(client)
    _request(client)
    old, new = mock_smtp[0]["code"], mock_smtp[1]["code"]
    if old != new:
        assert _verify(client, old).status_code == 400
    assert _verify(client, new).status_code == 200


def test_code_expires(client, mock_smtp, monkeypatch):
    _request(client)
    now = sync_server.time.time()
    monkeypatch.setattr(sync_server.time, "time", lambda: now + sync_server.LOGIN_CODE_TTL + 1)
    assert _verify(client, mock_smtp[0]["code"]).status_code == 400


def test_code_dies_after_max_attempts(client, mock_smtp):
    _request(client)
    code = mock_smtp[0]["code"]
    for _ in range(sync_server.LOGIN_CODE_MAX_ATTEMPTS):
        assert _verify(client, _wrong(code)).status_code == 400
    # Even the right code no longer works: the guesses used it up.
    assert _verify(client, code).status_code == 400


def test_code_bound_to_its_address(client, mock_smtp):
    _request(client)
    _request(client, email="other@example.com")
    assert _verify(client, mock_smtp[0]["code"], email="other@example.com").status_code == 400


def test_code_never_stored_in_clear(client, mock_smtp):
    _request(client)
    db = sqlite3.connect(sync_server.DB_PATH)
    stored = db.execute("SELECT code_hash FROM login_codes").fetchone()[0]
    db.close()
    assert mock_smtp[0]["code"] not in stored


def test_no_code_for_address_outside_allowlist(client, mock_smtp, monkeypatch):
    monkeypatch.setattr(sync_server, "ALLOWED_EMAILS", {"someone@example.com"})
    _request(client)
    assert mock_smtp == []
    assert _verify(client, "123456").status_code == 400


@pytest.mark.parametrize("body", [{}, {"email": EMAIL}, {"email": EMAIL, "code": "12345"},
                                  {"email": EMAIL, "code": "abcdef"}, {"code": "123456"}])
def test_malformed_requests(client, body):
    assert client.post("/auth/verify-code", json=body).status_code == 400


def test_verify_rate_limited_per_ip(client, mock_smtp):
    for _ in range(sync_server.CODE_RATE_LIMIT_MAX):
        _verify(client, "000000")
    assert _verify(client, "000000").status_code == 429


def test_preflight(client):
    assert client.options("/auth/verify-code").status_code == 200


def test_wrong_guesses_from_others_do_not_block_a_fresh_code(client, mock_smtp):
    """No per-address verify limit: a stranger's guesses can use up one code,
    but the owner's next code still works."""
    _request(client)
    for i in range(12):
        client.post("/auth/verify-code", json={"email": EMAIL, "code": "000000"},
                    environ_base={"REMOTE_ADDR": f"203.0.113.{i}"})
    _request(client)
    r = client.post("/auth/verify-code", json={"email": EMAIL, "code": mock_smtp[-1]["code"]},
                    environ_base={"REMOTE_ADDR": "198.51.100.1"})
    assert r.status_code == 200


def test_attempt_is_spent_before_the_comparison(client, mock_smtp):
    """The counter only ever moves up to the cap, and a used-up code stays
    unusable until it expires -- no read-then-write window to slip through."""
    _request(client)
    code = mock_smtp[0]["code"]
    for _ in range(sync_server.LOGIN_CODE_MAX_ATTEMPTS + 2):
        _verify(client, _wrong(code))
    db = sqlite3.connect(sync_server.DB_PATH)
    attempts = db.execute("SELECT attempts FROM login_codes").fetchone()[0]
    db.close()
    assert attempts == sync_server.LOGIN_CODE_MAX_ATTEMPTS
    assert _verify(client, code).status_code == 400
