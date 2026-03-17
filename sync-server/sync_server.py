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
