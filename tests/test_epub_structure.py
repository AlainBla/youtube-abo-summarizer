import os
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
    assert "chap-w-2026-34" in ids and "chap-w-2026-35" in ids


def test_every_document_in_the_archive_parses_as_xml(tmp_path):
    with zipfile.ZipFile(_book(tmp_path)) as z:
        for name in z.namelist():
            if name.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                ET.fromstring(z.read(name))


def test_nav_lists_parts_and_weeks(tmp_path):
    with zipfile.ZipFile(_book(tmp_path)) as z:
        nav = z.read("OEBPS/nav.xhtml").decode("utf-8")
    assert "Ungelesen" in nav
    assert "chapter-w-2026-34.xhtml" in nav


def test_container_points_at_the_package_document(tmp_path):
    with zipfile.ZipFile(_book(tmp_path)) as z:
        container = ET.fromstring(z.read("META-INF/container.xml"))
    rootfile = container.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
    assert rootfile.get("full-path") == "OEBPS/content.opf"
