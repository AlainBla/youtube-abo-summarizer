# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

Fetch new videos from YouTube channels (via OAuth subscriptions or an explicit list), pull their transcripts, summarize them with an LLM (OpenRouter by default, or a local Ollama instance), and render a single HTML report per run. Reports can optionally be sent via SMTP using `send_mail.py`.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in OPENROUTER_API_KEY and any optional settings
```

## Two-phase pipeline (recommended)

The pipeline is split into a **collect** phase and a **report** phase so that transcript fetching and LLM summarization only happen when new videos are found, not every time a digest is sent.

### Collect phase — run frequently (e.g. every hour)

```bash
# Pull from OAuth subscriptions
python collect.py --auth [--hours N]

# Explicit channels (IDs, handles, or URLs)
python collect.py UC123abc UC456def [--hours N]
python collect.py --file channels.txt [--hours N]
```

- Fetches new videos, transcripts, and summaries; persists results to `data/`.
- Videos already in the store are handled incrementally: skipped entirely if both transcript and summary exist; otherwise only the missing piece is fetched or generated.
- Without `--hours`, uses each channel's last-run timestamp from `last_run.json`; defaults to 24 h on first run.
- `--hours N` overrides last-run state and does **not** update it.
- `--prune-days N` removes store entries older than N days. Omitted by default (no pruning).
- Short videos (duration ≤ `SHORTS_MAX_SECONDS`, default 180 s) are **skipped by default**. Pass `--include-shorts` to collect them. Threshold is configurable via `SHORTS_MAX_SECONDS` in `.env`.

### Report phase — run on digest schedule (e.g. every 6 h or daily)

```bash
python report.py [--hours 24] [--output summary.html] [--skip-empty] [--send-to EMAIL] [--show-model] [--lang de|en]
```

- Reads `data/videos.db`, includes videos published within the last `--hours` hours.
- `--skip-empty` omits channels with no videos in the window.
- `--send-to EMAIL` sends the rendered HTML via SMTP after writing the file.
- `--show-model` shows the LLM model name badge on each video card (hidden by default).
- No YouTube API calls, no LLM calls.

### Cron scripts

| Script | Purpose |
|---|---|
| `collect.sh` | Runs `collect.py --auth`; schedule frequently (e.g. `*/30 * * * *`) |
| `run_6hours.sh` | Renders and mails a 6-hour digest |
| `run_12hours.sh` | Renders and mails a 12-hour digest |
| `run_daily.sh` | Renders and mails a 24-hour digest |

## Repair

`repair.py` scans all store entries and fixes gaps — missing transcript or summary files — and can force re-summarization of specific videos (e.g. after a model produced bad output).

```bash
# Re-summarize two specific videos (most common use case)
python repair.py --force-summarize --video VIDEO_ID_1 VIDEO_ID_2

# Preview what would be repaired without making changes
python repair.py --dry-run

# Repair all missing transcripts and summaries across the whole store
python repair.py

# Re-summarize everything (e.g. after switching models)
python repair.py --force-summarize
```

- Missing transcripts are re-fetched (skips `country_blocked` videos permanently).
- `--force-summarize` re-runs the LLM even if a summary already exists; also re-generates and stores tags.
- `--video ID [ID ...]` restricts all operations to the specified video IDs.
- `--dry-run` prints what would be done without writing anything.
- To backfill tags on existing videos (after upgrading from a version without tag support): `python repair.py --force-summarize`

## Export archive

`export.py` renders stored videos into a self-contained HTML file for offline browsing (client-side search, channel/tag/read/bookmark filters, sort, pagination; read and bookmark state persisted in browser cookies).

```bash
python export.py                        # last 7 days (default)
python export.py --all                  # all videos in store
python export.py --hours 48             # custom time window
python export.py --all --output full_archive.html
python export.py --show-model           # include LLM model badge on cards
python export.py --lang en              # embedded default language (overridden by cookie/browser)
python export.py --thumbnail            # show static thumbnails instead of embedded preview players
python export.py --channel UC123abc     # restrict to a single channel
python export.py --videos abc,def,ghi   # comma-separated list of video IDs
```

`--hours` and `--all` are mutually exclusive. Default output filename: `export_YYYY-MM-DD_HH-MM.html`.
`--show-model` shows the LLM model name badge on each card (hidden by default).

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

### On-demand video ingest

`POST /api/ingest` lets authorised users queue a video for fetching and summarisation without waiting for the next scheduled collect run. The endpoint appends the video ID to a queue file and returns 202 immediately; a separate cron job (`ingest_worker.sh`) processes the queue.

Requires two env vars in `sync-server/.env` (or the systemd unit):

| Variable | Description |
|---|---|
| `INGEST_EMAILS` | Comma-separated emails allowed to trigger ingest (empty = nobody) |
| `INGEST_QUEUE` | Absolute path to the queue file, e.g. `/home/alain/repos/youtube-abo-summarizer/data/ingest_queue.txt` |

`GET /api/whoami` returns `can_ingest: true` when the logged-in user is in `INGEST_EMAILS` and `INGEST_QUEUE` is configured. The export UI shows an "Ingest" button in the sync bar only when `can_ingest` is true.

Schedule `ingest_worker.sh` to run frequently (e.g. every minute). Edit the `PYTHON` variable at the top of the script to point to the virtualenv interpreter that has the project dependencies installed:

```
* * * * * /home/alain/repos/youtube-abo-summarizer/ingest_worker.sh
```

The worker processes each queued ID by running `collect.py --video <id>` and logs output to `data/ingest_worker.log`.

### Production deployment

`python sync_server.py` is for development only. In production: **Gunicorn + systemd + Nginx**.

- Gunicorn: `gunicorn --workers 2 --bind 127.0.0.1:5000 sync_server:app`
- Nginx terminates TLS and proxies to `127.0.0.1:5000`
- Add `ProxyFix` so Flask sees the real client IP (needed for the rate limiter):
  ```python
  from werkzeug.middleware.proxy_fix import ProxyFix
  app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
  ```
- See README for full systemd unit and Nginx config.

## All-in-one mode (legacy)

`summarize.py` still works as before — it fetches, summarizes, and renders in a single pass without touching `data/`. Useful for one-off runs or testing.

```bash
python summarize.py --auth [--hours 24] [--output summary.html] [--skip-empty] [--lang de|en]
python summarize.py UC123abc UC456def [--hours 24]
python summarize.py --file channels.txt [--hours 24]
```

## Send mail standalone

```bash
python send_mail.py "Subject" recipient@example.com summary_2026-02-23.html
```

## Architecture

| File | Role |
|---|---|
| `collect.py` | Collect-phase CLI: resolves channels, fetches videos/transcripts/summaries, writes to `data/` |
| `report.py` | Report-phase CLI: reads `data/`, renders HTML, optional SMTP send |
| `export.py` | Export CLI: renders a self-contained HTML archive with client-side search, channel/tag/read/bookmark filters, sort, and pagination; `--thumbnail` for static images, `--sync-url` to embed the sync server, `--show-model` for LLM badge |
| `repair.py` | Repair CLI: re-fetches missing transcripts and re-summarizes missing/broken summaries |
| `recover_from_export.py` | Restore store entries from a previously exported HTML file; inserts missing DB rows and summary files; leaves existing entries untouched; supports `--dry-run` |
| `store.py` | SQLite + file store: `data/videos.db` (metadata, including `tags TEXT` column storing JSON array), `data/transcripts/<id>.txt`, `data/summaries/<id>.html` |
| `summarize.py` | Legacy all-in-one CLI (fetch + render in one pass, no store involvement) |
| `youtube_client.py` | YouTube Data API v3 wrapper (auth, subscriptions, video search, channel resolution) |
| `transcripts.py` | `youtube-transcript-api` wrapper; language priority via `TRANSCRIPT_LANGS` (default: de,en); handles ip_blocked / rate_limited / country_blocked errors; on `ip_blocked` retries once via the configured proxy; `VideoUnplayable` is only classified as `country_blocked` when the reason mentions "country"/"region" — on `country_blocked`, retries once with a country-pinned Webshare proxy (`PROXY_FALLBACK_COUNTRY`, default: DE) if `WEBSHARE_PROXY_URL` is set; other `VideoUnplayable` causes fall to `unavailable` (retryable); `requests.exceptions.ProxyError` and `ConnectionError` are caught and mapped to `unavailable`; logs proxy configuration on startup |
| `openrouter.py` | LLM client (OpenRouter by default, or any OpenAI-compatible endpoint); summary language via `SUMMARY_LANG`; structured prompt enforces chronological sections scaled to video length, written as flowing prose (`<p>`) with bullets only for genuine enumerations, timestamp links placed inline after each relevant sentence; strips markdown fences from responses; extracts 3–7 English topic tags from the `<!-- tags: ... -->` comment appended by the model; returns `(summary_html, tags_list)` tuple; `max_tokens=16384` |
| `renderer.py` | Jinja2 renderer; writes the final HTML file; accepts `lang=` kwarg; sanitizes summaries at render time via `_sanitize_summary()` — strips any trailing incomplete HTML tag to guard against LLM output truncated mid-tag (which would cause the browser to consume subsequent cards as an attribute value) |
| `i18n.py` | UI string dicts for `de` (default) and `en`; `get_strings(lang)` and `resolve_lang(lang)` helpers |
| `template.html.j2` | Self-contained HTML template with embedded dark-theme CSS; read/bookmark buttons on each card, state persisted in browser cookies (365 days); strings from `i18n.py` via Jinja2 `{{ t.xxx }}` |
| `export.html.j2` | Export template: dark-theme CSS, controls bar, JS-rendered cards, search/date-filter/channel-filter/tag-filter/read-filter/bookmark-filter/sort/pagination; each filter and sort control has a visible label (`ctrl-label`); date filter accepts a "published after" date and filters client-side via ISO string comparison; tag chips on cards are clickable and toggle the tag filter; channel name in card meta is clickable and toggles the channel filter (`setChannelFilter()`); read/bookmark and language (`yt_lang`) state persisted in browser cookies; language selector in page header with flag emoji (🇩🇪/🇬🇧), priority: cookie → browser language → embedded default; sync bar shows "Ingest" button when `can_ingest` is true |
| `state.py` | Reads/writes `last_run.json` (channel_id → last checked ISO timestamp) |
| `send_mail.py` | Standalone script; sends an HTML file as an email via SMTP_SSL |
| `sync-server/sync_server.py` | Standalone Flask sync service: magic-link auth (supports STARTTLS port 587 and SSL port 465), per-user read/bookmark state in SQLite, last-write-wins merge; `POST /api/ingest` appends video ID to `INGEST_QUEUE` file and returns 202; `/api/whoami` returns `can_ingest` flag |
| `ingest_worker.sh` | Cron script that drains `INGEST_QUEUE` by running `collect.py --video <id>` for each entry; logs to `data/ingest_worker.log`; schedule every minute |

## Credentials and Sensitive Files

- `client_secrets.json` — Google OAuth credentials (never commit)
- `token.pickle` — cached OAuth token (never commit)
- `.env` — API keys and SMTP credentials (never commit)
- `last_run.json` — auto-generated state file (gitignored)
- `data/` — auto-generated store directory (gitignored): `videos.db`, `transcripts/`, `summaries/`

## Configuration (`.env`)

### LLM backend

Two sets of variables control which LLM is used. `LLM_*` takes precedence over
`OPENROUTER_*` when both are set.

| Variable | Precedence | Default | Notes |
|---|---|---|---|
| `LLM_MODEL` | 1st | — | Overrides `OPENROUTER_MODEL` |
| `OPENROUTER_MODEL` | 2nd | `gpt-oss-20b` | Used when `LLM_MODEL` is unset |
| `LLM_BASE_URL` | 1st | — | Overrides the hardcoded OpenRouter URL |
| `LLM_API_KEY` | 1st | — | Overrides `OPENROUTER_API_KEY` |
| `OPENROUTER_API_KEY` | 2nd | — | Required when using OpenRouter |

For local Ollama set `LLM_BASE_URL` + `LLM_MODEL`; no API key is needed (a
dummy is supplied automatically). For OpenRouter set `OPENROUTER_API_KEY` +
`OPENROUTER_MODEL` and leave the `LLM_*` vars unset.

```
# ── OpenRouter (default) ──────────────────────────────────────────────────────
OPENROUTER_API_KEY=...
OPENROUTER_MODEL=openai/gpt-oss-120b   # any OpenRouter model ID

# ── Local Ollama (alternative) ────────────────────────────────────────────────
# LLM_* variables take precedence over OPENROUTER_* when both are set.
# LLM_API_KEY is optional; Ollama needs no key (a dummy is used automatically).
# LLM_BASE_URL=http://localhost:11434/v1
# LLM_MODEL=llama3.2:latest

# ── Optional: summary output language ────────────────────────────────────────
# Any natural language name the model understands (default: German).
# SUMMARY_LANG=German

# ── Optional: transcript language preference ──────────────────────────────────
# Comma-separated BCP-47 language codes in priority order (default: de,en).
# Falls back to any available language if none match.
# TRANSCRIPT_LANGS=de,en

# ── Optional: residential proxy for transcript fetching ───────────────────────
# Format: http://USERNAME:PASSWORD@host:port
WEBSHARE_PROXY_URL=
# Country code for geo-block retry via Webshare country-pinning (default: DE).
# Appended to the Webshare username, e.g. DE, US, GB.
# PROXY_FALLBACK_COUNTRY=DE

# ── Required only for send_mail.py / report.py --send-to ──────────────────────
SMTP_HOST=mail.example.com
SMTP_PORT=587                  # defaults to 587
SMTP_USER=user@example.com
SMTP_PASS=your_smtp_password
SMTP_FROM=user@example.com     # optional, defaults to SMTP_USER
```
