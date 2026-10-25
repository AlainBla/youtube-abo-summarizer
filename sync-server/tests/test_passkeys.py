"""Passkey (WebAuthn) login, end to end against a software authenticator.

The authenticator below builds real attestation and assertion responses — an
EC P-256 key, "none" attestation, ES256 signatures — so py_webauthn verifies
them exactly as it would a browser's. Nothing in the verification is mocked.
"""
import hashlib
import json
import os
import sqlite3
import struct

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

import sync_server

ORIGIN = "http://testserver"
RP_ID = "testserver"
FLAGS_UP_UV = 0x01 | 0x04
FLAG_AT = 0x40


class SoftAuthenticator:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(16)
        self.sign_count = 0

    def _cose_key(self) -> bytes:
        nums = self.key.public_key().public_numbers()
        return cbor2.dumps({
            1: 2, 3: -7, -1: 1,
            -2: nums.x.to_bytes(32, "big"),
            -3: nums.y.to_bytes(32, "big"),
        })

    @staticmethod
    def _client_data(kind: str, challenge: str, origin: str) -> bytes:
        return json.dumps({
            "type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False,
        }).encode()

    def create(self, options: dict, origin: str = ORIGIN) -> dict:
        client_data = self._client_data("webauthn.create", options["challenge"], origin)
        auth_data = (
            hashlib.sha256(options["rp"]["id"].encode()).digest()
            + bytes([FLAGS_UP_UV | FLAG_AT])
            + struct.pack(">I", self.sign_count)
            + b"\0" * 16
            + struct.pack(">H", len(self.credential_id))
            + self.credential_id
            + self._cose_key()
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        cid = bytes_to_base64url(self.credential_id)
        return {
            "id": cid, "rawId": cid, "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "attestationObject": bytes_to_base64url(attestation),
            },
            "clientExtensionResults": {},
        }

    def get(self, options: dict, origin: str = ORIGIN) -> dict:
        self.sign_count += 1
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        auth_data = (
            hashlib.sha256(options["rpId"].encode()).digest()
            + bytes([FLAGS_UP_UV])
            + struct.pack(">I", self.sign_count)
        )
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        cid = bytes_to_base64url(self.credential_id)
        return {
            "id": cid, "rawId": cid, "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(signature),
            },
            "clientExtensionResults": {},
        }


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _register(client, token, authenticator, name="Laptop"):
    options = client.post("/auth/passkey/register/options", headers=_auth(token)).get_json()
    return client.post(
        "/auth/passkey/register/verify",
        headers=_auth(token),
        json={"credential": authenticator.create(options), "name": name},
    )


def _login(client, authenticator, redirect_uri="http://testserver/yt.html", origin=ORIGIN):
    options = client.post("/auth/passkey/login/options").get_json()
    credential = authenticator.get(options, origin=origin)
    return client.post(
        "/auth/passkey/login/verify",
        json={"credential": credential, "redirect_uri": redirect_uri},
    ), credential


@pytest.fixture
def authenticator():
    return SoftAuthenticator()


@pytest.fixture
def registered(client, session_token, authenticator):
    r = _register(client, session_token, authenticator)
    assert r.status_code == 201, r.get_json()
    return authenticator


# ── Pages ─────────────────────────────────────────────────────────────────────

def test_login_page_renders(client):
    r = client.get("/auth/passkey?redirect_uri=http://testserver/yt.html")
    assert r.status_code == 200
    assert b'const MODE = "login"' in r.data
    assert b'"http://testserver/yt.html"' in r.data


def test_manage_page_renders(client):
    r = client.get("/auth/passkey/manage?redirect_uri=file:///home/u/yt.html")
    assert r.status_code == 200
    assert b'const MODE = "manage"' in r.data


@pytest.mark.parametrize("path", ["/auth/passkey", "/auth/passkey/manage"])
def test_pages_reject_foreign_redirect(client, path):
    assert client.get(f"{path}?redirect_uri=https://evil.com/").status_code == 400


# ── Registration ──────────────────────────────────────────────────────────────

def test_register_options_require_session(client):
    assert client.post("/auth/passkey/register/options").status_code == 401


def test_register_options_shape(client, session_token):
    opts = client.post("/auth/passkey/register/options", headers=_auth(session_token)).get_json()
    assert opts["rp"]["id"] == RP_ID
    assert opts["user"]["name"] == "user@example.com"
    # The user handle is the row id, never the address.
    assert b"@" not in base64url_to_bytes(opts["user"]["id"])
    assert opts["authenticatorSelection"]["residentKey"] == "required"
    assert opts["authenticatorSelection"]["userVerification"] == "required"


def test_register_stores_passkey(client, session_token, registered):
    r = client.get("/api/passkeys", headers=_auth(session_token))
    keys = r.get_json()["passkeys"]
    assert [k["name"] for k in keys] == ["Laptop"]
    assert keys[0]["id"] == bytes_to_base64url(registered.credential_id)
    assert keys[0]["last_used"] is None


def test_register_excludes_existing(client, session_token, registered):
    opts = client.post("/auth/passkey/register/options", headers=_auth(session_token)).get_json()
    assert [c["id"] for c in opts["excludeCredentials"]] == [
        bytes_to_base64url(registered.credential_id)
    ]


def test_register_challenge_is_single_use(client, session_token, authenticator):
    options = client.post("/auth/passkey/register/options", headers=_auth(session_token)).get_json()
    credential = authenticator.create(options)
    first = client.post("/auth/passkey/register/verify", headers=_auth(session_token),
                        json={"credential": credential})
    again = client.post("/auth/passkey/register/verify", headers=_auth(session_token),
                        json={"credential": credential})
    assert first.status_code == 201
    assert again.status_code == 400


def test_register_rejects_wrong_origin(client, session_token, authenticator):
    options = client.post("/auth/passkey/register/options", headers=_auth(session_token)).get_json()
    r = client.post(
        "/auth/passkey/register/verify",
        headers=_auth(session_token),
        json={"credential": authenticator.create(options, origin="https://evil.com")},
    )
    assert r.status_code == 400
    assert client.get("/api/passkeys", headers=_auth(session_token)).get_json()["passkeys"] == []


def test_register_challenge_bound_to_user(client, session_token, authenticator, app):
    """A challenge issued to one session cannot complete another's registration."""
    options = client.post("/auth/passkey/register/options", headers=_auth(session_token)).get_json()
    other = _other_session("other@example.com")
    r = client.post("/auth/passkey/register/verify", headers=_auth(other),
                    json={"credential": authenticator.create(options)})
    assert r.status_code == 400


# ── Login ─────────────────────────────────────────────────────────────────────

def test_login_issues_session(client, registered):
    r, _ = _login(client, registered)
    assert r.status_code == 200, r.get_json()
    redirect = r.get_json()["redirect"]
    assert redirect.startswith("http://testserver/yt.html#session=")
    token = redirect.split("#session=", 1)[1]
    who = client.get("/api/whoami", headers=_auth(token)).get_json()
    assert who["email"] == "user@example.com"


def test_login_updates_sign_count_and_last_used(client, session_token, registered):
    _login(client, registered)
    db = sqlite3.connect(sync_server.DB_PATH)
    count, last_used = db.execute("SELECT sign_count, last_used FROM passkeys").fetchone()
    db.close()
    assert count == 1
    assert last_used is not None


def test_login_rejects_replayed_assertion(client, registered):
    first, credential = _login(client, registered)
    assert first.status_code == 200
    replay = client.post("/auth/passkey/login/verify",
                         json={"credential": credential, "redirect_uri": "http://testserver/"})
    assert replay.status_code == 400


def test_login_rejects_unknown_passkey(client, registered):
    stranger = SoftAuthenticator()
    r, _ = _login(client, stranger)
    assert r.status_code == 401
    assert r.get_json()["error"] == "unknown passkey"


def test_login_rejects_bad_signature(client, registered):
    options = client.post("/auth/passkey/login/options").get_json()
    credential = registered.get(options)
    impostor = SoftAuthenticator()
    impostor.credential_id = registered.credential_id
    forged = impostor.get(options)
    credential["response"]["signature"] = forged["response"]["signature"]
    r = client.post("/auth/passkey/login/verify",
                    json={"credential": credential, "redirect_uri": "http://testserver/"})
    assert r.status_code == 401


def test_login_rejects_wrong_origin(client, registered):
    r, _ = _login(client, registered, origin="https://evil.com")
    assert r.status_code == 401


def test_login_rejects_foreign_redirect(client, registered):
    r, _ = _login(client, registered, redirect_uri="https://evil.com/")
    assert r.status_code == 400


def test_login_accepts_file_redirect(client, registered):
    r, _ = _login(client, registered, redirect_uri="file:///home/u/yt.html")
    assert r.status_code == 200
    assert r.get_json()["redirect"].startswith("file:///home/u/yt.html#session=")


def test_login_rejects_expired_challenge(client, registered, monkeypatch):
    options = client.post("/auth/passkey/login/options").get_json()
    credential = registered.get(options)
    now = sync_server.time.time()
    monkeypatch.setattr(sync_server.time, "time",
                        lambda: now + sync_server.WEBAUTHN_CHALLENGE_TTL + 1)
    r = client.post("/auth/passkey/login/verify",
                    json={"credential": credential, "redirect_uri": "http://testserver/"})
    assert r.status_code == 400


def test_login_rejects_registration_challenge(client, session_token, registered):
    """A challenge minted for registration does not answer a login."""
    options = client.post("/auth/passkey/register/options", headers=_auth(session_token)).get_json()
    credential = registered.get({"challenge": options["challenge"], "rpId": RP_ID})
    r = client.post("/auth/passkey/login/verify",
                    json={"credential": credential, "redirect_uri": "http://testserver/"})
    assert r.status_code == 400


def test_login_honours_shrunk_allowlist(client, registered, monkeypatch):
    monkeypatch.setattr(sync_server, "ALLOWED_EMAILS", {"someone-else@example.com"})
    r, _ = _login(client, registered)
    assert r.status_code == 403


def test_login_options_rate_limited(client):
    for _ in range(sync_server.PASSKEY_RATE_LIMIT_MAX):
        assert client.post("/auth/passkey/login/options").status_code == 200
    assert client.post("/auth/passkey/login/options").status_code == 429


def test_login_options_discoverable(client):
    opts = client.post("/auth/passkey/login/options").get_json()
    assert opts["rpId"] == RP_ID
    assert opts.get("allowCredentials", []) == []
    assert opts["userVerification"] == "required"


# ── Management ────────────────────────────────────────────────────────────────

def test_delete_passkey(client, session_token, registered):
    cid = bytes_to_base64url(registered.credential_id)
    assert client.delete(f"/api/passkeys/{cid}", headers=_auth(session_token)).status_code == 204
    assert client.get("/api/passkeys", headers=_auth(session_token)).get_json()["passkeys"] == []
    r, _ = _login(client, registered)
    assert r.status_code == 401


def test_delete_other_users_passkey_is_404(client, registered):
    other = _other_session("other@example.com")
    cid = bytes_to_base64url(registered.credential_id)
    assert client.delete(f"/api/passkeys/{cid}", headers=_auth(other)).status_code == 404


def test_passkey_endpoints_require_session(client):
    assert client.get("/api/passkeys").status_code == 401
    assert client.delete("/api/passkeys/x").status_code == 401
    assert client.post("/auth/passkey/register/verify", json={}).status_code == 401


def test_magic_link_still_works(client, mock_smtp, registered):
    """Passkeys are an addition: the magic link stays the fallback."""
    client.post("/auth/request-link",
                json={"email": "user@example.com", "redirect_uri": "http://testserver/"})
    link = mock_smtp[0]["link"].replace("http://testserver", "")
    r = client.get(link)
    assert r.status_code == 302
    assert "#session=" in r.headers["Location"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _other_session(email):
    db = sqlite3.connect(sync_server.DB_PATH)
    db.execute("INSERT OR IGNORE INTO users (email, created_at) VALUES (?, '2026-01-01T00:00:00Z')",
               (email,))
    uid = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()[0]
    token = f"tok-{email}"
    db.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) "
               "VALUES (?, ?, '2026-01-01T00:00:00Z', '2999-01-01T00:00:00Z')", (token, uid))
    db.commit()
    db.close()
    return token


@pytest.mark.parametrize("method,path", [
    ("get", "/auth/passkey?redirect_uri=http://testserver/"),
    ("post", "/auth/passkey/login/options"),
    ("get", "/api/passkeys"),
])
def test_passkey_routes_503_without_webauthn(client, monkeypatch, method, path):
    monkeypatch.setattr(sync_server, "PASSKEYS_AVAILABLE", False)
    assert getattr(client, method)(path).status_code == 503


def test_magic_link_unaffected_without_webauthn(client, mock_smtp, monkeypatch):
    monkeypatch.setattr(sync_server, "PASSKEYS_AVAILABLE", False)
    r = client.post("/auth/request-link",
                    json={"email": "user@example.com", "redirect_uri": "http://testserver/"})
    assert r.status_code == 200
    assert len(mock_smtp) == 1
