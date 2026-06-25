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
import base64
import gzip
import json
import re
import sys
from datetime import datetime, timezone

import store


def _merge_index_summaries(payload: dict) -> list[dict]:
    """Merge the split {index, summaries} export payload back into flat dicts.

    Newer exports embed a lightweight ``index`` (no summary HTML) plus a
    ``summaries`` map; recover needs each video's ``summary`` field restored.
    """
    summaries = payload.get("summaries") or {}
    videos = []
    for entry in payload.get("index") or []:
        v = {k: val for k, val in entry.items() if k != "search_text"}
        v["summary"] = summaries.get(entry["video_id"])
        videos.append(v)
    return videos


def extract_videos(html: str) -> list[dict]:
    # New gzip+base64 format: const DATA_B64 = "...";
    m = re.search(r'const DATA_B64 = "([^"]*)";', html)
    if m:
        raw = gzip.decompress(base64.b64decode(m.group(1))).decode("utf-8")
        return _merge_index_summaries(json.loads(raw))

    # New uncompressed format: const DATA_OBJ = {...};
    m = re.search(r"const DATA_OBJ = (\{.*\});\s*\nlet VIDEOS = \[\];", html, re.DOTALL)
    if m:
        return _merge_index_summaries(json.loads(m.group(1).replace("<\\/", "</")))

    # Legacy format: const VIDEOS = [...];
    m = re.search(r"const VIDEOS = (\[.*?\]);\s*\n", html, re.DOTALL)
    if m:
        return json.loads(m.group(1).replace("<\\/", "</"))

    sys.exit("Error: could not find embedded video data in the file.")


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
