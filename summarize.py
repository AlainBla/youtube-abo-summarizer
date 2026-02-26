#!/usr/bin/env python3
"""YouTube subscription summarizer.

Usage:
  # Pull from OAuth subscriptions
  python summarize.py --auth [--hours 24] [--output summary.html]

  # Explicit channels (IDs, handles, or URLs)
  python summarize.py UC123abc UC456def [--hours 24] [--output summary.html]
  python summarize.py --file channels.txt [--hours 24] [--output summary.html]
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from googleapiclient.errors import HttpError

import state
import transcripts as tr
import openrouter
import renderer
from youtube_client import build_service, get_subscribed_channels, get_new_videos, resolve_channel_id

load_dotenv()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Summarize new YouTube videos from subscribed or explicit channels."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--auth",
        action="store_true",
        help="Use OAuth to pull channels from your YouTube subscriptions.",
    )
    source.add_argument(
        "--file",
        metavar="FILE",
        help="Path to a text file with one channel ID/handle/URL per line.",
    )
    parser.add_argument(
        "channels",
        nargs="*",
        metavar="CHANNEL",
        help="Channel IDs, handles, or URLs (positional, used when not using --auth or --file).",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=None,
        metavar="N",
        help="Look back N hours. Overrides the persisted last-run timestamp.",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        default=None,
        help="Output HTML file path (default: summary_YYYY-MM-DD.html).",
    )
    parser.add_argument(
        "--skip-empty",
        action="store_true",
        help="Exclude channels with no videos from the output.",
    )
    return parser.parse_args()


def resolve_since(channel_id: str, hours: int | None) -> datetime:
    if hours is not None:
        return datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    last = state.get_last_run(channel_id)
    if last:
        return last
    # Default: 24 hours back on first run
    return datetime.now(tz=timezone.utc) - timedelta(hours=24)


def load_channel_identifiers_from_file(path: str) -> list[str]:
    if not os.path.exists(path):
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path) as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def main():
    args = parse_args()

    model = os.environ.get("OPENROUTER_MODEL", "gpt-oss-20b")
    output_path = args.output or f"summary_{datetime.now().strftime('%Y-%m-%d')}.html"

    # --- Resolve channel list ---
    service = None

    if args.auth:
        print("Authenticating with YouTube...")
        service = build_service()
        print("Fetching subscriptions...")
        channels = get_subscribed_channels(service)
        print(f"Found {len(channels)} subscribed channels.")
    else:
        if args.file:
            identifiers = load_channel_identifiers_from_file(args.file)
        elif args.channels:
            identifiers = args.channels
        else:
            print(
                "Error: provide --auth, --file, or channel identifiers as arguments.",
                file=sys.stderr,
            )
            sys.exit(1)

        service = build_service()
        channels = []
        for ident in identifiers:
            resolved = resolve_channel_id(service, ident)
            if resolved:
                channels.append(resolved)
            else:
                print(f"Warning: could not resolve channel '{ident}', skipping.", file=sys.stderr)

    if not channels:
        print("No channels to process. Exiting.")
        sys.exit(0)

    # --- Fetch videos, transcripts, summaries ---
    channels_data = []
    now = datetime.now(tz=timezone.utc)

    for ch in channels:
        channel_id = ch["channel_id"]
        title = ch["title"]
        since = resolve_since(channel_id, args.hours)

        print(f"\n[{title}] Fetching videos since {since.strftime('%Y-%m-%d %H:%M')} UTC...")
        try:
            videos = get_new_videos(service, channel_id, since)
        except HttpError as e:
            if e.status_code == 403 and "quotaExceeded" in str(e):
                print("  YouTube API quota exceeded — stopping early.", file=sys.stderr)
                break
            print(f"  API error: {e}", file=sys.stderr)
            continue
        print(f"  {len(videos)} new video(s).")

        processed_videos = []
        for video in videos:
            vid_id = video["video_id"]
            vid_title = video["title"]
            print(f"  → {vid_title}")

            transcript, transcript_error = tr.get_transcript(vid_id)
            time.sleep(5)
            if transcript:
                print(f"    Summarizing via {model}...")
                summary, _tags = openrouter.summarize_video(vid_id, vid_title, transcript, model)
            else:
                if not transcript_error or transcript_error == "unavailable":
                    print("    No transcript available.")
                elif transcript_error == "country_blocked":
                    print("    Video in dieser Region gesperrt — kein Transkript.")
                summary = None

            processed_videos.append(
                {
                    "video_id": vid_id,
                    "title": vid_title,
                    "published_at": _fmt_date(video["published_at"]),
                    "thumbnail_url": video["thumbnail_url"],
                    "summary": summary,
                    "transcript_error": transcript_error,
                }
            )

        channels_data.append(
            {
                "channel_id": channel_id,
                "title": title,
                "videos": processed_videos,
            }
        )

        # Update state only when not using --hours (so last-run advances)
        if args.hours is None:
            state.set_last_run(channel_id, now)

    # --- Render HTML ---
    if args.skip_empty:
        channels_data = [ch for ch in channels_data if ch["videos"]]

    print(f"\nRendering HTML → {output_path}")
    renderer.render_html(channels_data, output_path)
    print("Done.")


def _fmt_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%B %d, %Y")
    except ValueError:
        return iso


if __name__ == "__main__":
    main()
