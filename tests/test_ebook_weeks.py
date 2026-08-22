import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import epub_builder
from export_harness import video


def test_videos_are_grouped_into_iso_weeks_in_chronological_order():
    weeks = epub_builder.group_by_week([
        video("c", "2026-08-21T10:00:00Z"),   # KW 34
        video("a", "2026-08-12T10:00:00Z"),   # KW 33
        video("b", "2026-08-19T10:00:00Z"),   # KW 34
    ])
    assert [(w["iso_year"], w["iso_week"]) for w in weeks] == [(2026, 33), (2026, 34)]
    assert [v["video_id"] for v in weeks[1]["videos"]] == ["b", "c"]


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
    assert [(w["iso_year"], w["iso_week"]) for w in weeks] == [(2025, 1), (2026, 1)]
    assert len(weeks) == 2
    assert [w["anchor"] for w in weeks] == ["w-2025-01", "w-2026-01"]


def test_anchor_is_stable_and_filename_safe():
    weeks = epub_builder.group_by_week([video("a", "2026-08-19T10:00:00Z")])
    assert weeks[0]["anchor"] == "w-2026-34"
