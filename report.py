#!/usr/bin/env python3
"""Render an HTML digest from stored video summaries.

This is the report phase. Run it on whatever schedule you want a digest
(e.g. every 6 hours or once a day). It reads from video_store.json, which
is populated by collect.py, so no transcript fetching or LLM calls happen here.

Usage:
  python report.py [--hours 24] [--output summary.html] [--skip-empty] [--send-to EMAIL]
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone

import renderer
import store
from send_mail import send as send_mail


def parse_args():
    parser = argparse.ArgumentParser(
        description="Render an HTML report from the video store."
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        metavar="N",
        help="Include videos published in the last N hours (default: 24).",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        default=None,
        help="Output HTML file path (default: summary_YYYY-MM-DD_HH-MM.html).",
    )
    parser.add_argument(
        "--skip-empty",
        action="store_true",
        help="Exclude channels with no videos from the output.",
    )
    parser.add_argument(
        "--send-to",
        metavar="EMAIL",
        default=None,
        help="Send the rendered report to this email address via SMTP.",
    )
    return parser.parse_args()


def _fmt_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%B %d, %Y")
    except ValueError:
        return iso


def main():
    args = parse_args()
    since = datetime.now(tz=timezone.utc) - timedelta(hours=args.hours)
    output_path = args.output or f"summary_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.html"

    entries = store.get_videos_since(since)

    if not entries:
        print(f"No videos in store published in the last {args.hours} hour(s).")
        if not args.skip_empty:
            # Render an empty report anyway so a mail doesn't get skipped silently
            renderer.render_html([], output_path)
            print(f"Empty report written → {output_path}")
        sys.exit(0)

    # Group by channel, preserving insertion order within each channel
    channels_by_id: dict[str, dict] = {}
    for e in entries:
        cid = e["channel_id"]
        if cid not in channels_by_id:
            channels_by_id[cid] = {
                "channel_id": cid,
                "title": e["channel_title"],
                "videos": [],
            }
        channels_by_id[cid]["videos"].append({
            "video_id": e["video_id"],
            "title": e["title"],
            "published_at": _fmt_date(e["published_at"]),
            "thumbnail_url": e["thumbnail_url"],
            "summary": e["summary"],
            "transcript_error": e.get("transcript_error"),
        })

    channels_data = sorted(channels_by_id.values(), key=lambda c: c["title"].lower())

    if args.skip_empty:
        channels_data = [c for c in channels_data if c["videos"]]

    if not channels_data:
        print("No channels with videos after filtering. Nothing to render.")
        sys.exit(0)

    print(f"Rendering HTML → {output_path}")
    renderer.render_html(channels_data, output_path)

    if args.send_to:
        subject = f"YouTube Summary {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        send_mail(subject, args.send_to, output_path)

    print("Done.")


if __name__ == "__main__":
    main()
