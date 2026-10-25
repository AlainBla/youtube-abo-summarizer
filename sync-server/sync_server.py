"""Sync server — persists read/bookmark state per user, magic-link and passkey auth."""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import time
import uuid
import smtplib
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from urllib.parse import urlparse, quote as urlquote

from flask import Flask, g, jsonify, redirect, render_template, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from dotenv import load_dotenv

# Passkeys are optional: without py_webauthn the passkey routes answer 503 and
# everything else — magic link included — keeps working, so a deployment that
# pulled this code before running pip install does not go down with it.
try:
    from webauthn import (
        generate_authentication_options,
        generate_registration_options,
        options_to_json,
        verify_authentication_response,
        verify_registration_response,
    )
    from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
    from webauthn.helpers.exceptions import WebAuthnException
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )
    PASSKEYS_AVAILABLE = True
except ImportError:
    PASSKEYS_AVAILABLE = False
    print("[sync] py_webauthn not installed — passkey login disabled "
          "(pip install -r requirements.txt)", file=sys.stderr)

load_dotenv()

SECRET_KEY = os.environ["SECRET_KEY"]
BASE_URL = os.environ["BASE_URL"].rstrip("/")
PORT = int(os.environ.get("PORT", 5000))
ALLOWED_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("ALLOWED_EMAILS", "").split(",")
    if e.strip()
}
INGEST_QUEUE = os.environ.get("INGEST_QUEUE", "")  # path to queue file
INGEST_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("INGEST_EMAILS", "").split(",")
    if e.strip()
}
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync.db")

serializer = URLSafeTimedSerializer(SECRET_KEY)

RATE_LIMIT_MAX = 3
RATE_LIMIT_WINDOW = 600  # seconds

# The mail carries a six-digit code beside the link, for a device where the
# link would open in the wrong place -- an installed web app on iOS has its own
# storage, and a link from the mail client always lands in the browser. Same
# lifetime as the link; one live code per address (a new request replaces it);
# it dies after LOGIN_CODE_MAX_ATTEMPTS wrong guesses, and with at most
# RATE_LIMIT_MAX codes per address per window a guess succeeds with odds of
# about 1 in 66 000 per ten minutes.
LOGIN_CODE_TTL = 900  # seconds, = the magic link's max_age
LOGIN_CODE_MAX_ATTEMPTS = 5
CODE_RATE_LIMIT_MAX = 20  # verify attempts per IP per window

# Passkeys: the relying party is the sync server's own host, and every ceremony
# runs on a page it serves itself, so the expected origin is BASE_URL's origin.
# An archive opened from file:// or another host could not run one at all.
def _origin_of(url: str) -> str:
    """scheme://host[:port] of url — BASE_URL may carry a path (/sync behind
    a reverse proxy), an origin never does."""
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


WEBAUTHN_RP_ID = urlparse(BASE_URL).hostname or ""
WEBAUTHN_ORIGIN = _origin_of(BASE_URL)
WEBAUTHN_RP_NAME = "YouTube Export Sync"
WEBAUTHN_CHALLENGE_TTL = 300  # seconds
# Each login page load asks for options, so the magic-link limit (3 per 10 min)
# would lock a user out after a couple of cancelled prompts.
PASSKEY_RATE_LIMIT_MAX = 20

app = Flask(__name__)

# Behind a reverse proxy every request arrives from 127.0.0.1, which turns the
# per-IP rate limits into one global limit: a stranger reloading the login page
# would lock everyone out. TRUSTED_PROXIES is the number of proxies in front
# (1 for Apache/Nginx on the same host); 0, the default, trusts no
# X-Forwarded-* header, which is right when clients connect directly.
TRUSTED_PROXIES = int(os.environ.get("TRUSTED_PROXIES", "0") or 0)
if TRUSTED_PROXIES:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=TRUSTED_PROXIES, x_proto=TRUSTED_PROXIES)


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
        CREATE TABLE IF NOT EXISTS rate_limit_hits (
            key        TEXT NOT NULL,
            hit_at     REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rate_limit_key_time
            ON rate_limit_hits (key, hit_at);
        CREATE TABLE IF NOT EXISTS login_codes (
            email       TEXT PRIMARY KEY,
            code_hash   TEXT NOT NULL,             -- HMAC, never the code itself
            expires_at  REAL NOT NULL,
            attempts    INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS passkeys (
            id          TEXT PRIMARY KEY,          -- credential ID, base64url
            user_id     INTEGER NOT NULL REFERENCES users(id),
            public_key  BLOB NOT NULL,
            sign_count  INTEGER NOT NULL,
            name        TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL,
            last_used   TEXT
        );
        CREATE TABLE IF NOT EXISTS webauthn_challenges (
            challenge   TEXT PRIMARY KEY,          -- base64url
            purpose     TEXT NOT NULL CHECK(purpose IN ('register', 'login')),
            user_id     INTEGER,                   -- set for 'register' only
            expires_at  REAL NOT NULL
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


# Ensure DB schema exists on every process start (CREATE TABLE IF NOT EXISTS = safe to repeat)
init_db()


# ── CORS ──────────────────────────────────────────────────────────────────────

@app.after_request
def add_cors(response):
    origin = request.headers.get("Origin", "")
    base_origin = _origin_of(BASE_URL)  # BASE_URL may carry a path; an Origin never does
    if origin == base_origin or origin == "null":
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    response.headers["Vary"] = "Origin"
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


def _rate_check(key: str, max_hits: int = RATE_LIMIT_MAX) -> bool:
    """Return True if request is within limit, False if exceeded.

    State is persisted in the rate_limit_hits SQLite table so it survives
    across worker processes and restarts.
    """
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW
    db = get_db()
    # Purge expired hits for this key
    db.execute(
        "DELETE FROM rate_limit_hits WHERE key = ? AND hit_at <= ?", (key, cutoff)
    )
    count = db.execute(
        "SELECT COUNT(*) FROM rate_limit_hits WHERE key = ?", (key,)
    ).fetchone()[0]
    if count >= max_hits:
        db.commit()
        return False
    db.execute(
        "INSERT INTO rate_limit_hits (key, hit_at) VALUES (?, ?)", (key, now)
    )
    db.commit()
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


def _create_session(user_id: int) -> str:
    """Insert a 30-day session for user_id and return its token (commits)."""
    session_token = str(uuid.uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    db = get_db()
    db.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (session_token, user_id, _now_iso(), expires_at),
    )
    db.commit()
    return session_token


def _code_hash(email: str, code: str) -> str:
    return hmac.new(SECRET_KEY.encode(), f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


def _store_login_code(email: str) -> str:
    """Create the address's one live login code (replacing any older one)."""
    code = f"{secrets.randbelow(10**6):06d}"
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO login_codes (email, code_hash, expires_at, attempts) "
        "VALUES (?, ?, ?, 0)",
        (email, _code_hash(email, code), time.time() + LOGIN_CODE_TTL),
    )
    db.commit()
    return code


def _send_magic_link(email: str, link: str, code: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = "Your login link for YouTube Export Sync"
    msg["From"] = SMTP_FROM
    msg["To"] = email
    msg.set_content(
        f"Click to log in:\n\n{link}\n\n"
        f"Or enter this code where you asked for the link (e.g. in the installed app):\n\n"
        f"    {code[:3]} {code[3:]}\n\n"
        f"Both expire in 15 minutes."
    )
    if SMTP_PORT == 465:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.starttls()
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
        print(f"[sync] invalid redirect_uri={redirect_uri!r} BASE_URL={BASE_URL!r}", file=sys.stderr)
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
    code = _store_login_code(email)
    try:
        _send_magic_link(email, link, code)
    except Exception as exc:
        print(f"[sync] SMTP error for {email}: {exc}", file=sys.stderr)
    return jsonify({}), 200


@app.route("/auth/verify-code", methods=["OPTIONS", "POST"])
def verify_code():
    """Exchange email + the mailed six-digit code for a session token."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    code = re.sub(r"\s+", "", str(data.get("code") or ""))
    if not email or not re.fullmatch(r"\d{6}", code):
        return jsonify({"error": "email and six-digit code required"}), 400

    ip = request.remote_addr or "unknown"
    # Deliberately no limit per address: anyone who knows the address could
    # spend it and lock the owner out. Guesses per address are already bounded
    # by request-link's own per-address limit (RATE_LIMIT_MAX codes per window)
    # times LOGIN_CODE_MAX_ATTEMPTS per code.
    if not _rate_check(f"code-ip:{ip}", CODE_RATE_LIMIT_MAX):
        return jsonify({"error": "too many attempts"}), 429

    db = get_db()
    invalid = (jsonify({"error": "invalid or expired code"}), 400)
    # Spend the attempt *before* comparing, in one statement: with several
    # gunicorn workers a read-then-update would let parallel requests all see
    # the same count and guess past LOGIN_CODE_MAX_ATTEMPTS.
    row = db.execute(
        "UPDATE login_codes SET attempts = attempts + 1 "
        "WHERE email = ? AND attempts < ? AND expires_at > ? RETURNING code_hash",
        (email, LOGIN_CODE_MAX_ATTEMPTS, time.time()),
    ).fetchone()
    db.commit()
    if row is None or not hmac.compare_digest(row["code_hash"], _code_hash(email, code)):
        return invalid
    # Single use: of two concurrent requests with the right code, only the one
    # whose DELETE removes the row gets a session.
    cur = db.execute(
        "DELETE FROM login_codes WHERE email = ? AND code_hash = ?", (email, row["code_hash"])
    )
    db.commit()
    if cur.rowcount != 1:
        return invalid
    if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
        return jsonify({"error": "forbidden"}), 403

    db.execute(
        "INSERT OR IGNORE INTO users (email, created_at) VALUES (?, ?)", (email, _now_iso())
    )
    user = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    return jsonify({"session": _create_session(user["id"])})


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
    db.execute(
        "INSERT OR IGNORE INTO users (email, created_at) VALUES (?, ?)", (email, _now_iso())
    )
    user = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    session_token = _create_session(user["id"])
    return redirect(f"{redirect_uri}#session={session_token}")


# ── Passkeys (WebAuthn) ───────────────────────────────────────────────────────
#
# Magic link stays the way in: a passkey can only be registered from a session,
# and losing every passkey just means asking for a link again. Both ceremonies
# run on pages served from BASE_URL (templates/passkey.html), because WebAuthn
# binds a credential to the origin it was created on; the login page then
# redirects to the archive with #session=, exactly like /auth/verify.

@app.before_request
def _passkeys_installed():
    if not PASSKEYS_AVAILABLE and (
        request.path.startswith("/auth/passkey") or request.path.startswith("/api/passkeys")
    ):
        return jsonify({"error": "passkeys not available on this server"}), 503
    return None


def _store_challenge(challenge: bytes, purpose: str, user_id: int | None = None) -> None:
    now = time.time()
    db = get_db()
    db.execute("DELETE FROM webauthn_challenges WHERE expires_at <= ?", (now,))
    db.execute(
        "INSERT INTO webauthn_challenges (challenge, purpose, user_id, expires_at) "
        "VALUES (?, ?, ?, ?)",
        (bytes_to_base64url(challenge), purpose, user_id, now + WEBAUTHN_CHALLENGE_TTL),
    )
    db.commit()


def _take_challenge(credential: dict, purpose: str, user_id: int | None = None) -> bytes | None:
    """Consume the challenge the credential's clientDataJSON claims to answer.

    Returns it only when this server issued it for this purpose (and user),
    it has not expired, and it was not used before — the row is deleted
    either way, so a challenge answers exactly one ceremony.
    """
    try:
        client_data = json.loads(
            base64url_to_bytes(credential["response"]["clientDataJSON"])
        )
        challenge = str(client_data["challenge"])
    except (KeyError, TypeError, ValueError):
        return None
    db = get_db()
    row = db.execute(
        "SELECT purpose, user_id, expires_at FROM webauthn_challenges WHERE challenge = ?",
        (challenge,),
    ).fetchone()
    db.execute("DELETE FROM webauthn_challenges WHERE challenge = ?", (challenge,))
    db.commit()
    if (
        row is None
        or row["purpose"] != purpose
        or row["user_id"] != user_id
        or row["expires_at"] <= time.time()
    ):
        return None
    return base64url_to_bytes(challenge)


def _user_handle(user_id: int) -> bytes:
    # The WebAuthn user handle must not carry personal data; the row id does not.
    return str(user_id).encode()


@app.route("/auth/passkey")
def passkey_login_page():
    redirect_uri = request.args.get("redirect_uri", "")
    if not _valid_redirect_uri(redirect_uri):
        return "Invalid redirect URI.", 400
    return render_template(
        "passkey.html", mode="login", redirect_uri=redirect_uri, api_base=BASE_URL
    )


@app.route("/auth/passkey/manage")
def passkey_manage_page():
    redirect_uri = request.args.get("redirect_uri", "")
    if not _valid_redirect_uri(redirect_uri):
        return "Invalid redirect URI.", 400
    return render_template(
        "passkey.html", mode="manage", redirect_uri=redirect_uri, api_base=BASE_URL
    )


@app.route("/auth/passkey/login/options", methods=["POST"])
def passkey_login_options():
    ip = request.remote_addr or "unknown"
    if not _rate_check(f"passkey:{ip}", PASSKEY_RATE_LIMIT_MAX):
        return jsonify({"error": "rate limited"}), 429
    options = generate_authentication_options(
        rp_id=WEBAUTHN_RP_ID,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    _store_challenge(options.challenge, "login")
    return app.response_class(options_to_json(options), mimetype="application/json")


@app.route("/auth/passkey/login/verify", methods=["POST"])
def passkey_login_verify():
    data = request.get_json(silent=True) or {}
    credential = data.get("credential")
    redirect_uri = (data.get("redirect_uri") or "").strip()
    if not isinstance(credential, dict):
        return jsonify({"error": "credential required"}), 400
    if not _valid_redirect_uri(redirect_uri):
        return jsonify({"error": "invalid redirect_uri"}), 400
    challenge = _take_challenge(credential, "login")
    if challenge is None:
        return jsonify({"error": "unknown or expired challenge"}), 400

    db = get_db()
    row = db.execute(
        "SELECT p.id, p.user_id, p.public_key, p.sign_count, u.email "
        "FROM passkeys p JOIN users u ON u.id = p.user_id WHERE p.id = ?",
        (str(credential.get("id") or ""),),
    ).fetchone()
    if row is None:
        return jsonify({"error": "unknown passkey"}), 401
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=WEBAUTHN_RP_ID,
            expected_origin=WEBAUTHN_ORIGIN,
            credential_public_key=row["public_key"],
            credential_current_sign_count=row["sign_count"],
            require_user_verification=True,
        )
    except WebAuthnException as exc:
        print(f"[sync] passkey login rejected: {exc}", file=sys.stderr)
        return jsonify({"error": "verification failed"}), 401
    # The allowlist may have shrunk since the passkey was registered.
    if ALLOWED_EMAILS and row["email"] not in ALLOWED_EMAILS:
        return jsonify({"error": "forbidden"}), 403

    db.execute(
        "UPDATE passkeys SET sign_count = ?, last_used = ? WHERE id = ?",
        (verified.new_sign_count, _now_iso(), row["id"]),
    )
    db.commit()
    session_token = _create_session(row["user_id"])
    return jsonify({"redirect": f"{redirect_uri}#session={session_token}"})


@app.route("/auth/passkey/register/options", methods=["POST"])
def passkey_register_options():
    user = _get_session_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    existing = get_db().execute(
        "SELECT id FROM passkeys WHERE user_id = ?", (user["user_id"],)
    ).fetchall()
    options = generate_registration_options(
        rp_id=WEBAUTHN_RP_ID,
        rp_name=WEBAUTHN_RP_NAME,
        user_id=_user_handle(user["user_id"]),
        user_name=user["email"],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(r["id"])) for r in existing
        ],
    )
    _store_challenge(options.challenge, "register", user["user_id"])
    return app.response_class(options_to_json(options), mimetype="application/json")


@app.route("/auth/passkey/register/verify", methods=["POST"])
def passkey_register_verify():
    user = _get_session_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    credential = data.get("credential")
    name = str(data.get("name") or "").strip()[:64]
    if not isinstance(credential, dict):
        return jsonify({"error": "credential required"}), 400
    challenge = _take_challenge(credential, "register", user["user_id"])
    if challenge is None:
        return jsonify({"error": "unknown or expired challenge"}), 400
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=WEBAUTHN_RP_ID,
            expected_origin=WEBAUTHN_ORIGIN,
            require_user_verification=True,
        )
    except WebAuthnException as exc:
        print(f"[sync] passkey registration rejected: {exc}", file=sys.stderr)
        return jsonify({"error": "verification failed"}), 400

    credential_id = bytes_to_base64url(verified.credential_id)
    db = get_db()
    try:
        db.execute(
            "INSERT INTO passkeys (id, user_id, public_key, sign_count, name, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (credential_id, user["user_id"], verified.credential_public_key,
             verified.sign_count, name, _now_iso()),
        )
    except sqlite3.IntegrityError:
        return jsonify({"error": "passkey already registered"}), 409
    db.commit()
    return jsonify({"id": credential_id, "name": name}), 201


@app.route("/api/passkeys")
def list_passkeys():
    user = _get_session_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    rows = get_db().execute(
        "SELECT id, name, created_at, last_used FROM passkeys "
        "WHERE user_id = ? ORDER BY created_at",
        (user["user_id"],),
    ).fetchall()
    return jsonify({"passkeys": [dict(r) for r in rows]})


@app.route("/api/passkeys/<credential_id>", methods=["DELETE"])
def delete_passkey(credential_id: str):
    user = _get_session_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    db = get_db()
    cur = db.execute(
        "DELETE FROM passkeys WHERE id = ? AND user_id = ?",
        (credential_id, user["user_id"]),
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "not found"}), 404
    return "", 204


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.route("/api/whoami")
def whoami():
    user = _get_session_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    can_ingest = (
        user["email"] in INGEST_EMAILS
        and bool(INGEST_QUEUE)
    )
    return jsonify({"email": user["email"], "can_ingest": can_ingest})


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
            if value not in (0, 1) or not isinstance(ts, str) or not re.match(
                r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$', ts
            ):
                return jsonify({"error": "entry must have value (0|1) and ts (ISO 8601 UTC)"}), 400

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


@app.route("/api/ingest", methods=["OPTIONS", "POST"])
def ingest():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    user = _get_session_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    if user["email"] not in INGEST_EMAILS:
        return jsonify({"error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    video_id = str(data.get("video_id") or "")
    if not re.match(r"^[A-Za-z0-9_-]{11}$", video_id):
        return jsonify({"error": "invalid video_id"}), 400
    if not INGEST_QUEUE:
        return jsonify({"error": "ingest not configured"}), 500
    try:
        with open(INGEST_QUEUE, "a") as f:
            f.write(video_id + "\n")
    except OSError as exc:
        print(f"[sync] ingest queue write failed: {exc}", file=sys.stderr)
        return jsonify({"error": str(exc)}), 500
    print(f"[sync] queued {video_id}", file=sys.stderr)
    return jsonify({"queued": True}), 202


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=PORT, debug=False)
