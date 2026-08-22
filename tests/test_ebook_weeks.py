import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import epub_builder
from export_harness import video


def test_videos_are_grouped_into_iso_weeks_newest_first():
    weeks = epub_builder.group_by_week([
        video("c", "2026-08-21T10:00:00Z"),   # KW 34
        video("a", "2026-08-12T10:00:00Z"),   # KW 33
        video("b", "2026-08-19T10:00:00Z"),   # KW 34
    ])
    assert [(w["iso_year"], w["iso_week"]) for w in weeks] == [(2026, 34), (2026, 33)]
    assert [v["video_id"] for v in weeks[0]["videos"]] == ["c", "b"]


def test_week_boundaries_are_monday_to_sunday():
    weeks = epub_builder.group_by_week([video("a", "2026-08-19T10:00:00Z")])
    assert weeks[0]["start"].isoformat() == "2026-08-17"
    assert weeks[0]["end"].isoformat() == "2026-08-23"


def test_new_year_does_not_collapse_two_different_week_ones():
    # 2026-01-01 is in ISO week 1 of 2026; 2024-12-31 is in ISO week 1 of 2025.
    weeks = epub_builder.group_by_week([
        video("old", "2024-12-31T10:00:00Z"),
        video("new", "2026-01-01T10:00:00Z"),
    ])
    assert [(w["iso_year"], w["iso_week"]) for w in weeks] == [(2026, 1), (2025, 1)]
    assert len(weeks) == 2
    assert [w["anchor"] for w in weeks] == ["w-2026-01", "w-2025-01"]


def test_anchor_is_stable_and_filename_safe():
    weeks = epub_builder.group_by_week([video("a", "2026-08-19T10:00:00Z")])
    assert weeks[0]["anchor"] == "w-2026-34"


# ── Default order: newest first ─────────────────────────────────────────────
# A digest is read front to back starting with what just came in, so the book
# opens with the newest week and, inside it, the newest video.

def test_newest_week_comes_first_by_default():
    weeks = epub_builder.group_by_week([
        video("older", "2026-08-12T10:00:00Z"),   # KW 33
        video("newer", "2026-08-19T10:00:00Z"),   # KW 34
    ])
    assert [w["iso_week"] for w in weeks] == [34, 33]


def test_newest_video_comes_first_within_a_week():
    weeks = epub_builder.group_by_week([
        video("a", "2026-08-17T10:00:00Z"),
        video("c", "2026-08-21T10:00:00Z"),
        video("b", "2026-08-19T10:00:00Z"),
    ])
    assert [v["video_id"] for v in weeks[0]["videos"]] == ["c", "b", "a"]


def test_oldest_first_is_still_available_explicitly():
    weeks = epub_builder.group_by_week([
        video("older", "2026-08-12T10:00:00Z"),
        video("newer", "2026-08-19T10:00:00Z"),
    ], newest_first=False)
    assert [w["iso_week"] for w in weeks] == [33, 34]
    assert [v["video_id"] for v in weeks[0]["videos"]] == ["older"]
