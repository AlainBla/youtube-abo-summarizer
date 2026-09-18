#!/usr/bin/env python3
"""Export stored video summaries to a self-contained HTML file.

The output is a single portable HTML file with client-side search (title +
summary), sort (date/channel/title), and pagination (20 items per page).
Intended for browsing a larger archive in a browser; works fully offline.

Usage:
  python export.py [--hours N | --all] [--channel CHANNEL_ID] [--videos ID,...] [--output export.html]
  python export.py --all --user you@example.com [--read-days N] [--output yt.html]

With --user the output is that sync user's personal view -- unread videos, read
ones collected within --read-days, and everything bookmarked -- and the
unfiltered archive is written beside it as <output>.full.html.
"""

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import renderer
import store
import sync_state

DEFAULT_SYNC_DB = sync_state.DEFAULT_SYNC_DB
# How long a video stays in the personal export after it was marked read.
DEFAULT_READ_DAYS = 30


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export stored video summaries to a browsable HTML file."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--hours",
        type=int,
        default=168,
        metavar="N",
        help="Include videos published in the last N hours (default: 168 = 7 days).",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Include all videos in the store (no time filter).",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        default=None,
        help="Output HTML file path (default: export_YYYY-MM-DD_HH-MM.html).",
    )
    parser.add_argument(
        "--show-model",
        action="store_true",
        help="Show the LLM model name badge on each video card.",
    )
    parser.add_argument(
        "--lang",
        metavar="LANG",
        default=None,
        help="Default UI language embedded in the export: de (default) or en. "
             "Overridden by cookie or browser language preference.",
    )
    parser.add_argument(
        "--sync-url",
        metavar="URL",
        default=None,
        help="URL of the sync server to embed in the export HTML. "
             "Enables cross-browser read/bookmark sync.",
    )
    parser.add_argument(
        "--thumbnail",
        action="store_true",
        help="Show static thumbnails instead of embedded YouTube preview players.",
    )
    parser.add_argument(
        "--no-compress",
        action="store_true",
        help="Embed data uncompressed (JSON.parse) instead of gzip+base64. "
             "Use for browsers without DecompressionStream support.",
    )
    parser.add_argument(
        "--channel",
        metavar="CHANNEL_ID",
        default=None,
        help="Restrict export to a single channel (channel ID).",
    )
    parser.add_argument(
        "--user",
        metavar="EMAIL",
        default=None,
        help="Filter the export down to this sync user's unread videos, the read "
             "ones added within --read-days, and everything bookmarked. The "
             "unfiltered archive is written beside it as <output>.full.html.",
    )
    parser.add_argument(
        "--sync-db",
        metavar="PATH",
        default=DEFAULT_SYNC_DB,
        help="Path to the sync server database read by --user "
             f"(default: {DEFAULT_SYNC_DB}).",
    )
    parser.add_argument(
        "--read-days",
        type=int,
        default=DEFAULT_READ_DAYS,
        metavar="N",
        help=f"With --user: keep a read video for N days after it entered the "
             f"store (default: {DEFAULT_READ_DAYS}).",
    )
    parser.add_argument(
        "--videos",
        metavar="ID[,ID,...]",
        default=None,
        help="Comma-separated list of video IDs to include (overrides time filter).",
    )
    return parser.parse_args()


def _fmt_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%B %d, %Y")
    except ValueError:
        return iso


def _fmt_duration(iso: str | None) -> str:
    """Format an ISO 8601 duration (e.g. 'PT1H2M3S') to 'H:MM:SS' or 'M:SS'."""
    if not iso:
        return ""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return ""
    h, mins, s = (int(x) if x else 0 for x in m.groups())
    if h:
        return f"{h}:{mins:02d}:{s:02d}"
    return f"{mins}:{s:02d}"


def _added_at(v: dict) -> datetime | None:
    """When this video entered the store, as an aware datetime.

    `collected_at` is what the "date added" sort uses; rows predating that
    column fall back to the publish date. Returns None when neither field
    can be read, which the caller treats as "do not drop this".
    """
    for key in ("collected_at", "published_at"):
        raw = v.get(key)
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def filter_personal(videos, read_ids, bookmark_ids, now=None, days=DEFAULT_READ_DAYS):
    """The videos one sync user still wants to see.

    Everything survives except a video that is read, not bookmarked, and
    entered the store more than `days` ago -- which is the same thing as
    "unread, plus recently arrived read ones, plus every bookmark", written
    as the single exclusion it is. Order is left untouched.
    """
    now = now or datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(days=days)

    def keep(v):
        video_id = v.get("video_id")
        if video_id not in read_ids or video_id in bookmark_ids:
            return True
        added = _added_at(v)
        return added is None or added >= cutoff

    return [v for v in videos if keep(v)]


def full_sidecar_path(output_path: str) -> str:
    """Where the unfiltered archive goes: yt.html -> yt.full.html."""
    root, ext = os.path.splitext(output_path)
    return f"{root}.full{ext}" if ext else f"{output_path}.full.html"


def main():
    args = parse_args()
    output_path = args.output or f"export_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.html"

    if args.videos:
        video_ids = [v.strip() for v in args.videos.split(",") if v.strip()]
        all_entries = store.get_all_videos()
        id_set = set(video_ids)
        entries = [e for e in all_entries if e["video_id"] in id_set]
        label = f"{len(video_ids)} video ID(s)"
    elif args.all:
        entries = store.get_all_videos()
        label = "all videos"
    else:
        since = datetime.now(tz=timezone.utc) - timedelta(hours=args.hours)
        entries = store.get_videos_since(since)
        label = f"last {args.hours} hour(s)"

    if args.channel:
        entries = [e for e in entries if e["channel_id"] == args.channel]
        label += f", channel {args.channel}"

    if not entries:
        print(f"No videos in store ({label}).")
        sys.exit(0)

    videos = [
        {
            "video_id": e["video_id"],
            "channel_id": e["channel_id"],
            "channel_title": e["channel_title"],
            "title": e["title"],
            "published_at": e["published_at"],
            "published_at_display": _fmt_date(e["published_at"]),
            "collected_at": e.get("collected_at"),
            "duration": _fmt_duration(e.get("duration")),
            "thumbnail_url": e["thumbnail_url"],
            "summary": e.get("summary"),
            "summary_model": e.get("summary_model") if args.show_model else None,
            "transcript_error": e.get("transcript_error"),
            "tags": e.get("tags") or [],
        }
        for e in entries
    ]

    def render(selection, path, full_url=None):
        renderer.render_export_html(
            selection, path,
            lang=args.lang or "de",
            sync_url=args.sync_url,
            show_embed=not args.thumbnail,
            compress=not args.no_compress,
            full_url=full_url,
        )

    if args.user:
        read_ids = sync_state.load_state_ids(args.sync_db, args.user, "read")
        bookmark_ids = sync_state.load_state_ids(args.sync_db, args.user, "bookmark")
        personal = filter_personal(videos, read_ids, bookmark_ids, days=args.read_days)
        full_path = full_sidecar_path(output_path)
        # The full archive is written first: the filtered page links to it, so
        # it must already be there by the time that page can be opened. Both
        # get their own .meta.json sidecar, so both notice their own updates.
        print(f"Rendering {len(videos)} video(s) → {full_path}")
        render(videos, full_path)
        print(f"Rendering {len(personal)} of {len(videos)} video(s) "
              f"for {args.user} → {output_path}")
        render(personal, output_path, full_url=os.path.basename(full_path))
    else:
        print(f"Rendering {len(videos)} video(s) → {output_path}")
        render(videos, output_path)
    print("Done.")


if __name__ == "__main__":
    main()
