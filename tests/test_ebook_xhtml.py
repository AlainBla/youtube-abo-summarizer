import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import epub_builder
import i18n
from export_harness import video


def test_named_entities_are_replaced_by_numeric_ones():
    out = epub_builder.xhtmlify("<p>a&nbsp;b</p>")
    assert "&nbsp;" not in out
    ET.fromstring("<div>" + out + "</div>")


def test_unclosed_tag_is_escaped_instead_of_breaking_the_document():
    out = epub_builder.xhtmlify("<p>text<b>bold</p>")
    ET.fromstring("<div>" + out + "</div>")     # must not raise
    assert "bold" in out


def test_none_summary_becomes_empty_string():
    assert epub_builder.xhtmlify(None) == ""


def test_chapter_is_well_formed_and_carries_every_video():
    week = epub_builder.group_by_week([
        video("v1", "2026-08-19T10:00:00Z", title='Quote " & <tag>'),
        video("v2", "2026-08-20T10:00:00Z"),
    ])[0]
    xhtml = epub_builder.render_chapter(week, i18n.get_strings("de"), "de", {}, set())
    root = ET.fromstring(xhtml)                 # must parse as XML
    ids = [s.get("id") for s in root.iter("{http://www.w3.org/1999/xhtml}section")]
    assert "v-v1" in ids and "v-v2" in ids
    assert "KW 34" in xhtml


def test_chapter_links_thumbnail_and_transcript_only_when_present():
    week = epub_builder.group_by_week(
        [video("v1", "2026-08-19T10:00:00Z", tags=["A", "B"])]
    )[0]
    plain = epub_builder.render_chapter(week, i18n.get_strings("de"), "de", {}, set())
    assert "images/" not in plain and "transcript-v1.xhtml" not in plain

    rich = epub_builder.render_chapter(
        week, i18n.get_strings("de"), "de", {"v1": "images/v1.jpg"}, {"v1"})
    ET.fromstring(rich)                          # must parse as XML, incl. <img/>
    assert 'src="images/v1.jpg"' in rich
    assert "transcript-v1.xhtml" in rich
    assert "A &#183; B" in rich                  # tag separator must not be double-escaped
