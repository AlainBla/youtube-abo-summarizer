"""A failed transcript re-fetch must not strip a video of its tags and model.

update_video_with_summary() writes summary_model and tags unconditionally;
repair.py used to pass neither, so 18 videos whose re-fetch hit ip_blocked
lost their tags (and dropped out of the tag filter) while keeping their
summaries.
"""
import os
import sys
import types

REPO = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, REPO)

import repair
import store


def _entry():
    return {"video_id": "vid", "title": "T", "channel_title": "C", "transcript": None,
            "summary": "<p>old</p>", "transcript_error": None,
            "summary_model": "model-x", "tags": ["Geopolitik", "Wirtschaft"]}


def _run(monkeypatch, fetch_result, summarize=None):
    calls = []
    monkeypatch.setattr(repair, "parse_args", lambda: types.SimpleNamespace(
        dry_run=False, video=None, force_summarize=False, no_proxy=False, model="m",
        fix_links=False, remap_tags=False, fill_durations=False))
    monkeypatch.setattr(repair.tr, "log_proxy_config", lambda **kw: None)
    monkeypatch.setattr(repair.tr, "get_transcript", lambda vid: fetch_result)
    monkeypatch.setattr(repair.time, "sleep", lambda s: None)
    monkeypatch.setattr(store, "get_all_videos", lambda **kw: [_entry()])
    monkeypatch.setattr(store, "get_llm_transcript_path", lambda vid: None)
    monkeypatch.setattr(store, "update_video_with_summary",
                        lambda *a, **kw: calls.append((a, kw)))
    if summarize:
        monkeypatch.setattr(repair.openrouter, "summarize_video", summarize)
    repair.main()
    return calls


def test_failed_fetch_keeps_tags_and_model(monkeypatch):
    calls = _run(monkeypatch, (None, None, "ip_blocked"))
    (args, kw), = calls
    assert args[3] == "ip_blocked"
    assert kw["tags"] == ["Geopolitik", "Wirtschaft"]
    assert kw["summary_model"] == "model-x"


def test_rejected_summary_after_fetch_keeps_tags(monkeypatch):
    def reject(*a, **kw):
        raise repair.openrouter.SummaryRejected("cut off")
    calls = _run(monkeypatch, ("transcript", "en", None), summarize=reject)
    (args, kw), = calls     # only the transcript write; the summary was rejected
    assert kw["transcript_lang"] == "en"
    assert kw["tags"] == ["Geopolitik", "Wirtschaft"]
