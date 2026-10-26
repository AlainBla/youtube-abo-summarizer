#!/usr/bin/env python3
"""Transcribe stored videos of caption-less channels with Whisper, then summarize.

collect.py stores every video of a channel that has switched captions off as
transcript_error "unavailable" -- there is nothing for it to fetch. This loop
picks those up for the channels listed in WHISPER_CHANNELS (.env, channel IDs,
comma-separated): download the original audio, transcribe it through
OpenRouter (whisper_transcribe.py), store the transcript, summarize it.

It runs on its own, after collect.py in collect.sh, never inside it: an hour of
audio is minutes of download and transcription, and collect.py's run budget
cannot absorb that. --limit bounds each run, so a backlog drains over several.

A video that cannot be transcribed (download refused, nothing recognisable in
the audio) is marked "whisper_failed" and left alone from then on -- otherwise
the same few failures would head the queue every run and starve the rest.
--retry gives them another chance.

Usage:
  python whisper_collect.py                    # up to --limit videos, newest first
  python whisper_collect.py --dry-run          # what would be done
  python whisper_collect.py --video ID,ID      # these videos (still only listed channels)
  python whisper_collect.py --limit 0          # the whole backlog in one go

Exit codes, as collect.py and repair.py: 10 when a transcript or summary was
written (collect.sh then re-exports the archive), 0 otherwise -- also when
another run holds the lock.
"""
import argparse
import fcntl
import os
import shutil
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

import openrouter
import store
import whisper_transcribe

EXIT_CONTENT_CHANGED = 10
FAILED = "whisper_failed"
DEFAULT_LIMIT = 3


def configured_channels() -> set[str]:
    return {c.strip() for c in os.environ.get("WHISPER_CHANNELS", "").split(",") if c.strip()}


def select_videos(entries, channels: set[str], has_transcript, retry: bool = False,
                  only: set[str] | None = None) -> list[dict]:
    """Entries to transcribe, newest first (the store's order).

    Only listed channels; never a video that already has a transcript on disk,
    one YouTube blocks for this region, or a stream still running (no duration
    yet -- its audio would be cut off wherever the stream stands).
    """
    picked = []
    for e in entries:
        if e.get("channel_id") not in channels:
            continue
        if only is not None and e["video_id"] not in only:
            continue
        if e.get("transcript_error") == "country_blocked":
            continue
        if e.get("transcript_error") == FAILED and not retry:
            continue
        if not e.get("duration"):
            continue
        if has_transcript(e["video_id"]):
            continue
        picked.append(e)
    return picked


def process(entry: dict, model: str, no_proxy: bool = False) -> bool:
    """Transcribe and summarize one video. True when something was written."""
    vid = entry["video_id"]
    # update_video_with_summary() writes summary_model and tags every time, so
    # the stored ones are passed back whenever no new summary replaces them.
    keep = {"summary_model": entry.get("summary_model"), "tags": entry.get("tags") or None}

    text, lang, cost = whisper_transcribe.transcribe_video(vid, no_proxy=no_proxy)
    print(f"    Kosten Transkription: ${cost:.4f}")
    if not text:
        print(f"    Kein Transkript erhalten — als {FAILED} markiert.")
        store.update_video_with_summary(vid, None, None, FAILED, **keep)
        return True

    store.update_video_with_summary(vid, text, None, None, transcript_lang=lang, **keep)
    print(f"    Transkript gespeichert ({len(text)} Zeichen, Sprache {lang or '?'}).")

    print(f"    Summarizing via {model}...")
    try:
        summary, tags = openrouter.summarize_video(vid, entry["title"], text, model,
                                                   channel=entry.get("channel_title"))
    except openrouter.SummaryRejected as e:
        print(f"    Summary rejected: {e} — transcript kept, repair.py can retry.")
        return True
    store.update_video_with_summary(vid, None, summary, None, summary_model=model, tags=tags)
    return True


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                   help=f"Videos per run (default {DEFAULT_LIMIT}, 0 = no limit).")
    p.add_argument("--video", metavar="ID,ID,...", help="Only these video IDs.")
    p.add_argument("--retry", action="store_true", help=f"Include videos marked {FAILED}.")
    p.add_argument("--dry-run", action="store_true", help="List what would be done, write nothing.")
    p.add_argument("--no-proxy", action="store_true", help="Download without any proxy.")
    p.add_argument("--model", default=None, help="LLM for the summary (default: LLM_MODEL / OPENROUTER_MODEL).")
    return p.parse_args(argv)


def _lock():
    """An exclusive, non-blocking lock, or None when another run holds it.

    collect.sh runs every 30 minutes and a long backlog run can outlive that;
    two runs would pay twice for the same audio.
    """
    store.DATA_DIR.mkdir(exist_ok=True)
    handle = open(store.DATA_DIR / "whisper.lock", "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def main(argv=None) -> int:
    args = parse_args(argv)
    channels = configured_channels()
    if not channels:
        return 0
    only = {v.strip() for v in args.video.split(",") if v.strip()} if args.video else None

    entries = store.get_all_videos(with_transcripts=False)
    todo = select_videos(entries, channels,
                         has_transcript=lambda vid: store.get_llm_transcript_path(vid) is not None,
                         retry=args.retry, only=only)
    total = len(todo)
    if args.limit > 0:
        todo = todo[: args.limit]
    if not todo:
        return 0
    print(f"Whisper: {total} Video(s) ohne Transkript, {len(todo)} in diesem Lauf "
          f"[{whisper_transcribe.whisper_model()}]")
    if args.dry_run:
        for e in todo:
            print(f"  {e['published_at'][:10]}  {e['title'][:70]}  ({e['video_id']}, {e.get('duration')})")
        return 0

    lock = _lock()
    if lock is None:
        print("Whisper: ein anderer Lauf ist noch aktiv — übersprungen.")
        return 0

    # A run killed by collect.sh's timeout never reaches TemporaryDirectory's
    # cleanup; with the lock held, any whisper-* directory is such a leftover.
    for stale in store.DATA_DIR.glob("whisper-*"):
        shutil.rmtree(stale, ignore_errors=True)

    model = args.model or os.environ.get("LLM_MODEL") or os.environ.get("OPENROUTER_MODEL", "gpt-oss-20b")
    changed = 0
    try:
        for e in todo:
            print(f"\n  {e['title'][:70]}  ({e['video_id']})")
            try:
                changed += process(e, model, no_proxy=args.no_proxy)
            except Exception as exc:  # noqa: BLE001 - one video must not end the run
                # Not the video's fault (provider down, key missing, ffmpeg gone):
                # no marker, the next run tries again.
                print(f"    Fehler: {exc} — nächster Lauf versucht es erneut.", file=sys.stderr)
    finally:
        lock.close()
    print(f"\nWhisper: {changed} von {len(todo)} Video(s) geändert.")
    return EXIT_CONTENT_CHANGED if changed else 0


if __name__ == "__main__":
    sys.exit(main())
