"""Long-video timestamp markers and links past the video's end.

znzYWHPv1WY (3:44 h) carried markers like "[220:13]"; the model stopped using
them at 22:00 and invented round labels up to 6:30:00. Markers past the first
hour are now [H:MM:SS], old transcripts are rewritten before the model sees
them, and links past the last marker are dropped.
"""
import sys, os
from types import SimpleNamespace
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import openrouter
from openrouter import _normalize_markers, _transcript_duration, _drop_links_past_end
from transcripts import _to_text, _marker


def _link(secs, label):
    return f'<a href="https://www.youtube.com/watch?v=X&t={secs}" class="ts-link">{label}</a>'


def test_marker_switches_to_hours_at_one_hour():
    assert _marker(0) == "[0:00]"
    assert _marker(3599) == "[59:59]"
    assert _marker(3600) == "[1:00:00]"
    assert _marker(13213) == "[3:40:13]"


def test_to_text_uses_hour_markers_on_long_videos():
    entries = [SimpleNamespace(start=0.0, text="a"), SimpleNamespace(start=13213.4, text="b")]
    assert _to_text(entries) == "[0:00] a\n[3:40:13] b"


def test_old_minute_markers_are_rewritten():
    t = "[0:00] a\n[59:58] b\n[60:40] c\n[220:13] d"
    assert _normalize_markers(t) == "[0:00] a\n[59:58] b\n[1:00:40] c\n[3:40:13] d"


def test_normalize_is_idempotent():
    t = "[0:00] a\n[1:00:40] c"
    assert _normalize_markers(t) == t


def test_duration_reads_both_forms():
    assert _transcript_duration("[0:00] a\n[224:15] b") == 13455
    assert _transcript_duration("[0:00] a\n[3:44:15] b") == 13455
    assert _transcript_duration("no markers") is None


def test_links_past_the_end_are_dropped():
    html = (f"<h3>Fazit {_link(23400, '6:30:00')}</h3>\n"
            f"<p>Echt. {_link(1320, '22:00')} Erfunden. {_link(14400, '4:00:00')}</p>")
    assert _drop_links_past_end(html, 13455) == (
        "<h3>Fazit</h3>\n" f"<p>Echt. {_link(1320, '22:00')} Erfunden.</p>")


def test_link_just_after_the_last_marker_is_kept():
    html = f"<p>Ende. {_link(13455 + 120, '3:46:15')}</p>"
    assert _drop_links_past_end(html, 13455) == html


def test_unknown_duration_keeps_everything():
    html = f"<p>x {_link(99999, '27:46:39')}</p>"
    assert _drop_links_past_end(html, None) == html


def test_summarize_video_normalizes_and_drops(monkeypatch):
    sent = {}

    class FakeCompletions:
        def create(self, **kw):
            sent["user"] = kw["messages"][1]["content"]
            content = (f"<h3>A {_link(0, '0:00')}</h3>\n<p>x. {_link(13213, '3:40:13')}"
                       f" y. {_link(23400, '6:30:00')}</p>\n<!-- tags: Geopolitik -->")
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content), finish_reason="stop")])

    monkeypatch.setattr(openrouter, "build_client",
                        lambda: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())))
    monkeypatch.setattr(openrouter.tag_vocab, "record_candidates", lambda rejected: None)
    html, _ = openrouter.summarize_video("X", "T", "[0:00] a\n[220:13] b", "m")
    assert "[3:40:13] b" in sent["user"] and "[220:13]" not in sent["user"]
    assert "Video duration: 3:40:13" in sent["user"]
    assert "t=13213" in html and "t=23400" not in html


def test_fix_links_drops_links_past_the_stored_transcript(tmp_path, monkeypatch, capsys):
    import repair, store
    monkeypatch.setattr(store, "SUMMARIES_DIR", tmp_path)
    (tmp_path / "X.html").write_text(
        f"<p>a. {_link(60, '1:00')} b. {_link(5000, '1:23:20')}</p>", encoding="utf-8")
    t = tmp_path / "X.de.txt"
    t.write_text("[0:00] a\n[20:00] b", encoding="utf-8")
    monkeypatch.setattr(store, "get_llm_transcript_path", lambda vid: t)
    assert repair.fix_links([{"video_id": "X", "title": "T"}], dry_run=False) == 1
    assert (tmp_path / "X.html").read_text(encoding="utf-8") == f"<p>a. {_link(60, '1:00')} b.</p>"


def test_fix_links_without_transcript_keeps_links(tmp_path, monkeypatch):
    import repair, store
    monkeypatch.setattr(store, "SUMMARIES_DIR", tmp_path)
    html = f"<p>a. {_link(5000, '1:23:20')}</p>"
    (tmp_path / "X.html").write_text(html, encoding="utf-8")
    monkeypatch.setattr(store, "get_llm_transcript_path", lambda vid: None)
    assert repair.fix_links([{"video_id": "X", "title": "T"}], dry_run=False) == 0


def _fake_client(monkeypatch, contents):
    calls = []

    class FakeCompletions:
        def create(self, **kw):
            calls.append(kw)
            content = contents[min(len(calls), len(contents)) - 1]
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content), finish_reason="stop")])

    monkeypatch.setattr(openrouter, "build_client",
                        lambda: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())))
    monkeypatch.setattr(openrouter.tag_vocab, "record_candidates", lambda rejected: None)
    return calls


STRETCHED = f"<p>a. {_link(60, '1:00')} b. {_link(3000, '50:00')}</p>\n<!-- tags: Geopolitik -->"
GOOD = f"<p>a. {_link(60, '1:00')} b. {_link(1200, '20:00')}</p>\n<!-- tags: Geopolitik -->"
TRANSCRIPT = "[0:00] a\n[20:00] b"


def test_stretched_summary_is_retried_once(monkeypatch):
    calls = _fake_client(monkeypatch, [STRETCHED, GOOD])
    html, _ = openrouter.summarize_video("X", "T", TRANSCRIPT, "m")
    assert len(calls) == 2
    assert "t=1200" in html and "t=3000" not in html


def test_clean_summary_is_not_retried(monkeypatch):
    calls = _fake_client(monkeypatch, [GOOD])
    openrouter.summarize_video("X", "T", TRANSCRIPT, "m")
    assert len(calls) == 1


def test_retry_that_stretches_again_keeps_first_and_drops(monkeypatch):
    calls = _fake_client(monkeypatch, [STRETCHED, STRETCHED, GOOD])
    html, _ = openrouter.summarize_video("X", "T", TRANSCRIPT, "m")
    assert len(calls) == 2  # never a third attempt
    assert "t=60" in html and "t=3000" not in html


def test_rejected_retry_keeps_the_first_summary(monkeypatch):
    truncated = "<p>Mitten im Satz und dann"
    calls = _fake_client(monkeypatch, [STRETCHED, truncated])
    html, _ = openrouter.summarize_video("X", "T", TRANSCRIPT, "m")
    assert len(calls) == 2
    assert html == f"<p>a. {_link(60, '1:00')} b.</p>"
