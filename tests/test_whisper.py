"""Tests for whisper_transcribe.py and whisper_collect.py (no network, no ffmpeg)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pathlib import Path

import pytest

import whisper_collect
import whisper_transcribe as wt

CHAN = "UCxmyVr1rPn-SIH0GbyV4jkg"


# ── whisper_transcribe ────────────────────────────────────────────────────────

def test_segments_land_on_one_timeline_shifted_by_their_piece():
    pieces = [
        (0.0, [{"start": 0.0, "text": " Welcome"}, {"start": 30.5, "text": "first"}]),
        (600.02, [{"start": 1.0, "text": "second piece"}]),
    ]
    entries = wt.merge_segments(pieces)
    assert [(round(e.start, 2), e.text) for e in entries] == [
        (0.0, "Welcome"), (30.5, "first"), (601.02, "second piece"),
    ]


def test_music_markers_are_dropped():
    pieces = [(0.0, [{"start": 0.0, "text": " ♪ ♪"}, {"start": 1, "text": "Music"},
                     {"start": 2, "text": "  "}, {"start": 3, "text": "Hello"}])]
    assert [e.text for e in wt.merge_segments(pieces)] == ["Hello"]


def test_the_merged_transcript_carries_the_usual_markers():
    from transcripts import _to_text
    entries = wt.merge_segments([(0.0, [{"start": 0, "text": "a"}]),
                                 (600.0, [{"start": 5, "text": "b"}])])
    assert _to_text(entries) == "[0:00] a\n[10:05] b"


def test_segment_list_offsets_are_read_not_assumed(tmp_path):
    csv_text = "chunk000.m4a,0.000000,600.023220\nchunk001.m4a,600.023220,1200.000000\n"
    assert wt.parse_segment_list(csv_text, tmp_path) == [
        (tmp_path / "chunk000.m4a", 0.0), (tmp_path / "chunk001.m4a", 600.02322),
    ]


@pytest.mark.parametrize("answer,code", [
    ("en", "en"), ("EN", "en"), ("English", "en"), ("german", "de"),
    ("klingon", None), ("", None), (None, None),
])
def test_language_answers_become_codes(answer, code):
    assert wt.language_code(answer) == code


def test_the_original_audio_track_is_asked_for_first():
    # A plain bitrate filter picked the Tamil AI dub of an English podcast.
    assert wt.AUDIO_FORMAT.startswith("bestaudio[language_preference>=10]")


def test_the_transcription_client_ignores_llm_base_url(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    assert str(wt._client().base_url).startswith("https://openrouter.ai/api/v1")


def test_transcribe_video_cleans_up_and_sums_cost(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(wt, "WORK_DIR", tmp_path)
    seen = {}

    def fake_download(vid, workdir, no_proxy=False):
        seen["workdir"] = workdir
        (workdir / "audio.m4a").write_bytes(b"x")
        return workdir / "audio.m4a"

    monkeypatch.setattr(wt, "download_audio", fake_download)
    monkeypatch.setattr(wt, "split_audio", lambda audio, workdir: [(workdir / "c0", 0.0), (workdir / "c1", 600.0)])
    answers = iter([
        {"language": "english", "usage": {"cost": 0.004}, "segments": [{"start": 0, "text": "Hi"}]},
        {"language": "english", "usage": {"cost": 0.002}, "segments": [{"start": 3, "text": "there"}]},
    ])
    monkeypatch.setattr(wt, "transcribe_chunk", lambda client, path, model: next(answers))
    text, lang, cost = wt.transcribe_video("vid")
    assert text == "[0:00] Hi\n[10:03] there"
    assert lang == "en"
    assert cost == pytest.approx(0.006)
    assert not seen["workdir"].exists()


def test_a_failed_download_returns_no_text(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(wt, "WORK_DIR", tmp_path)
    monkeypatch.setattr(wt, "download_audio", lambda vid, workdir, no_proxy=False: None)
    assert wt.transcribe_video("vid") == (None, None, 0.0)


# ── whisper_collect ───────────────────────────────────────────────────────────

def _entry(vid, channel=CHAN, error="unavailable", duration="PT30M", **kw):
    return {"video_id": vid, "channel_id": channel, "transcript_error": error,
            "duration": duration, "title": f"T {vid}", "published_at": "2026-10-01T00:00:00Z", **kw}


def test_selection_takes_listed_channels_without_transcript_only():
    entries = [
        _entry("a"),
        _entry("other", channel="UCother"),
        _entry("has", error=None),
        _entry("geo", error="country_blocked"),
        _entry("failed", error=whisper_collect.FAILED),
        _entry("live", duration=None),
    ]
    picked = whisper_collect.select_videos(entries, {CHAN}, has_transcript=lambda v: v == "has")
    assert [e["video_id"] for e in picked] == ["a"]


def test_retry_brings_back_failed_ones_and_video_restricts():
    entries = [_entry("a"), _entry("failed", error=whisper_collect.FAILED)]
    picked = whisper_collect.select_videos(entries, {CHAN}, lambda v: False, retry=True, only={"failed"})
    assert [e["video_id"] for e in picked] == ["failed"]


@pytest.fixture
def store_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(whisper_collect.store, "update_video_with_summary",
                        lambda vid, t, s, err, **kw: calls.append((vid, t, s, err, kw)))
    return calls


def test_process_stores_transcript_then_summary(monkeypatch, store_calls):
    monkeypatch.setattr(whisper_collect.whisper_transcribe, "transcribe_video",
                        lambda vid, no_proxy=False: ("[0:00] hi", "en", 0.01))
    monkeypatch.setattr(whisper_collect.openrouter, "summarize_video",
                        lambda vid, title, text, model, channel=None: ("<p>s</p>", ["KI"]))
    assert whisper_collect.process(_entry("a", channel_title="AdExchanger"), "m") is True
    assert store_calls[0][:4] == ("a", "[0:00] hi", None, None)
    assert store_calls[0][4]["transcript_lang"] == "en"
    assert store_calls[1][:4] == ("a", None, "<p>s</p>", None)
    assert store_calls[1][4] == {"summary_model": "m", "tags": ["KI"]}


def test_nothing_transcribed_marks_the_video(monkeypatch, store_calls):
    monkeypatch.setattr(whisper_collect.whisper_transcribe, "transcribe_video",
                        lambda vid, no_proxy=False: (None, None, 0.0))
    whisper_collect.process(_entry("a"), "m")
    assert store_calls == [("a", None, None, whisper_collect.FAILED,
                            {"summary_model": None, "tags": None})]


def test_a_rejected_summary_keeps_the_transcript(monkeypatch, store_calls):
    monkeypatch.setattr(whisper_collect.whisper_transcribe, "transcribe_video",
                        lambda vid, no_proxy=False: ("[0:00] hi", "en", 0.0))

    def reject(*a, **k):
        raise whisper_collect.openrouter.SummaryRejected("cut off")
    monkeypatch.setattr(whisper_collect.openrouter, "summarize_video", reject)
    assert whisper_collect.process(_entry("a"), "m") is True
    assert len(store_calls) == 1 and store_calls[0][1] == "[0:00] hi"


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(whisper_collect.store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(whisper_collect.store, "get_llm_transcript_path", lambda vid: None)
    monkeypatch.setenv("WHISPER_CHANNELS", CHAN)
    return tmp_path


def test_main_exits_10_when_it_wrote_and_honours_the_limit(monkeypatch, isolated):
    monkeypatch.setattr(whisper_collect.store, "get_all_videos",
                        lambda with_transcripts=True: [_entry("a"), _entry("b"), _entry("c")])
    done = []
    monkeypatch.setattr(whisper_collect, "process", lambda e, model, no_proxy=False: done.append(e["video_id"]) or True)
    assert whisper_collect.main(["--limit", "2"]) == whisper_collect.EXIT_CONTENT_CHANGED
    assert done == ["a", "b"]


def test_main_one_failing_video_does_not_end_the_run(monkeypatch, isolated):
    monkeypatch.setattr(whisper_collect.store, "get_all_videos",
                        lambda with_transcripts=True: [_entry("a"), _entry("b")])

    def process(e, model, no_proxy=False):
        if e["video_id"] == "a":
            raise RuntimeError("provider down")
        return True
    monkeypatch.setattr(whisper_collect, "process", process)
    assert whisper_collect.main([]) == whisper_collect.EXIT_CONTENT_CHANGED


def test_main_dry_run_and_unconfigured_exit_zero(monkeypatch, isolated):
    monkeypatch.setattr(whisper_collect.store, "get_all_videos", lambda with_transcripts=True: [_entry("a")])
    monkeypatch.setattr(whisper_collect, "process", lambda *a, **k: pytest.fail("dry run must not process"))
    assert whisper_collect.main(["--dry-run"]) == 0
    monkeypatch.setenv("WHISPER_CHANNELS", "")
    assert whisper_collect.main([]) == 0


def test_main_skips_when_another_run_holds_the_lock(monkeypatch, isolated):
    monkeypatch.setattr(whisper_collect.store, "get_all_videos", lambda with_transcripts=True: [_entry("a")])
    monkeypatch.setattr(whisper_collect, "process", lambda *a, **k: pytest.fail("must not run twice"))
    held = whisper_collect._lock()
    try:
        assert whisper_collect.main([]) == 0
    finally:
        held.close()


def test_main_removes_leftover_audio_of_a_killed_run(monkeypatch, isolated):
    (isolated / "whisper-abc").mkdir()
    (isolated / "whisper-abc" / "audio.m4a").write_bytes(b"x")
    monkeypatch.setattr(whisper_collect.store, "get_all_videos", lambda with_transcripts=True: [_entry("a")])
    monkeypatch.setattr(whisper_collect, "process", lambda *a, **k: True)
    whisper_collect.main([])
    assert not (isolated / "whisper-abc").exists()


def test_collect_sh_runs_the_whisper_step_before_the_export_gate():
    sh = Path(__file__).resolve().parent.parent.joinpath("collect.sh").read_text(encoding="utf-8")
    whisper_at = sh.index("python3 whisper_collect.py")
    assert whisper_at < sh.index("python3 export.py")
    assert "|| wrc=$?" in sh[whisper_at:sh.index("\n", whisper_at)]
