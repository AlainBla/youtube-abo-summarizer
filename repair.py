#!/usr/bin/env python3
"""Repair missing or outdated transcripts and summaries in the store.

Scans all DB entries and:
  - Re-fetches transcripts whose file is missing (skips country_blocked).
  - Re-summarizes videos that have a transcript file but no summary file.
  - With --force-summarize, re-summarizes even if a summary already exists
    (useful for fixing bad output from a specific model run).
  - With --video ID [ID ...], restricts all operations to those video IDs.

Usage:
  # Repair everything that is missing
  python repair.py

  # Preview without making any changes
  python repair.py --dry-run

  # Re-summarize two specific videos (e.g. bad output found)
  python repair.py --force-summarize --video abc123xyz def456uvw

  # Re-summarize all videos that have a transcript (fresh run with new model)
  python repair.py --force-summarize
"""

import argparse
import os
import time

from dotenv import load_dotenv

import openrouter
import store
import transcripts as tr

load_dotenv()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Repair missing transcripts and summaries in the store."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without making any changes.",
    )
    parser.add_argument(
        "--video",
        metavar="ID,ID,...",
        help="Comma-separated list of video IDs to limit repairs to.",
    )
    parser.add_argument(
        "--force-summarize",
        action="store_true",
        help="Re-summarize even if a summary file already exists.",
    )
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="Ignore WEBSHARE_PROXY_URL and fetch transcripts via direct connection.",
    )
    parser.add_argument(
        "--model",
        metavar="MODEL_ID",
        default=None,
        help="LLM model to use for summarization (overrides LLM_MODEL / OPENROUTER_MODEL env vars).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    tr.log_proxy_config(no_proxy=args.no_proxy)
    model = args.model or os.environ.get("LLM_MODEL") or os.environ.get("OPENROUTER_MODEL", "gpt-oss-20b")
    video_filter = {v.strip() for v in args.video.split(",") if v.strip()} if args.video else None

    entries = store.get_all_videos()
    if video_filter:
        entries = [e for e in entries if e["video_id"] in video_filter]
        not_found = video_filter - {e["video_id"] for e in entries}
        for vid_id in sorted(not_found):
            print(f"Warning: {vid_id} not found in store, skipping.")

    if not entries:
        print("No videos to process.")
        return

    print(f"Scanning {len(entries)} video(s)  [model: {model}]")
    if args.dry_run:
        print("  (dry-run — no changes will be written)")

    n_transcript_ok = 0
    n_transcript_fail = 0
    n_summarized = 0
    n_skipped = 0

    for entry in entries:
        vid_id    = entry["video_id"]
        vid_title = entry["title"]
        t_exists  = entry["transcript"] is not None
        s_exists  = entry["summary"] is not None
        t_error   = entry.get("transcript_error")

        # country_blocked is a permanent restriction — never re-fetch
        needs_transcript = not t_exists and t_error != "country_blocked"
        needs_summary    = not s_exists or args.force_summarize

        if not needs_transcript and not needs_summary:
            n_skipped += 1
            continue

        reasons = []
        if needs_transcript:
            reasons.append("transcript missing")
        if not s_exists:
            reasons.append("summary missing")
        elif args.force_summarize:
            reasons.append("force-summarize")

        print(f"\n  [{', '.join(reasons)}] {vid_title}  ({vid_id})")

        if args.dry_run:
            continue

        transcript = entry["transcript"]

        # ── Step 1: fetch transcript if missing ──────────────────────────────
        if needs_transcript:
            print(f"    Fetching transcript...")
            fetch_fn = tr.get_transcript_no_proxy if args.no_proxy else tr.get_transcript
            transcript, lang, t_error = fetch_fn(vid_id)
            time.sleep(2)
            if transcript:
                n_transcript_ok += 1
                store.update_video_with_summary(vid_id, transcript, None, t_error,
                                                transcript_lang=lang)
            else:
                print(f"    Still unavailable: {t_error or 'unavailable'}")
                store.update_video_with_summary(vid_id, None, None, t_error)
                n_transcript_fail += 1
                continue

        # ── Step 2: summarize ─────────────────────────────────────────────────
        if transcript is None:
            print(f"    No transcript available, cannot summarize.")
            n_skipped += 1
            continue

        if needs_summary or needs_transcript:
            print(f"    Summarizing via {model}...")
            llm_path = store.get_llm_transcript_path(vid_id)
            llm_input = llm_path.read_text(encoding="utf-8") if llm_path else transcript
            summary, tags = openrouter.summarize_video(vid_id, vid_title, llm_input, model)
            store.update_video_with_summary(
                vid_id, None, summary, t_error, summary_model=model, tags=tags
            )
            n_summarized += 1

    print(
        f"\nDone.  transcripts: {n_transcript_ok} fetched / {n_transcript_fail} failed  |  "
        f"summaries: {n_summarized} written  |  skipped: {n_skipped}"
    )


if __name__ == "__main__":
    main()
