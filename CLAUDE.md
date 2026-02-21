# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

Fetch new videos from YouTube channels (via OAuth subscriptions or an explicit list), pull their transcripts, summarize them with an LLM via OpenRouter, and render a single HTML report per run. Planned future feature: send the report via `mutt`.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in OPENROUTER_API_KEY
```

## Running

```bash
# Mode A — use YouTube OAuth subscriptions
python summarize.py --auth [--days 7] [--output summary.html]

# Mode B — explicit channels (IDs, handles, or URLs)
python summarize.py UC123abc UC456def [--days 7]
python summarize.py --file channels.txt [--days 7]
```

- Without `--days`, each channel's last-run timestamp is read from `last_run.json`; on first run it defaults to 7 days back.
- `--days N` overrides last-run state and does **not** update it.
- Default output filename: `summary_YYYY-MM-DD.html`.

## Architecture

| File | Role |
|---|---|
| `summarize.py` | CLI entry point; orchestrates the full pipeline |
| `youtube_client.py` | YouTube Data API v3 wrapper (auth, subscriptions, video search) |
| `transcripts.py` | `youtube-transcript-api` wrapper; prefers DE then EN |
| `openrouter.py` | OpenRouter client (OpenAI-compatible); returns HTML-fragment summaries |
| `renderer.py` | Jinja2 renderer; writes the final HTML file |
| `template.html.j2` | Self-contained HTML template with embedded dark-theme CSS |
| `state.py` | Reads/writes `last_run.json` (channel_id → last checked ISO timestamp) |

## Credentials and Sensitive Files

- `client_secrets.json` — Google OAuth credentials (never commit)
- `token.pickle` — cached OAuth token (never commit)
- `.env` — `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` (never commit)
- `last_run.json` — auto-generated state file (gitignored)

## Configuration (`.env`)

```
OPENROUTER_API_KEY=...
OPENROUTER_MODEL=gpt-oss-20b   # any OpenRouter model ID
```
