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
- `--prune-days N` removes store entries older than N days (default: 7).

### Report phase — run on digest schedule (e.g. every 6 h or daily)

```bash
python report.py [--hours 24] [--output summary.html] [--skip-empty] [--send-to EMAIL]
```

- Reads `data/videos.db`, includes videos published within the last `--hours` hours.
- `--skip-empty` omits channels with no videos in the window.
- `--send-to EMAIL` sends the rendered HTML via SMTP after writing the file.
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
- `--force-summarize` re-runs the LLM even if a summary already exists.
- `--video ID [ID ...]` restricts all operations to the specified video IDs.
- `--dry-run` prints what would be done without writing anything.

## Export archive

`export.py` renders stored videos into a self-contained HTML file for offline browsing (client-side search, sort, pagination).

```bash
python export.py                        # last 7 days (default)
python export.py --all                  # all videos in store
python export.py --hours 48             # custom time window
python export.py --all --output full_archive.html
```

`--hours` and `--all` are mutually exclusive. Default output filename: `export_YYYY-MM-DD_HH-MM.html`.

## All-in-one mode (legacy)

`summarize.py` still works as before — it fetches, summarizes, and renders in a single pass without touching `data/`. Useful for one-off runs or testing.

```bash
python summarize.py --auth [--hours 24] [--output summary.html] [--skip-empty]
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
| `export.py` | Export CLI: renders a self-contained HTML archive with client-side search, sort, and pagination |
| `repair.py` | Repair CLI: re-fetches missing transcripts and re-summarizes missing/broken summaries |
| `store.py` | SQLite + file store: `data/videos.db` (metadata), `data/transcripts/<id>.txt`, `data/summaries/<id>.html` |
| `summarize.py` | Legacy all-in-one CLI (fetch + render in one pass, no store involvement) |
| `youtube_client.py` | YouTube Data API v3 wrapper (auth, subscriptions, video search, channel resolution) |
| `transcripts.py` | `youtube-transcript-api` wrapper; prefers DE then EN; handles ip_blocked / rate_limited / country_blocked errors |
| `openrouter.py` | LLM client (OpenRouter by default, or any OpenAI-compatible endpoint); strips markdown fences from responses |
| `renderer.py` | Jinja2 renderer; writes the final HTML file |
| `template.html.j2` | Self-contained HTML template with embedded dark-theme CSS |
| `export.html.j2` | Export template: dark-theme CSS, controls bar, JS-rendered cards, search/channel-filter/sort/pagination |
| `state.py` | Reads/writes `last_run.json` (channel_id → last checked ISO timestamp) |
| `send_mail.py` | Standalone script; sends an HTML file as an email via SMTP_SSL |

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
# LLM_MODEL=gemma3:27b

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

# ── Required only for send_mail.py / report.py --send-to ──────────────────────
SMTP_HOST=mail.example.com
SMTP_PORT=587                  # defaults to 587
SMTP_USER=user@example.com
SMTP_PASS=your_smtp_password
SMTP_FROM=user@example.com     # optional, defaults to SMTP_USER
```
