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
import ytdlp_meta
import feeds
from youtube_client import build_service, get_subscribed_channels, get_new_videos, get_video_durations, resolve_channel_id, get_video_by_id

load_dotenv()


_SHORTS_DEFAULT_MAX_SECONDS = 180

# Exit code telling the caller that this run put new videos into the store.
# collect.sh chains the export onto it, so the archive -- and the "new videos"
# banner it shows -- is only regenerated when there is something to announce.
# Distinct from 0 (ran fine, nothing new) and 1 (failed).
EXIT_NEW_VIDEOS = 10


def _exit_code(added: int) -> int:
    return EXIT_NEW_VIDEOS if added else 0


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
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="Ignore WEBSHARE_PROXY_URL and fetch transcripts via direct connection.",
    )
    parser.add_argument(
        "--no-rss",
        action="store_true",
        help=(
            "Discover new videos through the YouTube API instead of the free RSS feed. "
            "The feed is used by default and costs no quota."
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


def _fill_feed_durations(videos: list[dict], get_service, no_proxy: bool = False) -> None:
    """Fill in the duration the RSS feed does not carry, in place.

    Three sources, cheapest first. The store: `collect.sh` looks back four hours
    every thirty minutes, so a video is re-listed by the feed for about eight
    runs, and a duration already on disk costs nothing. Then yt-dlp, free but a
    subprocess per video. Then, only for what is still missing, one batched
    videos().list -- 1 unit per 50 videos.

    That last step is not decoration: without it a run whose yt-dlp is broken
    (dependency not installed yet, watch page blocked for the server's IP) would
    read every short as "not short", because _is_short(None) is False, and spend
    transcript and LLM budget on it.
    """
    missing = []
    for v in videos:
        existing = store.get_video(v["video_id"])
        if existing and existing.get("duration"):
            v["duration"] = existing["duration"]
            continue

        meta = ytdlp_meta.get_video_metadata(v["video_id"], no_proxy=no_proxy)
        if meta:
            v["duration"] = meta.get("duration")
            v["title"] = meta.get("title") or v["title"]
            v["published_at"] = meta.get("published_at") or v["published_at"]
        else:
            v["duration"] = None
            missing.append(v)

    if not missing:
        return

    try:
        service = get_service()
    except Exception as exc:  # noqa: BLE001 - no credentials is a degraded run, not a crash
        print(
            f"    Dauer für {len(missing)} Video(s) unbekannt, YouTube API nicht verfügbar: {exc}",
            file=sys.stderr,
        )
        return
    try:
        durations = get_video_durations(service, [v["video_id"] for v in missing])
    except HttpError as e:
        print(f"    Dauer-Abruf über die API fehlgeschlagen: {e}", file=sys.stderr)
        return
    for v in missing:
        v["duration"] = durations.get(v["video_id"])


def _discover_videos(channel_id: str, since: datetime, get_service, use_rss: bool = True,
                     no_proxy: bool = False) -> list[dict]:
    """New videos for one channel, from the RSS feed where possible.

    The feed costs no quota and needs no token, so the API service stays
    unbuilt unless this channel actually falls back to it (feeds.get_new_videos_rss
    returns None when it cannot answer -- see the ~15-entry cap there).

    The feed carries no duration, and the shorts filter runs before any
    transcript or LLM work, so _fill_feed_durations() supplies them.
    """
    if use_rss:
        videos = feeds.get_new_videos_rss(channel_id, since, no_proxy=no_proxy)
        if videos is not None:
            _fill_feed_durations(videos, get_service, no_proxy=no_proxy)
            return videos
        print("    RSS-Feed konnte nicht antworten — YouTube API für diesen Kanal.", file=sys.stderr)

    service = get_service()
    videos = get_new_videos(service, channel_id, since)
    if videos:
        durations = get_video_durations(service, [v["video_id"] for v in videos])
        for v in videos:
            v["duration"] = durations.get(v["video_id"])
    return videos


def _resolve_identifiers(identifiers: list[str], get_service, no_proxy: bool = False) -> list[dict]:
    """Resolve channel IDs/handles/URLs to [{channel_id, title}].

    A UC... ID needs no API call at all: the feed states the channel title
    itself, where channels().list would charge a quota unit for it. Handles and
    URLs still go through the API -- the feed can only be addressed by ID.
    """
    channels = []
    for ident in identifiers:
        resolved = None
        if ident.startswith("UC") and len(ident) == 24:
            resolved = feeds.get_channel(ident, no_proxy=no_proxy)
        if not resolved:
            resolved = resolve_channel_id(get_service(), ident)
        if resolved:
            channels.append(resolved)
        else:
            print(f"Warning: could not resolve channel '{ident}', skipping.", file=sys.stderr)
    return channels


def _lazy_service():
    """Return a getter that builds the YouTube API service on first use.

    The single-video path prefers yt-dlp and usually never touches the API.
    Building the service up front would still demand credentials -- and with an
    expired token that means an interactive OAuth flow, which under cron is a
    worker hanging until someone notices.
    """
    cached = []

    def get():
        if not cached:
            cached.append(build_service())
        return cached[0]

    return get


def _fetch_video_metadata(video_id: str, get_service, no_proxy: bool = False) -> dict | None:
    """Metadata for one video: yt-dlp first, YouTube API as fallback.

    Ingest is triggered by hand at any hour, while the scheduled collect runs
    spend the project's daily quota on subscriptions and playlist pages. A
    quotaExceeded there used to take the ingest button down with it, over a
    lookup worth a single unit. yt-dlp costs nothing and needs no token; the
    API stays as the fallback for the cases yt-dlp cannot read.
    """
    meta = ytdlp_meta.get_video_metadata(video_id, no_proxy=no_proxy)
    if meta:
        return meta

    print("    yt-dlp lieferte keine Metadaten — Fallback auf die YouTube API.", file=sys.stderr)
    try:
        service = get_service()
    except Exception as exc:  # noqa: BLE001 - no credentials is a failed ingest, not a crash
        print(f"    YouTube API nicht verfügbar: {exc}", file=sys.stderr)
        return None
    try:
        return get_video_by_id(service, video_id)
    except HttpError as e:
        if e.resp.status == 403 and "quotaExceeded" in str(e):
            print("    YouTube API quota exceeded — Metadaten nicht abrufbar.", file=sys.stderr)
            return None
        raise


def _process_single_video(get_service, video_id: str, model: str, now: datetime, skip_shorts: bool = True, no_proxy: bool = False) -> bool:
    """Fetch and process a single video. Returns True if added to store."""
    # The store first, before any network call. The queue re-offers IDs that
    # were collected long ago, and every metadata field a stored video needs is
    # already a column here -- fetching first meant a yt-dlp run and, on a
    # blocked IP, a proxy retry, only to find the store already had everything.
    existing = store.get_video(video_id)
    if existing and existing["has_transcript"] and existing["has_summary"]:
        print(f"Fetching video {video_id}...")
        print(f"  → {existing['title']}")
        print("    Already in store with transcript and summary, skipping.")
        return False

    print(f"Fetching video {video_id}...")
    if existing:
        # Incomplete entry (transcript or summary missing): the metadata is
        # already known, only the missing piece has to be produced.
        video = existing
    else:
        video = _fetch_video_metadata(video_id, get_service, no_proxy=no_proxy)
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

    # Fetch transcript only if not already stored
    lang = None
    manual = None
    if existing and existing["has_transcript"]:
        llm_path = store.get_llm_transcript_path(vid_id)
        transcript = llm_path.read_text(encoding="utf-8") if llm_path else None
        lang = existing.get("transcript_lang")
        transcript_error = existing.get("transcript_error")
    else:
        fetch_fn = tr.get_transcript_no_proxy if no_proxy else tr.get_transcript
        transcript, lang, transcript_error = fetch_fn(vid_id)
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
        try:
            summary, tags = openrouter.summarize_video(vid_id, vid_title, llm_input, model)
        except openrouter.SummaryRejected as e:
            print(f"    Summary rejected: {e} — stored without summary.")
            summary, tags = None, None

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
    tr.log_proxy_config(no_proxy=args.no_proxy)
    model = os.environ.get("LLM_MODEL") or os.environ.get("OPENROUTER_MODEL", "gpt-oss-20b")

    # --- Handle single video(s) ---
    if args.video:
        video_ids = [v.strip() for v in args.video.split(",") if v.strip()]
        get_service = _lazy_service()
        now = datetime.now(tz=timezone.utc)
        added_count = 0
        for vid in video_ids:
            if _process_single_video(get_service, vid, model, now, skip_shorts=not args.include_shorts, no_proxy=args.no_proxy):
                added_count += 1
        print(f"\nDone. {added_count} video(s) added to store.")
        return added_count

    # --- Resolve channel list ---
    get_service = _lazy_service()

    if args.auth:
        print("Authenticating with YouTube...")
        print("Fetching subscriptions...")
        # The only API call a feed-driven run still needs: subscriptions.list,
        # one unit per 50 channels per run.
        channels = get_subscribed_channels(get_service())
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

        channels = _resolve_identifiers(identifiers, get_service, no_proxy=args.no_proxy)

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
            videos = _discover_videos(
                channel_id, since, get_service,
                use_rss=not args.no_rss, no_proxy=args.no_proxy,
            )
        except HttpError as e:
            if e.status_code == 403 and "quotaExceeded" in str(e):
                print("  YouTube API quota exceeded — stopping early.", file=sys.stderr)
                break
            print(f"  API error: {e}", file=sys.stderr)
            continue
        print(f"  {len(videos)} new video(s).")

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
                fetch_fn = tr.get_transcript_no_proxy if args.no_proxy else tr.get_transcript
                transcript, lang, transcript_error = fetch_fn(vid_id)
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
                try:
                    summary, tags = openrouter.summarize_video(vid_id, vid_title, llm_input, model)
                except openrouter.SummaryRejected as e:
                    print(f"    Summary rejected: {e} — stored without summary.")
                    summary, tags = None, None
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
    return total_added


if __name__ == "__main__":
    sys.exit(_exit_code(main() or 0))
