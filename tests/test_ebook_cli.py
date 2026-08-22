import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import ebook
from export_harness import video


def _stub_store(monkeypatch, entries):
    monkeypatch.setattr(ebook.store, "get_all_videos", lambda with_transcripts=True: entries)


def test_end_to_end_build_writes_a_readable_epub(tmp_path, monkeypatch):
    _stub_store(monkeypatch, [video("v1", "2026-08-19T10:00:00Z"),
                              video("v2", "2026-08-26T10:00:00Z")])
    out = tmp_path / "book.epub"
    monkeypatch.setattr(sys, "argv", ["ebook.py", "--all", "--no-thumbnails",
                                      "--no-transcripts", "--output", str(out)])
    ebook.main()
    with zipfile.ZipFile(out) as z:
        assert z.infolist()[0].filename == "mimetype"
        assert any(n.startswith("OEBPS/chapter-") for n in z.namelist())


def test_empty_selection_exits_zero_with_a_message(tmp_path, monkeypatch, capsys):
    _stub_store(monkeypatch, [])
    monkeypatch.setattr(sys, "argv", ["ebook.py", "--all", "--output", str(tmp_path / "x.epub")])
    with pytest.raises(SystemExit) as exc:
        ebook.main()
    assert exc.value.code == 0
    assert "No videos" in capsys.readouterr().out


def test_read_split_produces_two_parts(tmp_path, monkeypatch):
    _stub_store(monkeypatch, [video("v1", "2026-08-19T10:00:00Z"),
                              video("v2", "2026-08-26T10:00:00Z")])
    monkeypatch.setattr(ebook, "load_read_ids", lambda db, email: {"v1"})
    out = tmp_path / "book.epub"
    monkeypatch.setattr(sys, "argv", ["ebook.py", "--all", "--user", "a@b.com",
                                      "--no-thumbnails", "--no-transcripts",
                                      "--output", str(out)])
    ebook.main()
    with zipfile.ZipFile(out) as z:
        nav = z.read("OEBPS/nav.xhtml").decode("utf-8")
    assert "Ungelesen" in nav and "Gelesen" in nav


def test_all_read_and_drop_mode_exits_zero_with_a_message(tmp_path, monkeypatch, capsys):
    # Every selected video is read and --read drop discards the "read" part
    # entirely -- partition_by_read() then returns zero parts. Building an
    # EPUB from zero parts would emit an NCX with an empty navMap, which is
    # invalid per the DTD, so main() must bail out before calling build_epub.
    _stub_store(monkeypatch, [video("v1", "2026-08-19T10:00:00Z")])
    monkeypatch.setattr(ebook, "load_read_ids", lambda db, email: {"v1"})
    out = tmp_path / "book.epub"
    monkeypatch.setattr(sys, "argv", ["ebook.py", "--all", "--user", "a@b.com", "--read", "drop",
                                      "--no-thumbnails", "--no-transcripts",
                                      "--output", str(out)])
    with pytest.raises(SystemExit) as exc:
        ebook.main()
    assert exc.value.code == 0
    assert "No videos" in capsys.readouterr().out
    assert not out.exists()


def test_negative_limit_is_rejected():
    with pytest.raises(SystemExit):
        ebook.parse_args(["--limit", "-5"])
