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

```bash
# Remove videos already stored that VIDEO_TITLE_FILTERS would skip today
python collect.py --prune-filtered --dry-run   # list them first
python collect.py --prune-filtered             # then delete
```

- `--no-proxy` ignores `WEBSHARE_PROXY_URL` and fetches transcripts via a direct connection.
- Discovery goes through the public RSS feed (`feeds.py`), not the API: no quota, no token. `--no-rss` forces the API path. What a feed-driven run still spends: `subscriptions.list` under `--auth` (1 unit per 50 channels per run, ~2 per run here), plus the per-channel fallback whenever a feed cannot answer. Durations, which the feed does not carry, are filled by `_fill_feed_durations()` from three sources, cheapest first: the store (the four-hour window means the feed re-lists a video for ~8 runs), then `ytdlp_meta`, then one batched `videos().list` for whatever is still missing (1 unit per 50). That last step is the backstop for a run whose yt-dlp is broken or blocked — `_is_short(None)` is `False`, so without it every short would go through transcript and LLM work.
- The feed holds ~15 entries. When every entry lies inside the window the feed may have cut older ones off, so `feeds.get_new_videos_rss()` returns `None` ("cannot answer") and that one channel falls back to the API for that run — an empty list, by contrast, is a real answer meaning nothing is new. A channel posting more than 15 videos between two runs therefore still costs quota, and says so on stderr.

- Fetches new videos, transcripts, and summaries; persists results to `data/`.
- Videos already in the store are handled incrementally: skipped entirely if both transcript and summary exist; otherwise only the missing piece is fetched or generated.
- Without `--hours`, uses each channel's last-run timestamp from `last_run.json`; defaults to 24 h on first run.
- `--hours N` overrides last-run state and does **not** update it.
- Exit codes: `10` (`EXIT_NEW_VIDEOS`) when the run changed what the archive shows — a new video stored, **or** a summary written for a video that was already in the store but had none (its transcript fetch had failed earlier). `store.add_video()` is never called in that second case, so before this it left no trace at all: the archive kept showing "kein Transkript" until some unrelated run happened to store a new video. `_process_single_video()` returns `(added, summarized)` and the channel loop counts both; `main()` returns `(added, summarized, removed)` — the third is `--prune-filtered`'s deletions, which change the archive just as much — and `_exit_code(added, summarized, removed)` merges them — and only it does, the two counts stay separate in the "Done." line so a cron log says which happened. `0` when it ran fine but changed nothing, `11` (`EXIT_AUTH_FAILED`) when OAuth is unusable, non-zero otherwise. `collect.sh` gates the export on `10` — chaining the export with a plain `&&` instead would re-export on every run, flipping the archive's `generated_at` and leaving the update banner permanently on. `ingest_worker.sh` counts `10` as success, or it would re-queue every video it successfully ingested.
- `--prune-days N` removes store entries older than N days. Omitted by default (no pruning).
- `11` (`EXIT_AUTH_FAILED`) is `--auth`-specific and means "repeat this run with `--file`", not "try again later": `youtube_client.has_usable_token()` found no credentials that `build_service()` could use without a browser, the refresh was refused, `subscriptions.list` answered 401, or the account has no subscriptions at all. `_is_auth_failure()` decides, and deliberately excludes 403 (quota and rate limits arrive as 403) and bare `OSError` (DNS failures are subclasses of it) — those stay `1`, because a network blip must not trigger a second full pass over every channel. `collect.sh` answers `11` by re-running from `$CHANNELS_FILE`.
- Short videos (duration ≤ `SHORTS_MAX_SECONDS`, default 180 s) are **skipped by default**. Pass `--include-shorts` to collect them. Threshold is configurable via `SHORTS_MAX_SECONDS` in `.env`.
- `VIDEO_TITLE_FILTERS` in `.env` (not `cron.env` — `load_dotenv()` resolves `.env` relative to `collect.py`, so it is found under cron whatever the working directory) is a title blacklist: comma-separated regex patterns, matched case-insensitively with `re.search`, so `Letsplay` hits "Mein Letsplay #3" and `Let.?s ?Play` is needed for the apostrophe spelling. `_should_filter_title()` checks it **before** the transcript fetch and the LLM call, so a match costs nothing and never enters the store. Two traps: the comma is the separator, so a pattern must not contain one, and an invalid regex exits `1` rather than filtering silently. It applies on **both** paths — the channel loop *and* `_process_single_video()`, i.e. `--video` — so a blacklisted video queued through the archive's Ingest button is dropped without a word: the worker reports success, no re-export follows, and the page's "Auf Zusammenfassung warten" runs its full ten minutes for an event that will never come.
- `--prune-filtered` is the reversal for videos collected before a pattern existed: it scans the store through that same `_should_filter_title()` (so the two can never disagree about what matches), prints every hit with the pattern that caught it, and deletes the entries with their transcript and summary files via `store.delete_videos()`. `--dry-run` reports without writing and therefore returns 0, never the match count — a dry run must not make a caller re-export an archive it did not change. The run touches no network and no OAuth (it returns before `_lazy_service()`), an empty `VIDEO_TITLE_FILTERS` deletes nothing rather than everything, and a broken pattern aborts during the scan, before the first deletion. Back up `data/` first: the deletion is irreversible and `data/` is gitignored.

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
| `collect.sh` | Runs `collect.py --auth --hours 4` under `timeout -k 30s $COLLECT_TIMEOUT` (default 30 m), skipping OAuth entirely when `token.pickle` is missing; on exit 11 (OAuth unusable) or a timeout of that run — the only way to catch a browser prompt nobody will answer — it repeats the collection from `$CHANNELS_FILE`, which needs neither token nor quota; with `EXPORT_USER` set in `cron.env` that export becomes the personal pair (`--user`, plus `--read-days` from `EXPORT_READ_DAYS`); on exit code 10 (a new video stored, or a summary written for a stored-but-incomplete entry) immediately re-exports the archive to `$EXPORT_OUTPUT` (default `yt.html`, sync URL from `$SYNC_URL`); schedule frequently (e.g. `*/30 * * * *`) |
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
- `--fix-links` rewrites stored summaries through `openrouter.repair_summary_html()` — unwraps a summary the model returned as a JSON object, rebuilds anchors whose href or whose tag was never closed (recovering the sentences they swallowed), closes anchors left open without a label, relabels anchors that show only a period, recomputes each `t=` offset from its visible `MM:SS` label, closes anchors the model left open, adds a missing `ts-link` class, and drops `</p>` tags the model placed mid-sentence. Purely textual, no API calls, and it only touches files that actually change. Back up `data/summaries/` first: the rewrite is in place and `data/` is gitignored.
- `--video ID,ID,...` restricts all operations to the specified video IDs (comma-separated).
- `--dry-run` prints what would be done without writing anything.
- To backfill tags on existing videos (after upgrading from a version without tag support): `python repair.py --force-summarize`
- `--remap-tags` rewrites stored tags through the controlled vocabulary in `tags.py` and `tag_aliases.json`, no LLM calls; `--dry-run` reports the change without writing.
- Exit codes mirror `collect.py`: `10` (`EXIT_CONTENT_CHANGED`, pinned equal to `collect.EXIT_NEW_VIDEOS` by `tests/test_repair_exit_code.py`) when the run actually wrote something — summaries written, summary files rewritten by `--fix-links`, tags rewritten by `--remap-tags` — otherwise `0`. A `--dry-run` writes nothing and therefore always exits `0`, even while it reports what it would change. The constant is defined in `repair.py` rather than imported, because importing `collect` drags in `googleapiclient`/`google.auth` for one integer. Chain the export onto it to get the archive (and its update banner) refreshed after a repair:

```bash
python repair.py --fix-links; [ $? -eq 10 ] && python export.py --all --output yt.html
```

## Tags

Tags come from a fixed German vocabulary in `tags.py` — 161 entries in ten
groups. The summarize prompt carries the list, and `tags.canonicalize()`
enforces it: what is not on the list is not stored, but counted in
`data/tag_candidates.json`.

```bash
python tags.py --list                    # the vocabulary by group
python tags.py --candidates --min 3      # rejected suggestions seen 3+ times
python tags.py --build-aliases --dry-run # what the alias builder would map
python repair.py --remap-tags --dry-run  # what a store migration would change
```

The vocabulary grows deliberately: `--candidates` shows what the model keeps
asking for, and adding an entry is a commit in `tags.py`. Nothing at runtime can
extend it — that is what produced 10 700 distinct tags before.

`tag_aliases.json` maps old or off-list tags onto the vocabulary. It was built
once by `--build-aliases` for the English tag history and stays in service as a
net for the model's misses. `repair.py --remap-tags` applies vocabulary and
aliases to the whole store; back up `data/videos.db` first, the write is in
place and `data/` is gitignored.

## Export archive

`export.py` renders stored videos into a self-contained HTML file for offline browsing (client-side search, channel/tag/read/bookmark/length filters, sort — publish date, date added, channel, title —, pagination; read and bookmark state persisted in browser `localStorage`).

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
python export.py --all --user me@example.com   # personal view + yt.full.html beside it
```

`--hours` and `--all` are mutually exclusive. Default output filename: `export_YYYY-MM-DD_HH-MM.html`.
`--show-model` shows the LLM model name badge on each card (hidden by default).

### Personal export (`--user`)

`--user EMAIL` turns the output into that sync user's view: everything survives except a video that is **read, not bookmarked, and entered the store more than `--read-days` (default 30) days ago**. `export.filter_personal()` is written as that one exclusion rather than as the union it describes ("unread ∪ recently added read ∪ bookmarked"), because the union form needs three overlapping branches to test. "Entered the store" is `collected_at` (`_added_at()`), falling back to `published_at` for rows predating the column — the same rule the `added-desc` sort uses, so a year-old video backfilled yesterday counts as new here too; a row whose dates are unreadable is kept, never silently dropped.

Read and bookmark IDs come from the sync server's database (`--sync-db`, default `sync-server/sync.db`) through `sync_state.load_state_ids(db, email, kind)` — one query for both `video_state` types, shared with `ebook.load_read_ids()`, which is now a pass-through. An unknown email is a hard error, as in `ebook.py`: a typo would otherwise produce an export in which nothing is marked.

The unfiltered archive is written **first**, beside the filtered page: `export.full_sidecar_path()` puts `.full` before the extension (`yt.html` → `yt.full.html`), and the filtered page links to it by basename, so the link resolves under `file://` and over http alike. Each file gets its own `.meta.json`, so each notices its own updates. The sidecar is "full" only relative to the filtered page — same window, same `--channel`/`--videos`, minus the per-user filter — so the cron invocation passes `--all`. Order matters only in that the link must not dangle: the page that carries it is written second.

In the page, `full_url` (renderer kwarg → template) does three things: the header gains `#full-link` (`t.full_archive`, and `applyLang()` rewrites it from `s.fullArchive`), `singleVideoUrl()` resolves against `FULL_URL` so every share button copies a link into the full archive rather than into a filtered page that may not contain the video for the recipient (or, later, for this reader), and the `singleNotFound` state appends `singleFullLink()` — "Im vollständigen Archiv öffnen" — which is the obvious next step a filtered page has and a complete archive does not. A plain export renders `FULL_URL = null` and behaves exactly as before.

#### Backlog history (`data/export_stats.jsonl`)

Every `--user` run appends one line — `ts`, `user`, `read_days`, `window`, `personal_count`, `total_count` — through `export_stats.record()`, and then reads the series back for the page it is about to render (`export.record_backlog()`: record first, because the tooltip's "today" is that very run). The archive hangs it on its video count, twice over: as a `title` tooltip for a mouse, and as a panel (`#backlog-panel`) the count opens on click or tap — a phone or tablet has no hover at all, so the tooltip alone would hide the whole thing from every touch device. Both print the same string from `backlogTooltipText()`: "Bestand heute: 389 / vor 7 Tagen: 412 (−23) / vor 30 Tagen: 350 (+39) / vor 3 Monaten: —". The panel closes on a second tap, on a tap anywhere else (the count's own handler calls `stopPropagation()`, or the document handler would close it in the same gesture), and on Escape; the count carries `role="button"`, `tabindex="0"` and `aria-expanded`.

A record is only comparable to records made under the same rules, so `export_stats.fingerprint()` keys the series by **user, `--read-days` and the time window** (`window_label()`: `all` or `hours:N`). Change `--read-days` and a new series starts — the old one stays in the file, untouched, and simply is not what the current page asks for, so a filter change can never look like the backlog moved. `--channel`/`--videos` are deliberately *not* part of the key (the user asked for read-days and window), so a restricted personal export lands in the same series and will read as a sudden drop; don't run one against the archive you track.

`_count_at()` answers each window with the last count recorded **at or before** that day, never the nearest one — a history that does not reach back 90 days renders a dash rather than passing today's number off as history. An empty series yields `None`, and the page then renders no tooltip at all (`backlog_json` → `const BACKLOG = null`, no `has-backlog` class on `#video-count`). A failed write costs the statistic, never the export: `record()` catches `OSError` and warns on stderr. JSON Lines rather than a table in `videos.db` so a half-written line from a killed run is skipped by `load()` instead of breaking the next export.

`collect.sh` **and** `ingest_worker.sh` pass `--user`/`--read-days` when `EXPORT_USER`/`EXPORT_READ_DAYS` are set in `cron.env` — both write `$EXPORT_OUTPUT`, so a worker exporting without the flag would replace the personal page with the whole archive the minute after someone used the Ingest button on it (`tests/test_collect_shell_wiring.py` pins both invocations together) (the address is host-specific, so it may not appear in the tracked script — `tests/test_collect_shell_wiring.py` enforces that). Consequence to keep in mind: the export trigger is still exit code 10, and read state changes without any new video arriving, so the filtered page only catches up at the next run that stored something.

### Single-video deep link (`?v=ID`)

`yt.html?v=VIDEO_ID` shows exactly that one video. `readVideoParam()` is evaluated at script-parse time (before the data blob is parsed): `enterSingleVideoView()` drops the pre-rendered cards, hides the controls bar and pagination, shows `#single-bar` and writes a localized loading note into the grid. Once the index is decoded, `applyFiltersAndSort()` short-circuits to the single match and `renderPage()` pulls the summary chunk on demand — so the video may sit anywhere in the archive. Three states are kept apart deliberately: loading (from first paint), found, and `singleNotFound` — the last only ever decided against the fully decoded index, never against the pre-rendered page. `clearSingleVideo()` removes just the `v` parameter via `history.replaceState` and re-renders; no reload. Every card has a share button (`shareVideo()`, clipboard with a `prompt()` fallback for `file://`/non-secure contexts) that copies that URL. The button exists in **both** the Jinja macro `card(v)` (string `t.share_btn` from `i18n.py`) and the JS `buildCard()` (`s.shareBtn` from the JS `I18N`) — the two must stay markup-identical and carry the same label.

### Action row, twice per card

The read / bookmark / share row (`.video-actions`) is emitted a second time as the last element inside `.summary-details` (`.video-actions-bottom`), so a long summary can be marked read, bookmarked or copied where the reading ends instead of after scrolling back up. It lives inside the details block on purpose: it inherits that block's `hidden`, so it exists only while the summary is expanded, and `toggleSummary()`'s `nextElementSibling` still finds the details div. The bottom row carries a fourth button the top row does not: `collapse-btn` (`t.show_less` / `s.showLess`, new in `i18n.py`), which calls `collapseSummary()` — that function drives the card's own `.summary-toggle` through `toggleSummary()` instead of setting `details.hidden` itself, so the toggle's label can never read "weniger" over a collapsed summary, and it scrolls the card back into view when collapsing would leave it above the viewport. Cards whose summary has no "more" part (short summary, or a transcript error) get one row only, and no collapse button. Both the macro and `buildCard()` render it — `buildCard()` through a local `actionsHtml(extraClass)` so the two rows cannot drift — and every consumer of the button state therefore has to address *all* copies: `updateCardInPlace()` and the pre-render hydration IIFE use `querySelectorAll`, not `querySelector`.

### Waiting for a queued video

After a successful ingest the sync bar offers "Auf Zusammenfassung warten". The wait is opt-in (a page that navigates away on its own is unpleasant to read), polls the manifest sidecar every 15 s (`WAIT_POLL_MS`) for at most 10 minutes (`WAIT_MAX_MS`), and lives in `localStorage` under `yt_ingest_wait` so it survives the reload it causes.

The manifest cannot say *which* video an export brought — an ingested backfill is not `newest_id` — so a changed `generated_at` only triggers a reload into `?v=ID`, and the deep-link view decides: `ingestWaitObserve(id, found)` clears the wait when the video is on screen and keeps it when the export belonged to something else. `resumeIngestWait()` re-pins `generated_at` to the manifest of the page it is running on **before** polling resumes; without that the saved value is older than the current export by definition and every poll would reload again, forever. `tests/test_export_ingest_wait.py` pins that specifically.

A video that is already in the archive **with a summary** is neither queued nor waited for: `collect.py --video` would exit 0 on it, no re-export would follow, and the wait would run its full ten minutes for an event that is never coming. `ingestTargetKnown()` decides by looking the ID up in the decoded index and pulling its chunk (`ensureChunk` + `getSummary`), not by `transcript_error` — a summary can be missing without one — and `doIngest()` then jumps straight to `?v=ID` instead of POSTing, skipping the jump when `document.visibilityState === 'hidden'` so the userscript's background tab does not reload 12 MB nobody is looking at. A stored video *without* a summary is queued normally: that run can still produce one, exit 10, and re-export.

The offer only appears when `updatePollable()` holds (http(s)): a `file://` archive has no manifest to poll and the wait could only ever time out. `ingestTargetKnown()` waits for `whenDataReady()` rather than answering "unknown" before the index is decoded — the sync boot reveals the Ingest box from `/api/whoami` well before the data blob is parsed, and the userscript submits the moment it appears, so an early "not here" would queue videos that are already present; the 10 s race in `whenDataReady()` covers the archive that never decodes at all (no `DecompressionStream`), where waiting forever would swallow the submission instead. `resumeIngestWait()` honours an expired window instead of handing out a fresh one: it re-pins the baseline, then offers "weiter warten".

On the userscript side that shortcut is visible in the verdict: an input cleared without the button ever having been disabled is the signature of "already in the archive, nothing sent", reported as `already` rather than `queued` — otherwise the YouTube button would confirm a queueing that never happened. The relay then opens the archive at `?v=ID` in a foreground tab, since the background tab deliberately does not navigate.

While a wait is running `checkForUpdate()` returns early — the banner would announce the very manifest change the reload is seconds away from acting on.

### Update banner (manifest sidecar)

Every export also writes `<output>.meta.json` next to the HTML — e.g. `full_archive.html.meta.json` — holding `generated_at` (UTC, seconds), `video_count`, `newest_id`, `newest_published_at`, `summary_count` (videos with a non-empty summary), `summary_digest` (16 hex characters, a running sha256 over every video's id plus the hash of its summary, in the index's newest-first order — a video without a summary contributes its own record, so a *removed* summary changes the digest too) and `recent_ids` (the `EXPORT_RECENT_IDS` = 100 most recently *collected* video IDs, newest arrival first, ordered by `collected_at` falling back to `published_at` — so a backfill ingested today leads the list) (`renderer._export_manifest(index, chunks)`). The same object is embedded in the page as `const MANIFEST`. When the archive is served over http(s), the page polls the sidecar every 5 minutes (`UPDATE_POLL_MS`, `cache: 'no-store'`, skipped while the tab is hidden, plus one check on `visibilitychange` → visible) and compares `generated_at`. On a difference a sticky banner appears, with a reload button and a dismiss "×" that silences that one `generated_at` but not the next. `updateBannerKey(current, pending)` — a pure function, so the wording is unit-testable without a DOM (`tests/test_export_update_banner.py`) — decides the text: videos arrived → "N neue Videos verfügbar" (plus ", M entfernt" when videos left in the same export); otherwise, **only when nothing came or went**, `summary_count` grew → "M neue Zusammenfassungen verfügbar", or a differing `summary_digest` → "Zusammenfassungen aktualisiert"; else "Archiv aktualisiert".

The arrivals are counted by `arrivedVideos()`, not by the `video_count` delta, which is a *net* figure and says nothing about what came in: a personal export (`--user`) drops read videos on every run, so three new videos against five aged-out ones reads as an archive that shrank by two and used to leave only "Archiv aktualisiert". It counts the entries at the *front* of `pending.recent_ids` that the current page does not know yet and stops at the first one it does — a prefix count, not a set difference, because removals let older IDs slide into the pending window that the current one never carried, and those sit behind a known ID. Departures are then whatever the net change does not explain (`added - netVideos`, floored at 0). The count is capped by the window, so a page left open past 100 arrivals under-reports rather than guessing; `arrivedVideos()` returns `null` when either manifest predates `recent_ids`, and the old net-count rule applies unchanged there.

The came-or-went guard is what keeps a shrinking export (`--prune-days`, a narrower `--hours`) from announcing updated summaries when videos merely left, and the summary fields are compared only when both manifests carry them — an archive exported before they existed falls back to the generic wording instead of claiming a change it cannot establish. `file://` archives never poll. The sidecar is written **after** the HTML, so it never announces videos the served archive does not contain yet; deploy scripts that copy the export must copy the sidecar too (`ingest_worker.sh` re-exports in place, so nothing to do there).

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
- `select_videos()` filters (by `--channel`/`--videos`/`--tag`) and sorts newest-first; `main()` then applies the read filter and only afterwards cuts to `--limit` (default `DEFAULT_LIMIT` = 100, `0` = uncapped). The limit therefore counts the videos that actually reach the book: `--limit 50 --read drop` means the 50 newest unread ones. Each video is its own chapter document (`video-<video_id>.xhtml`, one spine item each), so a reader's next-chapter jump moves video by video; weeks stay the grouping level in nav and NCX, and `group_by_week(videos, newest_first=True)` orders both the weeks and the videos inside them newest first (pass `False` for chronological order), and a kicker line above each title names the week. A week's NCX navPoint points at its first video and must therefore carry that video's `playOrder` — differing values for one target are an NCX violation.
- `--sort added-desc|date-desc|date-asc` (default `added-desc`, because a store full of backfills and on-demand ingests makes arrival, not publish date, the useful notion of "new to me") drives both the cut and the book: `main()` sorts with `epub_builder.sort_key(mode)` before applying `--limit`, then passes `order=` into `group_by_week()`. `added-desc` ranks by `collected_at` (falling back to `published_at` for rows predating the column) and places each week by its freshest arrival.
- `--exclude-channel` (repeatable, comma-separated) drops whole channels by ID or by exact case-insensitive name via `drop_excluded_channels()`; it runs before the untranscribed filter, `--read` and `--limit`, and reports how many videos it removed. The same list can be configured as `EBOOK_EXCLUDE_CHANNELS` in `cron.env` (`configured_exclusions()`, environment before file); the two sources add up, since both mean "leave this out". `ebook.py` reads `cron.env` itself rather than relying on the cron scripts, because a sourced shell variable never reaches a child process.
- Videos without a summary (transcript fetch failed) are dropped before `--read` and `--limit`, so the limit counts real chapters; `--include-untranscribed` keeps them, and a selection consisting only of such videos exits 0 naming that flag. About 2 % of the store is affected.
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

The worker processes each queued ID by running `collect.py --video=<id>` (the `=` form, because a video ID starting with `-` is otherwise read as a flag — that happened, and one queued video failed 100 times) and logs output to `data/ingest_worker.log`.

Ingest metadata comes from yt-dlp (`ytdlp_meta.get_video_metadata()`), not the Data API: ingest is triggered by hand at any hour while the scheduled collect runs spend the project's daily quota, so a `quotaExceeded` there used to take the button down with it over a lookup worth one unit. The store is consulted before any of that: an ID whose transcript and summary are already on disk is answered from `data/videos.db` without a single network call, and an incomplete entry reuses its stored metadata instead of re-fetching it — the queue re-offers long-collected IDs, and paying a yt-dlp run plus a proxy retry to rediscover that is pure latency. The API remains the fallback for videos yt-dlp cannot read, and `yt-dlp` is in `requirements.txt` — an existing deployment needs `pip install -r requirements.txt` before the ingest button works again.

### YouTube userscript (`userscript/yt-ingest.user.js`)

A Violentmonkey/Tampermonkey script that puts a "Zusammenfassen" button on a YouTube watch page (and Shorts/live/`youtu.be`), which queues that video for transcription and summarisation.

It holds **no API token**. Pressing the button opens the archive in a background tab at `#ingest=<VIDEO_ID>`, and the script's other half — matched on the archive URL — types that ID into the page's own Ingest field and clicks its button, so the request goes out under the session the browser is already logged in with. Consequences worth knowing: it works only while that archive page exists at the configured URL and the account is logged in and in `INGEST_EMAILS`; and it needs no CORS exemption, since nothing cross-origin is ever requested (`GM_xmlhttpRequest` and a stored token would be the alternative, and would mean keeping a 30-day credential in script storage).

The hash, not `?v=ID`: the query form is the archive's own single-video deep link and would show the video rather than queue it. The hash is dropped via `history.replaceState` before submitting, so a reload cannot queue twice.

The verdict is read off `doIngest()`'s own mechanics, not off `#sync-status`: that line is localised *and* written by the login and sync handlers, so a change there right after the Ingest box appears may belong to something else entirely. The script instead watches the button's `disabled` flag (set while the request is in flight) and the input, which `doIngest()` clears only on 200/202 — a submission that never disabled the button was refused before sending (unusable ID). A timeout waiting for `#sync-ingest` to become visible is reported as "not logged in", which is what it almost always is. Success is relayed back to the YouTube tab through `GM_setValue`/`GM_addValueChangeListener`, so the button itself confirms.

Placement is verified, not assumed. The button is inserted as a sibling of the like/dislike pill (`segmented-like-dislike-button-view-model`, older renderer as fallback) — `rowSlotFor(anchor, body)`, pure and pinned by `tests/test_userscript.py`, walks up to the child of `#top-level-buttons-computed` and answers **null** when the walk leaves the row instead, which until v1.7.0 it did not: it fell out at `<body>` and handed that back as the parent, so on a renderer without those row ids (a tablet, portrait or landscape) the chip was appended to the end of the document, below the comments, where it measures as perfectly visible and nothing ever escalated it. 800 ms later the button is measured, and a spot that renders it at zero size **or scrolls it out of its own row** (`isClippedHorizontally()` — the action row scrolls sideways on a narrow viewport and `getBoundingClientRect()` reports the chip's size either way; horizontally only, because on a tablet the whole row legitimately starts below the fold) escalates: end of the action row, the actions container, and only then the floating corner, which sits 72 px plus the safe-area inset above the bottom edge so the mobile layout's fixed pivot bar does not cover it. A spot that measured badly is recorded on the button (`dataset.unusable`) and never offered again, or `upgradeFromFloating()` would keep pulling the button back into it. Escalation is not final either — the row does not exist yet when the script first runs, so an early corner verdict describes YouTube's rendering rather than the page; `upgradeFromFloating()` keeps offering the button a real slot for ~20 s and on `yt-page-data-updated`, at most five times so it cannot flicker between two spots, and `styleAsChip()` clears the fixed-position styles on the way back in. Colours are literal (`BUTTON_BG`/`BUTTON_FG`), never YouTube's CSS variables: those resolve per theme and produced a grey chip with grey text. The version in the log comes from `GM_info` — a container accepting the node and never showing it is what the first version got wrong. `yt-navigate-finish` is listened for on `document` *and* `window` (versions differ) and a 500 ms retry runs for ~20 s, because on a cold load neither event fires and the row does not exist yet at `document-idle`. Everything it decides is logged under `[yt-ingest]` with `console.log` — Firefox hides `console.info` unless that filter is on, which is exactly how a silent script looks like a broken one.

Two lines are host-specific and must be edited after installing: `ARCHIVE_URL` and the third `@match` (a `@match` cannot read a variable). `userscript/*.local.user.js` is gitignored for a filled-in copy — this repository is public. `tests/test_userscript.py` requires the file in Node (the browser half is skipped when there is no `document`) and pins the URL parsing, including an ID starting with `-`.

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
| `collect.py` | Collect-phase CLI: resolves channels, fetches videos/transcripts/summaries, writes to `data/`; `_should_filter_title()` drops a video whose title matches `VIDEO_TITLE_FILTERS` before transcript and LLM (on the channel loop *and* on `--video`), and `--prune-filtered`/`find_filtered_in_store()`/`prune_filtered()` remove from the store what that filter would skip today (`--dry-run` lists without writing); `_discover_videos()` asks `feeds` first and only falls back to `get_new_videos()` (filling durations from `ytdlp_meta` on the feed path, from `get_video_durations()` on the API path), `_resolve_identifiers()` reads a `UC…` channel's title from its feed and leaves handles/URLs to `resolve_channel_id()`, `--no-rss` forces the API path; `--video` takes its metadata from `ytdlp_meta` first and only falls back to the API; `_lazy_service()` defers `build_service()` until that fallback is taken and, with `require_token=True` (every run except `--auth`), refuses outright when `youtube_client.has_usable_token()` says no — otherwise a dead token opens an interactive OAuth flow and hangs the cron worker forever. `EXIT_AUTH_FAILED` (11) reports an unusable OAuth setup to `collect.sh` |
| `report.py` | Report-phase CLI: reads `data/`, renders HTML, optional SMTP send |
| `export.py` | Export CLI (also writes the `<output>.meta.json` update manifest via `renderer`): renders a self-contained HTML archive with client-side search, channel/tag/read/bookmark/length filters, sort (publish date, date added, channel, title), and pagination; passes each video's `collected_at` into the embedded index so the "date added" sort works; `--thumbnail` for static images, `--sync-url` to embed the sync server, `--show-model` for LLM badge, `--no-compress` to embed data uncompressed; `--user EMAIL` renders that sync user's personal view (`filter_personal()`) and writes the unfiltered archive beside it at `full_sidecar_path()` (`yt.html` → `yt.full.html`), which the filtered page links to; `record_backlog()` appends that run's count to `data/export_stats.jsonl` via `export_stats` and hands the resulting history to the personal page only (the sidecar is the unfiltered archive, where a backlog has no meaning) |
| `repair.py` | Repair CLI: re-fetches missing transcripts and re-summarizes missing/broken summaries; exits `10` (`EXIT_CONTENT_CHANGED`) when it wrote something, `0` on a no-op or any `--dry-run`, so an export can be chained onto it |
| `recover_from_export.py` | Restore store entries from a previously exported HTML file; inserts missing DB rows and summary files; leaves existing entries untouched; supports `--dry-run` |
| `store.py` | SQLite + file store: `data/videos.db` (metadata, including `tags TEXT` column storing JSON array), `data/transcripts/<id>.txt`, `data/summaries/<id>.html`; `delete_videos(ids)` is the single deletion path (row plus `<id>.txt`, every `<id>.<lang>.txt` and `<id>.html`; an unknown ID is a no-op, never an error) and `prune_older_than()` runs through it; `get_all_videos()`/`get_videos_since()` accept `with_transcripts: bool = True` — pass `False` to skip reading transcript files from disk when a caller (e.g. `ebook.py`) only needs metadata/summaries, cheaper against a store holding thousands of videos; `get_llm_transcript_path()` returns the best transcript `Path` for LLM/ebook input (de → en → stored `transcript_lang` → plain `<id>.txt`), or `None`; `update_tags()` writes only the tags column (`update_video_with_summary()` would reset `transcript_error` and `summary_model`) |
| `tags.py` | Controlled German tag vocabulary (161 tags in ten groups) and the gate that enforces it: `canonicalize()` maps a raw suggestion by exact hit, case-only difference, or `tag_aliases.json` alias, rejects everything else, deduplicates and caps at `MAX_TAGS` (3); `prompt_block()` renders the list for the summarize prompt; `record_candidates()` counts rejected suggestions in `data/tag_candidates.json`; CLI: `--list`, `--candidates [--min N]`, `--build-aliases [--limit N] [--model M] [--dry-run]` |
| `export_stats.py` | The personal export's backlog history: `record()` appends one line per `--user` run to `data/export_stats.jsonl` (never raises — a failed write costs the statistic, not the export), `load()` reads it back skipping a half-written line, `fingerprint()`/`window_label()` key a series by user + `--read-days` + time window so a changed filter starts a new line instead of bending the old one, `series()` selects one, and `backlog()` answers each window (7/30/90 days) with the last count at or before that day, `None` where the history does not reach |
| `sync_state.py` | One read-only query against the sync server's database: `load_state_ids(db, email, kind)` returns the video IDs a user flagged `read` or `bookmark` (both live in `video_state`, told apart by its `type` column). A missing database, an unknown email or an unknown `kind` is an error, never an empty set — a typo would otherwise produce an export or a book in which nothing is marked. Shared by `export.py --user` and `ebook.load_read_ids()` |
| `ebook.py` | Ebook CLI: `select_videos()` (filter/sort/limit), `load_read_ids()` (read state from the sync DB, via `sync_state`), `partition_by_read()` (Unread/Read/all split — `main()` only passes `--read` through when `--user` is set, otherwise forces `ignore`), `collect_thumbnails()` (cached JPEG downloads), `main()` — wires them together, formats each video's raw ISO `duration` via `export._fmt_duration()`, reads only from `store.py`, writes an `.epub` via `epub_builder.build_epub()` |
| `epub_builder.py` | Builds the EPUB 3 archive from selected videos: `group_by_week()` (ISO `(year, week)` buckets), summaries first through `renderer.sanitize_summary()` (nh3 allowlist; re-nests `p > ul`, which epubcheck rejects, and repairs mismatched tags), then `xhtmlify()` and `_normalize_lists()`/`_hoist_blocks_out_of_headings()` for the XHTML content model (guarantees every emitted fragment parses as XML — escapes bare `&` left over after the named-entity pass, e.g. in `&t=` timestamp-link hrefs, before falling back to escaping the whole fragment to plain text only on genuinely malformed markup), `_covered_date_range()` (earliest/latest `published_at` across the book), `render_chapter()`, `build_epub()` (writes the ZIP with `mimetype` first and uncompressed, as EPUB readers require) |
| `ebook/` | Jinja templates + stylesheet for the EPUB: `book.css`, `chapter.xhtml.j2`, `nav.xhtml.j2`, `content.opf.j2`, `toc.ncx.j2`, `title.xhtml.j2`, `transcript.xhtml.j2` |
| `summarize.py` | Legacy all-in-one CLI (fetch + render in one pass, no store involvement) |
| `feeds.py` | Quota-free channel discovery through `youtube.com/feeds/videos.xml?channel_id=UC…`: `get_new_videos_rss()` returns videos published after `since` in the same four-key shape as `youtube_client.get_new_videos()`, or `None` when it cannot answer — fetch failed, feed empty or unparseable, or every one of its ~15 entries lies inside the window, where older ones may have been cut off (an empty list is a real "nothing new"). `<published>` only, never `<updated>`: an edited description would otherwise resurrect a years-old video as new. `get_channel()` reads a channel's title from its feed, so a `UC…` identifier needs no `channels().list`. Direct fetch first, one retry through `WEBSHARE_PROXY_URL`; thumbnails normalised to `mqdefault.jpg`, matching the API path and `ytdlp_meta` |
| `youtube_client.py` | YouTube Data API v3 wrapper (auth, subscriptions, video search, channel resolution); `has_usable_token()` answers, without side effects, whether `build_service()` could proceed without a browser — valid credentials, or expired ones with a refresh token; anything else would land in `InstalledAppFlow.run_local_server()`, which under cron blocks forever instead of failing; `uploads_playlist_id()` derives a channel's uploads playlist from its ID (`UC…` → `UU…`) instead of asking `channels().list` for a string YouTube mints by rule — that call cost 1 quota unit per channel per run, roughly half the daily budget at ~100 subscriptions and a half-hourly collect; `_get_uploads_playlist_id()` still falls back to the API for an identifier that is not a `UC…` channel ID |
| `ytdlp_meta.py` | Quota-free single-video metadata via yt-dlp (`get_video_metadata()`): runs `python -m yt_dlp --dump-single-json` on the watch URL in a subprocess — no OAuth token, no API key, no quota — and returns the same dict shape as `youtube_client.get_video_by_id()`, or `None` on any failure (never raises, because the caller's whole point is the fallback). Direct fetch first, one retry through `WEBSHARE_PROXY_URL` (mirrors `transcripts.py`: the server's own IP is the blocked one); seconds → ISO 8601 duration, `release_timestamp`/`timestamp` → `published_at` with `upload_date` (midnight UTC) as last resort; the thumbnail URL is built as `i.ytimg.com/vi/<id>/mqdefault.jpg` rather than taken from yt-dlp's `.webp` field, which `ebook.collect_thumbnails()` could not use |
| `transcripts.py` | `youtube-transcript-api` wrapper; language priority via `TRANSCRIPT_LANGS` (default: de,en); handles ip_blocked / rate_limited / country_blocked errors; on `ip_blocked` retries once via the configured proxy; `VideoUnplayable` is only classified as `country_blocked` when the reason mentions "country"/"region" — on `country_blocked`, retries once with a country-pinned Webshare proxy (`PROXY_FALLBACK_COUNTRY`, default: DE) if `WEBSHARE_PROXY_URL` is set; other `VideoUnplayable` causes fall to `unavailable` (retryable); `requests.exceptions.ProxyError` and `ConnectionError` are caught and mapped to `unavailable`; logs proxy configuration on startup |
| `openrouter.py` | LLM client (OpenRouter by default, or any OpenAI-compatible endpoint); summary language via `SUMMARY_LANG`; `summarize_video(..., channel=)` hands the channel title to the model as its own delimited `<channel>` element (`_channel_line()`, omitted entirely when there is none — an empty element would invite an invented name) and the prompt tells it to name the presenter by that name, or by a more specific one the transcript itself supplies, instead of "der Creator"/"der Sprecher"/"der YouTuber"; both the single-pass and the synthesis message carry it, since the final prose comes out of the latter; structured prompt enforces chronological sections scaled to video length, written as flowing prose (`<p>`) with bullets only for genuine enumerations, timestamp links placed inline after each relevant sentence; extracts 2–3 tags from the `<!-- tags: ... -->` comment appended by the model, then strips markdown fences from what remains (`_clean_response()` — tags first, opening and closing fence independently, because tag extraction may already have consumed the closing fence); tags then go through `tags.canonicalize()`, so only vocabulary entries are ever stored and off-list suggestions are counted in `data/tag_candidates.json` instead; returns `(summary_html, tags_list)` tuple; `max_tokens=16384`; `_validate_summary()` raises `SummaryRejected` when the response hit the output cap (`finish_reason == "length"`), degenerated into a repetition loop (30+ consecutive repeats of one token), or ends mid-sentence rather than on a closing `>` — the last check catches a model that stops early (`finish_reason == "stop"`, not `"length"`) without ever exhausting the token budget, which the cap check misses — `collect.py` then stores the video without a summary and `repair.py` leaves the existing one unchanged, so garbage is never written to the store; the chunk (map) pass rejects repetition loops too but tolerates `length`, since the 2048-token chunk budget can legitimately be hit and truncated key points still feed the synthesis pass; `_unwrap_json_response()` returns the HTML from a response the model wrapped in a JSON object (`{"summary": "<div>..."}`) — matched leniently, because `_parse_tags()` cuts the response at the `<!-- tags: -->` comment and takes the object's closing quote and brace with it, so `json.loads()` alone would fail; `repair_summary_html()` runs the textual repair passes in order — `_unwrap_json_response()` first (its escaped newlines would otherwise look like running text to the paragraph pass), then `renderer._repair_broken_ts_links()` and `renderer._close_unterminated_ts_tags()` (until those anchors are rebuilt, the text they swallowed sits inside an attribute value where no later pass can see it), then `renderer._close_labelless_ts_links()` and `renderer._relabel_unlabelled_ts_links()`, then `_fix_timestamp_links()`, `_drop_stray_paragraph_ends()` and `_dedup_timestamps()` — and is the single entry point shared with `repair.py --fix-links`; `_fix_timestamp_links()` repairs the links the model gets wrong — it recomputes each `t=` from the visible `MM:SS` label (models write `t=202` or `t=2` for "02:02" instead of `t=122`; the label comes from a real transcript marker and is authoritative) and normalises anchors closed with the wrong tag (`</p>`, `</h3>`, `</article>`, or a stray `</` before `</a>`, which would otherwise leave the link open and swallow the rest of the card), keeping a structural closing tag after the inserted `</a>` and dropping non-structural ones; runs before `_drop_stray_paragraph_ends()`, which deletes a `</p>` whenever the next thing in the fragment is running text rather than a tag or the end of the summary (models close the paragraph after every timestamp link, before the sentence-final period, which makes the browser start a new block for each following sentence — visibly a paragraph beginning with a dot) and collapses runs of consecutive `</p>`; both run before `_dedup_timestamps()`, which compares `t=` values and whose block regex would otherwise stop at a stray `</p>` |
| `renderer.py` | Jinja2 renderer; writes the final HTML file; accepts `lang=`, `full_url=` and `backlog=` kwargs (`full_url` marks an export as a filtered view: header link, share targets, `singleNotFound` fallback; `backlog` embeds the personal export's history as `const BACKLOG` and marks its video count hoverable); sanitizes summaries at render time via `_sanitize_summary()` — strips any trailing incomplete HTML tag to guard against LLM output truncated mid-tag (which would cause the browser to consume subsequent cards as an attribute value); `_repair_broken_ts_links()` rebuilds anchors whose href the model never closed (`<a href="...&t=1:02` and the sentence simply runs on, or a closer carrying the forgotten attributes: `<a href="...&t=1:34</p class="ts-link">.</a>`) — the `M:SS` in the href becomes both the second count and the link label, and text stranded inside the bogus closer is kept after the anchor; without it the unclosed attribute swallows whole sentences, which then never reach the reader; `_close_unterminated_ts_tags()` closes an `<a>` tag that never reaches its `>` (href closed but the bracket forgotten, a second URL pasted into `class`, an attribute cut off mid-word) — the run up to the next `<` is read as attributes by the parser, so the sentence in it never reaches the card; the timestamp in the href supplies href and label, attribute debris is dropped, and what remains is put back as prose (a tag carrying no timestamp is left alone); `_close_labelless_ts_links()` closes an anchor the model opened and never closed — the tag is complete but no label and no `</a>` follow, so the browser runs the link to the next tag and whole sentences render in link styling; the href's timestamp becomes the label and the anchor closes where it should have (anchors whose content already reads as a label are left to `_fix_timestamp_links()`, which normalises a wrong closing tag); `_relabel_unlabelled_ts_links()` gives a timestamp anchor whose text is the sentence's period (or empty) its label back, derived from the href — the reverse of `_fix_timestamp_links()`'s rule that a real label beats a wrong `t=`, because here there is no label to prefer, and a colon-form `t=1:20` is ignored by YouTube so the link would jump to the start of the video; the punctuation moves out of the anchor to where it belongs; both are imported by `openrouter.repair_summary_html()` rather than duplicated; `_seconds_to_label()` formats the M:SS / H:MM:SS label and is what `openrouter._format_duration()` uses; `_split_export_data()` sorts videos newest-first (published_at desc, video_id desc tie-break) and splits them into a summary-free `index` plus a list of summary chunks (`EXPORT_CHUNK_SIZE` = 50 videos each; chunk k covers index positions `[k*50, (k+1)*50)`); `_export_manifest(index, chunks)` builds the update manifest (`generated_at`, `video_count`, `newest_id`, `newest_published_at`, `summary_count`, `summary_digest`, `recent_ids`) from the newest-first index plus the summary chunks — the digest is what lets the page tell a changed summary from an unchanged one at equal counts, and `recent_ids` (by `collected_at`, capped at `EXPORT_RECENT_IDS`) what lets it count arrivals separately from departures; `_total_duration_label()` sums the cards' duration strings (via `_duration_seconds()`/`_format_total_duration()`, the mirrors of the JS `durationSeconds()`/`formatTotalDuration()` — pinned against each other by `tests/test_export_total_duration.py`) into the header's `dd:hh:mm` runtime, empty when nothing in the selection carries a duration; `render_export_html()` embeds it as `const TOTAL_DURATION` and the manifest as `const MANIFEST` and writes the manifest to `<output_path>.meta.json` after the HTML file, pre-renders the first `EXPORT_FIRST_PAGE` (20) cards as static HTML (`_esc_html()` mirrors the JS `escHtml()` byte-for-byte, `_summary_preview()` mirrors the JS first-paragraph/"more" split) and embeds `index` plus the chunk array separately, gzip+base64 (`compress=True`, default) or as one plain `{index, summaries}` JS object literal with no chunking (`compress=False`) |
| `i18n.py` | UI string dicts for `de` (default) and `en`; `get_strings(lang)` and `resolve_lang(lang)` helpers |
| `template.html.j2` | Self-contained HTML template with embedded dark-theme CSS; read/bookmark buttons on each card, state persisted in browser `localStorage` (migrated automatically from the old cookie-based storage on first load, which is then cleared); strings from `i18n.py` via Jinja2 `{{ t.xxx }}` |
| `export.html.j2` | Export template: dark-theme CSS, controls bar (all filter controls carry `autocomplete="off"`), a Jinja macro `card(v)` pre-renders the first `EXPORT_FIRST_PAGE` (20) cards as static HTML directly into the grid — it must stay markup-identical to the JS `buildCard()`, enforced by `tests/test_export_prerender.py`; document order is header, controls bar, pre-rendered grid, read/bookmark hydration script, sync-boot script (only when `sync_url` is set), pagination, footer, main UI script, and finally the data-blob script (last element before `</body>`, schedules `bootstrap()` via `requestAnimationFrame`); `bootstrap()` decodes the gzip-compressed `index` plus chunk 0 (covers the pre-rendered first page) via `DecompressionStream`, sets `dataReady`, fills dropdowns, and renders — later chunks are decoded on demand (`ensureChunk`/`ensureChunks`) and prefetched during idle time, with `getSummary(v)` looked up per-video instead of a flat `SUMMARIES` map; a text search waits for every chunk to be decoded (`allChunksReady()`/`ensureAllChunks()`) before filtering, so it can't miss a match in an undecoded chunk; `search_text` is computed lazily per video on first search (`getSearchText()`), not precomputed server-side; a `dataReady` flag suppresses sync-driven re-renders until the index and chunk 0 are decoded, though the read/bookmark state merge itself still happens; if the visitor's resolved language differs from the export's embedded default, `bootstrap()` clears the pre-rendered grid so `applyLang()` rebuilds it; preview embeds are click-to-load facades (`loadEmbed()`); each filter and sort control has a visible label (`ctrl-label`); the sort dropdown offers `date-desc`/`date-asc` (publish date), `added-desc` (store `collected_at`, tie-broken by publish date descending, falling back to `published_at` when the field is absent), `channel`, and `title` — `applyLang()` writes the option labels by index, so inserting an option means shifting `sortOpts[n]` there too; date filter accepts a "published after" date and filters client-side via ISO string comparison; `#page-meta` is three spans — `#meta-generated`, `#video-count`, `#meta-duration` — rather than one line of text, because the video count has to survive `applyLang()` as its own node for the backlog tooltip to stay on it (`backlogTooltipText()`, pure and unit-tested, plus `setBacklogTooltip()`/`setBacklogPanel()`/`toggleBacklogPanel()`, wired at the end of the main script rather than in `bootstrap()`: the count is pre-rendered, and an affordance that is dead while a 12 MB blob decodes is one nobody tries twice — the click wiring runs *before* `setBacklogTooltip()`, which reads the panel's state for `aria-expanded`); the same text is both a `title` tooltip and the tap-opened `#backlog-panel`, since a touch device has no hover; `backlogLang` (not `currentLang`) is what both read, because the tooltip is written from `detectLang()` long before `applyLang()` first runs; the class `has-backlog` and its dotted underline appear only when `const BACKLOG` is not null, i.e. on a personal export; the header line (`#page-meta`) states the whole archive's runtime behind its video count and the results count (`#results-count`) that of the current selection, both as `dd:hh:mm` behind the video count (`totalDurationSeconds()`/`formatTotalDuration()`, both pure and pinned by `tests/test_export_total_duration.py`) — it reads the same display strings the length filter does, so a video without a duration contributes nothing and the sum is a lower bound, and the days field is not capped at two digits; the length filter (`#length-filter`) offers the upper bounds in `LENGTH_BUCKETS` (5/10/20/30/60 minutes, one `<option>` each after "Alle" — `applyLang()` labels them from that list, so a new bucket means a new option in the markup and nothing else) and cuts strictly (`< 5 Min` excludes exactly 5:00) against `durationSeconds(v)`, which parses the index's display string ("7:12", "1:02:03") once per video and caches it; a video whose duration is unknown (~4 % of the store, `duration` empty) can never satisfy "shorter than N" and therefore drops out of every narrowed view, staying visible under "Alle"; tag chips on cards are clickable and toggle the tag filter; channel name in card meta is clickable and toggles the channel filter (`setChannelFilter()`); read/bookmark state persisted in `localStorage` (migrated from the old cookie on first load, which is then cleared — cookies cap out around 340 IDs at the 4096-byte limit); language (`yt_lang`) state still in a cookie (small, unaffected); language selector in page header with flag emoji (🇩🇪/🇬🇧), priority: cookie → browser language → embedded default; sync bar shows "Ingest" button when `can_ingest` is true; an update banner (`#update-banner`) polls the `MANIFEST_URL` sidecar every `UPDATE_POLL_MS` (5 min) when the page is served over http(s), compares `generated_at` against the embedded `const MANIFEST` and offers a reload (`checkForUpdate()`/`updateBannerKey()`/`arrivedVideos()`/`renderUpdateBanner()`/`dismissUpdate()`, re-rendered from `applyLang()`; `updateBannerKey()` alone decides the wording and is pure, as is `arrivedVideos()`, so every branch is unit-testable); a sync-boot script harvests the magic-link token and fires `/api/whoami` and `/api/state` in parallel into `window.__syncBoot` before the main script runs, so `initSync()` (called at the end of `bootstrap()`) can consume already-in-flight responses instead of starting them late; when `sync_url` is set, `warnIfInsecure()` fires a single localized `alert()` on load unless `loginRedirectAccepted()` holds — that mirrors the server's `_valid_redirect_uri()` (any `file://` URI, or page origin === `SYNC_URL` origin); http-page/https-server gets the "no HTTPS" text, everything else the "wrong origin" text naming the expected origin |
| `state.py` | Reads/writes `last_run.json` (channel_id → last checked ISO timestamp) |
| `send_mail.py` | Standalone script; sends an HTML file as an email via SMTP_SSL |
| `sync-server/sync_server.py` | Standalone Flask sync service: magic-link auth (supports STARTTLS port 587 and SSL port 465), per-user read/bookmark state in SQLite, last-write-wins merge; `POST /api/ingest` appends video ID to `INGEST_QUEUE` file and returns 202; `/api/whoami` returns `can_ingest` flag |
| `userscript/yt-ingest.user.js` | Violentmonkey/Tampermonkey script: a button on YouTube that queues the current video, by driving the Ingest field of the logged-in archive page instead of holding a token of its own. Two host-specific lines (`ARCHIVE_URL`, the archive `@match`) |
| `ingest_worker.sh` | Cron script that drains `INGEST_QUEUE` by running `collect.py --video <id>` for each entry; re-exports `$EXPORT_OUTPUT` afterwards, honouring `EXPORT_USER`/`EXPORT_READ_DAYS` exactly as `collect.sh` does; logs to `data/ingest_worker.log`; schedule every minute |

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

# ── Optional: title blacklist (collect.py) ────────────────────────────────────
# Comma-separated regex patterns, case-insensitive, matched anywhere in the
# title. A hit is skipped before transcript and LLM, on scheduled runs and on
# --video/Ingest alike. Already-stored hits: collect.py --prune-filtered.
# VIDEO_TITLE_FILTERS=Letsplay,Let.?s ?Play

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
