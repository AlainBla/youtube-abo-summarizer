# AGENTS.md

Guidance for AI coding agents (Codex, Gemini CLI, etc.) working in this repository. See CLAUDE.md for the full reference; this file covers the key conventions and gotchas most relevant for automated tasks.

## What this project does

Two-phase pipeline: `collect.py` fetches new YouTube videos, transcripts, and LLM summaries into `data/`; `report.py` / `export.py` read that store and render HTML. An optional Flask sync server (`sync-server/`) lets users sync read/bookmark state across browsers and trigger on-demand ingestion.

## Directory layout

```
collect.py          # collect-phase CLI
report.py           # report-phase CLI
export.py           # export-archive CLI
repair.py           # gap-repair CLI
summarize.py        # legacy all-in-one CLI (no store)
store.py            # SQLite + file store (data/)
transcripts.py      # youtube-transcript-api wrapper
openrouter.py       # LLM client (OpenRouter / Ollama)
renderer.py         # Jinja2 HTML renderer
i18n.py             # de/en UI string dicts
state.py            # last_run.json helpers
send_mail.py        # standalone SMTP sender
youtube_client.py   # YouTube Data API v3 wrapper
template.html.j2    # report template
export.html.j2      # export archive template
sync-server/
  sync_server.py    # Flask sync service
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
- Proxy retry: on `country_blocked`, retries once with a country-pinned Webshare proxy if `WEBSHARE_PROXY_URL` is set

### Renderer / templates
- `renderer.render_html()` and `render_export_html()` accept `lang="de"|"en"`
- `_sanitize_summary()` strips trailing incomplete HTML tags (guards against LLM truncation)
- `export.html.j2`: tag chips and channel names on cards are both clickable — they call `setTagFilter()` / `setChannelFilter()` to toggle the corresponding filter

### Sync server (`sync-server/sync_server.py`)
| Endpoint | Method | Description |
|---|---|---|
| `/auth/request-link` | POST | Send magic-link email |
| `/auth/verify` | GET | Validate token, create session, redirect |
| `/api/whoami` | GET | Returns `{email, can_ingest}` |
| `/api/state` | GET / POST | Read or merge video read/bookmark state (last-write-wins) |
| `/api/session` | DELETE | Log out (delete session token) |
| `/api/ingest` | POST | Trigger `collect.py --video <id>` on the server |

`POST /api/ingest` requires:
- Bearer token belonging to a user whose email is in `INGEST_EMAILS`
- `COLLECT_SCRIPT` env var pointing to an executable `collect.py`
- Body: `{"video_id": "<11-char YouTube ID>"}`
- Runs as subprocess with 120 s timeout; returns `{"ok": true}` or `{"error": "..."}`.

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
| `COLLECT_SCRIPT` | Absolute path to `collect.py` (required for ingest) |
| `PORT` | Default: `5000` |
| `SMTP_*` | Same as above |

## What never to commit

`client_secrets.json`, `token.pickle`, `.env`, `last_run.json`, `data/`, HTML output files — all gitignored.

## Running tests

```bash
pytest
```

Tests live under `tests/` (and `sync-server/tests/` for the sync server). There are no mocked database layers — integration tests use real SQLite.
