#!/usr/bin/env python3
"""Render an HTML digest from stored video summaries.

This is the report phase. Run it on whatever schedule you want a digest
(e.g. every 6 hours or once a day). It reads from video_store.json, which
is populated by collect.py, so no transcript fetching or LLM calls happen here.

Usage:
  python report.py [--hours 24] [--output summary.html] [--skip-empty] [--send-to EMAIL]
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

import openrouter
import renderer
import store
import transcripts as tr
from send_mail import send as send_mail

load_dotenv()


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


_RETRYABLE_ERRORS = {"unavailable", "rate_limited", "ip_blocked", None}


def _retry_missing_transcripts(entries: list[dict], model: str) -> None:
    """For entries without a summary, attempt to fetch the transcript again.

    On success, generates a summary and persists both to the store in-place.
    Skips country_blocked videos (structural restriction, unlikely to change).
    """
    for entry in entries:
        if entry.get("summary"):
            continue
        if entry.get("transcript_error") not in _RETRYABLE_ERRORS:
            continue

        vid_id = entry["video_id"]
        vid_title = entry["title"]
        print(f"  Retrying transcript for: {vid_title}")
        transcript, transcript_error = tr.get_transcript(vid_id)
        time.sleep(2)

        summary = None
        if transcript:
            print(f"    Transcript found — summarizing via {model}...")
            summary = openrouter.summarize_video(vid_id, vid_title, transcript, model)

        store.update_video_with_summary(vid_id, transcript, summary, transcript_error,
                                        summary_model=model if summary else None)

        # Update the in-memory entry so the renderer sees the fresh data
        entry["summary"] = summary
        entry["transcript_error"] = transcript_error
        entry["summary_model"] = model if summary else None


def main():
    args = parse_args()
    model = os.environ.get("OPENROUTER_MODEL", "gpt-oss-20b")
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

    # Retry transcript fetching for videos that previously had no transcript
    print("Checking for missing transcripts...")
    _retry_missing_transcripts(entries, model)

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
            "duration": _fmt_duration(e.get("duration")),
            "thumbnail_url": e["thumbnail_url"],
            "summary": e["summary"],
            "summary_model": e.get("summary_model"),
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
