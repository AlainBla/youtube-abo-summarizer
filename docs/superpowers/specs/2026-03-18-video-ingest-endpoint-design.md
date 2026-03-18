# Video Ingest Endpoint Design

**Date:** 2026-03-18
**Status:** Approved
**Scope:** Add a `POST /api/ingest` endpoint to the sync server that lets authorised users submit a YouTube video ID for immediate ingestion via `collect.py --video`. Includes client-side URL → ID extraction in the export HTML.

---

## Overview

A new endpoint on the existing Flask sync server (`sync-server/sync_server.py`) allows explicitly-listed users to trigger on-demand ingestion of a single YouTube video. The request blocks until `collect.py --video <id>` completes and returns success or failure. The export HTML adds an ingest input to the sync bar, visible only when the logged-in user has ingest permission.

---

## Architecture

No new files. Changes to two existing files:
- `sync-server/sync_server.py` — new endpoint + env vars + extended whoami
- `export.html.j2` — ingest UI section in sync bar + client-side URL parsing + i18n

---

## Configuration

Two new variables in `sync-server/.env` (and `.env.example`):

| Variable | Required | Description |
|---|---|---|
| `COLLECT_SCRIPT` | Yes (for ingest) | Absolute path to `collect.py`, e.g. `/home/user/youtube-abo-summarizer/collect.py` |
| `INGEST_EMAILS` | No | Comma-separated list of email addresses permitted to trigger ingestion. **Empty (default) = nobody can ingest.** Independent of `ALLOWED_EMAILS`. |

---

## API

### `GET /api/whoami` (extended)

**Before:**
```json
{ "email": "user@example.com" }
```

**After:**
```json
{ "email": "user@example.com", "can_ingest": true }
```

`can_ingest` is `true` when the user's email is in `INGEST_EMAILS` and `COLLECT_SCRIPT` is set; `false` otherwise.

---

### `POST /api/ingest`  `Authorization: Bearer TOKEN`  (also handles `OPTIONS`)

The route is registered with `methods=["OPTIONS", "POST"]`. An `OPTIONS` request returns `{}` 200 immediately (same pattern as `/api/state` and `/api/session`), allowing cross-origin CORS preflight to succeed.

**Request body:**
```json
{ "video_id": "dQw4w9WgXcQ" }
```

**Validation:**
- Bearer token auth (same session middleware as all `/api/*` endpoints)
- User email must be in `INGEST_EMAILS` → 403 `{"error": "forbidden"}` if not
- `video_id` must match `^[A-Za-z0-9_-]{11}$` → 400 `{"error": "invalid video_id"}` if not
- `COLLECT_SCRIPT` must be set and the file must exist → 500 `{"error": "ingest not configured"}` if not

**Execution:**
```python
subprocess.run(
    [sys.executable, COLLECT_SCRIPT, "--video", video_id],
    capture_output=True,
    timeout=120,
    text=True,
)
```

**Responses:**
- `200 {"ok": true}` — exit code 0
- `500 {"error": "<stderr>"}` — non-zero exit code; `stderr` is `result.stderr.strip()` (may be empty string)
- `500 {"error": "timeout"}` — `subprocess.TimeoutExpired`; since `TimeoutExpired.stderr` is `None`, the literal string `"timeout"` is returned instead
- `403 {"error": "forbidden"}` — user not in INGEST_EMAILS
- `400 {"error": "invalid video_id"}` — regex mismatch

CORS headers apply as on all other endpoints.

---

## Client Integration

### `export.html.j2` changes

All additions are inside the existing `{% if sync_url %}` guard.

**`/api/whoami` response handling** — inside `initSync()`, after the existing `showSyncLoggedIn(data.email)` call and state-fetch chain, the response's `can_ingest` flag is checked:
```js
if (data.can_ingest) showIngestUI();
```
`showIngestUI()` sets `document.getElementById('sync-ingest').style.display = ''`.

**Ingest HTML** (inside `.sync-bar`, after the login/user divs):
```html
<div id="sync-ingest" style="display:none">
  <input type="text" id="ingest-input" placeholder="YouTube URL oder Video-ID" autocomplete="off">
  <button id="ingest-btn" onclick="doIngest()">Hinzufügen</button>
</div>
```

**Client-side URL → ID extraction** (never sends URLs to server):
```js
function extractVideoId(raw) {
  var m = raw.match(/(?:v=|youtu\.be\/|embed\/|shorts\/|live\/)([A-Za-z0-9_-]{11})/);
  return m ? m[1] : raw.trim();
}
```

Handles: `youtube.com/watch?v=ID`, `youtu.be/ID`, `youtube.com/embed/ID`, `youtube.com/shorts/ID`, `youtube.com/live/ID`, bare 11-char ID.

**`doIngest()` function:**
1. Extract video ID from input using `extractVideoId()`
2. Validate locally against `/^[A-Za-z0-9_-]{11}$/` — show `syncStatusIngestFailed` immediately if invalid
3. Disable `#ingest-btn` and set status to `syncStatusIngesting` ("Wird hinzugefügt…")
4. Retrieve the session token via `getSyncToken()`
5. POST `{ video_id }` to `SYNC_URL + '/api/ingest'` with `Authorization: Bearer <token>`
6. Re-enable `#ingest-btn`
7. On 200: set status to `syncStatusIngested`, clear input
8. On error: set status to `syncStatusIngestFailed`

**New i18n keys** (added to `I18N.de` and `I18N.en`):

| Key | de | en |
|---|---|---|
| `ingestPlaceholder` | `YouTube URL oder Video-ID` | `YouTube URL or video ID` |
| `ingestBtn` | `Hinzufügen` | `Add` |
| `syncStatusIngesting` | `Wird hinzugefügt…` | `Adding video…` |
| `syncStatusIngested` | `Video hinzugefügt` | `Video added` |
| `syncStatusIngestFailed` | `Hinzufügen fehlgeschlagen` | `Failed to add video` |

**`applyLang()` extension** updates `#ingest-input` placeholder and `#ingest-btn` text.

---

## Security Notes

- **Input validation**: backend rejects anything not matching `^[A-Za-z0-9_-]{11}$` — no shell injection possible since the value is passed as a positional argument to `subprocess.run` (list form, no shell=True).
- **Authorization**: `INGEST_EMAILS` is separate from `ALLOWED_EMAILS`; an empty list means nobody can ingest even if logged in.
- **No shell**: subprocess called with `shell=False` (default) and a list argument — the video ID never touches a shell.
- **Timeout**: 120-second subprocess timeout prevents indefinite blocking.
- **CORS**: same `Access-Control-Allow-Origin: *` as all other endpoints; safe because Bearer token auth is used.

---

## Out of Scope

- Batch ingestion (multiple videos per request)
- Ingestion status polling / async execution
- Ingestion history or audit log
- URL validation beyond video ID extraction
