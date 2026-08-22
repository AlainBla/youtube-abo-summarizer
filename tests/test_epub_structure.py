import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import epub_builder
import i18n
from export_harness import video

OPF_NS = {"opf": "http://www.idpf.org/2007/opf"}


def _book(tmp_path, **kwargs):
    weeks = epub_builder.group_by_week([
        video("v1", "2026-08-19T10:00:00Z"),
        video("v2", "2026-08-26T10:00:00Z"),
    ])
    parts = [{"key": "unread", "title": "Ungelesen", "weeks": weeks}]
    out = str(tmp_path / "book.epub")
    epub_builder.build_epub(parts, out, "Test Buch", "de", i18n.get_strings("de"),
                            book_id="urn:uuid:fixed", **kwargs)
    return out


def test_mimetype_is_the_first_entry_and_stored_uncompressed(tmp_path):
    with zipfile.ZipFile(_book(tmp_path)) as z:
        first = z.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"application/epub+zip"


def test_every_manifest_item_exists_and_every_content_file_is_manifested(tmp_path):
    with zipfile.ZipFile(_book(tmp_path)) as z:
        names = set(z.namelist())
        opf = ET.fromstring(z.read("OEBPS/content.opf"))
        hrefs = {i.get("href") for i in opf.findall(".//opf:manifest/opf:item", OPF_NS)}
        for href in hrefs:
            assert "OEBPS/" + href in names, href
        content = {n[len("OEBPS/"):] for n in names
                   if n.startswith("OEBPS/") and not n.endswith(".opf")}
        assert content == hrefs


def test_spine_starts_with_the_title_page_and_lists_every_chapter(tmp_path):
    with zipfile.ZipFile(_book(tmp_path)) as z:
        opf = ET.fromstring(z.read("OEBPS/content.opf"))
        ids = [i.get("idref") for i in opf.findall(".//opf:spine/opf:itemref", OPF_NS)]
    assert ids[0] == "title"
    assert "video-v1" in ids and "video-v2" in ids


def test_every_document_in_the_archive_parses_as_xml(tmp_path):
    with zipfile.ZipFile(_book(tmp_path)) as z:
        for name in z.namelist():
            if name.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                ET.fromstring(z.read(name))


def test_nav_lists_parts_and_weeks(tmp_path):
    with zipfile.ZipFile(_book(tmp_path)) as z:
        nav = z.read("OEBPS/nav.xhtml").decode("utf-8")
    assert "Ungelesen" in nav
    assert "video-v1.xhtml" in nav


def test_container_points_at_the_package_document(tmp_path):
    with zipfile.ZipFile(_book(tmp_path)) as z:
        container = ET.fromstring(z.read("META-INF/container.xml"))
    rootfile = container.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
    assert rootfile.get("full-path") == "OEBPS/content.opf"


def test_image_item_id_is_prefixed_so_a_digit_leading_video_id_stays_valid():
    # XML IDs may not start with a digit; YouTube video IDs can.
    assert epub_builder._item_id("images/9abcDEF012.jpg") == "img-9abcDEF012"


def test_ncx_item_id_matches_the_spine_toc_attribute():
    assert epub_builder._item_id("toc.ncx") == "toc-ncx"


def test_media_type_raises_a_diagnosable_error_for_unsupported_extensions():
    try:
        epub_builder._media_type("images/cover.png")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "images/cover.png" in str(exc)


def test_book_with_images_and_transcripts_manifests_and_spines_them(tmp_path):
    out = _book(
        tmp_path,
        images={"v1": b"\xff\xd8\xff\xe0fakejpeg"},
        transcripts={"v1": "line one\n" * 30},
    )
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        opf = ET.fromstring(z.read("OEBPS/content.opf"))
        hrefs = {i.get("href") for i in opf.findall(".//opf:manifest/opf:item", OPF_NS)}
        ids = {i.get("id") for i in opf.findall(".//opf:manifest/opf:item", OPF_NS)}
        for href in hrefs:
            assert "OEBPS/" + href in names, href
        assert "OEBPS/images/v1.jpg" in names
        assert "img-v1" in ids
        assert "OEBPS/transcript-v1.xhtml" in names
        spine_ids = [i.get("idref") for i in opf.findall(".//opf:spine/opf:itemref", OPF_NS)]
        assert "transcript-v1" in spine_ids


# ── Transcript paragraphing ──────────────────────────────────────────────────
# Real stored transcripts are a handful of very long lines, not prose with
# blank lines: a 60-minute video arrives as ~6 lines of several thousand
# characters each. Chunking by line count alone leaves single paragraphs of
# 25k+ characters, which is a wall of text with no page-break opportunities
# on an e-ink reader.

def test_a_very_long_line_is_split_into_readable_paragraphs():
    sentence = "Das ist ein Satz mit etwas Text darin. "
    one_long_line = sentence * 200          # ~7600 chars, no newline at all
    paragraphs = epub_builder._transcript_paragraphs(one_long_line)
    assert len(paragraphs) > 1
    assert max(len(p) for p in paragraphs) <= epub_builder.MAX_PARAGRAPH_CHARS


def test_splitting_happens_at_sentence_boundaries():
    text = ("A" * 900 + ". ") + ("B" * 900 + ". ") + ("C" * 900 + ".")
    paragraphs = epub_builder._transcript_paragraphs(text)
    assert len(paragraphs) == 3
    assert all(p.endswith(".") for p in paragraphs)


def test_a_sentence_longer_than_the_limit_is_still_emitted_whole():
    # No sentence boundary to cut at -- losing text would be worse than a
    # single oversized paragraph.
    text = "x" * (epub_builder.MAX_PARAGRAPH_CHARS * 2)
    paragraphs = epub_builder._transcript_paragraphs(text)
    assert "".join(paragraphs).count("x") == epub_builder.MAX_PARAGRAPH_CHARS * 2


def test_short_transcripts_are_left_as_one_paragraph():
    assert epub_builder._transcript_paragraphs("Kurzer Text.") == ["Kurzer Text."]


def test_no_text_is_lost_when_splitting():
    sentence = "Ein Satz mit Inhalt. "
    text = sentence * 300
    joined = " ".join(epub_builder._transcript_paragraphs(text))
    assert joined.split() == text.split()


def test_the_same_week_in_two_parts_loses_no_video(tmp_path):
    """With --read split the same calendar week appears in both parts. While
    chapter files were keyed by week, the second part's file overwrote the
    first and its videos vanished from the book; per-video documents cannot
    collide, and this test keeps it that way."""
    unread = epub_builder.group_by_week([video("v1", "2026-08-04T10:00:00Z")])
    read = epub_builder.group_by_week([video("v2", "2026-08-05T10:00:00Z")])
    assert unread[0]["anchor"] == read[0]["anchor"], "fixture must share one week"
    parts = [
        {"key": "unread", "title": "Ungelesen", "weeks": unread},
        {"key": "read", "title": "Gelesen", "weeks": read},
    ]
    out = str(tmp_path / "book.epub")
    epub_builder.build_epub(parts, out, "Test", "de", i18n.get_strings("de"), book_id="urn:uuid:x")

    with zipfile.ZipFile(out) as z:
        sections = [n[len("OEBPS/video-"):-len(".xhtml")]
                    for n in z.namelist() if n.startswith("OEBPS/video-")]
        opf = ET.fromstring(z.read("OEBPS/content.opf"))
        spine = [i.get("idref") for i in opf.findall(".//opf:spine/opf:itemref", OPF_NS)]
    assert sorted(sections) == ["v1", "v2"], "no video may be lost to a filename collision"
    assert len(set(spine)) == len(spine), "spine must not reference one chapter twice"


# ── One chapter per video ───────────────────────────────────────────────────
# Each video is its own spine item and its own file, so an e-reader's
# next-chapter jump moves video by video and every video starts on a fresh
# page. Weeks remain the grouping level in the table of contents.

def _book_with_two_weeks(tmp_path, **kwargs):
    weeks = epub_builder.group_by_week([
        video("v1", "2026-08-19T10:00:00Z"),
        video("v2", "2026-08-20T10:00:00Z"),
        video("v3", "2026-08-26T10:00:00Z"),
    ])
    parts = [{"key": "unread", "title": "Ungelesen", "weeks": weeks}]
    out = str(tmp_path / "book.epub")
    epub_builder.build_epub(parts, out, "Test", "de", i18n.get_strings("de"),
                            book_id="urn:uuid:x", **kwargs)
    return out


def test_every_video_gets_its_own_document(tmp_path):
    with zipfile.ZipFile(_book_with_two_weeks(tmp_path)) as z:
        docs = [n for n in z.namelist() if n.startswith("OEBPS/video-")]
        assert sorted(docs) == [
            "OEBPS/video-v1.xhtml", "OEBPS/video-v2.xhtml", "OEBPS/video-v3.xhtml",
        ]
        # each holds exactly its own video
        for vid in ("v1", "v2", "v3"):
            body = z.read("OEBPS/video-%s.xhtml" % vid).decode()
            assert "Title " + vid in body
            for other in {"v1", "v2", "v3"} - {vid}:
                assert "Title " + other not in body


def test_the_spine_lists_every_video_in_reading_order(tmp_path):
    with zipfile.ZipFile(_book_with_two_weeks(tmp_path)) as z:
        opf = ET.fromstring(z.read("OEBPS/content.opf"))
    ids = [i.get("idref") for i in opf.findall(".//opf:spine/opf:itemref", OPF_NS)]
    assert ids[0] == "title"
    # newest first: v3 is a week ahead of v2 and v1
    assert ids[1:4] == ["video-v3", "video-v2", "video-v1"]


def test_the_contents_keep_part_week_video_as_three_levels(tmp_path):
    with zipfile.ZipFile(_book_with_two_weeks(tmp_path)) as z:
        nav = z.read("OEBPS/nav.xhtml").decode()
    assert "Ungelesen" in nav
    assert "KW 34" in nav and "KW 35" in nav
    assert 'href="video-v1.xhtml"' in nav and 'href="video-v3.xhtml"' in nav
    assert "Title v1" in nav


def test_each_video_document_names_its_week(tmp_path):
    with zipfile.ZipFile(_book_with_two_weeks(tmp_path)) as z:
        body = z.read("OEBPS/video-v1.xhtml").decode()
    assert "KW 34" in body


def test_a_transcript_links_back_to_its_own_video_document(tmp_path):
    out = _book_with_two_weeks(tmp_path, transcripts={"v3": "Ein Transkript."})
    with zipfile.ZipFile(out) as z:
        body = z.read("OEBPS/transcript-v3.xhtml").decode()
        assert 'href="video-v3.xhtml"' in body
        assert 'href="transcript-v3.xhtml"' in z.read("OEBPS/video-v3.xhtml").decode()


def test_ncx_gives_one_playorder_per_target(tmp_path):
    """A week navPoint points at its first video, so both must carry the same
    playOrder -- the NCX forbids two navPoints with different playOrder for
    one target, and epubcheck rejects the book over it."""
    with zipfile.ZipFile(_book_with_two_weeks(tmp_path)) as z:
        ncx = ET.fromstring(z.read("OEBPS/toc.ncx"))
    ns = {"ncx": "http://www.daisy.org/z3986/2005/ncx/"}
    by_target = {}
    for point in ncx.iter("{http://www.daisy.org/z3986/2005/ncx/}navPoint"):
        src = point.find("ncx:content", ns).get("src")
        by_target.setdefault(src, set()).add(point.get("playOrder"))
    clashing = {src: orders for src, orders in by_target.items() if len(orders) > 1}
    assert not clashing, clashing
