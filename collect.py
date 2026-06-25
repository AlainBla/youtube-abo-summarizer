#!/usr/bin/env python3
"""Fetch new videos, pull transcripts, generate summaries, persist to data/.

This is the collection phase. Run it frequently (e.g. every hour or 15 minutes).
Use report.py to render an HTML digest from the stored data without any
YouTube API calls or LLM usage.

Storage layout (see store.py):
  data/videos.db              — SQLite metadata
  data/transcripts/<id>.txt   — raw transcript
  data/summaries/<id>.html    — HTML-fragment summary

Usage:
  # Pull from OAuth subscriptions
  python collect.py --auth [--hours N]

  # Explicit channels (IDs, handles, or URLs)
  python collect.py UC123abc UC456def [--hours N]
  python collect.py --file channels.txt [--hours N]

  # Single video
  python collect.py --video VIDEO_ID
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from googleapiclient.errors import HttpError

import state
import store
import transcripts as tr
import openrouter
from youtube_client import build_service, get_subscribed_channels, get_new_videos, get_video_durations, resolve_channel_id, get_video_by_id

load_dotenv()


_SHORTS_DEFAULT_MAX_SECONDS = 180


def _parse_duration_seconds(duration: str | None) -> int | None:
    """Parse ISO 8601 duration string (e.g. PT1H2M3S) to total seconds.

    Returns None if the string is missing or unparseable.
    """
    if not duration:
        return None
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not m:
        return None
    days, hours, minutes, seconds = (int(x) if x else 0 for x in m.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _is_short(duration: str | None) -> bool:
    """Return True if video duration is ≤ SHORTS_MAX_SECONDS (default 180 s).

    Threshold is read from the SHORTS_MAX_SECONDS env var (default: 180).
    """
    secs = _parse_duration_seconds(duration)
    if secs is None:
        return False
    threshold = int(os.environ.get("SHORTS_MAX_SECONDS", _SHORTS_DEFAULT_MAX_SECONDS))
    return secs <= threshold


def _should_filter_title(title: str) -> tuple[bool, str]:
    """Check if title matches any VIDEO_TITLE_FILTERS pattern.

    Returns (should_filter, matched_pattern) tuple.
    """
    patterns = os.environ.get("VIDEO_TITLE_FILTERS", "")
    if not patterns:
        return False, ""

    for pattern in patterns.split(","):
        pattern = pattern.strip()
        if not pattern:
            continue
        try:
            if re.search(pattern, title, re.IGNORECASE):
                return True, pattern
        except re.error as e:
            print(f"Invalid regex in VIDEO_TITLE_FILTERS: {e}", file=sys.stderr)
            sys.exit(1)
    return False, ""


def parse_args():
    parser = argparse.ArgumentParser(
        description="Collect new YouTube video summaries into data/."
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
    source.add_argument(
        "--video",
        metavar="VIDEO_IDS",
        help="Comma-separated list of video IDs to fetch.",
    )
    parser.add_argument(
        "channels",
        nargs="*",
        metavar="CHANNEL",
        help="Channel IDs, handles, or URLs.",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=None,
        metavar="N",
        help="Look back N hours. Overrides the persisted last-run timestamp.",
    )
    parser.add_argument(
        "--prune-days",
        type=int,
        default=None,
        metavar="N",
        help="Remove store entries older than N days after collecting. Omit to keep all entries.",
    )
    parser.add_argument(
        "--include-shorts",
        action="store_true",
        help=(
            "Include short videos (≤ SHORTS_MAX_SECONDS, default 180 s). "
            "By default short videos are skipped."
        ),
    )
    return parser.parse_args()


def _resolve_since(channel_id: str, hours: int | None) -> datetime:
    if hours is not None:
        return datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    last = state.get_last_run(channel_id)
    if last:
        return last
    return datetime.now(tz=timezone.utc) - timedelta(hours=24)


def _load_identifiers_from_file(path: str) -> list[str]:
    if not os.path.exists(path):
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path) as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def _process_single_video(service, video_id: str, model: str, now: datetime, skip_shorts: bool = True) -> bool:
    """Fetch and process a single video. Returns True if added to store."""
    print(f"Fetching video {video_id}...")
    video = get_video_by_id(service, video_id)
    if not video:
        print(f"Error: video '{video_id}' not found.", file=sys.stderr)
        return False

    vid_id = video["video_id"]
    vid_title = video["title"]
    channel_id = video["channel_id"]
    channel_title = video["channel_title"]

    if skip_shorts and _is_short(video.get("duration")):
        print(f"  → {vid_title} [Short, skipped]")
        return False

    should_filter, matched_pattern = _should_filter_title(vid_title)
    if should_filter:
        print(f"    → Titel ignoriert (Filter match: '{matched_pattern}')")
        return False

    print(f"  → {vid_title}")

    existing = store.get_video(vid_id)

    if existing and existing["has_transcript"] and existing["has_summary"]:
        print(f"    Already in store with transcript and summary, skipping.")
        return False

    # Fetch transcript only if not already stored
    lang = None
    manual = None
    if existing and existing["has_transcript"]:
        llm_path = store.get_llm_transcript_path(vid_id)
        transcript = llm_path.read_text(encoding="utf-8") if llm_path else None
        lang = existing.get("transcript_lang")
        transcript_error = existing.get("transcript_error")
    else:
        transcript, lang, transcript_error = tr.get_transcript(vid_id)
        if transcript and lang not in ["de", "en"]:
            # Fetch manual DE/EN transcript as second file
            manual, manual_lang = tr.get_manual_transcript(vid_id)
            if manual:
                (store.TRANSCRIPTS_DIR / f"{vid_id}.{manual_lang}.txt").write_text(
                    manual, encoding="utf-8"
                )
        if not transcript:
            if not transcript_error or transcript_error == "unavailable":
                print("    No transcript available.")
            elif transcript_error == "country_blocked":
                print("    Video in dieser Region gesperrt — kein Transkript.")

    # Summarize only if we have a transcript and no summary yet
    summary = None
    tags = None
    if transcript and (not existing or not existing["has_summary"]):
        if existing:
            # DB row exists — path lookup works
            llm_path = store.get_llm_transcript_path(vid_id)
            llm_input = llm_path.read_text(encoding="utf-8") if llm_path else transcript
        else:
            # New video — DB row not created yet, use manual from memory if available
            llm_input = manual if manual else transcript
        print(f"    Summarizing via {model}...")
        summary, tags = openrouter.summarize_video(vid_id, vid_title, llm_input, model)

    if existing:
        store.update_video_with_summary(
            vid_id,
            transcript if not existing["has_transcript"] else None,
            summary,
            transcript_error,
            model if summary else existing.get("summary_model"),
            tags=tags,
            transcript_lang=lang,
        )
        return False
    else:
        return store.add_video({
            "channel_id": channel_id,
            "channel_title": channel_title,
            "video_id": vid_id,
            "title": vid_title,
            "published_at": video["published_at"],
            "thumbnail_url": video["thumbnail_url"],
            "duration": video.get("duration"),
            "summary_model": model if summary else None,
            "transcript": transcript,
            "transcript_lang": lang,
            "summary": summary,
            "transcript_error": transcript_error,
            "tags": tags,
            "collected_at": now.isoformat(),
        })


def main():
    args = parse_args()
    tr.log_proxy_config()
    model = os.environ.get("LLM_MODEL") or os.environ.get("OPENROUTER_MODEL", "gpt-oss-20b")

    # --- Handle single video(s) ---
    if args.video:
        video_ids = [v.strip() for v in args.video.split(",") if v.strip()]
        service = build_service()
        now = datetime.now(tz=timezone.utc)
        added_count = 0
        for vid in video_ids:
            if _process_single_video(service, vid, model, now, skip_shorts=not args.include_shorts):
                added_count += 1
        print(f"\nDone. {added_count} video(s) added to store.")
        return

    # --- Resolve channel list ---
    if args.auth:
        print("Authenticating with YouTube...")
        service = build_service()
        print("Fetching subscriptions...")
        channels = get_subscribed_channels(service)
        print(f"Found {len(channels)} subscribed channels.")
    else:
        if args.file:
            identifiers = _load_identifiers_from_file(args.file)
        elif args.channels:
            identifiers = args.channels
        else:
            print(
                "Error: provide --auth, --file, --video, or channel identifiers as arguments.",
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
    now = datetime.now(tz=timezone.utc)
    total_added = 0

    for ch in channels:
        channel_id = ch["channel_id"]
        channel_title = ch["title"]
        since = _resolve_since(channel_id, args.hours)

        print(f"\n[{channel_title}] Fetching videos since {since.strftime('%Y-%m-%d %H:%M')} UTC...")
        try:
            videos = get_new_videos(service, channel_id, since)
        except HttpError as e:
            if e.status_code == 403 and "quotaExceeded" in str(e):
                print("  YouTube API quota exceeded — stopping early.", file=sys.stderr)
                break
            print(f"  API error: {e}", file=sys.stderr)
            continue
        print(f"  {len(videos)} new video(s).")

        if videos:
            durations = get_video_durations(service, [v["video_id"] for v in videos])
            for v in videos:
                v["duration"] = durations.get(v["video_id"])

        for video in videos:
            vid_id = video["video_id"]
            vid_title = video["title"]

            if not args.include_shorts and _is_short(video.get("duration")):
                print(f"  → {vid_title} [Short, skipped]")
                continue

            should_filter, matched_pattern = _should_filter_title(vid_title)
            if should_filter:
                print(f"    → Titel ignoriert (Filter match: '{matched_pattern}')")
                continue

            print(f"  → {vid_title}")

            existing = store.get_video(vid_id)

            if existing and existing["has_transcript"] and existing["has_summary"]:
                print(f"    Already in store with transcript and summary, skipping.")
                continue

            # Fetch transcript only if not already stored
            lang = None
            manual = None
            if existing and existing["has_transcript"]:
                llm_path = store.get_llm_transcript_path(vid_id)
                transcript = llm_path.read_text(encoding="utf-8") if llm_path else None
                lang = existing.get("transcript_lang")
                transcript_error = existing.get("transcript_error")
            else:
                transcript, lang, transcript_error = tr.get_transcript(vid_id)
                if transcript and lang not in ["de", "en"]:
                    # Fetch manual DE/EN transcript as second file
                    manual, manual_lang = tr.get_manual_transcript(vid_id)
                    if manual:
                        (store.TRANSCRIPTS_DIR / f"{vid_id}.{manual_lang}.txt").write_text(
                            manual, encoding="utf-8"
                        )
                time.sleep(5)
                if not transcript:
                    if not transcript_error or transcript_error == "unavailable":
                        print("    No transcript available.")
                    elif transcript_error == "country_blocked":
                        print("    Video in dieser Region gesperrt — kein Transkript.")

            # Summarize only if we have a transcript and no summary yet
            if transcript and (not existing or not existing["has_summary"]):
                if existing:
                    # DB row exists — path lookup works
                    llm_path = store.get_llm_transcript_path(vid_id)
                    llm_input = llm_path.read_text(encoding="utf-8") if llm_path else transcript
                else:
                    # New video — DB row not created yet, use manual from memory if available
                    llm_input = manual if manual else transcript
                print(f"    Summarizing via {model}...")
                summary, tags = openrouter.summarize_video(vid_id, vid_title, llm_input, model)
            else:
                summary = None
                tags = None

            if existing:
                store.update_video_with_summary(
                    vid_id,
                    transcript if not existing["has_transcript"] else None,
                    summary,
                    transcript_error,
                    model if summary else existing.get("summary_model"),
                    tags=tags,
                    transcript_lang=lang,
                )
            else:
                added = store.add_video({
                    "channel_id": channel_id,
                    "channel_title": channel_title,
                    "video_id": vid_id,
                    "title": vid_title,
                    "published_at": video["published_at"],
                    "thumbnail_url": video["thumbnail_url"],
                    "duration": video.get("duration"),
                    "summary_model": model if summary else None,
                    "transcript": transcript,
                    "transcript_lang": lang,
                    "summary": summary,
                    "transcript_error": transcript_error,
                    "tags": tags,
                    "collected_at": now.isoformat(),
                })
                if added:
                    total_added += 1

        if args.hours is None:
            state.set_last_run(channel_id, now)

    # --- Prune old entries ---
    if args.prune_days is not None:
        removed = store.prune_older_than(args.prune_days)
        if removed:
            print(f"\nPruned {removed} store entry(s) older than {args.prune_days} days.")

    print(f"\nDone. {total_added} new video(s) added to store.")


if __name__ == "__main__":
    main()
