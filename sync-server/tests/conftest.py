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
    # Rate limit state lives in the per-test SQLite DB (tmp_path via app fixture).
    # No manual cleanup needed — each test gets a fresh DB automatically.
    yield


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
