# youtube-abo-summarizer

Fetches new videos from your YouTube subscriptions (or an explicit channel list), retrieves their transcripts, summarizes them with an LLM via [OpenRouter](https://openrouter.ai), and renders a self-contained HTML report — optionally delivered by email.

## Features

- **Two-phase pipeline**: Separate collection (fetch + summarize) from reporting (render + send), so transcripts and LLM calls only happen when new videos arrive — not on every digest
- **Two source modes**: OAuth-based subscription list or explicit channel IDs/handles
- **Incremental runs**: Tracks the last-checked timestamp per channel in `last_run.json`; only fetches videos published since the last run
- **Transcript fetching**: Prefers German, falls back to English, then any available language
- **AI summarization**: Generates structured HTML summaries (overview, key points, takeaway) with clickable timestamp links into the video
- **Transcript and summary storage**: Transcripts and summaries are cached to `data/`. On subsequent runs, videos that already have both a transcript and a summary are skipped entirely — no redundant YouTube or LLM calls. If only the transcript is missing it is fetched; if only the summary is missing the stored transcript is re-used and only the LLM call is made
- **Dark-theme HTML report**: Self-contained, mobile-responsive, with per-channel sections and video cards
- **Email delivery**: Sends the report via SMTP
- **Cron-ready**: Includes shell scripts for frequent collection and daily/6-hour/12-hour digest delivery

## Requirements

- Python 3.8+
- A [Google Cloud project](https://console.cloud.google.com/) with the YouTube Data API v3 enabled and OAuth 2.0 credentials
- An [OpenRouter](https://openrouter.ai) API key
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

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | Yes | Your OpenRouter API key |
| `OPENROUTER_MODEL` | No | Model ID (default: `gpt-oss-20b`) |
| `SMTP_HOST` | For email | SMTP server hostname |
| `SMTP_PORT` | For email | `465` (SSL) or `587` (STARTTLS) |
| `SMTP_USER` | For email | SMTP username |
| `SMTP_PASS` | For email | SMTP password |
| `SMTP_FROM` | No | Sender address (defaults to `SMTP_USER`) |
| `WEBSHARE_PROXY_URL` | No | Residential proxy URL for transcript fetching |

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

Results are written to `data/` (SQLite metadata + individual transcript and summary files). Videos already in the store are handled incrementally: if both transcript and summary exist they are skipped entirely; if only one is missing, only the missing piece is fetched or generated. Old entries are pruned after 7 days by default (`--prune-days N` to change).

### 2. Report — render and optionally send a digest

```bash
# Render a 24-hour digest (default)
python report.py --output summary.html

# Custom time window
python report.py --hours 6 --output summary_6h.html

# Skip channels with no new videos and send via email
python report.py --hours 24 --skip-empty --send-to you@example.com
```

No YouTube API calls or LLM calls happen here — it reads only from `data/`.

## Usage — all-in-one mode (for ad-hoc runs)

`summarize.py` fetches, summarizes, and renders in a single pass without using the store. Useful for one-off runs or testing.

```bash
python summarize.py --auth
python summarize.py --auth --hours 12
python summarize.py UC123abc @SomeHandle
python summarize.py --file channels.txt --output report.html --skip-empty
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
| `store.py` | SQLite + file store: `data/videos.db` (metadata), `data/transcripts/<id>.txt`, `data/summaries/<id>.html` |
| `summarize.py` | All-in-one CLI: fetch + render in a single pass (no store involvement) |
| `youtube_client.py` | YouTube Data API v3 wrapper (OAuth, subscriptions, video search, channel resolution) |
| `transcripts.py` | `youtube-transcript-api` wrapper; language selection, timestamp formatting, error handling |
| `openrouter.py` | OpenRouter client; returns HTML-fragment summaries with timestamp links |
| `renderer.py` | Jinja2 renderer; writes the final HTML report |
| `template.html.j2` | Self-contained dark-theme HTML template |
| `state.py` | Reads/writes `last_run.json` (per-channel ISO timestamps) |
| `send_mail.py` | SMTP email sender |

## Limitations

### YouTube API quota
The YouTube Data API has a daily quota of **10,000 units**. Fetching videos from many channels in a single run can exhaust this quickly. The tool stops gracefully when the quota is exceeded, but remaining channels are skipped for that run.

### Transcript availability
- Only **German and English** transcripts are requested by preference. Videos in other languages may fall back to an auto-generated transcript or show a "no transcript" notice.
- **Live streams**, **Shorts**, and some copyright-claimed videos may have no transcript.
- **Region-locked videos** (VideoUnplayable) are detected and skipped gracefully.

### IP blocking
YouTube actively blocks transcript requests from **datacenter IP addresses**. If the tool runs on a server or VPS, most transcript fetches will be blocked. Symptoms: the HTML report shows "IP blocked" notices for the majority of videos.

**Mitigation**: Set `WEBSHARE_PROXY_URL` in `.env` to route transcript requests through a residential proxy. The tool includes full support for [Webshare](https://webshare.io) proxies via `youtube-transcript-api`'s `GenericProxyConfig`.

### OpenRouter cost and availability
Summarization costs money per token. There is no local fallback if the OpenRouter API is unavailable or the key is exhausted.

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
