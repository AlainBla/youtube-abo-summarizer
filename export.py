#!/usr/bin/env python3
"""Export stored video summaries to a self-contained HTML file.

The output is a single portable HTML file with client-side search (title +
summary), sort (date/channel/title), and pagination (20 items per page).
Intended for browsing a larger archive in a browser; works fully offline.

Usage:
  python export.py [--hours N | --all] [--channel CHANNEL_ID] [--videos ID,...] [--output export.html]
"""

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone

import renderer
import store


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
        "--channel",
        metavar="CHANNEL_ID",
        default=None,
        help="Restrict export to a single channel (channel ID).",
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
            "duration": _fmt_duration(e.get("duration")),
            "thumbnail_url": e["thumbnail_url"],
            "summary": e.get("summary"),
            "summary_model": e.get("summary_model") if args.show_model else None,
            "transcript_error": e.get("transcript_error"),
            "tags": e.get("tags") or [],
        }
        for e in entries
    ]

    print(f"Rendering {len(videos)} video(s) → {output_path}")
    renderer.render_export_html(
        videos, output_path,
        lang=args.lang or "de",
        sync_url=args.sync_url,
        show_embed=not args.thumbnail,
    )
    print("Done.")


if __name__ == "__main__":
    main()
