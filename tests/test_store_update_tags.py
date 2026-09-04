"""Tests for update_tags() — writing tags without touching anything else."""
import importlib
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

import store as store_module


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A store rooted in a temp directory."""
    monkeypatch.setattr(store_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store_module, "DB_PATH", tmp_path / "videos.db")
    monkeypatch.setattr(store_module, "TRANSCRIPTS_DIR", tmp_path / "transcripts")
    monkeypatch.setattr(store_module, "SUMMARIES_DIR", tmp_path / "summaries")
    store_module.add_video(
        {
            "video_id": "vid1",
            "channel_id": "chan1",
            "channel_title": "Kanal",
            "title": "Titel",
            "published_at": "2026-09-01T10:00:00Z",
            "thumbnail_url": "https://example.com/t.jpg",
            "duration": "PT10M",
            "tags": ["Alt-Tag"],
            "collected_at": "2026-09-01T10:05:00Z",
        }
    )
    store_module.update_video_with_summary(
        "vid1",
        transcript=None,
        summary=None,
        transcript_error="ip_blocked",
        summary_model="test-model",
        tags=["Alt-Tag"],
    )
    return store_module


def test_tags_are_replaced(store):
    store.update_tags("vid1", ["Gaming", "Indie-Spiele"])
    assert store.get_video("vid1")["tags"] == ["Gaming", "Indie-Spiele"]


def test_other_columns_are_left_alone(store):
    store.update_tags("vid1", ["Gaming"])
    entry = store.get_video("vid1")
    assert entry["transcript_error"] == "ip_blocked"
    assert entry["summary_model"] == "test-model"
    assert entry["title"] == "Titel"


def test_an_empty_list_clears_the_tags(store):
    store.update_tags("vid1", [])
    assert store.get_video("vid1")["tags"] == []


def test_umlauts_are_stored_unescaped(store):
    store.update_tags("vid1", ["Künstliche Intelligenz"])
    raw = store._conn().execute(
        "SELECT tags FROM videos WHERE video_id = 'vid1'"
    ).fetchone()[0]
    assert "Künstliche Intelligenz" in raw
