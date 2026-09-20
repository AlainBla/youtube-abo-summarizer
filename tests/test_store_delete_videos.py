"""delete_videos() removes a row and every file that belonged to it.

It is the one place in store.py that deletes; prune_older_than() runs through
it, and collect.py's --prune-filtered does too. A transcript left behind would
be read by a later run for a row that no longer exists, so the file cleanup
matters as much as the row.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import store as store_module


def _add(vid, published="2026-09-01T10:00:00Z", title="Titel"):
    store_module.add_video(
        {
            "video_id": vid,
            "channel_id": "chan1",
            "channel_title": "Kanal",
            "title": title,
            "published_at": published,
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
    return store_module


def test_the_row_and_all_of_its_files_go(store, tmp_path):
    _add("vid1")
    # Both transcript spellings exist in the wild: the plain one and the
    # per-language variants get_llm_transcript_path() prefers.
    (tmp_path / "transcripts" / "vid1.txt").write_text("plain", encoding="utf-8")
    (tmp_path / "transcripts" / "vid1.de.txt").write_text("deutsch", encoding="utf-8")
    (tmp_path / "transcripts" / "vid1.en.txt").write_text("english", encoding="utf-8")
    (tmp_path / "summaries" / "vid1.html").write_text("<p>x</p>", encoding="utf-8")

    assert store.delete_videos(["vid1"]) == 1
    assert store.get_video("vid1") is None
    assert not list((tmp_path / "transcripts").glob("vid1*"))
    assert not (tmp_path / "summaries" / "vid1.html").exists()


def test_other_videos_are_untouched(store, tmp_path):
    _add("vid1")
    _add("vid2")
    (tmp_path / "transcripts" / "vid2.txt").write_text("bleibt", encoding="utf-8")

    store.delete_videos(["vid1"])
    assert store.get_video("vid2") is not None
    assert (tmp_path / "transcripts" / "vid2.txt").exists()


def test_an_unknown_id_is_not_an_error_and_is_not_counted(store):
    _add("vid1")
    assert store.delete_videos(["nope", "vid1", "nope2"]) == 1


def test_duplicates_in_the_selection_count_once(store):
    _add("vid1")
    assert store.delete_videos(["vid1", "vid1"]) == 1


def test_an_empty_selection_touches_nothing(store):
    _add("vid1")
    assert store.delete_videos([]) == 0
    assert store.get_video("vid1") is not None


def test_prune_older_than_still_deletes_by_date_through_the_same_path(store, tmp_path):
    _add("alt", published="2020-01-01T00:00:00Z")
    _add("neu", published="2026-09-01T10:00:00Z")
    (tmp_path / "summaries" / "alt.html").write_text("<p>alt</p>", encoding="utf-8")

    assert store.prune_older_than(days=30) == 1
    assert store.get_video("alt") is None
    assert store.get_video("neu") is not None
    assert not (tmp_path / "summaries" / "alt.html").exists()
