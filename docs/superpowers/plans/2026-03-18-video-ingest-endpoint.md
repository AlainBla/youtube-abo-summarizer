# Video Ingest Endpoint Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `POST /api/ingest` endpoint to the sync server so explicitly-listed users can submit a YouTube video URL or ID for immediate ingestion via `collect.py --video`, with a matching UI in `export.html.j2`.

**Architecture:** Two files change. The server (`sync-server/sync_server.py`) gains two new env vars (`COLLECT_SCRIPT`, `INGEST_EMAILS`), an extended `/api/whoami` response (`can_ingest` bool), and a new `POST /api/ingest` endpoint that validates input and runs `collect.py --video <id>` as a subprocess. The export template (`export.html.j2`) gains 5 i18n keys, a hidden `#sync-ingest` div in the sync bar, and three new JS functions (`extractVideoId`, `showIngestUI`, `doIngest`) wired into the existing `initSync()` chain.

**Tech Stack:** Python/Flask, `subprocess.run`, pytest (server tests); Jinja2 template, vanilla JS (client — no automated test framework, manual verification).

---

## File Map

| File | Change |
|---|---|
| `sync-server/sync_server.py` | Add `COLLECT_SCRIPT`/`INGEST_EMAILS` module-level config; extend `whoami`; add `POST /api/ingest` |
| `sync-server/.env.example` | Document the two new env vars |
| `sync-server/tests/test_sync_server.py` | Tests for whoami extension + all ingest endpoint paths |
| `export.html.j2` | 5 new i18n keys; `#sync-ingest` HTML; `applyLang` extension; `extractVideoId`, `showIngestUI`, `doIngest`; wire into `initSync` |

---

## Task 1: Server — config vars + whoami extension

**Files:**
- Modify: `sync-server/sync_server.py` (after line 27, the `ALLOWED_EMAILS` block; and the `whoami` route at line 231)
- Modify: `sync-server/.env.example`
- Modify: `sync-server/tests/test_sync_server.py` (after the existing whoami tests, around line 160)

### Background

The existing module-level config block (lines 20–32 of `sync_server.py`) reads env vars at import time. Two new vars are added here. The `whoami` route (line 231) currently returns `{"email": ...}`; it needs a `can_ingest` field added.

- [ ] **Step 1: Write the failing tests**

Add these tests to `sync-server/tests/test_sync_server.py` immediately after the `test_whoami_valid_token` test (after line 159):

```python
# ── GET /api/whoami — can_ingest ─────────────────────────────────────────────

def test_whoami_can_ingest_false_by_default(client, session_token):
    # No INGEST_EMAILS set → can_ingest must be False
    r = client.get("/api/whoami", headers={"Authorization": f"Bearer {session_token}"})
    assert r.status_code == 200
    assert r.get_json().get("can_ingest") is False


def test_whoami_can_ingest_false_without_collect_script(client, session_token, monkeypatch, tmp_path):
    # INGEST_EMAILS set but COLLECT_SCRIPT not set → False
    monkeypatch.setattr(sync_server, "INGEST_EMAILS", {"user@example.com"})
    monkeypatch.setattr(sync_server, "COLLECT_SCRIPT", "")
    r = client.get("/api/whoami", headers={"Authorization": f"Bearer {session_token}"})
    assert r.get_json().get("can_ingest") is False


def test_whoami_can_ingest_true(client, session_token, monkeypatch, tmp_path):
    # Both INGEST_EMAILS and COLLECT_SCRIPT set for this user → True
    script = tmp_path / "collect.py"
    script.write_text("# fake")
    monkeypatch.setattr(sync_server, "INGEST_EMAILS", {"user@example.com"})
    monkeypatch.setattr(sync_server, "COLLECT_SCRIPT", str(script))
    r = client.get("/api/whoami", headers={"Authorization": f"Bearer {session_token}"})
    assert r.get_json().get("can_ingest") is True


def test_whoami_can_ingest_false_email_not_in_list(client, session_token, monkeypatch, tmp_path):
    # COLLECT_SCRIPT set, but user's email not in INGEST_EMAILS → False
    script = tmp_path / "collect.py"
    script.write_text("# fake")
    monkeypatch.setattr(sync_server, "INGEST_EMAILS", {"other@example.com"})
    monkeypatch.setattr(sync_server, "COLLECT_SCRIPT", str(script))
    r = client.get("/api/whoami", headers={"Authorization": f"Bearer {session_token}"})
    assert r.get_json().get("can_ingest") is False
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd sync-server && python -m pytest tests/test_sync_server.py -k "can_ingest" -v
```

Expected: 4 failures — `can_ingest` key not yet present, `.get()` returns `None` which is not `False`/`True`.

- [ ] **Step 3: Add module-level config to sync_server.py**

In `sync-server/sync_server.py`, add these two lines immediately after the `ALLOWED_EMAILS` block (after line 27):

```python
COLLECT_SCRIPT = os.environ.get("COLLECT_SCRIPT", "")
INGEST_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("INGEST_EMAILS", "").split(",")
    if e.strip()
}
```

- [ ] **Step 4: Extend the whoami route**

In `sync-server/sync_server.py`, replace the `whoami` route body:

**Before (lines 231–236):**
```python
@app.route("/api/whoami")
def whoami():
    user = _get_session_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"email": user["email"]})
```

**After:**
```python
@app.route("/api/whoami")
def whoami():
    user = _get_session_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    can_ingest = (
        user["email"].lower() in INGEST_EMAILS
        and bool(COLLECT_SCRIPT)
    )
    return jsonify({"email": user["email"], "can_ingest": can_ingest})
```

- [ ] **Step 5: Update .env.example**

In `sync-server/.env.example`, add after the `ALLOWED_EMAILS` line:

```
# Comma-separated emails allowed to trigger on-demand ingestion. Empty = nobody.
INGEST_EMAILS=
# Absolute path to collect.py (required for ingest to work)
COLLECT_SCRIPT=
```

- [ ] **Step 6: Run tests to confirm they pass**

```bash
cd sync-server && python -m pytest tests/test_sync_server.py -k "can_ingest" -v
```

Expected: 4 tests pass.

- [ ] **Step 7: Run the full test suite**

```bash
cd sync-server && python -m pytest tests/ -v
```

Expected: all previously passing tests still pass.

- [ ] **Step 8: Commit**

```bash
git add sync-server/sync_server.py sync-server/.env.example sync-server/tests/test_sync_server.py
git commit -m "feat(sync): add INGEST_EMAILS/COLLECT_SCRIPT config and can_ingest to whoami"
```

---

## Task 2: Server — POST /api/ingest endpoint

**Files:**
- Modify: `sync-server/sync_server.py` (add route after the `delete_session` route, before `if __name__ == "__main__":`)
- Modify: `sync-server/tests/test_sync_server.py` (add ingest tests after the whoami tests)

### Background

The new endpoint must handle `OPTIONS` preflight (same as `/api/state` and `/api/session`). Validation order: 401 → 403 → 400 → 500 (config). Then `subprocess.run` with a 120-second timeout. Exit code 0 → 200 `{"ok": true}`. Non-zero → 500 with stderr (or fallback string). `TimeoutExpired` → 500 `{"error": "timeout"}` (because `TimeoutExpired.stderr` is `None`). The `COLLECT_SCRIPT` existence check happens per-request, not at startup.

- [ ] **Step 1: Write the failing tests**

Add these tests to `sync-server/tests/test_sync_server.py` after the `can_ingest` tests:

```python
# ── POST /api/ingest ─────────────────────────────────────────────────────────

def test_ingest_options_preflight(client):
    r = client.options("/api/ingest")
    assert r.status_code == 200


def test_ingest_no_token(client):
    r = client.post("/api/ingest", json={"video_id": "dQw4w9WgXcQ"})
    assert r.status_code == 401


def test_ingest_forbidden_not_in_ingest_emails(client, session_token, monkeypatch, tmp_path):
    script = tmp_path / "collect.py"
    script.write_text("# fake")
    monkeypatch.setattr(sync_server, "INGEST_EMAILS", {"other@example.com"})
    monkeypatch.setattr(sync_server, "COLLECT_SCRIPT", str(script))
    r = client.post(
        "/api/ingest",
        json={"video_id": "dQw4w9WgXcQ"},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert r.status_code == 403
    assert r.get_json()["error"] == "forbidden"


def test_ingest_invalid_video_id(client, session_token, monkeypatch, tmp_path):
    script = tmp_path / "collect.py"
    script.write_text("# fake")
    monkeypatch.setattr(sync_server, "INGEST_EMAILS", {"user@example.com"})
    monkeypatch.setattr(sync_server, "COLLECT_SCRIPT", str(script))
    for bad_id in ["tooshort", "this_is_too_long_id!!", "has space     ", "", None]:
        r = client.post(
            "/api/ingest",
            json={"video_id": bad_id},
            headers={"Authorization": f"Bearer {session_token}"},
        )
        assert r.status_code == 400, f"expected 400 for {bad_id!r}"
        assert r.get_json()["error"] == "invalid video_id"


def test_ingest_authz_checked_before_config(client, session_token, monkeypatch):
    # 403 must take priority over 500-config (spec validation order: 401 → 403 → 400 → 500)
    monkeypatch.setattr(sync_server, "INGEST_EMAILS", {"other@example.com"})
    monkeypatch.setattr(sync_server, "COLLECT_SCRIPT", "")
    r = client.post(
        "/api/ingest",
        json={"video_id": "dQw4w9WgXcQ"},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert r.status_code == 403


def test_ingest_not_configured_no_script(client, session_token, monkeypatch):
    monkeypatch.setattr(sync_server, "INGEST_EMAILS", {"user@example.com"})
    monkeypatch.setattr(sync_server, "COLLECT_SCRIPT", "")
    r = client.post(
        "/api/ingest",
        json={"video_id": "dQw4w9WgXcQ"},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert r.status_code == 500
    assert r.get_json()["error"] == "ingest not configured"


def test_ingest_not_configured_script_missing(client, session_token, monkeypatch):
    monkeypatch.setattr(sync_server, "INGEST_EMAILS", {"user@example.com"})
    monkeypatch.setattr(sync_server, "COLLECT_SCRIPT", "/nonexistent/collect.py")
    r = client.post(
        "/api/ingest",
        json={"video_id": "dQw4w9WgXcQ"},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert r.status_code == 500
    assert r.get_json()["error"] == "ingest not configured"


def test_ingest_success(client, session_token, monkeypatch, tmp_path):
    # collect.py exits 0
    script = tmp_path / "collect.py"
    script.write_text("import sys; sys.exit(0)")
    monkeypatch.setattr(sync_server, "INGEST_EMAILS", {"user@example.com"})
    monkeypatch.setattr(sync_server, "COLLECT_SCRIPT", str(script))
    r = client.post(
        "/api/ingest",
        json={"video_id": "dQw4w9WgXcQ"},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}


def test_ingest_collect_failure_with_stderr(client, session_token, monkeypatch, tmp_path):
    # collect.py exits non-zero with stderr message
    script = tmp_path / "collect.py"
    script.write_text("import sys; sys.stderr.write('something went wrong'); sys.exit(1)")
    monkeypatch.setattr(sync_server, "INGEST_EMAILS", {"user@example.com"})
    monkeypatch.setattr(sync_server, "COLLECT_SCRIPT", str(script))
    r = client.post(
        "/api/ingest",
        json={"video_id": "dQw4w9WgXcQ"},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert r.status_code == 500
    assert r.get_json()["error"] == "something went wrong"


def test_ingest_collect_failure_empty_stderr(client, session_token, monkeypatch, tmp_path):
    # collect.py exits non-zero with no stderr → fallback message
    script = tmp_path / "collect.py"
    script.write_text("import sys; sys.exit(1)")
    monkeypatch.setattr(sync_server, "INGEST_EMAILS", {"user@example.com"})
    monkeypatch.setattr(sync_server, "COLLECT_SCRIPT", str(script))
    r = client.post(
        "/api/ingest",
        json={"video_id": "dQw4w9WgXcQ"},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert r.status_code == 500
    assert r.get_json()["error"] == "collect.py exited with non-zero status"


def test_ingest_timeout(client, session_token, monkeypatch, tmp_path):
    script = tmp_path / "collect.py"
    script.write_text("# fake")
    monkeypatch.setattr(sync_server, "INGEST_EMAILS", {"user@example.com"})
    monkeypatch.setattr(sync_server, "COLLECT_SCRIPT", str(script))

    def fake_run(*args, **kwargs):
        raise sync_server.subprocess.TimeoutExpired(cmd=args[0], timeout=120)

    monkeypatch.setattr(sync_server.subprocess, "run", fake_run)
    r = client.post(
        "/api/ingest",
        json={"video_id": "dQw4w9WgXcQ"},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert r.status_code == 500
    assert r.get_json()["error"] == "timeout"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd sync-server && python -m pytest tests/test_sync_server.py -k "ingest" -v
```

Expected: all fail — endpoint doesn't exist yet. (12 tests)

- [ ] **Step 3: Add `import subprocess` to sync_server.py**

At the top of `sync-server/sync_server.py`, add `subprocess` to the existing stdlib imports block:

```python
import subprocess
```

(Add after `import sys` on line 5.)

- [ ] **Step 4: Add the ingest endpoint to sync_server.py**

Add the following route in `sync-server/sync_server.py` immediately before the `if __name__ == "__main__":` block (after the `delete_session` route):

```python
@app.route("/api/ingest", methods=["OPTIONS", "POST"])
def ingest():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    user = _get_session_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    if user["email"].lower() not in INGEST_EMAILS:
        return jsonify({"error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    video_id = str(data.get("video_id") or "")
    if not re.match(r"^[A-Za-z0-9_-]{11}$", video_id):
        return jsonify({"error": "invalid video_id"}), 400
    if not COLLECT_SCRIPT or not os.path.isfile(COLLECT_SCRIPT):
        return jsonify({"error": "ingest not configured"}), 500
    try:
        result = subprocess.run(
            [sys.executable, COLLECT_SCRIPT, "--video", video_id],
            capture_output=True,
            timeout=120,
            text=True,
        )
    except subprocess.TimeoutExpired:
        return jsonify({"error": "timeout"}), 500
    if result.returncode != 0:
        msg = result.stderr.strip() or "collect.py exited with non-zero status"
        return jsonify({"error": msg}), 500
    return jsonify({"ok": True})
```

- [ ] **Step 5: Run ingest tests to confirm they pass**

```bash
cd sync-server && python -m pytest tests/test_sync_server.py -k "ingest" -v
```

Expected: all 12 ingest tests pass.

- [ ] **Step 6: Run full test suite**

```bash
cd sync-server && python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add sync-server/sync_server.py sync-server/tests/test_sync_server.py
git commit -m "feat(sync): add POST /api/ingest endpoint"
```

---

## Task 3: Client — ingest UI in export.html.j2

**Files:**
- Modify: `export.html.j2` (four separate locations; exact line numbers given below for the file as it currently stands)

### Background

`export.html.j2` is a Jinja2 template — there's no automated JS test framework. Changes are verified by manually running `export.py` and opening the HTML. All changes are inside or adjacent to the existing `{% if sync_url %}` block.

There are five places to edit:

1. **i18n** — add 5 keys to both `I18N.de` and `I18N.en` (around lines 467–473 and 514–520)
2. **Sync bar HTML** — add `#sync-ingest` div after `#sync-user` div (after line 357, before `<span class="sync-status"...>`)
3. **`applyLang()`** — add two lines inside the `{% if sync_url %}` block (after line 883)
4. **`showSyncLoggedOut()`** — add one line to hide `#sync-ingest` on logout/401
5. **New JS functions + initSync wire-up** — add `extractVideoId`, `showIngestUI`, `doIngest` functions; modify the `.then(function(data)` handler in `initSync` to call `showIngestUI()` when `can_ingest` is true

There is no automated test for the client. Verification is done by running export.py and visually checking the result.

- [ ] **Step 1: Add i18n keys to I18N.de**

In `export.html.j2`, find the `syncStatusFailed` line in `I18N.de` (currently line 473):

```js
    syncStatusFailed: 'Sync fehlgeschlagen',
```

Add these 5 lines immediately after it (before the closing `},`):

```js
    ingestPlaceholder: 'YouTube URL oder Video-ID',
    ingestBtn: 'Hinzuf\u00fcgen',
    syncStatusIngesting: 'Wird hinzugef\u00fcgt\u2026',
    syncStatusIngested: 'Video hinzugef\u00fcgt',
    syncStatusIngestFailed: 'Hinzuf\u00fcgen fehlgeschlagen',
```

- [ ] **Step 2: Add i18n keys to I18N.en**

Find the `syncStatusFailed` line in `I18N.en` (currently line 520):

```js
    syncStatusFailed: 'Sync failed',
```

Add these 5 lines immediately after it (before the closing `}`):

```js
    ingestPlaceholder: 'YouTube URL or video ID',
    ingestBtn: 'Add',
    syncStatusIngesting: 'Adding video\u2026',
    syncStatusIngested: 'Video added',
    syncStatusIngestFailed: 'Failed to add video',
```

- [ ] **Step 3: Add #sync-ingest HTML to the sync bar**

Find this block in `export.html.j2` (currently lines 354–358):

```html
  <div id="sync-user" style="display:none">
    <span class="sync-user-email" id="sync-user-email"></span>
    <button id="sync-logout-btn" onclick="doLogout()">Abmelden</button>
  </div>
  <span class="sync-status" id="sync-status"></span>
```

Replace it with:

```html
  <div id="sync-user" style="display:none">
    <span class="sync-user-email" id="sync-user-email"></span>
    <button id="sync-logout-btn" onclick="doLogout()">Abmelden</button>
  </div>
  <div id="sync-ingest" style="display:none">
    <input type="text" id="ingest-input" placeholder="YouTube URL oder Video-ID" autocomplete="off">
    <button id="ingest-btn" onclick="doIngest()">Hinzufügen</button>
  </div>
  <span class="sync-status" id="sync-status"></span>
```

- [ ] **Step 4: Extend applyLang() for ingest elements**

Find this block inside the `{% if sync_url %}` section of `applyLang()` (currently lines 876–884):

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

Replace it with:

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
    var ingestInput = document.getElementById('ingest-input');
    if (ingestInput) ingestInput.placeholder = s.ingestPlaceholder;
    var ingestBtn = document.getElementById('ingest-btn');
    if (ingestBtn) ingestBtn.textContent = s.ingestBtn;
  })();
  {% endif %}
```

- [ ] **Step 5: Update showSyncLoggedOut() to also hide #sync-ingest**

`showSyncLoggedOut()` was written before `#sync-ingest` existed. When called on 401 or logout, it must hide the ingest widget too, otherwise the ingest input stays visible in a logged-out state.

Find `showSyncLoggedOut` (currently around line 616):

```js
function showSyncLoggedOut() {
  document.getElementById('sync-login').style.display = '';
  document.getElementById('sync-user').style.display = 'none';
  updateSyncStatus(I18N[currentLang || 'de'].syncStatusNotLoggedIn);
}
```

Replace it with:

```js
function showSyncLoggedOut() {
  document.getElementById('sync-login').style.display = '';
  document.getElementById('sync-user').style.display = 'none';
  var ingest = document.getElementById('sync-ingest');
  if (ingest) ingest.style.display = 'none';
  updateSyncStatus(I18N[currentLang || 'de'].syncStatusNotLoggedIn);
}
```

- [ ] **Step 6: Add extractVideoId, showIngestUI, and doIngest functions**

Find the `syncRequestLink` function (currently starts at line 750). Add these three functions immediately before it (after the closing `}` of `doLogout`, before `function syncRequestLink`):

```js
function extractVideoId(raw) {
  var m = raw.match(/(?:v=|youtu\.be\/|embed\/|shorts\/|live\/)([A-Za-z0-9_-]{11})/);
  return m ? m[1] : raw.trim();
}

function showIngestUI() {
  var el = document.getElementById('sync-ingest');
  if (el) el.style.display = '';
}

function doIngest() {
  var raw = (document.getElementById('ingest-input').value || '').trim();
  var videoId = extractVideoId(raw);
  var s = I18N[currentLang || 'de'];
  if (!/^[A-Za-z0-9_-]{11}$/.test(videoId)) {
    updateSyncStatus(s.syncStatusIngestFailed);
    return;
  }
  var btn = document.getElementById('ingest-btn');
  if (btn) btn.disabled = true;
  updateSyncStatus(s.syncStatusIngesting);
  var token = getSyncToken();
  fetch(SYNC_URL + '/api/ingest', {
    method: 'POST',
    headers: {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
    body: JSON.stringify({video_id: videoId})
  })
  .then(function(r) {
    if (btn) btn.disabled = false;
    if (r.status === 401) {
      localStorage.removeItem('yt_sync_token');
      showSyncLoggedOut();
      return null;
    }
    return r.json().then(function(body) { return {status: r.status, body: body}; });
  })
  .then(function(res) {
    if (!res) return;
    if (res.status === 200) {
      updateSyncStatus(s.syncStatusIngested);
      document.getElementById('ingest-input').value = '';
    } else {
      updateSyncStatus(s.syncStatusIngestFailed);
    }
  })
  .catch(function() {
    if (btn) btn.disabled = false;
    updateSyncStatus(s.syncStatusIngestFailed);
  });
}
```

- [ ] **Step 7: Wire can_ingest into initSync**

Find this block in `initSync` (currently lines 720–726):

```js
    .then(function(data) {
      if (!data) return null;
      showSyncLoggedIn(data.email);
      return fetch(SYNC_URL + '/api/state', {
        headers: {'Authorization': 'Bearer ' + token}
      });
    })
```

Replace it with:

```js
    .then(function(data) {
      if (!data) return null;
      showSyncLoggedIn(data.email);
      if (data.can_ingest) showIngestUI();
      return fetch(SYNC_URL + '/api/state', {
        headers: {'Authorization': 'Bearer ' + token}
      });
    })
```

- [ ] **Step 8: Manual verification**

Generate a test export (requires DB with at least one video; if DB is empty, skip to the next step):

```bash
python export.py --all --sync-url https://sync.example.com --output /tmp/test_ingest.html
```

Open `/tmp/test_ingest.html` in a browser. Confirm:
- Sync bar shows email input + "Link senden" button (logged out state) as before
- Switching language to English updates all sync bar text correctly (including any ingest strings if the UI were visible)
- `#sync-ingest` is hidden (not yet logged in)
- JS console shows no errors

In the browser console, verify `extractVideoId` handles all URL forms:
```js
extractVideoId("https://www.youtube.com/watch?v=dQw4w9WgXcQ")  // → "dQw4w9WgXcQ"
extractVideoId("https://youtu.be/dQw4w9WgXcQ")                 // → "dQw4w9WgXcQ"
extractVideoId("https://www.youtube.com/embed/dQw4w9WgXcQ")    // → "dQw4w9WgXcQ"
extractVideoId("https://www.youtube.com/shorts/dQw4w9WgXcQ")   // → "dQw4w9WgXcQ"
extractVideoId("https://www.youtube.com/live/dQw4w9WgXcQ")     // → "dQw4w9WgXcQ"
extractVideoId("dQw4w9WgXcQ")                                   // → "dQw4w9WgXcQ"
extractVideoId("tooshort")                                      // → "tooshort" (invalid, rejected by doIngest)
```

If no videos in DB, verify the template renders without errors (no Jinja2 exception).

- [ ] **Step 9: Commit**

```bash
git add export.html.j2
git commit -m "feat(export): add ingest UI to sync bar (can_ingest gate, doIngest, i18n)"
```

---

## Final Step: Push

```bash
git push
```
