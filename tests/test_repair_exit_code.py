"""repair.py signals "stored content changed" through its exit code, the same
way collect.py signals "new videos landed" -- collect.sh's cron wrapper only
re-exports the archive on that code, so repair.py must produce it too when a
summary is written, --fix-links rewrites a summary file, or --remap-tags
rewrites tags (tags feed the archive's filters and chips, so they count as a
content change as well).
"""
import os
import sys
import types

import pytest

REPO = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, REPO)

import repair


def _args(**overrides):
    base = dict(
        dry_run=False,
        video=None,
        force_summarize=False,
        no_proxy=False,
        model=None,
        fix_links=False,
        remap_tags=False,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


# ── _exit_code: the pure decision, no store/network involved ────────────────

def test_no_op_run_exits_zero():
    assert repair._exit_code(dry_run=False, changed=False) == 0


def test_written_change_exits_with_the_content_changed_code():
    assert repair._exit_code(dry_run=False, changed=True) == repair.EXIT_CONTENT_CHANGED


def test_dry_run_never_signals_a_change_even_when_it_reports_one():
    """--dry-run writes nothing, so it must always exit 0 -- even though
    fix_links()/remap_tags() keep counting what they *would* have changed."""
    assert repair._exit_code(dry_run=True, changed=True) == 0
    assert repair._exit_code(dry_run=True, changed=False) == 0


def test_exit_code_constant_matches_collect_exit_new_videos():
    collect = pytest.importorskip("collect", reason="collect.py runtime deps unavailable")
    assert repair.EXIT_CONTENT_CHANGED == collect.EXIT_NEW_VIDEOS


# ── Each of the three repair paths actually triggers the signal ─────────────

def test_fix_links_path_triggers_the_signal(monkeypatch, tmp_path):
    monkeypatch.setattr(repair, "parse_args", lambda: _args(fix_links=True))
    monkeypatch.setattr(
        repair.store, "get_all_videos",
        lambda with_transcripts=True: [{"video_id": "vid1", "title": "T"}],
    )
    monkeypatch.setattr(repair.store, "SUMMARIES_DIR", tmp_path)
    (tmp_path / "vid1.html").write_text("<p>before</p>", encoding="utf-8")
    monkeypatch.setattr(repair.openrouter, "repair_summary_html", lambda html: html + " changed")

    assert repair.main() == repair.EXIT_CONTENT_CHANGED


def test_fix_links_path_dry_run_still_exits_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(repair, "parse_args", lambda: _args(fix_links=True, dry_run=True))
    monkeypatch.setattr(
        repair.store, "get_all_videos",
        lambda with_transcripts=True: [{"video_id": "vid1", "title": "T"}],
    )
    monkeypatch.setattr(repair.store, "SUMMARIES_DIR", tmp_path)
    (tmp_path / "vid1.html").write_text("<p>before</p>", encoding="utf-8")
    monkeypatch.setattr(repair.openrouter, "repair_summary_html", lambda html: html + " changed")

    assert repair.main() == 0
    # dry-run must not have written anything back
    assert (tmp_path / "vid1.html").read_text(encoding="utf-8") == "<p>before</p>"


def test_remap_tags_path_triggers_the_signal(monkeypatch):
    monkeypatch.setattr(repair, "parse_args", lambda: _args(remap_tags=True))
    monkeypatch.setattr(
        repair.store, "get_all_videos",
        lambda with_transcripts=True: [
            {"video_id": "vid1", "title": "T", "tags": ["Definitely Not A Real Tag 12345"]}
        ],
    )
    monkeypatch.setattr(repair.store, "update_tags", lambda vid, tags: None)

    assert repair.main() == repair.EXIT_CONTENT_CHANGED


def test_remap_tags_path_no_op_exits_zero(monkeypatch):
    monkeypatch.setattr(repair, "parse_args", lambda: _args(remap_tags=True))
    monkeypatch.setattr(
        repair.store, "get_all_videos",
        lambda with_transcripts=True: [{"video_id": "vid1", "title": "T", "tags": []}],
    )
    monkeypatch.setattr(repair.store, "update_tags", lambda vid, tags: None)

    assert repair.main() == 0


def test_summarize_path_triggers_the_signal(monkeypatch):
    """A video already in the store with a transcript but no summary gets one
    written -- the same gap collect.py fills in its `existing` branch."""
    monkeypatch.setattr(repair, "parse_args", lambda: _args())
    monkeypatch.setattr(repair.tr, "log_proxy_config", lambda no_proxy=False: None)
    monkeypatch.setattr(
        repair.store, "get_all_videos",
        lambda with_transcripts=True: [{
            "video_id": "vid1",
            "title": "T",
            "transcript": "some transcript text",
            "summary": None,
            "transcript_error": None,
            "tags": [],
        }],
    )
    monkeypatch.setattr(repair.store, "get_llm_transcript_path", lambda vid: None)
    monkeypatch.setattr(
        repair.openrouter, "summarize_video",
        lambda vid, title, transcript, model: ("<p>summary</p>", ["Tag1"]),
    )
    monkeypatch.setattr(repair.store, "update_video_with_summary", lambda *a, **kw: None)

    assert repair.main() == repair.EXIT_CONTENT_CHANGED


def test_summarize_path_no_op_exits_zero(monkeypatch):
    """Everything already complete -- nothing fetched, nothing summarized."""
    monkeypatch.setattr(repair, "parse_args", lambda: _args())
    monkeypatch.setattr(repair.tr, "log_proxy_config", lambda no_proxy=False: None)
    monkeypatch.setattr(
        repair.store, "get_all_videos",
        lambda with_transcripts=True: [{
            "video_id": "vid1",
            "title": "T",
            "transcript": "already there",
            "summary": "<p>already there</p>",
            "transcript_error": None,
            "tags": [],
        }],
    )

    assert repair.main() == 0
