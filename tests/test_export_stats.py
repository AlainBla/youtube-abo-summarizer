"""The personal export writes down how big it was, run after run.

export_stats.py is pure file handling plus two lookups, so everything here
runs against a tmp_path JSONL file -- no renderer, no store.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import export_stats

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
FP = export_stats.fingerprint("me@example.com", 30, "all")


def _entry(days_ago, count, fp=None, total=5000):
    fp = fp or FP
    return {
        "ts": (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds"),
        **fp,
        "personal_count": count,
        "total_count": total,
    }


def _write(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


# ── the fingerprint ──────────────────────────────────────────────────────────

def test_the_window_label_tells_all_from_a_bounded_run():
    assert export_stats.window_label(True, None) == "all"
    assert export_stats.window_label(True, 168) == "all"
    assert export_stats.window_label(False, 48) == "hours:48"
    assert export_stats.window_label(False, None) == "all"


def test_a_different_read_days_is_a_different_series():
    entries = [_entry(1, 400), _entry(2, 390, fp=export_stats.fingerprint(
        "me@example.com", 7, "all"))]
    assert [e["personal_count"] for e in export_stats.series(entries, FP)] == [400]


def test_a_different_user_is_a_different_series():
    other = export_stats.fingerprint("someone@else.example", 30, "all")
    entries = [_entry(1, 400), _entry(1, 11, fp=other)]
    assert [e["personal_count"] for e in export_stats.series(entries, FP)] == [400]


def test_a_different_window_is_a_different_series():
    bounded = export_stats.fingerprint("me@example.com", 30, "hours:48")
    entries = [_entry(1, 400), _entry(1, 12, fp=bounded)]
    assert [e["personal_count"] for e in export_stats.series(entries, FP)] == [400]


def test_a_series_is_returned_oldest_first_whatever_the_file_order():
    entries = [_entry(1, 400), _entry(9, 350), _entry(5, 380)]
    counts = [e["personal_count"] for e in export_stats.series(entries, FP)]
    assert counts == [350, 380, 400]


# ── reading and writing the file ─────────────────────────────────────────────

def test_a_record_round_trips(tmp_path):
    path = tmp_path / "export_stats.jsonl"
    assert export_stats.record(_entry(0, 389), path) is True
    assert export_stats.record(_entry(1, 400), path) is True
    assert [e["personal_count"] for e in export_stats.load(path)] == [389, 400]


def test_a_missing_file_is_an_empty_history(tmp_path):
    assert export_stats.load(tmp_path / "nothing.jsonl") == []


def test_a_half_written_line_is_skipped_not_fatal(tmp_path):
    # What a killed export leaves behind. One truncated measurement must not
    # cost the whole history.
    path = tmp_path / "export_stats.jsonl"
    _write(path, [_entry(2, 350), _entry(1, 380)])
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"ts": "2026-09-20T12:00:00+00:00", "personal_c')
    assert [e["personal_count"] for e in export_stats.load(path)] == [350, 380]


def test_a_failed_write_is_reported_but_does_not_raise(tmp_path, capsys):
    # The export is the product, the statistic a convenience: a read-only
    # data/ costs a warning, never the run.
    path = tmp_path / "readonly-dir" / "export_stats.jsonl"
    path.parent.mkdir()
    path.parent.chmod(0o500)
    try:
        assert export_stats.record(_entry(0, 389), path) is False
        assert "could not write" in capsys.readouterr().err
    finally:
        path.parent.chmod(0o700)


# ── the tooltip's numbers ────────────────────────────────────────────────────

def test_each_window_reports_the_last_count_at_or_before_that_day():
    entries = [_entry(0, 389), _entry(7, 412), _entry(30, 350), _entry(95, 200)]
    assert export_stats.backlog(entries, NOW) == {
        "now": 389, "d7": 412, "d30": 350, "d90": 200,
    }


def test_a_window_the_history_does_not_reach_stays_empty():
    # Nothing older than three days: the 30- and 90-day cells have no honest
    # answer, and None is what the page renders as a dash.
    entries = [_entry(0, 389), _entry(3, 395)]
    assert export_stats.backlog(entries, NOW) == {
        "now": 389, "d7": None, "d30": None, "d90": None,
    }


def test_a_window_never_reaches_forward_to_a_younger_record():
    # The oldest record is 5 days old. Reporting it as "30 days ago" would
    # pass today's backlog off as history.
    entries = [_entry(5, 400), _entry(0, 389)]
    assert export_stats.backlog(entries, NOW)["d30"] is None


def test_several_runs_on_one_day_resolve_to_the_last_one_before_the_mark():
    # collect.sh re-exports on every run that stored something, so a day holds
    # many records; the lookup takes the newest one that is still old enough.
    entries = [
        _entry(7.5, 420), _entry(7.1, 415), _entry(6.9, 402), _entry(0, 389),
    ]
    assert export_stats.backlog(entries, NOW)["d7"] == 415


def test_an_empty_series_yields_no_tooltip_at_all():
    assert export_stats.backlog([], NOW) is None


def test_a_naive_timestamp_is_read_as_utc():
    entries = [
        {"ts": (NOW - timedelta(days=8)).replace(tzinfo=None).isoformat(),
         **FP, "personal_count": 420},
        _entry(0, 389),
    ]
    assert export_stats.backlog(entries, NOW)["d7"] == 420


def test_an_unreadable_timestamp_is_ignored_rather_than_fatal():
    entries = [{"ts": "irgendwann", **FP, "personal_count": 999}, _entry(0, 389)]
    assert export_stats.backlog(entries, NOW)["now"] == 389
