"""Read and bookmark state come out of the sync database the same way.

ebook.py only ever needed "read"; the personal export also needs "bookmark",
and both live in the same video_state table under different `type` values --
so the loader takes the type rather than hard-coding it twice.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import ebook
import sync_state


def _sync_db(tmp_path, rows):
    path = tmp_path / "sync.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, created_at TEXT);
        CREATE TABLE video_state (user_id INTEGER, video_id TEXT, type TEXT, value INTEGER,
                                  updated_at TEXT, PRIMARY KEY (user_id, video_id, type));
    """)
    db.execute("INSERT INTO users (id, email, created_at) VALUES (1, 'a@b.com', '')")
    db.execute("INSERT INTO users (id, email, created_at) VALUES (2, 'other@b.com', '')")
    for vid, typ, val in rows:
        db.execute("INSERT INTO video_state VALUES (1, ?, ?, ?, '')", (vid, typ, val))
    db.execute("INSERT INTO video_state VALUES (2, 'foreign', 'read', 1, '')")
    db.commit()
    db.close()
    return str(path)


def test_read_ids_exclude_bookmarks_and_cleared_flags(tmp_path):
    path = _sync_db(tmp_path, [
        ("v1", "read", 1), ("v2", "read", 0), ("v3", "bookmark", 1),
    ])
    assert sync_state.load_state_ids(path, "a@b.com", "read") == {"v1"}


def test_bookmark_ids_are_read_from_the_same_table(tmp_path):
    path = _sync_db(tmp_path, [
        ("v1", "read", 1), ("v3", "bookmark", 1), ("v4", "bookmark", 0),
    ])
    assert sync_state.load_state_ids(path, "a@b.com", "bookmark") == {"v3"}


def test_another_users_state_is_never_returned(tmp_path):
    path = _sync_db(tmp_path, [("v1", "read", 1)])
    assert "foreign" not in sync_state.load_state_ids(path, "a@b.com", "read")


def test_unknown_user_is_an_error_not_an_empty_set(tmp_path):
    path = _sync_db(tmp_path, [])
    with pytest.raises(SystemExit):
        sync_state.load_state_ids(path, "nobody@example.com", "read")


def test_missing_database_is_an_error(tmp_path):
    with pytest.raises(SystemExit):
        sync_state.load_state_ids(str(tmp_path / "nope.db"), "a@b.com", "read")


def test_an_unknown_state_type_is_refused(tmp_path):
    path = _sync_db(tmp_path, [])
    with pytest.raises(ValueError):
        sync_state.load_state_ids(path, "a@b.com", "watched")


def test_ebook_still_loads_read_ids_through_the_shared_loader(tmp_path):
    path = _sync_db(tmp_path, [("v1", "read", 1), ("v3", "bookmark", 1)])
    assert ebook.load_read_ids(path, "a@b.com") == {"v1"}
