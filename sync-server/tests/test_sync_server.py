import pytest
import sync_server


# ── _valid_redirect_uri ────────────────────────────────────────────────────────

def test_valid_redirect_uri_base_url():
    assert sync_server._valid_redirect_uri("http://testserver/export.html") is True


def test_valid_redirect_uri_base_url_with_query():
    assert sync_server._valid_redirect_uri("http://testserver/x?a=1") is True


def test_valid_redirect_uri_file():
    assert sync_server._valid_redirect_uri("file:///home/user/export.html") is True


def test_valid_redirect_uri_foreign_origin():
    assert sync_server._valid_redirect_uri("https://evil.com/steal") is False


def test_valid_redirect_uri_empty():
    assert sync_server._valid_redirect_uri("") is False


# ── POST /auth/request-link ───────────────────────────────────────────────────

def test_request_link_sends_email(client, mock_smtp):
    r = client.post(
        "/auth/request-link",
        json={"email": "user@example.com", "redirect_uri": "http://testserver/"},
    )
    assert r.status_code == 200
    assert len(mock_smtp) == 1
    assert mock_smtp[0]["email"] == "user@example.com"
    assert "/auth/verify" in mock_smtp[0]["link"]
    assert "redirect_uri=" in mock_smtp[0]["link"]


def test_request_link_invalid_redirect_uri(client, mock_smtp):
    r = client.post(
        "/auth/request-link",
        json={"email": "user@example.com", "redirect_uri": "https://evil.com"},
    )
    assert r.status_code == 400
    assert len(mock_smtp) == 0


def test_request_link_missing_fields(client, mock_smtp):
    r = client.post("/auth/request-link", json={"email": "user@example.com"})
    assert r.status_code == 400
    assert len(mock_smtp) == 0


def test_request_link_rate_limit_per_email(client, mock_smtp):
    for _ in range(3):
        client.post(
            "/auth/request-link",
            json={"email": "user@example.com", "redirect_uri": "http://testserver/"},
        )
    r = client.post(
        "/auth/request-link",
        json={"email": "user@example.com", "redirect_uri": "http://testserver/"},
    )
    assert r.status_code == 200  # silently rate-limited, still 200
    assert len(mock_smtp) == 3  # 4th not sent


def test_request_link_allowlist_blocks(client, mock_smtp, monkeypatch):
    monkeypatch.setattr(sync_server, "ALLOWED_EMAILS", {"allowed@example.com"})
    r = client.post(
        "/auth/request-link",
        json={"email": "other@example.com", "redirect_uri": "http://testserver/"},
    )
    assert r.status_code == 200  # silently rejected
    assert len(mock_smtp) == 0


def test_request_link_allowlist_permits(client, mock_smtp, monkeypatch):
    monkeypatch.setattr(sync_server, "ALLOWED_EMAILS", {"allowed@example.com"})
    r = client.post(
        "/auth/request-link",
        json={"email": "allowed@example.com", "redirect_uri": "http://testserver/"},
    )
    assert r.status_code == 200
    assert len(mock_smtp) == 1


# ── GET /auth/verify ──────────────────────────────────────────────────────────

def test_verify_valid_token_creates_session(client):
    token = sync_server.serializer.dumps({"email": "new@example.com"})
    r = client.get(f"/auth/verify?token={token}&redirect_uri=http://testserver/x")
    assert r.status_code == 302
    location = r.headers["Location"]
    assert "#session=" in location
    session_uuid = location.split("#session=")[1]
    assert len(session_uuid) == 36  # UUID4


def test_verify_creates_user_on_first_login(client):
    import sqlite3
    token = sync_server.serializer.dumps({"email": "brand-new@example.com"})
    client.get(f"/auth/verify?token={token}&redirect_uri=http://testserver/")
    db = sqlite3.connect(sync_server.DB_PATH)
    row = db.execute(
        "SELECT email FROM users WHERE email=?", ("brand-new@example.com",)
    ).fetchone()
    db.close()
    assert row is not None


def test_verify_idempotent_for_existing_user(client):
    token = sync_server.serializer.dumps({"email": "existing@example.com"})
    client.get(f"/auth/verify?token={token}&redirect_uri=http://testserver/")
    r2 = token2 = sync_server.serializer.dumps({"email": "existing@example.com"})
    r = client.get(f"/auth/verify?token={token2}&redirect_uri=http://testserver/")
    assert r.status_code == 302  # no error on second login


def test_verify_expired_token(client, monkeypatch):
    from itsdangerous import SignatureExpired
    monkeypatch.setattr(
        sync_server.serializer,
        "loads",
        lambda *a, **kw: (_ for _ in ()).throw(SignatureExpired("expired")),
    )
    r = client.get("/auth/verify?token=anything&redirect_uri=http://testserver/")
    assert r.status_code == 400


def test_verify_bad_token(client):
    r = client.get("/auth/verify?token=notavalidtoken&redirect_uri=http://testserver/")
    assert r.status_code == 400


def test_verify_invalid_redirect_uri(client):
    token = sync_server.serializer.dumps({"email": "x@example.com"})
    r = client.get(f"/auth/verify?token={token}&redirect_uri=https://evil.com")
    assert r.status_code == 400


# ── GET /api/whoami ───────────────────────────────────────────────────────────

def test_whoami_no_token(client):
    r = client.get("/api/whoami")
    assert r.status_code == 401


def test_whoami_invalid_token(client):
    r = client.get("/api/whoami", headers={"Authorization": "Bearer notreal"})
    assert r.status_code == 401


def test_whoami_valid_token(client, session_token):
    r = client.get("/api/whoami", headers={"Authorization": f"Bearer {session_token}"})
    assert r.status_code == 200
    assert r.get_json()["email"] == "user@example.com"


# ── GET /api/state ────────────────────────────────────────────────────────────

def test_get_state_empty(client, session_token):
    r = client.get("/api/state", headers={"Authorization": f"Bearer {session_token}"})
    assert r.status_code == 200
    assert r.get_json() == {"read": {}, "bookmark": {}}


def test_get_state_returns_both_values(client, session_token):
    client.post(
        "/api/state",
        json={"read": {"vid1": {"value": 1, "ts": "2026-03-17T10:00:00Z"}}, "bookmark": {}},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    client.post(
        "/api/state",
        json={"read": {"vid1": {"value": 0, "ts": "2026-03-17T11:00:00Z"}}, "bookmark": {}},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    r = client.get("/api/state", headers={"Authorization": f"Bearer {session_token}"})
    data = r.get_json()
    assert data["read"]["vid1"]["value"] == 0  # cleared entry is returned
    assert data["read"]["vid1"]["ts"] == "2026-03-17T11:00:00Z"


# ── POST /api/state ───────────────────────────────────────────────────────────

def test_post_state_stores_entry(client, session_token):
    r = client.post(
        "/api/state",
        json={"read": {"abc123": {"value": 1, "ts": "2026-03-17T10:00:00Z"}}, "bookmark": {}},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["read"]["abc123"]["value"] == 1
    assert data["read"]["abc123"]["ts"] == "2026-03-17T10:00:00Z"


def test_post_state_server_newer_wins(client, session_token):
    # Establish server state at 12:00
    client.post(
        "/api/state",
        json={"read": {"vid1": {"value": 1, "ts": "2026-03-17T12:00:00Z"}}, "bookmark": {}},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    # Submit stale clear at 10:00
    r = client.post(
        "/api/state",
        json={"read": {"vid1": {"value": 0, "ts": "2026-03-17T10:00:00Z"}}, "bookmark": {}},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    data = r.get_json()
    assert data["read"]["vid1"]["value"] == 1  # server value preserved
    assert data["read"]["vid1"]["ts"] == "2026-03-17T12:00:00Z"


def test_post_state_client_newer_wins(client, session_token):
    client.post(
        "/api/state",
        json={"read": {"vid1": {"value": 1, "ts": "2026-03-17T10:00:00Z"}}, "bookmark": {}},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    r = client.post(
        "/api/state",
        json={"read": {"vid1": {"value": 0, "ts": "2026-03-17T12:00:00Z"}}, "bookmark": {}},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    data = r.get_json()
    assert data["read"]["vid1"]["value"] == 0  # newer clear wins
    assert data["read"]["vid1"]["ts"] == "2026-03-17T12:00:00Z"


def test_post_state_absent_entries_unchanged(client, session_token):
    # Two entries
    client.post(
        "/api/state",
        json={
            "read": {
                "vid1": {"value": 1, "ts": "2026-03-17T10:00:00Z"},
                "vid2": {"value": 1, "ts": "2026-03-17T10:00:00Z"},
            },
            "bookmark": {},
        },
        headers={"Authorization": f"Bearer {session_token}"},
    )
    # Update only vid1
    client.post(
        "/api/state",
        json={"read": {"vid1": {"value": 0, "ts": "2026-03-17T11:00:00Z"}}, "bookmark": {}},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    r = client.get("/api/state", headers={"Authorization": f"Bearer {session_token}"})
    data = r.get_json()
    assert data["read"]["vid2"]["value"] == 1  # untouched


def test_post_state_value_zero_propagates(client, session_token):
    client.post(
        "/api/state",
        json={"read": {"vid1": {"value": 1, "ts": "2026-03-17T10:00:00Z"}}, "bookmark": {}},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    r = client.post(
        "/api/state",
        json={"read": {"vid1": {"value": 0, "ts": "2026-03-17T11:00:00Z"}}, "bookmark": {}},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    data = r.get_json()
    assert data["read"]["vid1"]["value"] == 0


def test_post_state_bookmark_type(client, session_token):
    r = client.post(
        "/api/state",
        json={"read": {}, "bookmark": {"vid1": {"value": 1, "ts": "2026-03-17T10:00:00Z"}}},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    data = r.get_json()
    assert data["bookmark"]["vid1"]["value"] == 1
    assert "vid1" not in data["read"]


def test_post_state_video_id_too_long(client, session_token):
    r = client.post(
        "/api/state",
        json={"read": {"x" * 65: {"value": 1, "ts": "2026-03-17T10:00:00Z"}}, "bookmark": {}},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert r.status_code == 400


def test_post_state_too_many_entries(client, session_token):
    entries = {
        f"vid{i}": {"value": 1, "ts": "2026-03-17T10:00:00Z"} for i in range(2001)
    }
    r = client.post(
        "/api/state",
        json={"read": entries, "bookmark": {}},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert r.status_code == 400


def test_post_state_invalid_entry_format(client, session_token):
    r = client.post(
        "/api/state",
        json={"read": {"vid1": "not-an-object"}, "bookmark": {}},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert r.status_code == 400


def test_post_state_missing_ts(client, session_token):
    r = client.post(
        "/api/state",
        json={"read": {"vid1": {"value": 1}}, "bookmark": {}},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert r.status_code == 400


# ── DELETE /api/session ───────────────────────────────────────────────────────

def test_delete_session(client, session_token):
    r = client.delete(
        "/api/session", headers={"Authorization": f"Bearer {session_token}"}
    )
    assert r.status_code == 204
    # Subsequent whoami should fail
    r2 = client.get(
        "/api/whoami", headers={"Authorization": f"Bearer {session_token}"}
    )
    assert r2.status_code == 401


def test_delete_session_only_deletes_current(client, session_token):
    """A second session for the same user must remain valid after deleting one."""
    import sqlite3, uuid
    from datetime import datetime, timezone, timedelta
    # Create a second session
    db = sqlite3.connect(sync_server.DB_PATH)
    db.row_factory = sqlite3.Row
    user = db.execute(
        "SELECT id FROM users WHERE email=?", ("user@example.com",)
    ).fetchone()
    token2 = str(uuid.uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
        (token2, user["id"], now, expires_at),
    )
    db.commit()
    db.close()

    client.delete("/api/session", headers={"Authorization": f"Bearer {session_token}"})
    r = client.get("/api/whoami", headers={"Authorization": f"Bearer {token2}"})
    assert r.status_code == 200  # second session still valid
