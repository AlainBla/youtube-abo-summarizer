"""Tests for repair.py --remap-tags."""
import json
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

import repair
import tags


@pytest.fixture
def written(monkeypatch, tmp_path):
    """Capture store writes and point the alias table at a temp file."""
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(repair.store, "update_tags", lambda vid, t: calls.append((vid, t)))
    path = tmp_path / "tag_aliases.json"
    path.write_text(
        json.dumps({"Indie Game": ["Indie-Spiele"], "Bloodborne": ["Soulslike"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(tags, "ALIASES_PATH", path)
    tags.load_aliases.cache_clear()
    yield calls
    tags.load_aliases.cache_clear()


def test_old_tags_are_mapped_and_written(written):
    entries = [{"video_id": "vid1", "tags": ["Indie Game", "Bloodborne"]}]
    repair.remap_tags(entries, dry_run=False)
    assert written == [("vid1", ["Indie-Spiele", "Soulslike"])]


def test_duplicates_collapse(written):
    entries = [{"video_id": "vid1", "tags": ["Indie Game", "Indie-Spiele"]}]
    repair.remap_tags(entries, dry_run=False)
    assert written == [("vid1", ["Indie-Spiele"])]


def test_a_video_without_mappable_tags_ends_up_with_an_empty_list(written):
    entries = [{"video_id": "vid1", "tags": ["Straße von Hormus"]}]
    repair.remap_tags(entries, dry_run=False)
    assert written == [("vid1", [])]


def test_unchanged_tags_are_not_rewritten(written):
    entries = [{"video_id": "vid1", "tags": ["Gaming"]}]
    repair.remap_tags(entries, dry_run=False)
    assert written == []


def test_a_video_without_tags_is_skipped(written):
    entries = [{"video_id": "vid1", "tags": []}, {"video_id": "vid2", "tags": None}]
    repair.remap_tags(entries, dry_run=False)
    assert written == []


def test_dry_run_writes_nothing(written):
    entries = [{"video_id": "vid1", "tags": ["Indie Game"]}]
    repair.remap_tags(entries, dry_run=True)
    assert written == []


def test_remapping_does_not_log_candidates(written, monkeypatch):
    """Old tags are history, not suggestions — they must not pollute the log."""
    seen: list[list[str]] = []
    monkeypatch.setattr(tags, "record_candidates", lambda r: seen.append(list(r)))
    repair.remap_tags([{"video_id": "vid1", "tags": ["Straße von Hormus"]}], dry_run=False)
    assert seen == []
