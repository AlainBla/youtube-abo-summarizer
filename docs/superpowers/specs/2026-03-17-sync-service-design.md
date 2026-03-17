# Sync Service Design

**Date:** 2026-03-17
**Status:** Approved
**Scope:** Standalone backend service for syncing read/bookmark state across browsers, with magic-link email authentication.

---

## Overview

A lightweight Flask service (`sync-server/`) that persists per-user read and bookmark state in SQLite. The existing export HTML files (`export.html.j2`) are extended to call this service when a `--sync-url` is provided at export time. State conflict resolution uses last-write-wins based on ISO timestamps stored alongside each flag.

---

## Architecture

```
sync-server/          # standalone directory, separate from main project
  sync_server.py      # Flask app (~300 lines)
  sync.db             # auto-created SQLite (gitignored)
  .env                # SECRET_KEY, SMTP_*, PORT, BASE_URL
  requirements.txt    # flask, itsdangerous, python-dotenv
  run.sh              # start script (gunicorn or flask run)
```

The export HTML remains a static file. It communicates with the sync server via `fetch()` using a Bearer token stored in `localStorage`. This works whether the file is opened from disk (`file://`) or served from a web server. The sync URL is embedded at export time via `export.py --sync-url URL`; if omitted, all sync code is absent from the generated HTML.

---

## Data Model

```sql
CREATE TABLE users (
  id         INTEGER PRIMARY KEY,
  email      TEXT UNIQUE NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE sessions (
  token      TEXT PRIMARY KEY,   -- random UUID
  user_id    INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL        -- 30-day TTL
);

CREATE TABLE video_state (
  user_id    INTEGER NOT NULL REFERENCES users(id),
  video_id   TEXT NOT NULL,
  type       TEXT NOT NULL CHECK(type IN ('read', 'bookmark')),
  value      INTEGER NOT NULL,    -- 1=set, 0=cleared
  updated_at TEXT NOT NULL,       -- ISO 8601, e.g. "2026-03-17T14:23:00Z"
  PRIMARY KEY (user_id, video_id, type)
);
```

Cleared entries (`value=0`) are stored rather than deleted so that a "mark unread" action propagates correctly across browsers via last-write-wins.

---

## API

All endpoints return `Content-Type: application/json`. All `/api/*` endpoints require `Authorization: Bearer TOKEN`. All responses include `Access-Control-Allow-Origin: *` to support static file origins.

```
POST /auth/request-link
  body: { email, redirect_uri }
  Always returns 200 (no user enumeration). Sends magic-link email.

GET  /auth/verify?token=TOKEN&redirect_uri=URI
  Validates signed token (itsdangerous URLSafeTimedSerializer, 15-min TTL).
  Creates user on first login. Creates 30-day session UUID in DB.
  Redirects to redirect_uri#session=UUID.

GET  /api/whoami                    Authorization: Bearer TOKEN
  200 { email } or 401

GET  /api/state                     Authorization: Bearer TOKEN
  200 { read: {video_id: iso_ts, ...}, bookmark: {video_id: iso_ts, ...} }
  Only value=1 entries are included.

POST /api/state                     Authorization: Bearer TOKEN
  body: { read: {video_id: iso_ts, ...}, bookmark: {video_id: iso_ts, ...} }
  Merges: updates DB row only when submitted ts > stored ts.
  value=0 (cleared) entries are accepted and propagated if their ts is newer.
  Returns merged full state (same shape as GET /api/state).

DELETE /api/session                 Authorization: Bearer TOKEN
  Deletes session row. Returns 204.
```

---

## Authentication Flow

1. Export page shows a login bar (email input + "Send link" button) when `SYNC_URL` is set and no valid session exists in `localStorage`.
2. User submits email → `POST /auth/request-link` with `{ email, redirect_uri: window.location.href }`.
3. Server emails a magic link: `https://<BASE_URL>/auth/verify?token=SIGNED&redirect_uri=ENCODED`. Token is signed with itsdangerous, expires in 15 minutes.
4. User clicks the link → server validates → creates session UUID in DB → redirects to `redirect_uri#session=UUID`.
5. Export page detects `#session=UUID` in the URL fragment on load → stores UUID in `localStorage` as `yt_sync_token` → strips fragment with `history.replaceState` → calls `GET /api/whoami` to confirm and display logged-in email.
6. **Logout**: calls `DELETE /api/session`, removes `yt_sync_token` from `localStorage`.
7. **Expired/invalid session**: any 401 response clears the token and re-shows the login bar.

---

## Client Integration

### `export.py` changes

- Add `--sync-url URL` argument.
- Pass `sync_url=URL` (or `None`) to the Jinja2 renderer.

### `export.html.j2` changes

All sync additions are wrapped in `{% if sync_url %}` guards so the file is unchanged when no sync URL is provided.

**Embedded constant:**
```js
const SYNC_URL = "{{ sync_url }}";
```

**State format:**
- Existing cookies `yt_read` and `yt_bookmark` remain as comma-separated video IDs — no changes to filtering code.
- New `localStorage` entries `yt_read_ts` and `yt_bookmark_ts` store `{ video_id: iso_timestamp }` JSON objects used for sync and conflict resolution.

**Login bar:**
- Shown in page header when no valid session exists: email input + "Send link" button.
- When logged in: shows email address + "Logout" button.

**Sync status indicator:**
- One-line text near the header: "Synced just now" / "Sync failed" / "Not logged in".
- Updated after each sync attempt.

**On page load:**
1. Read `#session=...` URL fragment if present → store in `localStorage` as `yt_sync_token` → strip with `history.replaceState`.
2. Call `GET /api/whoami`; on 401 clear token and show login bar.
3. On success: call `GET /api/state` → merge with local state (last-write-wins on timestamp) → update cookies and `localStorage`.

**On toggle:**
- Existing `toggleRead` / `toggleBookmark` functions extended to:
  1. Record current ISO timestamp in `localStorage` (`yt_read_ts` / `yt_bookmark_ts`).
  2. Fire background `POST /api/state` with the single changed entry `{ [type]: { [video_id]: timestamp } }`.
  3. Update sync status indicator.

---

## Conflict Resolution

Last-write-wins on `updated_at` (ISO 8601 UTC). The merge logic:

- For each `(video_id, type)` submitted, compare submitted timestamp to the stored timestamp.
- If submitted timestamp is strictly newer (or no stored entry exists), overwrite.
- Otherwise keep stored value.
- The `POST /api/state` response always returns the complete merged state so the client can correct any stale local entries.

---

## Configuration (`.env` in `sync-server/`)

| Variable | Description |
|---|---|
| `SECRET_KEY` | itsdangerous signing key (required) |
| `BASE_URL` | Public URL of the sync server, e.g. `https://sync.example.com` |
| `PORT` | Port to listen on (default: 5000) |
| `SMTP_HOST` | Mail server host |
| `SMTP_PORT` | Mail server port (default: 587) |
| `SMTP_USER` | SMTP username |
| `SMTP_PASS` | SMTP password |
| `SMTP_FROM` | From address (defaults to `SMTP_USER`) |

---

## Out of Scope

- Password-based authentication.
- Multi-user access control (each user sees only their own state).
- Token revocation lists (sessions expire naturally after 30 days).
- Rate limiting (personal/small-group tool).
