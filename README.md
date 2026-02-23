# youtube-abo-summarizer

Fetches new videos from your YouTube subscriptions (or an explicit channel list), retrieves their transcripts, summarizes them with an LLM via [OpenRouter](https://openrouter.ai), and renders a self-contained HTML report — optionally delivered by email.

## Features

- **Two source modes**: OAuth-based subscription list or explicit channel IDs/handles
- **Incremental runs**: Tracks the last-checked timestamp per channel in `last_run.json`; only fetches videos published since the last run
- **Transcript fetching**: Prefers German, falls back to English, then any available language
- **AI summarization**: Generates structured HTML summaries (overview, key points, takeaway) with clickable timestamp links into the video
- **Dark-theme HTML report**: Self-contained, mobile-responsive, with per-channel sections and video cards
- **Email delivery**: Sends the report via SMTP
- **Cron-ready**: Includes shell scripts for daily, 6-hour, and 12-hour scheduled runs

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

## Usage

```bash
# Use your YouTube OAuth subscriptions
python summarize.py --auth

# Look back N hours instead of using persisted state
python summarize.py --auth --hours 12

# Explicit channels (IDs, handles, or URLs)
python summarize.py UC123abc @SomeHandle https://youtube.com/c/SomeChannel

# Read channels from a file (one per line)
python summarize.py --file channels.txt

# Custom output file; omit channels with no new videos
python summarize.py --auth --output report.html --skip-empty
```

**State tracking**: Without `--hours`, each channel's last-run timestamp is read from `last_run.json` and updated after the run. `--hours N` overrides this and does **not** update the state, so it is safe for ad-hoc or re-processing runs.

## Email delivery

```bash
python3 send_mail.py "YouTube Summary 2026-02-23" recipient@example.com summary_2026-02-23.html
```

## Scheduled runs (cron)

The included shell scripts activate the virtual environment, run the summarizer, send the email, and clean up reports older than 7 days:

| Script | Lookback | State updated |
|---|---|---|
| `run_daily.sh` | Since last run | Yes |
| `run_12hours.sh` | 12 hours | No |
| `run_6hours.sh` | 6 hours | No |

Example crontab entries:

```
0  7 * * *    /path/to/run_daily.sh   >> /path/to/cron.log 2>&1
0  */6 * * *  /path/to/run_6hours.sh  >> /path/to/cron.log 2>&1
```

## Architecture

| File | Role |
|---|---|
| `summarize.py` | CLI entry point; orchestrates the full pipeline |
| `youtube_client.py` | YouTube Data API v3 wrapper (OAuth, subscriptions, video search) |
| `transcripts.py` | `youtube-transcript-api` wrapper; language selection and timestamp insertion |
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

### No deduplication
If the same video appears in multiple channels (e.g., a collab), it will be summarized once per channel it appears in.

### No transcript caching
Transcripts are fetched fresh on every run. Re-running the tool for the same time window (e.g., with `--hours`) will re-fetch and re-summarize all videos.

### OpenRouter cost and availability
Summarization costs money per token. There is no local fallback if the OpenRouter API is unavailable or the key is exhausted.

## Sensitive files (never commit)

| File | Contents |
|---|---|
| `client_secrets.json` | Google OAuth app credentials |
| `token.pickle` | Cached OAuth token |
| `.env` | API keys and SMTP credentials |
| `last_run.json` | Per-channel run state |

All of the above are listed in `.gitignore`.

## License

MIT
