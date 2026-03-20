# youtube-abo-summarizer

Fetches new videos from your YouTube subscriptions (or an explicit channel list), retrieves their transcripts, summarizes them with an LLM, and renders a self-contained HTML report — optionally delivered by email. Supports [OpenRouter](https://openrouter.ai) (default) and local [Ollama](https://ollama.com) instances (or any OpenAI-compatible endpoint).

![youtube-abo-summarizer](youtube-abo-summarizer.png)

## Features

- **Two-phase pipeline**: Separate collection (fetch + summarize) from reporting (render + send), so transcripts and LLM calls only happen when new videos arrive — not on every digest
- **Two source modes**: OAuth-based subscription list or explicit channel IDs/handles
- **Incremental runs**: Tracks the last-checked timestamp per channel in `last_run.json`; only fetches videos published since the last run
- **Transcript fetching**: Configurable language priority (`TRANSCRIPT_LANGS`, default: `de,en`); falls back to any available language
- **AI summarization**: Generates structured HTML summaries written as flowing prose (bullet points only for genuine enumerations); sections are in chronological order and scaled to video length (2–3 sections for short videos, up to 6–10 for long ones); each section contains clickable timestamp links placed inline after the relevant sentence; output language configurable via `SUMMARY_LANG` (default: German). The same LLM call also extracts 3–7 concise English topic tags, stored alongside the summary
- **Transcript and summary storage**: Transcripts and summaries are cached to `data/`. On subsequent runs, videos that already have both a transcript and a summary are skipped entirely — no redundant YouTube or LLM calls. If only the transcript is missing it is fetched; if only the summary is missing the stored transcript is re-used and only the LLM call is made
- **Dark-theme HTML report**: Self-contained, mobile-responsive, with per-channel sections and video cards
- **Browsable archive export**: Single portable HTML file with client-side search, date filter (published after), channel filter, tag filter, read/bookmark filter, sort, and pagination — works fully offline; each filter and sort control has a visible label; tag chips and channel names on cards are clickable and toggle their respective filters directly
- **Read/bookmark tracking**: Each video card has read and bookmark buttons; state is persisted in browser cookies (365 days) and shared between the report and export views
- **Multi-language UI**: Report and export templates support German (`de`, default) and English (`en`); select via `--lang` on the CLI; the export additionally shows an in-page language selector that persists the choice in a cookie and falls back to the browser's preferred language
- **Repair tool**: Re-fetches missing transcripts and re-summarizes missing or broken summaries; supports targeting specific videos
- **Email delivery**: Sends the report via SMTP
- **Cron-ready**: Includes shell scripts for frequent collection and daily/6-hour/12-hour digest delivery

## Requirements

- Python 3.8+
- A [Google Cloud project](https://console.cloud.google.com/) with the YouTube Data API v3 enabled and OAuth 2.0 credentials — required for fetching your subscription list via `--auth`; explicit channel IDs, handles, or a channel file can be used as an alternative
- An LLM backend: [OpenRouter](https://openrouter.ai) API key (default) **or** a local [Ollama](https://ollama.com) instance
- An SMTP server for email delivery (optional)

## Setup

```bash
git clone https://github.com/AlainBla/youtube-abo-summarizer.git
cd youtube-abo-summarizer

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env and fill in your credentials
```

Place your Google OAuth credentials in `client_secrets.json` (downloaded from the Google Cloud Console).

### `.env` variables

**LLM backend** — `LLM_*` variables take precedence over `OPENROUTER_*` when both are set.

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | OpenRouter only | Your OpenRouter API key |
| `OPENROUTER_MODEL` | No | Model ID for OpenRouter (default: `gpt-oss-20b`) |
| `LLM_BASE_URL` | Ollama / custom | API base URL, e.g. `http://localhost:11434/v1` |
| `LLM_MODEL` | No | Overrides `OPENROUTER_MODEL` when set |
| `LLM_API_KEY` | No | Overrides `OPENROUTER_API_KEY` when set; omit for Ollama |
| `SMTP_HOST` | For email | SMTP server hostname |
| `SMTP_PORT` | For email | `465` (SSL) or `587` (STARTTLS) |
| `SMTP_USER` | For email | SMTP username |
| `SMTP_PASS` | For email | SMTP password |
| `SMTP_FROM` | No | Sender address (defaults to `SMTP_USER`) |
| `SUMMARY_LANG` | No | Language for LLM-generated summaries (default: `German`); any name the model understands, e.g. `English` |
| `TRANSCRIPT_LANGS` | No | Comma-separated transcript language priority list (default: `de,en`); falls back to any available language |
| `WEBSHARE_PROXY_URL` | No | Residential proxy URL for transcript fetching |
| `PROXY_FALLBACK_COUNTRY` | No | Country code used for the geo-block retry (default: `DE`); appended to the Webshare username, e.g. `US`, `GB` |

**Sync server** (`sync-server/.env`) — in addition to the SMTP vars above:

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | Yes | Long random string for signing magic-link tokens |
| `BASE_URL` | Yes | Public URL of the sync server, e.g. `https://sync.example.com` |
| `ALLOWED_EMAILS` | No | Comma-separated allowlist for magic-link login (empty = any email) |
| `INGEST_EMAILS` | No | Comma-separated emails allowed to trigger on-demand video ingest (empty = nobody) |
| `INGEST_QUEUE` | No | Absolute path to the ingest queue file (required for ingest), e.g. `/path/to/data/ingest_queue.txt` |
| `PORT` | No | Port to listen on (default: `5000`) |

## Usage — two-phase pipeline (recommended)

The pipeline is split into a **collect** phase and a **report** phase. Run collection frequently so new videos are picked up quickly; run report on whatever digest schedule you want. Transcript fetching and LLM summarization only happen during collection.

### 1. Collect — fetch new videos, transcripts, and summaries

```bash
# Use your YouTube OAuth subscriptions
python collect.py --auth

# Look back N hours instead of using persisted state
python collect.py --auth --hours 2

# Explicit channels (IDs, handles, or URLs)
python collect.py UC123abc @SomeHandle

# Read channels from a file (one per line)
python collect.py --file channels.txt
```

Results are written to `data/` (SQLite metadata + individual transcript and summary files). Videos already in the store are handled incrementally: if both transcript and summary exist they are skipped entirely; if only one is missing, only the missing piece is fetched or generated. Pass `--prune-days N` to remove entries older than N days; by default nothing is pruned.

### 2. Report — render and optionally send a digest

```bash
# Render a 24-hour digest (default)
python report.py --output summary.html

# Custom time window
python report.py --hours 6 --output summary_6h.html

# Skip channels with no new videos and send via email
python report.py --hours 24 --skip-empty --send-to you@example.com

# Show the LLM model badge on each card
python report.py --show-model

# Render the report in English
python report.py --lang en
```

No YouTube API calls or LLM calls happen here — it reads only from `data/`.

## Usage — export archive

`export.py` renders all (or a subset of) stored videos into a single self-contained HTML file for offline browsing. It includes client-side search across titles and summaries, a "published after" date filter, channel/tag/read/bookmark filter dropdowns (each with a descriptive label), sorting by date/channel/title, and pagination (20 items per page). Tag chips on each video card are clickable and set the tag filter directly. Read and bookmark state is tracked via browser cookies and persists across sessions. No server required.

A language selector in the page header lets you switch between German and English at any time. The choice is stored in a cookie (`yt_lang`) and automatically applied when you open any export file. If no cookie is set, the browser's preferred language is used; if that language is not supported, the embedded default (set via `--lang`) is used.

```bash
# Last 7 days (default)
python export.py

# All videos in the store
python export.py --all

# Custom time window
python export.py --hours 48

# Custom output filename
python export.py --all --output full_archive.html

# Show the LLM model badge on each card
python export.py --show-model

# Embed English as the fallback language (overridden by cookie/browser)
python export.py --lang en

# Static thumbnails instead of embedded YouTube players
python export.py --thumbnail

# Embed sync server URL (enables cross-browser read/bookmark sync)
python export.py --all --sync-url https://sync.example.com --output archive.html
```

`--hours` and `--all` are mutually exclusive. The default output filename is `export_YYYY-MM-DD_HH-MM.html`.
The LLM model badge is hidden by default; use `--show-model` to display it.

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

`POST /api/ingest` lets authorised users queue a video for fetching and summarisation without waiting for the next scheduled collection run. The endpoint appends the video ID to a queue file and returns 202 immediately; processing happens asynchronously via a cron job.

Configure in `sync-server/.env` (or the systemd unit):

| Variable | Description |
|---|---|
| `INGEST_EMAILS` | Comma-separated emails allowed to trigger ingest (empty = nobody) |
| `INGEST_QUEUE` | Absolute path to the queue file, e.g. `/path/to/data/ingest_queue.txt` |

`GET /api/whoami` returns `can_ingest: true` when the logged-in user is in `INGEST_EMAILS` and `INGEST_QUEUE` is set. The export page shows an "Ingest" button in the sync bar only when `can_ingest` is true.

Schedule `ingest_worker.sh` to run every minute. Edit the `PYTHON` variable at the top of the script to point to the virtualenv interpreter that has the project dependencies:

```
* * * * * /path/to/youtube-abo-summarizer/ingest_worker.sh
```

The worker drains the queue by running `collect.py --video <id>` for each entry and logs output to `data/ingest_worker.log`.

### Production deployment

`python sync_server.py` starts Flask's development server — not suitable for production. Use **Gunicorn + systemd + Nginx**:

**1. Install Gunicorn**

```bash
cd sync-server
pip install gunicorn
```

**2. systemd service** — `/etc/systemd/system/yt-sync.service`:

```ini
[Unit]
Description=YouTube Export Sync Server
After=network.target

[Service]
User=www-data
WorkingDirectory=/path/to/youtube-abo-summarizer/sync-server
EnvironmentFile=/path/to/youtube-abo-summarizer/sync-server/.env
ExecStart=/path/to/.venv/bin/gunicorn \
    --workers 2 \
    --bind 127.0.0.1:5000 \
    --access-logfile /var/log/yt-sync/access.log \
    --error-logfile /var/log/yt-sync/error.log \
    sync_server:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo mkdir -p /var/log/yt-sync && sudo chown www-data /var/log/yt-sync
sudo systemctl enable --now yt-sync
```

**3. Nginx reverse proxy** — `/etc/nginx/sites-available/yt-sync`:

```nginx
server {
    listen 443 ssl;
    server_name sync.example.com;

    ssl_certificate     /etc/letsencrypt/live/sync.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sync.example.com/privkey.pem;

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name sync.example.com;
    return 301 https://$host$request_uri;
}
```

```bash
sudo ln -s /etc/nginx/sites-available/yt-sync /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
# TLS certificate:
sudo certbot --nginx -d sync.example.com
```

**4. ProxyFix** — add near the top of `sync_server.py` so Flask sees the real client IP (required for the rate limiter to work correctly behind Nginx):

```python
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
```

After code changes: `sudo systemctl restart yt-sync`

## Usage — repair

`repair.py` scans the store and fixes missing or broken transcripts and summaries. The most common use case is re-summarizing specific videos after finding bad model output.

```bash
# Re-summarize two specific videos
python repair.py --force-summarize --video abc123xyz def456uvw

# Preview what would be repaired without making any changes
python repair.py --dry-run

# Repair all missing transcripts and summaries
python repair.py

# Re-summarize everything (e.g. after switching to a better model)
python repair.py --force-summarize
```

| Flag | Description |
|---|---|
| `--video ID [ID ...]` | Restrict to specific video IDs |
| `--force-summarize` | Re-summarize even if a summary already exists; also re-generates tags |
| `--dry-run` | Print what would be done without writing anything |

`country_blocked` videos are never re-fetched (permanent restriction).

To backfill tags on videos summarized before tag support was added, run `python repair.py --force-summarize`.

## Usage — all-in-one mode (for ad-hoc runs)

`summarize.py` fetches, summarizes, and renders in a single pass without using the store. Useful for one-off runs or testing.

```bash
python summarize.py --auth
python summarize.py --auth --hours 12
python summarize.py UC123abc @SomeHandle
python summarize.py --file channels.txt --output report.html --skip-empty --lang en
```

## Email delivery (standalone)

```bash
python3 send_mail.py "YouTube Summary 2026-02-23" recipient@example.com summary_2026-02-23.html
```

## Scheduled runs (cron)

Recommended crontab setup:

```
# Collect every 30 minutes
*/30 * * * *  /path/to/collect.sh >> /path/to/cron.log 2>&1

# Send a 6-hour digest
0 */6 * * *   /path/to/run_6hours.sh >> /path/to/cron.log 2>&1

# Send a daily digest at 07:00
0 7   * * *   /path/to/run_daily.sh  >> /path/to/cron.log 2>&1
```

| Script | Purpose |
|---|---|
| `collect.sh` | Runs `collect.py --auth`; schedule this frequently |
| `run_6hours.sh` | Renders and emails a 6-hour digest via `report.py` |
| `run_12hours.sh` | Renders and emails a 12-hour digest via `report.py` |
| `run_daily.sh` | Renders and emails a 24-hour digest via `report.py` |

Each report script activates the virtual environment, renders the HTML, sends the email, and cleans up HTML files older than 7 days.

## Architecture

| File | Role |
|---|---|
| `collect.py` | Collect-phase CLI: resolves channels, fetches videos/transcripts/summaries, writes to `data/` |
| `report.py` | Report-phase CLI: reads `data/`, renders HTML, optional SMTP send |
| `export.py` | Export CLI: renders a self-contained HTML archive with client-side search, channel/tag/read/bookmark filters, sort, and pagination |
| `repair.py` | Repair CLI: re-fetches missing transcripts and re-summarizes missing/broken summaries (also re-generates tags with `--force-summarize`) |
| `recover_from_export.py` | Restores store entries from a previously exported HTML file; inserts missing DB rows and summary files; leaves existing entries untouched; supports `--dry-run` |
| `store.py` | SQLite + file store: `data/videos.db` (metadata + tags as JSON array), `data/transcripts/<id>.txt`, `data/summaries/<id>.html` |
| `summarize.py` | All-in-one CLI: fetch + render in a single pass (no store involvement) |
| `youtube_client.py` | YouTube Data API v3 wrapper (OAuth, subscriptions, video search, channel resolution) |
| `transcripts.py` | `youtube-transcript-api` wrapper; language selection, timestamp formatting, error handling; on `ip_blocked` retries via proxy; on `country_blocked` retries with country-pinned proxy; `requests.exceptions.ProxyError` / `ConnectionError` caught and mapped to `unavailable`; logs proxy config on startup |
| `openrouter.py` | LLM client (OpenRouter by default, or any OpenAI-compatible endpoint); returns `(summary_html, tags)` tuple — structured HTML with chronological sections, proportional depth, and timestamp links, plus 3–7 English topic tags extracted from a `<!-- tags: ... -->` comment appended by the model; `max_tokens=16384` |
| `renderer.py` | Jinja2 renderer; writes the final HTML report; accepts `lang=` kwarg; sanitizes summaries at render time to strip any trailing incomplete HTML tag (guards against LLM output truncated mid-tag) |
| `i18n.py` | UI string dicts for `de` (default) and `en`; `get_strings()` and `resolve_lang()` helpers used by the renderer |
| `template.html.j2` | Self-contained dark-theme HTML report template; read/bookmark buttons with cookie-based state; all UI strings sourced from `i18n.py` via `{{ t.xxx }}` |
| `export.html.j2` | Export template: dark-theme CSS, controls bar, JS-rendered cards, search/date/channel/tag/read/bookmark filters (each with a visible label), sort, pagination; channel name on each card is clickable and toggles the channel filter; in-page language selector with flag emoji (cookie `yt_lang`, browser fallback); full `de`/`en` string set in the embedded `I18N` object; sync bar shows "Ingest" button when `can_ingest` is true |
| `state.py` | Reads/writes `last_run.json` (per-channel ISO timestamps) |
| `send_mail.py` | SMTP email sender |
| `sync-server/sync_server.py` | Standalone Flask sync service: magic-link auth (STARTTLS port 587 or SSL port 465), per-user read/bookmark state in SQLite, last-write-wins merge; `POST /api/ingest` appends video ID to `INGEST_QUEUE` and returns 202; `/api/whoami` returns `can_ingest` flag |
| `ingest_worker.sh` | Cron script that drains `INGEST_QUEUE` by calling `collect.py --video <id>` for each entry; logs to `data/ingest_worker.log` |

## Limitations

### YouTube API quota
The YouTube Data API has a daily quota of **10,000 units**. Fetching videos from many channels in a single run can exhaust this quickly. The tool stops gracefully when the quota is exceeded, but remaining channels are skipped for that run.

### Transcript availability
- Transcript languages are requested in the order defined by `TRANSCRIPT_LANGS` (default: `de,en`). Videos without a matching transcript fall back to any available language; if no transcript exists at all a "no transcript" notice is shown.
- **Live streams**, **Shorts**, and some copyright-claimed videos may have no transcript.
- **Region-locked videos** are detected via `VideoUnplayable`. Only videos whose unplayable reason explicitly mentions "country" or "region" are treated as geo-blocked. When `WEBSHARE_PROXY_URL` is set, the tool automatically retries such videos once using a country-pinned proxy (default: `DE`, configurable via `PROXY_FALLBACK_COUNTRY`). The video is only marked `country_blocked` (permanent skip) if the retry also fails; other `VideoUnplayable` causes — including future live events — are marked `unavailable` and retried on the next run.

### IP blocking
YouTube actively blocks transcript requests from **datacenter IP addresses**. If the tool runs on a server or VPS, most transcript fetches will be blocked. Symptoms: the HTML report shows "IP blocked" notices for the majority of videos.

**Mitigation**: Set `WEBSHARE_PROXY_URL` in `.env` to route transcript requests through a residential proxy. The tool includes full support for [Webshare](https://webshare.io) proxies via `youtube-transcript-api`'s `GenericProxyConfig`. Geo-blocked videos are automatically retried via a country-pinned Webshare proxy (see `PROXY_FALLBACK_COUNTRY` above).

### LLM cost and availability
When using OpenRouter, summarization costs money per token and depends on API availability. As an alternative, point the tool at a local [Ollama](https://ollama.com) instance (free, offline) by setting `LLM_BASE_URL=http://localhost:11434/v1` and `LLM_MODEL=<model>` in `.env`.

## Sensitive files (never commit)

| File/Dir | Contents |
|---|---|
| `client_secrets.json` | Google OAuth app credentials |
| `token.pickle` | Cached OAuth token |
| `.env` | API keys and SMTP credentials |
| `last_run.json` | Per-channel run state |
| `data/` | SQLite database, transcripts, and summaries |

All of the above are listed in `.gitignore`.

## License

MIT
