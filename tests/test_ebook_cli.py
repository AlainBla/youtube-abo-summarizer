import os
import re
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import ebook
from export_harness import raw_store_video, video


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
        assert any(n.startswith("OEBPS/video-") for n in z.namelist())


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


def test_no_user_produces_a_single_unlabelled_part(tmp_path, monkeypatch):
    # Without --user there is no read state to split on. Before this fix,
    # the default --read "split" mode still ran against an empty read_ids
    # set, so the whole book ended up in a single part titled "Ungelesen"
    # (Unread) even though nobody's read state was ever consulted.
    _stub_store(monkeypatch, [video("v1", "2026-08-19T10:00:00Z")])
    out = tmp_path / "book.epub"
    monkeypatch.setattr(sys, "argv", ["ebook.py", "--all", "--no-thumbnails",
                                      "--no-transcripts", "--output", str(out)])
    ebook.main()
    with zipfile.ZipFile(out) as z:
        nav = z.read("OEBPS/nav.xhtml").decode("utf-8")
    assert "Ungelesen" not in nav and "Gelesen" not in nav
    assert "Videos" in nav


def test_raw_iso_duration_is_formatted_before_reaching_the_chapter(tmp_path, monkeypatch):
    # Regression: chapter.xhtml.j2 used to print the store's raw ISO-8601
    # duration ("PT1H2M3S") verbatim instead of "1:02:03".
    _stub_store(monkeypatch, [raw_store_video("v1", "2026-08-19T10:00:00Z")])
    out = tmp_path / "book.epub"
    monkeypatch.setattr(sys, "argv", ["ebook.py", "--all", "--no-thumbnails",
                                      "--no-transcripts", "--output", str(out)])
    ebook.main()
    with zipfile.ZipFile(out) as z:
        chapter = z.read("OEBPS/video-v1.xhtml").decode("utf-8")
    assert "1:02:03" in chapter
    assert "PT1H2M3S" not in chapter


def test_title_page_shows_the_covered_date_range(tmp_path, monkeypatch):
    _stub_store(monkeypatch, [video("v1", "2026-08-19T10:00:00Z"),
                              video("v2", "2026-08-26T10:00:00Z")])
    out = tmp_path / "book.epub"
    monkeypatch.setattr(sys, "argv", ["ebook.py", "--all", "--no-thumbnails",
                                      "--no-transcripts", "--output", str(out)])
    ebook.main()
    with zipfile.ZipFile(out) as z:
        title_page = z.read("OEBPS/title.xhtml").decode("utf-8")
    assert "19.08.2026" in title_page and "26.08.2026" in title_page


def test_read_drop_excludes_dropped_videos_thumbnails_and_transcripts(tmp_path, monkeypatch):
    # Regression: main() used to pass the full `selected` list -- including
    # videos --read drop excludes from the book -- into collect_thumbnails()
    # and the transcript loop. build_epub() embeds every image/transcript
    # it's handed, and content.opf.j2 puts every transcript into the spine,
    # so a dropped video's thumbnail and transcript page used to still ship
    # in the archive (the transcript backlinked to nav.xhtml) even though no
    # chapter links to either.
    _stub_store(monkeypatch, [video("v1", "2026-08-19T10:00:00Z"),
                              video("v2", "2026-08-26T10:00:00Z")])
    monkeypatch.setattr(ebook, "load_read_ids", lambda db, email: {"v1"})  # v1 is read

    # Fake thumbnail fetch -- no real network.
    monkeypatch.setattr(ebook, "_default_fetch", lambda url, timeout=10: b"\xff\xd8jpegdata")

    # Fake transcript store -- both videos "have" a transcript on disk.
    transcript_paths = {}
    for vid in ("v1", "v2"):
        p = tmp_path / f"{vid}.txt"
        p.write_text(f"Transcript for {vid}.", encoding="utf-8")
        transcript_paths[vid] = p
    monkeypatch.setattr(ebook.store, "get_llm_transcript_path", lambda vid: transcript_paths.get(vid))
    monkeypatch.setattr(ebook, "THUMBNAIL_CACHE_DIR", str(tmp_path / "thumb_cache"))

    out = tmp_path / "book.epub"
    monkeypatch.setattr(sys, "argv", ["ebook.py", "--all", "--user", "a@b.com", "--read", "drop",
                                      "--output", str(out)])
    ebook.main()

    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert "OEBPS/transcript-v2.xhtml" in names
    assert "OEBPS/transcript-v1.xhtml" not in names
    assert "OEBPS/images/v2.jpg" in names
    assert "OEBPS/images/v1.jpg" not in names


# ── --limit applies after the read filter ───────────────────────────────────
# "--limit 50 --read drop" must mean "the 50 newest unread videos", not "the
# unread ones among the 50 newest" -- with a mostly-read store the latter
# quietly produces a nearly empty book.

def _read_ids_stub(monkeypatch, ids):
    monkeypatch.setattr(ebook, "load_read_ids", lambda db, email: set(ids))


def _chapter_video_ids(path) -> list[str]:
    import zipfile
    with zipfile.ZipFile(path) as z:
        found = []
        for name in z.namelist():
            if name.startswith("OEBPS/video-"):
                found.append(name[len("OEBPS/video-"):-len(".xhtml")])
    return found


def test_limit_counts_unread_videos_under_read_drop(tmp_path, monkeypatch):
    videos = [video("v%d" % i, "2026-08-%02dT00:00:00Z" % (i + 1)) for i in range(6)]
    _stub_store(monkeypatch, videos)
    # The three newest are read; only v0..v2 are unread.
    _read_ids_stub(monkeypatch, ["v5", "v4", "v3"])
    out = tmp_path / "book.epub"
    monkeypatch.setattr(sys, "argv", ["ebook.py", "--all", "--limit", "2",
                                      "--user", "a@b.com", "--read", "drop",
                                      "--no-thumbnails", "--no-transcripts",
                                      "--output", str(out)])
    ebook.main()
    assert sorted(_chapter_video_ids(out)) == ["v1", "v2"], "expected the 2 newest unread"


def test_limit_still_counts_everything_under_read_split(tmp_path, monkeypatch):
    videos = [video("v%d" % i, "2026-08-%02dT00:00:00Z" % (i + 1)) for i in range(6)]
    _stub_store(monkeypatch, videos)
    _read_ids_stub(monkeypatch, ["v5"])
    out = tmp_path / "book.epub"
    monkeypatch.setattr(sys, "argv", ["ebook.py", "--all", "--limit", "2",
                                      "--user", "a@b.com", "--read", "split",
                                      "--no-thumbnails", "--no-transcripts",
                                      "--output", str(out)])
    ebook.main()
    # split keeps read videos, so the newest two overall are v5 (read) and v4.
    assert sorted(_chapter_video_ids(out)) == ["v4", "v5"]
