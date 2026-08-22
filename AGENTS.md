# AGENTS.md

Guidance for AI coding agents (Codex, Gemini CLI, etc.) working in this repository. See CLAUDE.md for the full reference; this file covers the key conventions and gotchas most relevant for automated tasks.

## What this project does

Two-phase pipeline: `collect.py` fetches new YouTube videos, transcripts, and LLM summaries into `data/`; `report.py` / `export.py` read that store and render HTML. An optional Flask sync server (`sync-server/`) lets users sync read/bookmark state across browsers and trigger on-demand ingestion.

## Directory layout

```
collect.py              # collect-phase CLI
report.py               # report-phase CLI
export.py               # export-archive CLI
repair.py               # gap-repair CLI
recover_from_export.py  # restore store entries from an exported HTML file
summarize.py            # legacy all-in-one CLI (no store)
store.py                # SQLite + file store (data/)
transcripts.py          # youtube-transcript-api wrapper
openrouter.py           # LLM client (OpenRouter / Ollama)
renderer.py             # Jinja2 HTML renderer
i18n.py                 # de/en UI string dicts
state.py                # last_run.json helpers
send_mail.py            # standalone SMTP sender
youtube_client.py       # YouTube Data API v3 wrapper
template.html.j2        # report template
export.html.j2          # export archive template
ingest_worker.sh        # cron script: drains INGEST_QUEUE via collect.py
sync-server/
  sync_server.py        # Flask sync service
  .env.example
```

Generated at runtime (gitignored): `data/`, `last_run.json`, `*.html` output files.

## Key conventions

### Store
- `data/videos.db` — SQLite; schema in `store.py`; `tags` column is a JSON array (`TEXT`)
- `store.get_video(video_id)` returns a dict with `has_transcript` and `has_summary` flags (file-existence checks)
- `store.add_video()` and `store.update_video_with_summary()` accept a `tags=` list kwarg
- All store read helpers deserialise `tags` to `list[str]` (empty list when `NULL`)

### LLM client
- `openrouter.summarize_video()` returns `(summary_html: str, tags: list[str])`
- Tags come from a `<!-- tags: ... -->` HTML comment appended by the model; `_parse_tags()` strips it
- `max_tokens=16384`; raises `ValueError` if the model returns `null` content

### Transcripts
- `get_transcript()` returns `(text: str | None, status: str | None)`
- Status values: `None` (success), `"ip_blocked"`, `"rate_limited"`, `"country_blocked"`, `"unavailable"`
- `country_blocked` only when the `VideoUnplayable` reason mentions "country"/"region"; other `VideoUnplayable` causes → `unavailable`
- `requests.exceptions.ProxyError` / `ConnectionError` → `unavailable`
- Proxy retry: on `ip_blocked`, retries once via the configured proxy (if set); on `country_blocked`, retries once with a country-pinned Webshare proxy if `WEBSHARE_PROXY_URL` is set

### Collect / cron wiring
- `collect.py` exits with `EXIT_NEW_VIDEOS` (10) when a run stored at least one new video, `0` when it ran fine but stored nothing. `main()` returns the count; the mapping lives in `_exit_code()` so it stays testable
- Anything that shells out to `collect.py` must treat 10 as success — `ingest_worker.sh` would otherwise re-queue every video it just ingested. `collect.sh` gates the archive re-export on exactly this code; a plain `&&` would re-export every run and keep the export's update banner permanently lit (the banner triggers on a changed `generated_at`, not on new rows)

- The repository is **public**. No sync URL, output path or mail address belongs in a tracked `*.sh`; they come from `cron.env` (gitignored, template `cron.env.example`), which every cron script sources. `tests/test_collect_shell_wiring.py` guards this

### Renderer / templates
- `renderer.render_html()` accepts `lang="de"|"en"`
- `renderer.render_export_html()` accepts `lang=`, `sync_url=`, and `show_embed=` (default `True`; pass `False` for `--thumbnail` mode which renders static `<img>` instead of YouTube `<iframe>`)
- `_sanitize_summary()` strips trailing incomplete HTML tags (guards against LLM truncation)
- `renderer._split_export_data()` is the single source of truth for both sort order and chunk boundaries: videos are sorted `published_at` descending (`video_id` descending as tie-break), then split into a summary-free `index` plus a list of summary chunks of `EXPORT_CHUNK_SIZE` (50) videos each — chunk `k` covers index positions `[k*50, (k+1)*50)`. Anything that reorders or re-chunks export data must go through this function, not reimplement the sort/slice logic
- `renderer.EXPORT_FIRST_PAGE` (20) drives both the number of statically pre-rendered cards and the JS `PAGE_SIZE` (rendered into the template from the same constant) — they cannot drift apart because one is derived from the other
- The Jinja macro `card(v)` in `export.html.j2` pre-renders the first page into the document and **must stay markup-identical** to the JS `buildCard()` — `tests/test_export_prerender.py` enforces this by diffing their output; if you change one, change the other in the same commit
- Summary HTML is never in a flat `SUMMARIES` map — always go through `getSummary(v)`, which looks up `CHUNKS[v._c]` (the chunk index stamped onto each video during `bootstrap()`). Direct chunk access bypasses the on-demand decode and will read `undefined` for chunks not yet fetched
- The data-blob `<script>` (embedding `INDEX_B64`/`SUM_B64` or `DATA_OBJ`) must stay the last element before `</body>`, so the shell and pre-rendered grid parse and paint before the browser has to handle the base64 payload. `bootstrap()` is scheduled via `requestAnimationFrame` from *inside* that same script rather than from the earlier UI script — scheduling it earlier could fire the callback while the parser hasn't reached `INDEX_B64`/`SUM_B64` yet (temporal dead zone)
- `export.html.j2`: the `added-desc` sort orders by `collected_at` (when the video entered the store, so on-demand ingests surface at the top), tie-broken by `published_at` descending because `collect.py` stamps one timestamp per run; it falls back to `published_at` for records without the field. `export.py` must keep passing `collected_at` through into the video dicts or the option silently degrades to publish order
- `renderer._export_manifest()` + `render_export_html()`: every export writes `<output_path>.meta.json` **after** the HTML (never before — a manifest ahead of its archive announces videos the served page does not have yet) and embeds the same object as `const MANIFEST`. `export.html.j2` polls it (`checkForUpdate()`, `MANIFEST_URL` = the sidecar's basename, resolved relative to the page) every `UPDATE_POLL_MS` and shows `#update-banner`. Only `generated_at` decides whether something changed; the count difference decides only the wording, so a shrinking filtered export still reports correctly. Any deploy step that copies the export must copy the sidecar too
- `tests/dom_stub.js` wraps `setInterval` and unrefs the timer: the page schedules 5-minute intervals (update poll, sync pull) that would otherwise keep every Node test process alive until the first tick. Snippets may still call `process.exit(0)` after logging; both mechanisms are fine
- `export.html.j2`: the `?v=ID` deep link enters its view from a parse-time hook, *before* the data blob is parsed — a large archive would otherwise show 20 wrong cards or an empty grid while the index decodes. Keep the three states apart: loading (set by `enterSingleVideoView()`), found, and `singleNotFound`; the not-found verdict is only valid against the decoded index, never against what happens to be rendered
- `export.html.j2`: the card share button lives in the Jinja macro *and* in `buildCard()`; its label comes from `i18n.py` (`share_btn`) on one side and the JS `I18N` (`shareBtn`) on the other. Both strings must match or `tests/test_export_prerender.py` fails
- `export.html.j2`: tag chips and channel names on cards are both clickable — they call `setTagFilter()` / `setChannelFilter()` to toggle the corresponding filter
- `export.html.j2`: with `sync_url` set, `warnIfInsecure()` shows one localized `alert()` on load when `loginRedirectAccepted()` is false. That check mirrors the server's `_valid_redirect_uri()`: any `file://` URI passes, otherwise `window.location.origin` must equal the `SYNC_URL` origin. `loginRedirectUri()` is the single source of the redirect URI — `syncRequestLink()` sends exactly what the check evaluates, so keep them together
- `export.html.j2`: toggling read/bookmark only re-renders the affected card (via `updateCardInPlace()`) unless the current read/bookmark filter would exclude it, in which case `applyFiltersAndSort()` is called instead

### Sync server (`sync-server/sync_server.py`)
| Endpoint | Method | Description |
|---|---|---|
| `/auth/request-link` | POST | Send magic-link email |
| `/auth/verify` | GET | Validate token, create session, redirect |
| `/api/whoami` | GET | Returns `{email, can_ingest}` |
| `/api/state` | GET / POST | Read or merge video read/bookmark state (last-write-wins) |
| `/api/session` | DELETE | Log out (delete session token) |
| `/api/ingest` | POST | Append video ID to `INGEST_QUEUE` file; returns 202 |

`POST /api/ingest` requires:
- Bearer token belonging to a user whose email is in `INGEST_EMAILS`
- `INGEST_QUEUE` env var set to an absolute path for the queue file
- Body: `{"video_id": "<11-char YouTube ID>"}`
- Appends the ID to the queue file and returns `{"queued": true}` with HTTP 202. Processing is async via `ingest_worker.sh`.

`can_ingest` in `/api/whoami` is `true` only when both conditions above are configured.

**Production**: use Gunicorn behind Nginx, not `python sync_server.py`. Add `ProxyFix` so the rate limiter sees real client IPs. See README for the full systemd + Nginx setup.

## Environment variables

### Main pipeline (`.env` in project root)

| Variable | Notes |
|---|---|
| `OPENROUTER_API_KEY` | Required for OpenRouter |
| `OPENROUTER_MODEL` | Default: `gpt-oss-20b` |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | Override OpenRouter; use for Ollama |
| `SUMMARY_LANG` | Language name for LLM output (default: `German`) |
| `TRANSCRIPT_LANGS` | BCP-47 priority list (default: `de,en`) |
| `WEBSHARE_PROXY_URL` | Residential proxy URL |
| `PROXY_FALLBACK_COUNTRY` | Country code for geo-block retry (default: `DE`) |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `SMTP_FROM` | Email delivery |

### Sync server (`sync-server/.env`)

| Variable | Notes |
|---|---|
| `SECRET_KEY` | Required; signs magic-link tokens |
| `BASE_URL` | Required; public server URL |
| `ALLOWED_EMAILS` | Login allowlist (empty = any email) |
| `INGEST_EMAILS` | Who may call `POST /api/ingest` (empty = nobody) |
| `INGEST_QUEUE` | Absolute path to the queue file (required for ingest) |
| `PORT` | Default: `5000` |
| `SMTP_*` | Same as above |

## What never to commit

`client_secrets.json`, `token.pickle`, `.env`, `last_run.json`, `data/`, HTML output files — all gitignored.

## Running tests

```bash
pytest
```

Tests live under `tests/` (and `sync-server/tests/` for the sync server). There are no mocked database layers — integration tests use real SQLite.
