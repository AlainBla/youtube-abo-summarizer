# Sync Service Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone Flask sync service (`sync-server/`) and extend `export.html.j2` to sync read/bookmark state across browsers via magic-link auth and last-write-wins timestamps.

**Architecture:** Standalone Flask app in `sync-server/`, SQLite for users/sessions/state. Export HTML embeds `SYNC_URL` at generation time; all sync JS behind `{% if sync_url %}` guards. Bearer token in `localStorage`; no cookies on the server side.

**Tech Stack:** Python 3.11+, Flask, itsdangerous, python-dotenv, pytest (server); Jinja2 (client template); vanilla JS (no framework)

**Spec clarification — POST body format:** The spec shorthand `{ video_id: iso_ts }` cannot convey `value=0` (cleared) entries. This plan uses `{ video_id: { value: 0|1, ts: iso_ts } }` throughout, matching the GET response shape exactly. This is the only format that makes value=0 propagation work.

---

## File Structure

**New files:**
- `sync-server/sync_server.py` — single Flask app file (~370 lines): DB schema, CORS, auth endpoints, state API
- `sync-server/requirements.txt` — flask, itsdangerous, python-dotenv
- `sync-server/.env.example` — all required variables with comments
- `sync-server/.gitignore` — sync.db, .env, __pycache__, .venv
- `sync-server/run.sh` — start script
- `sync-server/tests/__init__.py` — empty
- `sync-server/tests/conftest.py` — pytest fixtures: app, client, mock_smtp, session_token, reset_rate_limits
- `sync-server/tests/test_sync_server.py` — full test suite

**Modified files:**
- `export.py:49-56` — add `--sync-url URL` argument
- `export.py:114-115` — pass `sync_url` to renderer
- `renderer.py:93-124` — add `sync_url=None` kwarg to `render_export_html`, pass to template
- `export.html.j2:7-295` — add `.sync-bar` CSS inside `{% if sync_url %}` block
- `export.html.j2:299-310` — add sync bar HTML between header and controls-bar
- `export.html.j2:366-371` — add `SYNC_URL` constant after other constants
- `export.html.j2:374-451` — add sync i18n strings to I18N.de / I18N.en objects
- `export.html.j2:524-570` — extend `applyLang` to call `syncApplyLang`
- `export.html.j2:489-507` — extend `toggleRead` / `toggleBookmark` to call `syncToggle`
- `export.html.j2:827` — call `initSync()` after `applyLang(detectLang())`

---

## Task 1: Server scaffold

**Files:**
- Create: `sync-server/sync_server.py`
- Create: `sync-server/requirements.txt`
- Create: `sync-server/.env.example`
- Create: `sync-server/.gitignore`
- Create: `sync-server/run.sh`

- [ ] **Step 1: Create the directory and support files**

```bash
mkdir -p sync-server
```

`sync-server/requirements.txt`:
```
flask
itsdangerous
python-dotenv
pytest
```

`sync-server/.env.example`:
```bash
# Required
SECRET_KEY=change-me-to-a-long-random-string
BASE_URL=https://sync.example.com
SMTP_HOST=mail.example.com
SMTP_USER=user@example.com
SMTP_PASS=your-smtp-password

# Optional
PORT=5000
SMTP_PORT=587
SMTP_FROM=user@example.com
# Comma-separated list of allowed emails. Empty = open registration (any email).
ALLOWED_EMAILS=
```

`sync-server/.gitignore`:
```
sync.db
.env
__pycache__/
*.pyc
.venv/
```

`sync-server/run.sh`:
```bash
#!/bin/bash
set -e
cd "$(dirname "$0")"
python sync_server.py
```
```bash
chmod +x sync-server/run.sh
```

- [ ] **Step 2: Write the Flask skeleton with DB schema and CORS**

`sync-server/sync_server.py`:
```python
"""Sync server — persists read/bookmark state per user, magic-link auth."""
import os
import sqlite3
import time
import uuid
import smtplib
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from urllib.parse import urlparse, quote as urlquote

from flask import Flask, g, jsonify, redirect, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.environ["SECRET_KEY"]
BASE_URL = os.environ["BASE_URL"].rstrip("/")
PORT = int(os.environ.get("PORT", 5000))
ALLOWED_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("ALLOWED_EMAILS", "").split(",")
    if e.strip()
}
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync.db")

serializer = URLSafeTimedSerializer(SECRET_KEY)

# In-memory rate limiter: key → list of hit timestamps
_rate_limits: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_MAX = 3
RATE_LIMIT_WINDOW = 600  # seconds

app = Flask(__name__)


# ── Database ──────────────────────────────────────────────────────────────────

def init_db(path: str = DB_PATH) -> None:
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id         INTEGER PRIMARY KEY,
            email      TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token      TEXT PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS video_state (
            user_id    INTEGER NOT NULL REFERENCES users(id),
            video_id   TEXT NOT NULL,
            type       TEXT NOT NULL CHECK(type IN ('read', 'bookmark')),
            value      INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, video_id, type)
        );
    """)
    db.commit()
    db.close()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db


@app.teardown_appcontext
def close_db(exc: BaseException | None) -> None:
    db = g.pop("db", None)
    if db:
        db.close()


# ── CORS ──────────────────────────────────────────────────────────────────────

@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    return response


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _valid_redirect_uri(uri: str) -> bool:
    """Accept URIs whose origin matches BASE_URL, or any file:// URI."""
    try:
        p = urlparse(uri)
        if p.scheme == "file":
            return True
        b = urlparse(BASE_URL)
        return p.scheme == b.scheme and p.netloc == b.netloc
    except Exception:
        return False


def _rate_check(key: str) -> bool:
    """Return True if request is within limit, False if exceeded."""
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW
    _rate_limits[key] = [t for t in _rate_limits[key] if t > cutoff]
    if len(_rate_limits[key]) >= RATE_LIMIT_MAX:
        return False
    _rate_limits[key].append(now)
    return True


def _get_session_user():
    """Return sessions+users row if Bearer token is valid and not expired."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    now = _now_iso()
    return get_db().execute(
        "SELECT s.token, s.user_id, u.email "
        "FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token = ? AND s.expires_at > ?",
        (token, now),
    ).fetchone()


def _send_magic_link(email: str, link: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = "Your login link for YouTube Export Sync"
    msg["From"] = SMTP_FROM
    msg["To"] = email
    msg.set_content(f"Click to log in:\n\n{link}\n\nExpires in 15 minutes.")
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(msg)


# ── Auth endpoints ────────────────────────────────────────────────────────────

@app.route("/auth/request-link", methods=["OPTIONS", "POST"])
def request_link():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    redirect_uri = (data.get("redirect_uri") or "").strip()

    if not email or not redirect_uri:
        return jsonify({"error": "email and redirect_uri required"}), 400
    if not _valid_redirect_uri(redirect_uri):
        return jsonify({"error": "invalid redirect_uri"}), 400

    ip = request.remote_addr or "unknown"
    if not _rate_check(f"email:{email}") or not _rate_check(f"ip:{ip}"):
        return jsonify({}), 200  # silently rate-limit

    if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
        return jsonify({}), 200  # silently reject non-allowlisted

    token = serializer.dumps({"email": email})
    link = (
        f"{BASE_URL}/auth/verify"
        f"?token={urlquote(token)}"
        f"&redirect_uri={urlquote(redirect_uri)}"
    )
    _send_magic_link(email, link)
    return jsonify({}), 200


@app.route("/auth/verify")
def verify():
    token = request.args.get("token", "")
    redirect_uri = request.args.get("redirect_uri", "")

    if not _valid_redirect_uri(redirect_uri):
        return "Invalid redirect URI.", 400

    try:
        payload = serializer.loads(token, max_age=900)  # 15 min
    except SignatureExpired:
        return "Link expired. Please request a new one.", 400
    except BadSignature:
        return "Invalid link.", 400

    email = payload["email"]
    db = get_db()
    now = _now_iso()
    db.execute(
        "INSERT OR IGNORE INTO users (email, created_at) VALUES (?, ?)", (email, now)
    )
    user = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    session_token = str(uuid.uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    db.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (session_token, user["id"], now, expires_at),
    )
    db.commit()
    return redirect(f"{redirect_uri}#session={session_token}")


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.route("/api/whoami")
def whoami():
    user = _get_session_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"email": user["email"]})


@app.route("/api/state")
def get_state():
    user = _get_session_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    rows = get_db().execute(
        "SELECT video_id, type, value, updated_at FROM video_state WHERE user_id = ?",
        (user["user_id"],),
    ).fetchall()
    result: dict = {"read": {}, "bookmark": {}}
    for row in rows:
        result[row["type"]][row["video_id"]] = {
            "value": row["value"],
            "ts": row["updated_at"],
        }
    return jsonify(result)


@app.route("/api/state", methods=["OPTIONS", "POST"])
def post_state():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    user = _get_session_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    read_entries = data.get("read") or {}
    bookmark_entries = data.get("bookmark") or {}

    if len(read_entries) + len(bookmark_entries) > 2000:
        return jsonify({"error": "too many entries"}), 400

    db = get_db()
    for type_name, entries in [("read", read_entries), ("bookmark", bookmark_entries)]:
        for video_id, entry in entries.items():
            if not isinstance(video_id, str) or len(video_id) > 64:
                return jsonify({"error": "invalid video_id"}), 400
            if not isinstance(entry, dict):
                return jsonify({"error": "entry must be {value, ts} object"}), 400
            value = entry.get("value")
            ts = entry.get("ts")
            if value not in (0, 1) or not isinstance(ts, str) or not ts:
                return jsonify({"error": "entry must have value (0|1) and ts"}), 400

            existing = db.execute(
                "SELECT updated_at FROM video_state "
                "WHERE user_id=? AND video_id=? AND type=?",
                (user["user_id"], video_id, type_name),
            ).fetchone()
            if existing is None or ts > existing["updated_at"]:
                db.execute(
                    "INSERT OR REPLACE INTO video_state "
                    "(user_id, video_id, type, value, updated_at) VALUES (?,?,?,?,?)",
                    (user["user_id"], video_id, type_name, value, ts),
                )
    db.commit()

    rows = get_db().execute(
        "SELECT video_id, type, value, updated_at FROM video_state WHERE user_id = ?",
        (user["user_id"],),
    ).fetchall()
    result: dict = {"read": {}, "bookmark": {}}
    for row in rows:
        result[row["type"]][row["video_id"]] = {
            "value": row["value"],
            "ts": row["updated_at"],
        }
    return jsonify(result)


@app.route("/api/session", methods=["OPTIONS", "DELETE"])
def delete_session():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    user = _get_session_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    token = request.headers.get("Authorization", "")[7:]
    db = get_db()
    db.execute("DELETE FROM sessions WHERE token = ?", (token,))
    db.commit()
    return "", 204


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=PORT, debug=False)
```

- [ ] **Step 3: Install dependencies and verify server starts**

```bash
cd sync-server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a minimal `.env` for local testing:
```bash
cat > .env << 'EOF'
SECRET_KEY=dev-secret-key-change-in-prod
BASE_URL=http://localhost:5000
SMTP_HOST=localhost
SMTP_USER=test@example.com
SMTP_PASS=testpass
EOF
```

```bash
python -c "import sync_server; sync_server.init_db(); print('DB ok')"
```
Expected: `DB ok`

- [ ] **Step 4: Commit scaffold**

```bash
cd ..
git add sync-server/
git commit -m "feat(sync): add standalone sync server scaffold with DB schema"
```

---

## Task 2: Server tests

**Files:**
- Create: `sync-server/tests/__init__.py`
- Create: `sync-server/tests/conftest.py`
- Create: `sync-server/tests/test_sync_server.py`

- [ ] **Step 1: Write test infrastructure (conftest.py)**

`sync-server/tests/__init__.py`: *(empty file)*

`sync-server/tests/conftest.py`:
```python
import os
import sys
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta

# Set env vars before importing sync_server (load_dotenv must not override these)
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["BASE_URL"] = "http://testserver"
os.environ["SMTP_HOST"] = "localhost"
os.environ["SMTP_PORT"] = "587"
os.environ["SMTP_USER"] = "test@example.com"
os.environ["SMTP_PASS"] = "testpass"
os.environ["ALLOWED_EMAILS"] = ""  # open registration by default

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import sync_server


@pytest.fixture(autouse=True)
def reset_rate_limits():
    sync_server._rate_limits.clear()
    yield
    sync_server._rate_limits.clear()


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(sync_server, "DB_PATH", db_path)
    sync_server.init_db(db_path)
    sync_server.app.config["TESTING"] = True
    yield sync_server.app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def mock_smtp(monkeypatch):
    """Patch _send_magic_link; returns list of {email, link} dicts."""
    sent = []

    def fake_send(email: str, link: str) -> None:
        sent.append({"email": email, "link": link})

    monkeypatch.setattr(sync_server, "_send_magic_link", fake_send)
    return sent


@pytest.fixture
def session_token(app):
    """Insert a user+session directly into DB; return the token string."""
    db = sqlite3.connect(sync_server.DB_PATH)
    db.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    email = "user@example.com"
    db.execute(
        "INSERT OR IGNORE INTO users (email, created_at) VALUES (?, ?)", (email, now)
    )
    user = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    token = str(uuid.uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    db.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
        (token, user["id"], now, expires_at),
    )
    db.commit()
    db.close()
    return token
```

- [ ] **Step 2: Write all tests**

`sync-server/tests/test_sync_server.py`:
```python
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
```

- [ ] **Step 3: Run tests — expect all to pass (server is already written)**

```bash
cd sync-server
source .venv/bin/activate
python -m pytest tests/ -v
```

Expected: all tests pass. If any fail, fix `sync_server.py` before proceeding.

- [ ] **Step 4: Commit**

```bash
cd ..
git add sync-server/tests/
git commit -m "test(sync): add full pytest suite for sync server"
```

---

## Task 3: export.py and renderer.py changes

**Files:**
- Modify: `export.py`
- Modify: `renderer.py`

- [ ] **Step 1: Add `--sync-url` to export.py**

In `export.py`, after the `--lang` argument block (line ~55), add:

```python
    parser.add_argument(
        "--sync-url",
        metavar="URL",
        default=None,
        help="URL of the sync server to embed in the export HTML. "
             "Enables cross-browser read/bookmark sync.",
    )
```

Change the `main()` call to renderer at line ~115:
```python
    renderer.render_export_html(videos, output_path, lang=args.lang or "de", sync_url=args.sync_url)
```

- [ ] **Step 2: Add `sync_url` kwarg to `renderer.render_export_html`**

In `renderer.py`, change the function signature at line 93:
```python
def render_export_html(
    videos: list[dict],
    output_path: str,
    lang: str = i18n_module.DEFAULT_LANG,
    sync_url: str | None = None,
) -> None:
```

In the `template.render(...)` call, add:
```python
        sync_url=sync_url,
```

- [ ] **Step 3: Verify — export without sync URL produces unchanged output**

```bash
python export.py --hours 1 --output /tmp/test_no_sync.html
grep -c "SYNC_URL" /tmp/test_no_sync.html || echo "0 — correct, no sync code"
```
Expected: `0 — correct, no sync code`

- [ ] **Step 4: Commit**

```bash
git add export.py renderer.py
git commit -m "feat(export): add --sync-url argument, pass to renderer"
```

---

## Task 4: export.html.j2 — sync bar HTML and CSS

**Files:**
- Modify: `export.html.j2`

- [ ] **Step 1: Add CSS for sync bar (inside `<style>` block, before closing `</style>`)**

In `export.html.j2`, before `</style>` at line ~295, add:

```css
    {% if sync_url %}
    /* ── Sync bar ── */
    .sync-bar {
      max-width: 900px;
      margin: 0 auto 0.75rem;
      display: flex;
      align-items: center;
      gap: 0.6rem;
      flex-wrap: wrap;
      font-size: 0.9rem;
    }
    .sync-bar input[type="email"] {
      background: #1a1a1a;
      border: 1px solid #2a2a2a;
      border-radius: 6px;
      color: #e0e0e0;
      font-size: 0.9rem;
      padding: 0.35rem 0.6rem;
      outline: none;
      min-width: 200px;
    }
    .sync-bar input[type="email"]:focus { border-color: #555; }
    .sync-bar input[type="email"]::placeholder { color: #555; }
    .sync-bar button {
      background: #2a2a2a;
      border: 1px solid #3a3a3a;
      border-radius: 6px;
      color: #ccc;
      font-size: 0.85rem;
      padding: 0.35rem 0.75rem;
      cursor: pointer;
    }
    .sync-bar button:hover { background: #333; }
    .sync-user-email { color: #aaa; font-size: 0.85rem; }
    .sync-status { color: #555; font-size: 0.8rem; margin-left: auto; }
    {% endif %}
```

- [ ] **Step 2: Add sync bar HTML element (between `</header>` and `<div class="controls-bar">`)**

In `export.html.j2`, after `</header>` at line ~310 and before `<div class="controls-bar">`, add:

```html
{% if sync_url %}
<div class="sync-bar" id="sync-bar">
  <div id="sync-login">
    <input type="email" id="sync-email" placeholder="E-Mail für Sync" autocomplete="email">
    <button id="sync-send-btn" onclick="syncRequestLink()">Link senden</button>
  </div>
  <div id="sync-user" style="display:none">
    <span class="sync-user-email" id="sync-user-email"></span>
    <button id="sync-logout-btn" onclick="doLogout()">Abmelden</button>
  </div>
  <span class="sync-status" id="sync-status"></span>
</div>
{% endif %}
```

- [ ] **Step 3: Generate a test export with sync URL and verify HTML structure**

```bash
python export.py --hours 168 --sync-url http://localhost:5000 --output /tmp/test_sync.html
grep -c "sync-bar" /tmp/test_sync.html
grep -c "SYNC_URL" /tmp/test_sync.html
```
Expected: both return `1` (or more).

- [ ] **Step 4: Commit**

```bash
git add export.html.j2
git commit -m "feat(export): add sync bar HTML and CSS (hidden when no sync URL)"
```

---

## Task 5: export.html.j2 — sync JavaScript

**Files:**
- Modify: `export.html.j2`

This task adds all sync JS: constants, i18n, localStorage helpers, sync functions, and integration with `applyLang` + `toggleRead`/`toggleBookmark`.

- [ ] **Step 1: Add SYNC_URL constant after existing constants (line ~371)**

After `const EMBEDDED_DEFAULT = '{{ default_lang }}';`, add:

```js
{% if sync_url %}
const SYNC_URL = "{{ sync_url }}";
{% endif %}
```

- [ ] **Step 2: Add sync i18n strings to I18N.de and I18N.en objects**

In `export.html.j2`, inside `I18N.de` after `nextPage: 'Weiter \u2192'` (last de entry), add:
```js
    syncEmailPlaceholder: 'E-Mail f\u00fcr Sync',
    syncSendLink: 'Link senden',
    syncLogout: 'Abmelden',
    syncStatusNotLoggedIn: 'Nicht angemeldet',
    syncStatusSending: 'Link wird gesendet\u2026',
    syncStatusSent: 'Link gesendet \u2014 E-Mail pr\u00fcfen',
    syncStatusSyncing: 'Synchronisiere\u2026',
    syncStatusSynced: 'Synchronisiert',
    syncStatusFailed: 'Sync fehlgeschlagen',
```

Inside `I18N.en` after `nextPage: 'Next \u2192'` (last en entry), add:
```js
    syncEmailPlaceholder: 'Email for sync',
    syncSendLink: 'Send link',
    syncLogout: 'Log out',
    syncStatusNotLoggedIn: 'Not logged in',
    syncStatusSending: 'Sending link\u2026',
    syncStatusSent: 'Link sent \u2014 check your email',
    syncStatusSyncing: 'Syncing\u2026',
    syncStatusSynced: 'Synced',
    syncStatusFailed: 'Sync failed',
```

- [ ] **Step 3: Extend applyLang to update sync bar text**

In `export.html.j2`, inside `applyLang(lang)`, before the final `applyFiltersAndSort();` call, add:

```js
  {% if sync_url %}
  // Update sync bar strings
  (function() {
    var emailEl = document.getElementById('sync-email');
    if (emailEl) emailEl.placeholder = s.syncEmailPlaceholder;
    var sendEl = document.getElementById('sync-send-btn');
    if (sendEl) sendEl.textContent = s.syncSendLink;
    var logoutEl = document.getElementById('sync-logout-btn');
    if (logoutEl) logoutEl.textContent = s.syncLogout;
  })();
  {% endif %}
```

- [ ] **Step 4: Extend toggleRead and toggleBookmark to call syncToggle**

In `export.html.j2`, in `window.toggleRead`:
```js
window.toggleRead = function(videoId) {
  if (readSet.has(videoId)) {
    readSet.delete(videoId);
  } else {
    readSet.add(videoId);
  }
  saveSet(COOKIE_READ, readSet);
  {% if sync_url %}syncToggle('read', videoId, readSet.has(videoId) ? 1 : 0);{% endif %}
  applyFiltersAndSort(currentPage);
};
```

In `window.toggleBookmark`:
```js
window.toggleBookmark = function(videoId) {
  if (bookmarkSet.has(videoId)) {
    bookmarkSet.delete(videoId);
  } else {
    bookmarkSet.add(videoId);
  }
  saveSet(COOKIE_BOOKMARK, bookmarkSet);
  {% if sync_url %}syncToggle('bookmark', videoId, bookmarkSet.has(videoId) ? 1 : 0);{% endif %}
  applyFiltersAndSort(currentPage);
};
```

- [ ] **Step 5: Add all sync JS functions (inside `{% if sync_url %}` block, after the toggles)**

Add the following block after `window.toggleBookmark` and before the language detection section:

```js
{% if sync_url %}
// ── Sync helpers ────────────────────────────────────────────────────────────

function updateSyncStatus(msg) {
  var el = document.getElementById('sync-status');
  if (el) el.textContent = msg;
}

function getSyncToken() {
  return localStorage.getItem('yt_sync_token') || '';
}

function getLocalTs(type) {
  try {
    return JSON.parse(localStorage.getItem('yt_' + type + '_ts') || '{}');
  } catch (e) {
    return {};
  }
}

function saveLocalTs(type, obj) {
  localStorage.setItem('yt_' + type + '_ts', JSON.stringify(obj));
}

function getIsoNow() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
}

function showSyncLoggedIn(email) {
  document.getElementById('sync-login').style.display = 'none';
  document.getElementById('sync-user').style.display = '';
  document.getElementById('sync-user-email').textContent = email;
}

function showSyncLoggedOut() {
  document.getElementById('sync-login').style.display = '';
  document.getElementById('sync-user').style.display = 'none';
  updateSyncStatus(I18N[currentLang || 'de'].syncStatusNotLoggedIn);
}

// Merge server state into local cookies + localStorage.
// Returns { read: pushBackEntries, bookmark: pushBackEntries } of locally-newer entries.
function applyServerState(serverData) {
  var readTs = getLocalTs('read');
  var bookmarkTs = getLocalTs('bookmark');
  var pushBack = {read: {}, bookmark: {}};

  ['read', 'bookmark'].forEach(function(type) {
    var serverEntries = serverData[type] || {};
    var localTs = type === 'read' ? readTs : bookmarkTs;
    var localSet = type === 'read' ? readSet : bookmarkSet;

    // Process server entries
    Object.keys(serverEntries).forEach(function(vid) {
      var entry = serverEntries[vid];
      var srvTs = entry.ts;
      var locTs = localTs[vid] || '1970-01-01T00:00:00Z';
      if (srvTs >= locTs) {
        // Server wins — apply to local
        localTs[vid] = srvTs;
        if (entry.value === 1) {
          localSet.add(vid);
        } else {
          localSet.delete(vid);
        }
      } else {
        // Local wins — collect for push-back
        pushBack[type][vid] = {value: localSet.has(vid) ? 1 : 0, ts: locTs};
      }
    });

    // Local entries absent from server response are also locally-newer
    Object.keys(localTs).forEach(function(vid) {
      if (!serverEntries[vid]) {
        pushBack[type][vid] = {value: localSet.has(vid) ? 1 : 0, ts: localTs[vid]};
      }
    });

    // Persist updated timestamps
    if (type === 'read') { readTs = localTs; }
    else { bookmarkTs = localTs; }
  });

  saveLocalTs('read', readTs);
  saveLocalTs('bookmark', bookmarkTs);
  saveSet(COOKIE_READ, readSet);
  saveSet(COOKIE_BOOKMARK, bookmarkSet);

  var hasPushBack = (
    Object.keys(pushBack.read).length + Object.keys(pushBack.bookmark).length > 0
  );
  if (hasPushBack) {
    var tok = getSyncToken();
    fetch(SYNC_URL + '/api/state', {
      method: 'POST',
      headers: {'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'},
      body: JSON.stringify(pushBack)
    }).catch(function() {});  // fire-and-forget
  }
}

function initSync() {
  // Step 1: harvest #session=UUID from URL fragment, then strip immediately
  var hash = window.location.hash;
  var m = hash.match(/[#&]session=([^&]+)/);
  if (m) {
    localStorage.setItem('yt_sync_token', decodeURIComponent(m[1]));
    try {
      history.replaceState(
        null, '',
        window.location.pathname + window.location.search
      );
    } catch (e) {}  // file:// may throw SecurityError — silently continue
  }

  var token = getSyncToken();
  if (!token) {
    showSyncLoggedOut();
    return;
  }

  var s = I18N[currentLang || 'de'];
  updateSyncStatus(s.syncStatusSyncing);

  fetch(SYNC_URL + '/api/whoami', {headers: {'Authorization': 'Bearer ' + token}})
    .then(function(r) {
      if (r.status === 401) {
        localStorage.removeItem('yt_sync_token');
        showSyncLoggedOut();
        return null;
      }
      return r.json();
    })
    .then(function(data) {
      if (!data) return null;
      showSyncLoggedIn(data.email);
      return fetch(SYNC_URL + '/api/state', {
        headers: {'Authorization': 'Bearer ' + token}
      });
    })
    .then(function(r) {
      return r ? r.json() : null;
    })
    .then(function(serverData) {
      if (!serverData) return;
      applyServerState(serverData);
      applyFiltersAndSort(currentPage);
      updateSyncStatus(
        I18N[currentLang || 'de'].syncStatusSynced +
        ' \u2014 ' + new Date().toLocaleTimeString()
      );
    })
    .catch(function() {
      updateSyncStatus(I18N[currentLang || 'de'].syncStatusFailed);
    });
}

function syncRequestLink() {
  var email = (document.getElementById('sync-email').value || '').trim();
  if (!email) return;
  var s = I18N[currentLang || 'de'];
  updateSyncStatus(s.syncStatusSending);
  fetch(SYNC_URL + '/auth/request-link', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({email: email, redirect_uri: window.location.href})
  })
  .then(function() { updateSyncStatus(s.syncStatusSent); })
  .catch(function() { updateSyncStatus(s.syncStatusFailed); });
}

function doLogout() {
  var token = getSyncToken();
  if (token) {
    fetch(SYNC_URL + '/api/session', {
      method: 'DELETE',
      headers: {'Authorization': 'Bearer ' + token}
    }).catch(function() {});
  }
  localStorage.removeItem('yt_sync_token');
  showSyncLoggedOut();
}

function syncToggle(type, videoId, value) {
  var token = getSyncToken();
  if (!token) return;
  var ts = getIsoNow();
  var localTs = getLocalTs(type);
  localTs[videoId] = ts;
  saveLocalTs(type, localTs);
  var body = {};
  body[type] = {};
  body[type][videoId] = {value: value, ts: ts};
  var otherType = type === 'read' ? 'bookmark' : 'read';
  body[otherType] = {};
  fetch(SYNC_URL + '/api/state', {
    method: 'POST',
    headers: {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  })
  .then(function(r) { return r.json(); })
  .then(function(serverData) {
    applyServerState(serverData);
    updateSyncStatus(
      I18N[currentLang || 'de'].syncStatusSynced +
      ' \u2014 ' + new Date().toLocaleTimeString()
    );
  })
  .catch(function() {
    updateSyncStatus(I18N[currentLang || 'de'].syncStatusFailed);
  });
}
{% endif %}
```

- [ ] **Step 6: Call initSync() at page boot**

In `export.html.j2`, after `applyLang(detectLang());` at the very end of the `<script>` block, add:

```js
{% if sync_url %}
initSync();
{% endif %}
```

- [ ] **Step 7: Generate export with sync URL and open in browser for manual verification**

```bash
python export.py --all --sync-url http://localhost:5000 --output /tmp/test_sync_full.html
```

Open `/tmp/test_sync_full.html` in a browser. Verify:
- Sync bar visible below page header (email input + "Link senden" button)
- Status shows "Nicht angemeldet" (or language equivalent)
- No JS console errors
- Existing read/bookmark/filter functionality still works

Also verify export without `--sync-url`:
```bash
python export.py --all --output /tmp/test_no_sync_full.html
```
Open in browser. Verify: no sync bar, no console errors.

- [ ] **Step 8: Test the full auth + sync flow with running server**

```bash
cd sync-server && source .venv/bin/activate
python sync_server.py &
```

Open `/tmp/test_sync_full.html`, enter an email, click "Link senden". Check server logs for the magic link URL. Copy the link, open it in the same browser → should redirect back to the export file with `#session=UUID`. The sync bar should show the email and status "Synchronisiert".

Mark a few videos as read in browser A. Open the same export file in browser B (incognito), log in with the same email, verify read state syncs.

Kill the test server: `kill %1`

- [ ] **Step 9: Commit**

```bash
cd ..
git add export.html.j2
git commit -m "feat(export): add cross-browser sync via SYNC_URL (magic-link auth, last-write-wins)"
```

---

## Task 6: Final wiring and documentation

**Files:**
- Modify: `sync-server/sync_server.py` (if any issues surfaced in manual testing)
- Modify: `CLAUDE.md`

- [ ] **Step 1: Run full server test suite one final time**

```bash
cd sync-server && source .venv/bin/activate
python -m pytest tests/ -v
```
Expected: all pass.

- [ ] **Step 2: Update CLAUDE.md — add sync server section**

Add the following to the `Architecture` table in `CLAUDE.md`:

```markdown
| `sync-server/sync_server.py` | Standalone Flask sync service: magic-link auth, per-user read/bookmark state in SQLite, last-write-wins merge |
```

And add a new section after the Export archive section:

```markdown
## Sync server (optional)

`sync-server/` is a standalone Flask service for syncing read/bookmark state across browsers.

```bash
cd sync-server
cp .env.example .env   # fill in SECRET_KEY, BASE_URL, SMTP_*
pip install -r requirements.txt
python sync_server.py
```

Pass `--sync-url` to `export.py` to embed the server URL in generated HTML:

```bash
python export.py --all --sync-url https://sync.example.com --output archive.html
```

Users log in via magic link (email → click link → session stored in browser localStorage).
State syncs automatically on page load and on each read/bookmark toggle.
```

- [ ] **Step 3: Update README.md**

Add a "Sync server" section to `README.md` (same content as the CLAUDE.md addition above). Project convention requires README.md and CLAUDE.md to stay in sync when code changes.

- [ ] **Step 4: Commit documentation**

```bash
git add CLAUDE.md README.md
git commit -m "docs: document sync server setup and --sync-url export option"
```
