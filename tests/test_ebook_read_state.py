import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import ebook
from export_harness import video


def _sync_db(tmp_path, rows):
    path = tmp_path / "sync.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, created_at TEXT);
        CREATE TABLE video_state (user_id INTEGER, video_id TEXT, type TEXT, value INTEGER,
                                  updated_at TEXT, PRIMARY KEY (user_id, video_id, type));
    """)
    db.execute("INSERT INTO users (id, email, created_at) VALUES (1, 'a@b.com', '')")
    for vid, typ, val in rows:
        db.execute("INSERT INTO video_state VALUES (1, ?, ?, ?, '')", (vid, typ, val))
    db.commit()
    db.close()
    return str(path)


def test_only_videos_marked_read_are_returned(tmp_path):
    path = _sync_db(tmp_path, [("v1", "read", 1), ("v2", "read", 0), ("v3", "bookmark", 1)])
    assert ebook.load_read_ids(path, "a@b.com") == {"v1"}


def test_unknown_user_is_an_error_not_an_empty_set(tmp_path):
    path = _sync_db(tmp_path, [])
    with pytest.raises(SystemExit):
        ebook.load_read_ids(path, "nobody@example.com")


def test_missing_database_is_an_error(tmp_path):
    with pytest.raises(SystemExit):
        ebook.load_read_ids(str(tmp_path / "nope.db"), "a@b.com")


def _videos():
    return [video("v1", "2026-01-01T00:00:00Z"), video("v2", "2026-01-02T00:00:00Z")]


def test_split_puts_unread_first_and_keeps_both_parts():
    parts = ebook.partition_by_read(_videos(), {"v1"}, "split")
    assert [(k, [v["video_id"] for v in vs]) for k, vs in parts] == [
        ("unread", ["v2"]), ("read", ["v1"]),
    ]


def test_drop_removes_read_videos_entirely():
    parts = ebook.partition_by_read(_videos(), {"v1"}, "drop")
    assert [(k, [v["video_id"] for v in vs]) for k, vs in parts] == [("unread", ["v2"])]


def test_ignore_yields_one_undivided_part():
    parts = ebook.partition_by_read(_videos(), {"v1"}, "ignore")
    assert [k for k, _ in parts] == ["all"]
    assert len(parts[0][1]) == 2


def test_empty_parts_are_dropped():
    parts = ebook.partition_by_read(_videos(), set(), "split")
    assert [k for k, _ in parts] == ["unread"]
