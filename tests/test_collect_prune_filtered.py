"""--prune-filtered removes what VIDEO_TITLE_FILTERS would skip today.

The title filter only ever applied to videos on their way in, so switching it
on leaves everything it would have skipped sitting in the archive. This is the
reversal, and it deliberately reads the store's titles through the same
_should_filter_title() the collect loop uses -- so the two can never disagree
about what counts as a match.
"""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, REPO)

collect = pytest.importorskip("collect", reason="collect.py runtime deps unavailable")
import store as store_module


def _add(vid, title):
    store_module.add_video(
        {
            "video_id": vid,
            "channel_id": "chan1",
            "channel_title": "Kanal",
            "title": title,
            "published_at": "2026-09-01T10:00:00Z",
            "thumbnail_url": "https://example.com/t.jpg",
            "duration": "PT10M",
            "tags": [],
            "collected_at": "2026-09-01T10:05:00Z",
        }
    )


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store_module, "DB_PATH", tmp_path / "videos.db")
    monkeypatch.setattr(store_module, "TRANSCRIPTS_DIR", tmp_path / "transcripts")
    monkeypatch.setattr(store_module, "SUMMARIES_DIR", tmp_path / "summaries")
    (tmp_path / "transcripts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "summaries").mkdir(parents=True, exist_ok=True)
    _add("keep1", "Rust im Kernel")
    _add("drop1", "Letsplay Teil 3")
    _add("drop2", "MEIN LETSPLAY FINALE")
    return store_module


def test_matches_are_found_case_insensitively(store, monkeypatch):
    monkeypatch.setenv("VIDEO_TITLE_FILTERS", "Letsplay")
    found = {m[0] for m in collect.find_filtered_in_store()}
    assert found == {"drop1", "drop2"}


def test_the_channel_is_reported_so_a_false_positive_can_be_spotted(store, monkeypatch):
    monkeypatch.setenv("VIDEO_TITLE_FILTERS", "Letsplay")
    channels = {m[1] for m in collect.find_filtered_in_store()}
    assert channels == {"Kanal"}


def test_the_matched_pattern_is_reported_for_eyeballing(store, monkeypatch):
    monkeypatch.setenv("VIDEO_TITLE_FILTERS", "Rust,Letsplay")
    patterns = {m[0]: m[3] for m in collect.find_filtered_in_store()}
    assert patterns == {"keep1": "Rust", "drop1": "Letsplay", "drop2": "Letsplay"}


def test_pruning_deletes_the_matches_and_keeps_the_rest(store, monkeypatch, tmp_path):
    monkeypatch.setenv("VIDEO_TITLE_FILTERS", "Letsplay")
    (tmp_path / "summaries" / "drop1.html").write_text("<p>x</p>", encoding="utf-8")

    assert collect.prune_filtered() == 2
    assert store.get_video("drop1") is None
    assert store.get_video("drop2") is None
    assert store.get_video("keep1") is not None
    assert not (tmp_path / "summaries" / "drop1.html").exists()


def test_a_dry_run_writes_nothing_and_reports_zero(store, monkeypatch, capsys):
    # Zero, not two: the count is what reached _exit_code(), and a dry run must
    # not make a caller re-export an archive it did not change.
    monkeypatch.setenv("VIDEO_TITLE_FILTERS", "Letsplay")
    assert collect.prune_filtered(dry_run=True) == 0
    assert store.get_video("drop1") is not None
    out = capsys.readouterr().out
    assert "drop1" in out and "2 entry(s) would be deleted" in out


def test_an_empty_filter_list_deletes_nothing(store, monkeypatch):
    monkeypatch.setenv("VIDEO_TITLE_FILTERS", "   ")
    assert collect.prune_filtered() == 0
    assert store.get_video("drop1") is not None


def test_an_unset_filter_list_deletes_nothing(store, monkeypatch):
    monkeypatch.delenv("VIDEO_TITLE_FILTERS", raising=False)
    assert collect.prune_filtered() == 0
    assert store.get_video("drop1") is not None


def test_no_match_leaves_the_store_alone(store, monkeypatch):
    monkeypatch.setenv("VIDEO_TITLE_FILTERS", "Kochshow")
    assert collect.prune_filtered() == 0
    assert store.get_video("drop1") is not None


def test_an_invalid_regex_aborts_before_anything_is_deleted(store, monkeypatch):
    # _should_filter_title() exits 1 on a broken pattern. The scan runs before
    # the delete, so a typo in .env can never take half the archive with it.
    monkeypatch.setenv("VIDEO_TITLE_FILTERS", "Letsplay,[unterminated")
    with pytest.raises(SystemExit) as exc:
        collect.prune_filtered()
    assert exc.value.code == 1
    assert store.get_video("drop1") is not None


def test_deleting_reports_the_changed_archive_through_the_exit_code():
    assert collect._exit_code(0, 0, 0) == 0
    assert collect._exit_code(0, 0, 3) == collect.EXIT_NEW_VIDEOS
