"""Tests for store.py transcript_lang support."""
import json
import pytest
from datetime import datetime, timezone
from pathlib import Path


@pytest.fixture
def store_env(tmp_path, monkeypatch):
    """Patch store module to use a temporary directory."""
    import store
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "videos.db")
    monkeypatch.setattr(store, "TRANSCRIPTS_DIR", tmp_path / "transcripts")
    monkeypatch.setattr(store, "SUMMARIES_DIR", tmp_path / "summaries")
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "summaries").mkdir()
    return tmp_path


def _base_entry(vid_id="vid1", lang="ja"):
    return {
        "video_id": vid_id,
        "channel_id": "ch1",
        "channel_title": "Channel",
        "title": "Title",
        "published_at": "2026-01-01T00:00:00+00:00",
        "thumbnail_url": "https://example.com/thumb.jpg",
        "duration": "PT5M",
        "summary_model": None,
        "transcript": "Hello world",
        "transcript_lang": lang,
        "summary": None,
        "transcript_error": None,
        "tags": [],
        "collected_at": datetime.now(tz=timezone.utc).isoformat(),
    }


class TestAddVideo:
    def test_writes_lang_suffixed_transcript(self, store_env):
        import store
        store.add_video(_base_entry("vid1", "ja"))
        assert (store_env / "transcripts" / "vid1.ja.txt").exists()
        assert not (store_env / "transcripts" / "vid1.txt").exists()

    def test_stores_transcript_lang_in_db(self, store_env):
        import store
        store.add_video(_base_entry("vid1", "ja"))
        v = store.get_video("vid1")
        assert v["transcript_lang"] == "ja"


class TestGetVideo:
    def test_has_transcript_true_for_lang_suffixed_file(self, store_env):
        import store
        store.add_video(_base_entry("vid1", "ja"))
        v = store.get_video("vid1")
        assert v["has_transcript"] is True

    def test_has_transcript_true_for_legacy_txt(self, store_env):
        import store
        # Simulate legacy entry: no transcript_lang in DB, but <id>.txt exists
        entry = _base_entry("vid1", "ja")
        entry["transcript_lang"] = None
        entry["transcript"] = None
        store.add_video(entry)
        (store_env / "transcripts" / "vid1.txt").write_text("legacy", encoding="utf-8")
        v = store.get_video("vid1")
        assert v["has_transcript"] is True

    def test_has_manual_transcript_true_when_de_file_exists(self, store_env):
        import store
        store.add_video(_base_entry("vid1", "ja"))
        (store_env / "transcripts" / "vid1.de.txt").write_text("deutsch", encoding="utf-8")
        v = store.get_video("vid1")
        assert v["has_manual_transcript"] is True

    def test_has_manual_transcript_false_when_no_manual_file(self, store_env):
        import store
        store.add_video(_base_entry("vid1", "ja"))
        v = store.get_video("vid1")
        assert v["has_manual_transcript"] is False


class TestGetLlmTranscriptPath:
    def test_returns_de_when_de_file_exists(self, store_env):
        import store
        store.add_video(_base_entry("vid1", "ja"))
        de_path = store_env / "transcripts" / "vid1.de.txt"
        de_path.write_text("deutsch", encoding="utf-8")
        result = store.get_llm_transcript_path("vid1")
        assert result == de_path

    def test_returns_orig_lang_when_no_manual(self, store_env):
        import store
        store.add_video(_base_entry("vid1", "ja"))
        result = store.get_llm_transcript_path("vid1")
        assert result == store_env / "transcripts" / "vid1.ja.txt"

    def test_returns_legacy_txt_when_no_lang_in_db(self, store_env):
        import store
        entry = _base_entry("vid1", "ja")
        entry["transcript_lang"] = None
        entry["transcript"] = None
        store.add_video(entry)
        legacy = store_env / "transcripts" / "vid1.txt"
        legacy.write_text("legacy", encoding="utf-8")
        result = store.get_llm_transcript_path("vid1")
        assert result == legacy


class TestPruneOlderThan:
    def test_deletes_lang_suffixed_and_manual_files(self, store_env):
        import store
        entry = _base_entry("vid1", "ja")
        entry["published_at"] = "2020-01-01T00:00:00+00:00"
        store.add_video(entry)
        de_path = store_env / "transcripts" / "vid1.de.txt"
        de_path.write_text("deutsch", encoding="utf-8")
        store.prune_older_than(days=1)
        assert not (store_env / "transcripts" / "vid1.ja.txt").exists()
        assert not de_path.exists()


class TestGetAllVideos:
    def test_reads_lang_suffixed_transcript(self, store_env):
        import store
        store.add_video(_base_entry("vid1", "ja"))
        videos = store.get_all_videos()
        assert len(videos) == 1
        assert videos[0]["transcript"] == "Hello world"

    def test_reads_legacy_txt_transcript(self, store_env):
        import store
        entry = _base_entry("vid1", "ja")
        entry["transcript_lang"] = None
        entry["transcript"] = None
        store.add_video(entry)
        (store_env / "transcripts" / "vid1.txt").write_text("legacy text", encoding="utf-8")
        videos = store.get_all_videos()
        assert videos[0]["transcript"] == "legacy text"


class TestUpdateVideoWithSummary:
    def test_writes_lang_suffixed_transcript(self, store_env):
        import store
        store.add_video(_base_entry("vid1", "ja"))
        store.update_video_with_summary(
            "vid1", transcript="updated content", summary=None,
            transcript_error=None, transcript_lang="ja"
        )
        assert (store_env / "transcripts" / "vid1.ja.txt").read_text() == "updated content"

    def test_coalesce_preserves_existing_lang_when_none_passed(self, store_env):
        import store
        store.add_video(_base_entry("vid1", "ja"))
        # Pass transcript_lang=None — should NOT overwrite "ja" in DB
        store.update_video_with_summary(
            "vid1", transcript=None, summary=None,
            transcript_error=None, transcript_lang=None
        )
        v = store.get_video("vid1")
        assert v["transcript_lang"] == "ja"


class TestGetVideosSince:
    def test_reads_lang_suffixed_transcript(self, store_env):
        import store
        from datetime import datetime, timezone
        store.add_video(_base_entry("vid1", "ja"))
        since = datetime(2025, 1, 1, tzinfo=timezone.utc)
        videos = store.get_videos_since(since)
        assert len(videos) == 1
        assert videos[0]["transcript"] == "Hello world"

    def test_reads_legacy_txt_transcript(self, store_env):
        import store
        from datetime import datetime, timezone
        entry = _base_entry("vid1", "ja")
        entry["transcript_lang"] = None
        entry["transcript"] = None
        store.add_video(entry)
        (store_env / "transcripts" / "vid1.txt").write_text("legacy text", encoding="utf-8")
        since = datetime(2025, 1, 1, tzinfo=timezone.utc)
        videos = store.get_videos_since(since)
        assert videos[0]["transcript"] == "legacy text"
