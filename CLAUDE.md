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

- `--no-proxy` ignores `WEBSHARE_PROXY_URL` and fetches transcripts via a direct connection.

- Fetches new videos, transcripts, and summaries; persists results to `data/`.
- Videos already in the store are handled incrementally: skipped entirely if both transcript and summary exist; otherwise only the missing piece is fetched or generated.
- Without `--hours`, uses each channel's last-run timestamp from `last_run.json`; defaults to 24 h on first run.
- `--hours N` overrides last-run state and does **not** update it.
- Exit codes: `10` (`EXIT_NEW_VIDEOS`) when the run stored at least one new video, `0` when it ran fine but added nothing, non-zero otherwise. `collect.sh` gates the export on `10` — chaining the export with a plain `&&` instead would re-export on every run, flipping the archive's `generated_at` and leaving the update banner permanently on. `ingest_worker.sh` counts `10` as success, or it would re-queue every video it successfully ingested.
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
| `collect.sh` | Runs `collect.py --auth --hours 4`; on exit code 10 (new videos stored) immediately re-exports the archive to `$EXPORT_OUTPUT` (default `yt.html`, sync URL from `$SYNC_URL`); schedule frequently (e.g. `*/30 * * * *`) |
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

# Repair broken timestamp links in stored summaries (no LLM calls)
python repair.py --fix-links --dry-run
python repair.py --fix-links
```

- Missing transcripts are re-fetched (skips `country_blocked` videos permanently).
- `--force-summarize` re-runs the LLM even if a summary already exists; also re-generates and stores tags.
- `--fix-links` rewrites stored summaries through `openrouter._fix_timestamp_links()` — recomputes each `t=` offset from its visible `MM:SS` label, closes anchors the model left open, adds a missing `ts-link` class. Purely textual, no API calls, and it only touches files that actually change. Back up `data/summaries/` first: the rewrite is in place and `data/` is gitignored.
- `--video ID,ID,...` restricts all operations to the specified video IDs (comma-separated).
- `--dry-run` prints what would be done without writing anything.
- To backfill tags on existing videos (after upgrading from a version without tag support): `python repair.py --force-summarize`

## Export archive

`export.py` renders stored videos into a self-contained HTML file for offline browsing (client-side search, channel/tag/read/bookmark filters, sort — publish date, date added, channel, title —, pagination; read and bookmark state persisted in browser `localStorage`).

The "Zuletzt hinzugefügt" / "Recently added" sort (`added-desc`) orders by the store's `collected_at` column, i.e. when the video entered `data/videos.db` — so a video queued through the export's Ingest button sorts to the top even when it was published long ago. `collect.py` stamps one `collected_at` per run, so a whole run shares one value; ties fall back to publish date descending. Videos missing `collected_at` (archives exported before the field was embedded) fall back to `published_at`.

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
python export.py --no-compress          # embed data uncompressed (JSON.parse) for old browsers
```

`--hours` and `--all` are mutually exclusive. Default output filename: `export_YYYY-MM-DD_HH-MM.html`.
`--show-model` shows the LLM model name badge on each card (hidden by default).

### Single-video deep link (`?v=ID`)

`yt.html?v=VIDEO_ID` shows exactly that one video. `readVideoParam()` is evaluated at script-parse time (before the data blob is parsed): `enterSingleVideoView()` drops the pre-rendered cards, hides the controls bar and pagination, shows `#single-bar` and writes a localized loading note into the grid. Once the index is decoded, `applyFiltersAndSort()` short-circuits to the single match and `renderPage()` pulls the summary chunk on demand — so the video may sit anywhere in the archive. Three states are kept apart deliberately: loading (from first paint), found, and `singleNotFound` — the last only ever decided against the fully decoded index, never against the pre-rendered page. `clearSingleVideo()` removes just the `v` parameter via `history.replaceState` and re-renders; no reload. Every card has a share button (`shareVideo()`, clipboard with a `prompt()` fallback for `file://`/non-secure contexts) that copies that URL. The button exists in **both** the Jinja macro `card(v)` (string `t.share_btn` from `i18n.py`) and the JS `buildCard()` (`s.shareBtn` from the JS `I18N`) — the two must stay markup-identical and carry the same label.

### Update banner (manifest sidecar)

Every export also writes `<output>.meta.json` next to the HTML — e.g. `full_archive.html.meta.json` — holding `generated_at` (UTC, seconds), `video_count`, `newest_id` and `newest_published_at` (`renderer._export_manifest()`). The same object is embedded in the page as `const MANIFEST`. When the archive is served over http(s), the page polls the sidecar every 5 minutes (`UPDATE_POLL_MS`, `cache: 'no-store'`, skipped while the tab is hidden, plus one check on `visibilitychange` → visible) and compares `generated_at`. On a difference a sticky banner appears: "N neue Videos verfügbar" when `video_count` grew, otherwise "Archiv aktualisiert", with a reload button and a dismiss "×" that silences that one `generated_at` but not the next. `file://` archives never poll. The sidecar is written **after** the HTML, so it never announces videos the served archive does not contain yet; deploy scripts that copy the export must copy the sidecar too (`ingest_worker.sh` re-exports in place, so nothing to do there).

The first `EXPORT_FIRST_PAGE` (20) cards are pre-rendered as static HTML directly into the document (by a Jinja macro that must stay markup-identical to the JS `buildCard()`, see the `export.html.j2` row below), so the page paints before any embedded data blob is decoded. Embedded data itself is split by `_split_export_data()` into a summary-free `index` (metadata driving filter/sort/dropdowns; a lowercased `search_text` field is computed lazily in the browser on first search, not precomputed server-side) and a series of summary "chunks" (`EXPORT_CHUNK_SIZE` = 50 videos each, in the same newest-first order as the index). The index and each chunk are gzip+base64 embedded separately and decompressed in-browser via the native `DecompressionStream('gzip')` API (Chrome 80+/FF 113+/Safari 16.4+); `bootstrap()` decodes the index plus chunk 0 (which covers the pre-rendered first page) up front, then decodes further chunks on demand as later pages are viewed (`ensureChunk`/`ensureChunks`/`getSummary`) and prefetches the rest during idle time. A full-text search waits for every chunk to be decoded before filtering, so it can never miss a match sitting in an undecoded chunk. `--no-compress` embeds one plain `{index, summaries}` JS object literal instead — no chunking, no `DecompressionStream` needed, but the whole archive loads up front. Preview iframes are click-to-load facades (thumbnail + play button) that swap in the YouTube embed only on click.

## Ebook export

`ebook.py` renders stored videos into a single EPUB 3 archive — one chapter per ISO calendar week, with each week's videos as sections (summary, optional thumbnail, optional transcript). Like `export.py`, it only reads from `data/` (and, for `--user`, the sync server's SQLite database) — no YouTube or LLM calls.

```bash
python ebook.py --all                                   # newest 100 videos (DEFAULT_LIMIT), no window
python ebook.py --all --limit 0                          # all videos, no cap
python ebook.py --hours 48                                # only the last 48 hours
python ebook.py --all --channel UC123abc                  # restrict to one channel
python ebook.py --all --tag Rust                          # restrict to one tag
python ebook.py --all --videos abc,def,ghi                # explicit video IDs
python ebook.py --all --no-thumbnails --no-transcripts    # smaller file, faster build
python ebook.py --all --user you@example.com               # split into Unread / Read parts
python ebook.py --all --user you@example.com --read drop   # keep unread videos only
python ebook.py --all --lang en                            # embedded UI language
```

- `--hours` / `--all` are mutually exclusive, mirroring `export.py`; neither is required — no window flag behaves like `--all` (all videos in the store, still capped by `--limit`).
- `select_videos()` filters (by `--channel`/`--videos`/`--tag`), sorts newest-first, then cuts to `--limit` (default `DEFAULT_LIMIT` = 100; `0` = no limit) — the cut happens before grouping into weeks, so "the newest 100" means exactly that, not "100 per week". `--limit` rejects negative values at the argparse layer (`_limit_type`): `picked[:limit]` would otherwise slice from the wrong end and silently drop the newest videos instead of the oldest.
- `--user EMAIL` reads that user's read state from the sync database (`--sync-db`, default `sync-server/sync.db`) via `load_read_ids()`; an unknown email is a hard error. `--read` only takes effect when `--user` is given — `main()` forces mode `ignore` otherwise, so a book built without `--user` always gets one undivided "Videos" part rather than a misleading "Unread" label with an empty (never-consulted) read state. With `--user` set, `--read` controls the split: `split` (default) produces an "Unread" part followed by a "Read" part; `drop` keeps only unread videos; `ignore` puts everything into one "Videos" part regardless. Empty parts are dropped; `partition_by_read()` in `ebook.py`.
- `main()` exits 0 with a message — without touching the filesystem — both when the selection is empty (`select_videos()` returned nothing) and when every part ends up empty after `--read` (e.g. `--read drop` with nothing unread left): building an EPUB from zero parts would emit an NCX with an empty `navMap`, which is invalid per the EPUB DTD, so that case is caught before `build_epub()` is ever called.
- `collect_thumbnails()` downloads (or reuses from disk) each selected video's thumbnail as raw JPEG, caching under `data/thumbnails/<video_id>.jpg`; a 0-byte cache file (debris from a killed write) is treated as a miss and refetched, never trusted as a hit. Writes land via a temp file + `os.replace()` so a killed write can never leave a truncated file for a later run to load. A failed/oversized/non-https thumbnail is skipped, not fatal — a book missing one thumbnail is still a book.
- Transcripts (unless `--no-transcripts`) come from `store.get_llm_transcript_path()` (same de→en→stored-lang→plain priority used for LLM input) and get their own XHTML page per video, linked back to the chapter.
- Store rows carry the raw ISO-8601 `duration` ("PT1H2M3S"); `main()` reformats it through `export._fmt_duration()` (the same helper `export.py` uses, imported rather than duplicated — cf. `repair.py`'s `openrouter._fix_timestamp_links()`) into "H:MM:SS"/"M:SS" before handing videos to `epub_builder`, so `chapter.xhtml.j2` never prints the raw ISO string.
- A video with no summary (`transcript_error` set — `ip_blocked`/`rate_limited`/`country_blocked`/anything else) renders the matching localized message from `i18n.py` in its section instead of an empty `<div class="summary">`, mirroring the mapping in `export.html.j2`.
- `epub_builder.build_epub()` does the actual packaging: `group_by_week()` buckets by ISO `(year, week)` (never the week number alone — ISO week 1 can start in December); `xhtmlify()` guarantees every fragment parses as XML — after rewriting named entities it also escapes any bare `&` that isn't part of one of the five predefined XML entities or a numeric reference (stored summaries are full of these in `&t=` timestamp-link hrefs; left alone a single stray `&` used to make the whole fragment fall back to escaped plain text, losing every heading, list, and link) — falling back to stripped-and-escaped plain text only when a stored summary contains genuinely malformed markup (e.g. a mismatched tag); the ZIP is written with `mimetype` as the first, uncompressed entry (`zipfile.ZIP_STORED`) as EPUB readers require; `build_epub()` also computes the book's covered date range (earliest to latest `published_at` across every video, via `_covered_date_range()`) and passes it to the title page.
- `ebook/` holds the Jinja templates and stylesheet: `book.css`, `chapter.xhtml.j2`, `nav.xhtml.j2`, `content.opf.j2`, `toc.ncx.j2`, `title.xhtml.j2`, `transcript.xhtml.j2`. `title.xhtml.j2` shows the covered date range (when the book has any videos) alongside the video counts and generation date.
- `i18n.py` carries `book_title`, `book_period`, `book_week`, `book_watch`, `book_transcript`, `book_contents`, `book_back`, and the per-part titles `book_part_unread`/`book_part_read`/`book_part_all`, in both `de` and `en`.
- The resulting `.epub` can be delivered to a Kindle via Amazon's Send-to-Kindle (email attachment or app) — no conversion needed, it's a spec-valid EPUB 3 file.

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
| `export.py` | Export CLI (also writes the `<output>.meta.json` update manifest via `renderer`): renders a self-contained HTML archive with client-side search, channel/tag/read/bookmark filters, sort (publish date, date added, channel, title), and pagination; passes each video's `collected_at` into the embedded index so the "date added" sort works; `--thumbnail` for static images, `--sync-url` to embed the sync server, `--show-model` for LLM badge, `--no-compress` to embed data uncompressed |
| `repair.py` | Repair CLI: re-fetches missing transcripts and re-summarizes missing/broken summaries |
| `recover_from_export.py` | Restore store entries from a previously exported HTML file; inserts missing DB rows and summary files; leaves existing entries untouched; supports `--dry-run` |
| `store.py` | SQLite + file store: `data/videos.db` (metadata, including `tags TEXT` column storing JSON array), `data/transcripts/<id>.txt`, `data/summaries/<id>.html`; `get_all_videos()`/`get_videos_since()` accept `with_transcripts: bool = True` — pass `False` to skip reading transcript files from disk when a caller (e.g. `ebook.py`) only needs metadata/summaries, cheaper against a store holding thousands of videos; `get_llm_transcript_path()` returns the best transcript `Path` for LLM/ebook input (de → en → stored `transcript_lang` → plain `<id>.txt`), or `None` |
| `ebook.py` | Ebook CLI: `select_videos()` (filter/sort/limit), `load_read_ids()` (read state from the sync DB), `partition_by_read()` (Unread/Read/all split — `main()` only passes `--read` through when `--user` is set, otherwise forces `ignore`), `collect_thumbnails()` (cached JPEG downloads), `main()` — wires them together, formats each video's raw ISO `duration` via `export._fmt_duration()`, reads only from `store.py`, writes an `.epub` via `epub_builder.build_epub()` |
| `epub_builder.py` | Builds the EPUB 3 archive from selected videos: `group_by_week()` (ISO `(year, week)` buckets), `xhtmlify()` (guarantees every emitted fragment parses as XML — escapes bare `&` left over after the named-entity pass, e.g. in `&t=` timestamp-link hrefs, before falling back to escaping the whole fragment to plain text only on genuinely malformed markup), `_covered_date_range()` (earliest/latest `published_at` across the book), `render_chapter()`, `build_epub()` (writes the ZIP with `mimetype` first and uncompressed, as EPUB readers require) |
| `ebook/` | Jinja templates + stylesheet for the EPUB: `book.css`, `chapter.xhtml.j2`, `nav.xhtml.j2`, `content.opf.j2`, `toc.ncx.j2`, `title.xhtml.j2`, `transcript.xhtml.j2` |
| `summarize.py` | Legacy all-in-one CLI (fetch + render in one pass, no store involvement) |
| `youtube_client.py` | YouTube Data API v3 wrapper (auth, subscriptions, video search, channel resolution) |
| `transcripts.py` | `youtube-transcript-api` wrapper; language priority via `TRANSCRIPT_LANGS` (default: de,en); handles ip_blocked / rate_limited / country_blocked errors; on `ip_blocked` retries once via the configured proxy; `VideoUnplayable` is only classified as `country_blocked` when the reason mentions "country"/"region" — on `country_blocked`, retries once with a country-pinned Webshare proxy (`PROXY_FALLBACK_COUNTRY`, default: DE) if `WEBSHARE_PROXY_URL` is set; other `VideoUnplayable` causes fall to `unavailable` (retryable); `requests.exceptions.ProxyError` and `ConnectionError` are caught and mapped to `unavailable`; logs proxy configuration on startup |
| `openrouter.py` | LLM client (OpenRouter by default, or any OpenAI-compatible endpoint); summary language via `SUMMARY_LANG`; structured prompt enforces chronological sections scaled to video length, written as flowing prose (`<p>`) with bullets only for genuine enumerations, timestamp links placed inline after each relevant sentence; extracts 3–7 English topic tags from the `<!-- tags: ... -->` comment appended by the model, then strips markdown fences from what remains (`_clean_response()` — tags first, opening and closing fence independently, because tag extraction may already have consumed the closing fence); returns `(summary_html, tags_list)` tuple; `max_tokens=16384`; `_validate_summary()` raises `SummaryRejected` when the response hit the output cap (`finish_reason == "length"`) or degenerated into a repetition loop (30+ consecutive repeats of one token) — `collect.py` then stores the video without a summary and `repair.py` leaves the existing one unchanged, so garbage is never written to the store; the chunk (map) pass rejects repetition loops too but tolerates `length`, since the 2048-token chunk budget can legitimately be hit and truncated key points still feed the synthesis pass; `_fix_timestamp_links()` then repairs the links the model gets wrong — it recomputes each `t=` from the visible `MM:SS` label (models write `t=202` or `t=2` for "02:02" instead of `t=122`; the label comes from a real transcript marker and is authoritative) and normalises anchors closed with the wrong tag (`</p>`, `</h3>`, `</article>`, or a stray `</` before `</a>`, which would otherwise leave the link open and swallow the rest of the card), keeping a structural closing tag after the inserted `</a>` and dropping non-structural ones; runs before `_dedup_timestamps()`, which compares `t=` values |
| `renderer.py` | Jinja2 renderer; writes the final HTML file; accepts `lang=` kwarg; sanitizes summaries at render time via `_sanitize_summary()` — strips any trailing incomplete HTML tag to guard against LLM output truncated mid-tag (which would cause the browser to consume subsequent cards as an attribute value); `_split_export_data()` sorts videos newest-first (published_at desc, video_id desc tie-break) and splits them into a summary-free `index` plus a list of summary chunks (`EXPORT_CHUNK_SIZE` = 50 videos each; chunk k covers index positions `[k*50, (k+1)*50)`); `_export_manifest()` builds the update manifest (`generated_at`, `video_count`, `newest_id`, `newest_published_at`) from the newest-first index; `render_export_html()` embeds it as `const MANIFEST` and writes it to `<output_path>.meta.json` after the HTML file, pre-renders the first `EXPORT_FIRST_PAGE` (20) cards as static HTML (`_esc_html()` mirrors the JS `escHtml()` byte-for-byte, `_summary_preview()` mirrors the JS first-paragraph/"more" split) and embeds `index` plus the chunk array separately, gzip+base64 (`compress=True`, default) or as one plain `{index, summaries}` JS object literal with no chunking (`compress=False`) |
| `i18n.py` | UI string dicts for `de` (default) and `en`; `get_strings(lang)` and `resolve_lang(lang)` helpers |
| `template.html.j2` | Self-contained HTML template with embedded dark-theme CSS; read/bookmark buttons on each card, state persisted in browser `localStorage` (migrated automatically from the old cookie-based storage on first load, which is then cleared); strings from `i18n.py` via Jinja2 `{{ t.xxx }}` |
| `export.html.j2` | Export template: dark-theme CSS, controls bar (all filter controls carry `autocomplete="off"`), a Jinja macro `card(v)` pre-renders the first `EXPORT_FIRST_PAGE` (20) cards as static HTML directly into the grid — it must stay markup-identical to the JS `buildCard()`, enforced by `tests/test_export_prerender.py`; document order is header, controls bar, pre-rendered grid, read/bookmark hydration script, sync-boot script (only when `sync_url` is set), pagination, footer, main UI script, and finally the data-blob script (last element before `</body>`, schedules `bootstrap()` via `requestAnimationFrame`); `bootstrap()` decodes the gzip-compressed `index` plus chunk 0 (covers the pre-rendered first page) via `DecompressionStream`, sets `dataReady`, fills dropdowns, and renders — later chunks are decoded on demand (`ensureChunk`/`ensureChunks`) and prefetched during idle time, with `getSummary(v)` looked up per-video instead of a flat `SUMMARIES` map; a text search waits for every chunk to be decoded (`allChunksReady()`/`ensureAllChunks()`) before filtering, so it can't miss a match in an undecoded chunk; `search_text` is computed lazily per video on first search (`getSearchText()`), not precomputed server-side; a `dataReady` flag suppresses sync-driven re-renders until the index and chunk 0 are decoded, though the read/bookmark state merge itself still happens; if the visitor's resolved language differs from the export's embedded default, `bootstrap()` clears the pre-rendered grid so `applyLang()` rebuilds it; preview embeds are click-to-load facades (`loadEmbed()`); each filter and sort control has a visible label (`ctrl-label`); the sort dropdown offers `date-desc`/`date-asc` (publish date), `added-desc` (store `collected_at`, tie-broken by publish date descending, falling back to `published_at` when the field is absent), `channel`, and `title` — `applyLang()` writes the option labels by index, so inserting an option means shifting `sortOpts[n]` there too; date filter accepts a "published after" date and filters client-side via ISO string comparison; tag chips on cards are clickable and toggle the tag filter; channel name in card meta is clickable and toggles the channel filter (`setChannelFilter()`); read/bookmark state persisted in `localStorage` (migrated from the old cookie on first load, which is then cleared — cookies cap out around 340 IDs at the 4096-byte limit); language (`yt_lang`) state still in a cookie (small, unaffected); language selector in page header with flag emoji (🇩🇪/🇬🇧), priority: cookie → browser language → embedded default; sync bar shows "Ingest" button when `can_ingest` is true; an update banner (`#update-banner`) polls the `MANIFEST_URL` sidecar every `UPDATE_POLL_MS` (5 min) when the page is served over http(s), compares `generated_at` against the embedded `const MANIFEST` and offers a reload (`checkForUpdate()`/`renderUpdateBanner()`/`dismissUpdate()`, re-rendered from `applyLang()`); a sync-boot script harvests the magic-link token and fires `/api/whoami` and `/api/state` in parallel into `window.__syncBoot` before the main script runs, so `initSync()` (called at the end of `bootstrap()`) can consume already-in-flight responses instead of starting them late; when `sync_url` is set, `warnIfInsecure()` fires a single localized `alert()` on load unless `loginRedirectAccepted()` holds — that mirrors the server's `_valid_redirect_uri()` (any `file://` URI, or page origin === `SYNC_URL` origin); http-page/https-server gets the "no HTTPS" text, everything else the "wrong origin" text naming the expected origin |
| `state.py` | Reads/writes `last_run.json` (channel_id → last checked ISO timestamp) |
| `send_mail.py` | Standalone script; sends an HTML file as an email via SMTP_SSL |
| `sync-server/sync_server.py` | Standalone Flask sync service: magic-link auth (supports STARTTLS port 587 and SSL port 465), per-user read/bookmark state in SQLite, last-write-wins merge; `POST /api/ingest` appends video ID to `INGEST_QUEUE` file and returns 202; `/api/whoami` returns `can_ingest` flag |
| `ingest_worker.sh` | Cron script that drains `INGEST_QUEUE` by running `collect.py --video <id>` for each entry; logs to `data/ingest_worker.log`; schedule every minute |

### Cron configuration (`cron.env`)

The cron scripts carry no host-specific values — this repository is public. They source `cron.env` (gitignored, template in `cron.env.example`) for `EXPORT_OUTPUT` (archive path, default `<repo>/yt.html`), `SYNC_URL` (unset → export runs without `--sync-url`) and `DIGEST_TO` (required by `run_*.sh`, which abort without it). A plain assignment in `cron.env` overrides the same variable set in the crontab line, since the file is sourced after the environment is inherited. `tests/test_collect_shell_wiring.py` fails if a concrete host or mail address reappears in a tracked `*.sh`.

## Credentials and Sensitive Files

- `client_secrets.json` — Google OAuth credentials (never commit)
- `token.pickle` — cached OAuth token (never commit)
- `.env` — API keys and SMTP credentials (never commit)
- `cron.env` — host-specific cron settings (never commit; see `cron.env.example`)
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
