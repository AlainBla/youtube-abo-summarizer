#!/usr/bin/env python3
"""Render stored video summaries into an EPUB 3 ebook.

Reads only from the local store (and optionally the sync server's database
for a user's read state) -- no YouTube and no LLM calls.
"""

import argparse
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

import epub_builder
import export
import i18n as i18n_module
import store

DEFAULT_LIMIT = 100
DEFAULT_SYNC_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync-server", "sync.db")
# __file__-relative, matching store.py's DATA_DIR -- a cwd-relative path here
# would write thumbnails somewhere other than the rest of data/ whenever this
# is invoked from outside the repo root (e.g. cron), silently defeating the
# on-disk cache (collect_thumbnails() would never see a hit).
THUMBNAIL_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "thumbnails")
PART_TITLE_KEYS = {"unread": "book_part_unread", "read": "book_part_read", "all": "book_part_all"}


def select_videos(entries, channel=None, videos=None, tag=None, limit=DEFAULT_LIMIT):
    """Filter, order newest-first and cut to `limit` (0 = no limit).

    The cut happens before any grouping, so "the newest 100" means exactly
    that -- not "100 per week".
    """
    picked = entries
    if videos:
        wanted = set(videos)
        picked = [v for v in picked if v["video_id"] in wanted]
    if channel:
        picked = [v for v in picked if v.get("channel_id") == channel]
    if tag:
        picked = [v for v in picked if tag in (v.get("tags") or [])]
    picked = sorted(
        picked,
        key=lambda v: ((v.get("published_at") or ""), v.get("video_id") or ""),
        reverse=True,
    )
    return picked[:limit] if limit else picked


def load_read_ids(sync_db, email):
    """Video IDs this user has marked as read, straight from the sync database.

    Read-only. An unknown email is an error rather than an empty set -- a typo
    would otherwise silently produce a book in which nothing is marked read.
    """
    if not os.path.exists(sync_db):
        sys.exit(f"Error: sync database not found: {sync_db}")
    db = sqlite3.connect(f"file:{sync_db}?mode=ro", uri=True)
    try:
        row = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            sys.exit(f"Error: no sync user with email '{email}'.")
        rows = db.execute(
            "SELECT video_id FROM video_state WHERE user_id = ? AND type = 'read' AND value = 1",
            (row[0],),
        ).fetchall()
    finally:
        db.close()
    return {r[0] for r in rows}


def partition_by_read(videos, read_ids, mode):
    """Split into (part_key, videos) pairs according to --read.

    "ignore" always yields a single "all" part. Otherwise videos are split
    into "unread" and "read" (in that order); "drop" mode omits the "read"
    part entirely. Empty parts are always dropped from the result.
    """
    if mode == "ignore":
        parts = [("all", videos)]
    else:
        unread = [v for v in videos if v["video_id"] not in read_ids]
        read = [v for v in videos if v["video_id"] in read_ids]
        parts = [("unread", unread)] if mode == "drop" else [("unread", unread), ("read", read)]
    return [(key, vs) for key, vs in parts if vs]


def _default_fetch(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def collect_thumbnails(videos, cache_dir, fetch=None, max_bytes=2_000_000):
    """Download (or reuse from disk) each video's thumbnail as raw JPEG bytes.

    Returns ({video_id: jpeg bytes}, failure_count). A thumbnail is skipped
    -- not raised -- on a non-https URL, a fetch error, or an oversized
    payload, because a book missing one thumbnail is still a book. Downloads
    land in `cache_dir` as "<video_id>.jpg" so a rebuild works with no
    network at all once the cache is warm.
    """
    fetch = fetch or _default_fetch
    os.makedirs(cache_dir, exist_ok=True)
    images, failed = {}, 0
    for v in videos:
        video_id = v["video_id"]
        path = os.path.join(cache_dir, "%s.jpg" % video_id)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            # A 0-byte file can only be the debris of a killed write (real
            # downloads are validated non-empty before ever being written,
            # see below) -- treat it as a miss and refetch rather than
            # trusting it, the same as a truncated one would be.
            with open(path, "rb") as f:
                images[video_id] = f.read()
            continue
        url = v.get("thumbnail_url") or ""
        if not url.startswith("https://"):
            failed += 1
            continue
        try:
            blob = fetch(url)
        except Exception:
            failed += 1
            continue
        if not blob or len(blob) > max_bytes:
            failed += 1
            continue
        # Write to a temp file and rename into place so a process killed
        # mid-write can never leave a truncated "<video_id>.jpg" behind for
        # a later run to load as if it were a complete, valid image.
        tmp_path = path + ".tmp"
        with open(tmp_path, "wb") as f:
            f.write(blob)
        os.replace(tmp_path, path)
        images[video_id] = blob
    return images, failed


def _limit_type(value):
    """argparse type for --limit: reject negatives.

    select_videos() does `picked[:limit]`; a negative limit would slice from
    the wrong end (`picked[:-5]` drops the newest videos instead of keeping
    them), silently building the wrong book. Fail fast at the CLI boundary
    instead.
    """
    n = int(value)
    if n < 0:
        raise argparse.ArgumentTypeError("--limit must not be negative")
    return n


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Build an EPUB ebook from stored summaries. "
                     "If neither --hours nor --all is given, behaves like --all "
                     "(all videos in the store, still capped by --limit).")
    window = parser.add_mutually_exclusive_group()
    window.add_argument("--hours", type=int, help="only videos published in the last N hours")
    window.add_argument("--all", action="store_true",
                        help="all videos in the store (also the default when neither "
                             "--hours nor --all is given)")
    parser.add_argument("--channel", help="restrict to one channel ID")
    parser.add_argument("--videos", help="comma-separated video IDs")
    parser.add_argument("--tag", help="restrict to videos carrying this tag")
    parser.add_argument("--limit", type=_limit_type, default=DEFAULT_LIMIT,
                        help="keep only the N newest videos that survive the read filter (0 = no limit)")
    parser.add_argument("--user", help="email whose read state is taken from the sync database")
    parser.add_argument("--sync-db", default=DEFAULT_SYNC_DB, help="path to the sync server database")
    parser.add_argument("--include-untranscribed", action="store_true",
                        help="also include videos with no summary (their transcript could "
                             "not be fetched); left out by default")
    parser.add_argument("--sort", choices=list(epub_builder.SORT_MODES), default="added-desc",
                        help="order of the book: most recently added to the store first "
                             "(default), newest publish date first, or chronological")
    parser.add_argument("--read", choices=["split", "drop", "ignore"], default="split",
                        help="read videos: move to the back, drop them, or ignore the state")
    parser.add_argument("--no-thumbnails", dest="thumbnails", action="store_false",
                        help="do not download and embed thumbnails")
    parser.add_argument("--no-transcripts", dest="transcripts", action="store_false",
                        help="do not append transcripts")
    parser.add_argument("--output", help="output file (default: ebook_YYYY-MM-DD_HH-MM.epub)")
    parser.add_argument("--lang", choices=["de", "en"], default="de")
    return parser.parse_args(argv)


def main():
    args = parse_args()
    lang = i18n_module.resolve_lang(args.lang)
    strings = i18n_module.get_strings(lang)

    if args.hours is not None:
        # `is not None`, not truthiness: `--hours 0` must mean "the last 0
        # hours" (i.e. essentially nothing), not silently fall through to
        # "all videos in the store" the way a bare `if args.hours:` would.
        since = datetime.now(tz=timezone.utc) - timedelta(hours=args.hours)
        entries = store.get_videos_since(since, with_transcripts=False)
    else:
        entries = store.get_all_videos(with_transcripts=False)

    video_ids = [v.strip() for v in args.videos.split(",")] if args.videos else None
    # Deliberately unlimited here: --limit is applied *after* the read filter
    # below, so "--limit 50 --read drop" means "the 50 newest unread videos",
    # not "the unread ones among the 50 newest" -- which would quietly produce
    # a nearly empty book on a mostly-read store.
    selected = select_videos(entries, channel=args.channel, videos=video_ids,
                              tag=args.tag, limit=0)
    if not selected:
        print("No videos to put in the book.")
        sys.exit(0)

    # A video whose transcript could not be fetched has no summary either, so
    # its chapter would be nothing but a "no transcript" notice. Filter here,
    # before --read and --limit, so the limit counts real chapters.
    if not args.include_untranscribed:
        with_summary = [v for v in selected if v.get("summary")]
        if not with_summary:
            print("No videos with a summary to put in the book "
                  "(pass --include-untranscribed to include the rest).")
            sys.exit(0)
        selected = with_summary

    # Store rows carry the raw ISO-8601 duration ("PT1H2M3S") -- chapter.xhtml.j2
    # just prints it, so format it the same way export.py does before it ever
    # reaches the template. Copy each dict rather than mutate the row in
    # place, since `entries` may be a cached/shared list.
    selected = [dict(v, duration=export._fmt_duration(v.get("duration"))) for v in selected]

    # The "Unread"/"Read" split only makes sense once a user's read state has
    # actually been loaded. Without --user, read_ids is always empty, so
    # "split" mode would still produce a single part -- but labelled
    # "Unread" instead of the generic "Videos" -- misrepresenting a book that
    # was never partitioned by anyone's read state.
    read_ids = load_read_ids(args.sync_db, args.user) if args.user else set()
    read_mode = args.read if args.user else "ignore"

    # Apply --limit to what survives the read filter, newest first, before
    # partitioning for real: under "drop" that is the unread videos, under
    # "split"/"ignore" it is everything, so the limit always counts the
    # videos that actually end up in the book.
    surviving = [v for _, videos in partition_by_read(selected, read_ids, read_mode)
                 for v in videos]
    # Sort before cutting, so --limit keeps the top N of the chosen order:
    # with --sort added-desc that is the most recently added videos, not the
    # most recently published ones.
    surviving.sort(key=epub_builder.sort_key(args.sort), reverse=args.sort != "date-asc")
    if args.limit:
        surviving = surviving[:args.limit]

    part_pairs = partition_by_read(surviving, read_ids, read_mode)
    parts = []
    kept = []
    for key, videos in part_pairs:
        parts.append({
            "key": key,
            "title": strings[PART_TITLE_KEYS[key]],
            "weeks": epub_builder.group_by_week(videos, order=args.sort),
        })
        kept.extend(videos)

    if not parts:
        # Every selected video was filtered out by --read (e.g. --read drop
        # with nothing unread left). build_epub() would otherwise emit an
        # EPUB with an empty NCX navMap, which is invalid per the DTD -- a
        # book nobody can open, produced silently. Bail out instead.
        print("No videos left after applying --read — nothing to build.")
        sys.exit(0)

    # Thumbnails and transcripts must only be gathered for videos that
    # actually ended up in `parts` -- `selected` still holds videos dropped
    # by --read (e.g. already-read ones under --read drop). build_epub()
    # embeds every image/transcript it is handed regardless of whether a
    # chapter links to it, and content.opf.j2 puts every transcript into the
    # spine, so a dropped video's transcript page would otherwise still ship
    # in the book's reading order (linked from nav.xhtml) after a wasted
    # thumbnail fetch.
    images, failed = ({}, 0)
    if args.thumbnails:
        images, failed = collect_thumbnails(kept, THUMBNAIL_CACHE_DIR)
        if failed:
            print(f"{failed} thumbnail(s) could not be fetched — building without them.")

    transcripts = {}
    if args.transcripts:
        for v in kept:
            path = store.get_llm_transcript_path(v["video_id"])
            if path:
                transcripts[v["video_id"]] = path.read_text(encoding="utf-8")

    output = args.output or f"ebook_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.epub"
    print(f"Building {output} — {len(kept)} video(s), {len(parts)} part(s).")
    epub_builder.build_epub(parts, output, strings["book_title"], lang, strings,
                            images=images, transcripts=transcripts)
    print("Done.")


if __name__ == "__main__":
    main()
