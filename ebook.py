#!/usr/bin/env python3
"""Render stored video summaries into an EPUB 3 ebook.

Reads only from the local store (and optionally the sync server's database
for a user's read state) -- no YouTube and no LLM calls.
"""

import argparse
import os
import sys
from datetime import datetime

DEFAULT_LIMIT = 100
DEFAULT_SYNC_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync-server", "sync.db")


def select_videos(entries, channel=None, videos=None, tag=None, limit=DEFAULT_LIMIT):
    """Filter, order newest-first and cut to `limit` (0 = no limit).

    The cut happens before any grouping, so "the newest 100" means exactly
    that -- not "100 per week".
    """
    picked = entries
    if videos:
        wanted = set(videos)
        picked = [v for v in picked if v["video_id"] in wanted]
    if channel:
        picked = [v for v in picked if v.get("channel_id") == channel]
    if tag:
        picked = [v for v in picked if tag in (v.get("tags") or [])]
    picked = sorted(
        picked,
        key=lambda v: ((v.get("published_at") or ""), v.get("video_id") or ""),
        reverse=True,
    )
    return picked[:limit] if limit else picked


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Build an EPUB ebook from stored summaries.")
    window = parser.add_mutually_exclusive_group()
    window.add_argument("--hours", type=int, help="only videos published in the last N hours")
    window.add_argument("--all", action="store_true", help="all videos in the store")
    parser.add_argument("--channel", help="restrict to one channel ID")
    parser.add_argument("--videos", help="comma-separated video IDs")
    parser.add_argument("--tag", help="restrict to videos carrying this tag")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help="keep only the N newest videos (0 = no limit)")
    parser.add_argument("--user", help="email whose read state is taken from the sync database")
    parser.add_argument("--sync-db", default=DEFAULT_SYNC_DB, help="path to the sync server database")
    parser.add_argument("--read", choices=["split", "drop", "ignore"], default="split",
                        help="read videos: move to the back, drop them, or ignore the state")
    parser.add_argument("--no-thumbnails", dest="thumbnails", action="store_false",
                        help="do not download and embed thumbnails")
    parser.add_argument("--no-transcripts", dest="transcripts", action="store_false",
                        help="do not append transcripts")
    parser.add_argument("--output", help="output file (default: ebook_YYYY-MM-DD_HH-MM.epub)")
    parser.add_argument("--lang", choices=["de", "en"], default="de")
    return parser.parse_args(argv)
