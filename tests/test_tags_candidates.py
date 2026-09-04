"""Tests for the rejected-tag candidate log."""
import json
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

import tags


@pytest.fixture
def candidates_path(tmp_path, monkeypatch):
    path = tmp_path / "data" / "tag_candidates.json"
    monkeypatch.setattr(tags, "CANDIDATES_PATH", path)
    return path


def test_a_rejected_tag_is_counted(candidates_path):
    tags.record_candidates(["Bloodborne"])
    data = json.loads(candidates_path.read_text(encoding="utf-8"))
    assert data["Bloodborne"]["count"] == 1
    assert data["Bloodborne"]["last_seen"]


def test_counts_add_up_across_calls(candidates_path):
    tags.record_candidates(["Bloodborne"])
    tags.record_candidates(["Bloodborne", "Valve"])
    data = json.loads(candidates_path.read_text(encoding="utf-8"))
    assert data["Bloodborne"]["count"] == 2
    assert data["Valve"]["count"] == 1


def test_repeats_within_one_call_are_counted_once_each(candidates_path):
    tags.record_candidates(["Valve", "Valve"])
    data = json.loads(candidates_path.read_text(encoding="utf-8"))
    assert data["Valve"]["count"] == 2


def test_nothing_rejected_writes_no_file(candidates_path):
    tags.record_candidates([])
    assert not candidates_path.exists()


def test_umlauts_stay_readable_in_the_file(candidates_path):
    tags.record_candidates(["Straße von Hormus"])
    assert "Straße von Hormus" in candidates_path.read_text(encoding="utf-8")


def test_a_corrupt_log_does_not_raise(candidates_path):
    candidates_path.parent.mkdir(parents=True)
    candidates_path.write_text("{not json", encoding="utf-8")
    tags.record_candidates(["Valve"])          # must not raise
    data = json.loads(candidates_path.read_text(encoding="utf-8"))
    assert data["Valve"]["count"] == 1


def test_an_unwritable_path_does_not_raise(tmp_path, monkeypatch):
    """A summarize run must not fail because the log cannot be written."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(tags, "CANDIDATES_PATH", blocker / "tag_candidates.json")
    tags.record_candidates(["Valve"])          # must not raise


def test_load_candidates_on_a_missing_file_is_empty(candidates_path):
    assert tags.load_candidates() == {}
