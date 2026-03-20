#!/usr/bin/env python3
"""Recover videos and summaries from a previously exported HTML file.

Extracts the embedded VIDEOS JSON blob, inserts missing entries into the
local store (data/videos.db) and writes summaries to data/summaries/.
Existing entries are left untouched.

Usage:
  python recover_from_export.py export.html
  python recover_from_export.py export.html --dry-run
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone

import store


def extract_videos(html: str) -> list[dict]:
    m = re.search(r"const VIDEOS = (\[.*?\]);\s*\n", html, re.DOTALL)
    if not m:
        sys.exit("Error: could not find 'const VIDEOS = [...]' in the file.")
    return json.loads(m.group(1).replace("<\\/", "</"))


def main():
    parser = argparse.ArgumentParser(
        description="Recover videos and summaries from an exported HTML file."
    )
    parser.add_argument("file", help="Path to the exported HTML file.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without writing anything.",
    )
    args = parser.parse_args()

    with open(args.file, encoding="utf-8") as f:
        html = f.read()

    videos = extract_videos(html)
    print(f"Found {len(videos)} video(s) in export.")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    inserted = skipped = summaries_written = 0

    for v in videos:
        video_id = v["video_id"]
        existing = store.get_video(video_id)

        if existing:
            # Already in DB — only recover a missing summary file.
            if v.get("summary") and not existing["has_summary"]:
                print(f"  {video_id}: in DB but summary missing — restoring summary")
                if not args.dry_run:
                    store.update_video_with_summary(
                        video_id,
                        transcript=None,
                        summary=v["summary"],
                        transcript_error=existing.get("transcript_error"),
                        summary_model=existing.get("summary_model"),
                        tags=existing.get("tags") or [],
                    )
                summaries_written += 1
            else:
                skipped += 1
            continue

        print(f"  {video_id}: inserting — {v['title'][:60]}")
        if not args.dry_run:
            store.add_video({
                "video_id":        video_id,
                "channel_id":      v["channel_id"],
                "channel_title":   v["channel_title"],
                "title":           v["title"],
                "published_at":    v["published_at"],
                "thumbnail_url":   v.get("thumbnail_url"),
                "duration":        None,   # export stores display format, not ISO 8601
                "summary_model":   v.get("summary_model"),
                "transcript":      None,
                "summary":         v.get("summary"),
                "transcript_error": v.get("transcript_error"),
                "tags":            v.get("tags") or [],
                "collected_at":    now,
            })
        inserted += 1

    print(
        f"\nDone — inserted: {inserted}, summaries restored: {summaries_written}, "
        f"skipped (already present): {skipped}"
        + (" [dry run]" if args.dry_run else "")
    )


if __name__ == "__main__":
    main()
