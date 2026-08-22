import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import ebook
from export_harness import video


def _entries(n):
    return [video("v%03d" % i, "2026-01-%02dT00:00:00Z" % (i + 1)) for i in range(n)]


def test_limit_keeps_the_newest_entries():
    picked = ebook.select_videos(_entries(10), limit=3)
    assert [v["video_id"] for v in picked] == ["v009", "v008", "v007"]


def test_limit_zero_keeps_everything():
    assert len(ebook.select_videos(_entries(10), limit=0)) == 10


def test_tag_and_channel_filters_apply_before_the_limit():
    entries = _entries(4)
    entries[0]["tags"] = ["Rust"]
    entries[1]["channel_id"] = "UC2"
    assert [v["video_id"] for v in ebook.select_videos(entries, tag="Rust", limit=100)] == ["v000"]
    assert [v["video_id"] for v in ebook.select_videos(entries, channel="UC2", limit=100)] == ["v001"]


def test_explicit_video_ids_win_over_order():
    picked = ebook.select_videos(_entries(5), videos=["v001", "v003"], limit=100)
    assert sorted(v["video_id"] for v in picked) == ["v001", "v003"]


def test_defaults():
    args = ebook.parse_args([])
    assert args.limit == 100 and args.read == "split" and args.lang == "de"
    assert args.thumbnails is True and args.transcripts is True


def test_hours_and_all_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        ebook.parse_args(["--hours", "24", "--all"])
