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
  sync_server.py      # Flask app (~350 lines)
  sync.db             # auto-created SQLite (gitignored)
  .env                # SECRET_KEY, SMTP_*, PORT, BASE_URL, ALLOWED_EMAILS
  requirements.txt    # flask, itsdangerous, python-dotenv
  run.sh              # start script (gunicorn or flask run)
```

The export HTML remains a static file. It communicates with the sync server via `fetch()` using a Bearer token stored in `localStorage`. No `withCredentials` is used; CORS uses `Access-Control-Allow-Origin: *` (compatible with Bearer-token auth). The sync URL is embedded at export time via `export.py --sync-url URL`; if omitted, all sync code is absent from the generated HTML.

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
  expires_at TEXT NOT NULL        -- 30-day TTL; checked on every authenticated request
);

CREATE TABLE video_state (
  user_id    INTEGER NOT NULL REFERENCES users(id),
  video_id   TEXT NOT NULL,       -- max 64 characters
  type       TEXT NOT NULL CHECK(type IN ('read', 'bookmark')),
  value      INTEGER NOT NULL,    -- 1=set, 0=cleared
  updated_at TEXT NOT NULL,       -- ISO 8601 UTC, e.g. "2026-03-17T14:23:00Z"
  PRIMARY KEY (user_id, video_id, type)
);
```

Cleared entries (`value=0`) are stored rather than deleted so that a "mark unread" action propagates correctly across browsers via last-write-wins.

---

## API

All endpoints return `Content-Type: application/json`. All `/api/*` endpoints require `Authorization: Bearer TOKEN`. All responses include `Access-Control-Allow-Origin: *` (no `withCredentials`; compatible with Bearer-token auth).

**Session auth middleware**: validates token exists in `sessions` table **and** `expires_at > now(UTC)`; returns 401 otherwise.

**redirect_uri validation rule** (applied on both `/auth/request-link` and `/auth/verify`): accepted if the URL's origin exactly matches `BASE_URL`, or if the URL scheme is `file://`. The `file://` allowance is intentional for this personal-tool use case where export files are often opened directly from disk; it is an acknowledged trust trade-off, not an oversight.

```
POST /auth/request-link
  body: { email, redirect_uri }
  Applies redirect_uri validation rule (see above). Returns 400 on invalid URI.
  Rate-limited: max 3 requests per email per 10 minutes AND per IP per 10 minutes
    (in-memory counters; reset on process restart — intentional for a personal tool).
  If ALLOWED_EMAILS is non-empty, only sends email when address is in the list;
    otherwise (empty list) accepts any address (open registration).
  Always returns 200 regardless of whether the email was sent (no user enumeration).

GET  /auth/verify?token=TOKEN&redirect_uri=URI
  Applies redirect_uri validation rule. Returns 400 on invalid URI.
  Validates signed token (itsdangerous URLSafeTimedSerializer, 15-min TTL).
  Creates user on first login. Creates 30-day session UUID in DB.
  Note: SECRET_KEY rotation invalidates all outstanding magic links (token
    signatures); existing session rows in the DB remain valid since they use
    random UUIDs (not signed tokens).
  Redirects to redirect_uri#session=UUID.

GET  /api/whoami                    Authorization: Bearer TOKEN
  200 { email } or 401

GET  /api/state                     Authorization: Bearer TOKEN
  200 {
    read:     { video_id: { value: 0|1, ts: iso_ts }, ... },
    bookmark: { video_id: { value: 0|1, ts: iso_ts }, ... }
  }
  All stored entries returned (both value=1 and value=0) so the client can
  detect remotely-cleared flags.

POST /api/state                     Authorization: Bearer TOKEN
  body: {
    read:     { video_id: iso_ts, ... },
    bookmark: { video_id: iso_ts, ... }
  }
  Constraints: max 2000 entries total across both types; video_id max 64 chars;
    invalid/missing iso_ts → 400.
  Merge: for each (video_id, type) in the request, update the DB row only when
    submitted ts > stored ts (or no row exists). DB rows NOT present in the
    request are left unchanged (this is a partial update, not a replacement).
  Returns merged full state in the same shape as GET /api/state.

DELETE /api/session                 Authorization: Bearer TOKEN
  Deletes the current session row only. Other sessions for the same user are
  unaffected. Returns 204.
```

---

## Authentication Flow

1. Export page shows a login bar (email input + "Send link" button) when `SYNC_URL` is set and no valid session exists in `localStorage`.
2. User submits email → `POST /auth/request-link` with `{ email, redirect_uri: window.location.href }`.
3. Server validates `redirect_uri`, applies rate limit, emails magic link: `https://<BASE_URL>/auth/verify?token=SIGNED&redirect_uri=ENCODED`. Token signed with itsdangerous, expires in 15 minutes.
4. User clicks link → server validates token and `redirect_uri` → creates session UUID in DB → redirects to `redirect_uri#session=UUID`.
5. Export page detects `#session=UUID` in the URL fragment on load:
   - **Step 1**: store UUID in `localStorage` as `yt_sync_token`.
   - **Step 2**: strip fragment with `history.replaceState` immediately, before any network call. Wrap in `try/catch` — `replaceState` may throw a security error when the page is opened via `file://` in some browsers; the error is silently swallowed and sync continues normally.
   - **Step 3**: call `GET /api/whoami` to confirm validity and display logged-in email.
6. **Logout**: calls `DELETE /api/session`, removes `yt_sync_token` from `localStorage`. Only the current session is invalidated.
7. **Expired/invalid session**: any 401 response clears the token from `localStorage` and re-shows the login bar.

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
- New `localStorage` entries `yt_read_ts` and `yt_bookmark_ts` store `{ video_id: iso_timestamp }` JSON objects. A missing entry means "never set locally"; its effective timestamp is treated as epoch (always loses to any server value).

**Login bar:**
- Shown in page header when no valid session exists: email input + "Send link" button.
- When logged in: shows email address + "Logout" button.

**Sync status indicator:**
- One-line text near the header: "Synced just now" / "Sync failed" / "Not logged in".
- Updated after each sync attempt.

**On page load:**
1. If `#session=...` fragment is present: store UUID → strip URL (try/catch) → then proceed.
2. Call `GET /api/whoami`; on 401 clear token and show login bar.
3. On success: call `GET /api/state` → run client-side merge (see below) → update cookies and `localStorage`.

**Client-side merge (page load):**

Server response shape: `{ read: { video_id: { value, ts } }, bookmark: { video_id: { value, ts } } }`.

For each `(video_id, type)` in the server response:
- Compare server `ts` against local `ts` from `localStorage` (treat missing local entry as epoch).
- If `server ts >= local ts`: apply server `value` to local state:
  - `value=1`: add `video_id` to the cookie set and update `localStorage` timestamp.
  - `value=0`: **remove** `video_id` from the cookie set and update `localStorage` timestamp.
- If `local ts > server ts`: collect this entry for push-back.

After processing all server entries, also check for local entries not present in the server response (locally-newer by definition). Collect all locally-newer entries and push them in a single `POST /api/state`.

**On toggle:**
- Existing `toggleRead` / `toggleBookmark` functions extended to:
  1. Record current ISO timestamp (UTC) in `localStorage` (`yt_read_ts` / `yt_bookmark_ts`).
  2. Fire background `POST /api/state` with the single changed entry `{ [type]: { [video_id]: iso_timestamp } }`.
  3. Apply the returned merged state using the same merge logic as page load (handles the rare case where the server has a newer conflicting entry).
  4. Update sync status indicator.

---

## Conflict Resolution

Last-write-wins on `updated_at` (ISO 8601 UTC). Server-side merge:

- For each `(video_id, type)` submitted: overwrite if submitted ts is strictly newer than stored ts, or no stored entry exists. Otherwise keep stored value.
- Entries absent from the request body are left unchanged.
- `POST /api/state` returns the complete merged state (including `value=0` entries) so the client can detect and apply remotely-cleared flags.

---

## Configuration (`.env` in `sync-server/`)

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | Yes | itsdangerous signing key. Rotation invalidates outstanding magic links; existing DB sessions are unaffected. |
| `BASE_URL` | Yes | Public URL of the sync server, e.g. `https://sync.example.com`. Used in `redirect_uri` validation and magic-link emails. |
| `PORT` | No | Port to listen on (default: 5000) |
| `ALLOWED_EMAILS` | No | Comma-separated list of permitted email addresses. **Empty (default) = open registration** (any email accepted). |
| `SMTP_HOST` | Yes | Mail server host |
| `SMTP_PORT` | No | Mail server port (default: 587) |
| `SMTP_USER` | Yes | SMTP username |
| `SMTP_PASS` | Yes | SMTP password |
| `SMTP_FROM` | No | From address (defaults to `SMTP_USER`) |

---

## Security Notes

- **Open redirect mitigation**: `redirect_uri` validated on both `/auth/request-link` and `/auth/verify`. Accepted: origin matches `BASE_URL`, or `file://` scheme (personal-tool trade-off, documented).
- **Fragment stripping**: export page strips `#session=UUID` before any outbound fetch; `history.replaceState` failures (e.g. `file://` in some browsers) are silently caught.
- **Session expiry**: auth middleware checks `expires_at > now(UTC)` on every `/api/*` request.
- **Rate limiting**: `/auth/request-link` limited per email and per IP (max 3/10 min, in-memory; resets on restart — intentional for a personal tool).
- **Logout scope**: `DELETE /api/session` removes only the current session.
- **CORS**: `Access-Control-Allow-Origin: *` is safe here because auth uses Bearer tokens (not cookies); `withCredentials: true` is never set.

---

## Out of Scope

- Password-based authentication.
- Multi-user access control (each user sees only their own state).
- Logout-all-devices endpoint.
- Persistent rate-limit storage across server restarts.
