import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import epub_builder
import i18n
from export_harness import raw_store_video, video


def test_named_entities_are_replaced_by_numeric_ones():
    out = epub_builder.xhtmlify("<p>a&nbsp;b</p>")
    assert "&nbsp;" not in out
    # A degenerate implementation that escapes every input to plain text
    # would also satisfy the assertion above; pin the actual expected
    # markup so valid input is required to survive as markup.
    assert out == "<p>a&#160;b</p>"
    ET.fromstring("<div>" + out + "</div>")


def test_unclosed_tag_is_escaped_instead_of_breaking_the_document():
    out = epub_builder.xhtmlify("<p>text<b>bold</p>")
    ET.fromstring("<div>" + out + "</div>")     # must not raise
    assert "bold" in out


def test_none_summary_becomes_empty_string():
    assert epub_builder.xhtmlify(None) == ""


def test_control_char_is_stripped_so_the_fallback_still_parses():
    # \x0c (form feed) is not a legal XML 1.0 Char even though HTML tolerates
    # it; the fallback path must not let it survive into the escaped output.
    out = epub_builder.xhtmlify("<p>a\x0cb<b>unclosed</p>")
    assert "\x0c" not in out
    ET.fromstring("<div>" + out + "</div>")     # must not raise


def test_fallback_resolves_entities_instead_of_double_escaping():
    out = epub_builder.xhtmlify("<p>a&nbsp;b<b>")
    assert "&amp;#160;" not in out
    assert "\xa0" in out
    ET.fromstring("<div>" + out + "</div>")     # must not raise


def test_numeric_reference_to_invalid_char_is_stripped_in_fallback():
    # "&#12;" is a literal numeric character reference in the input (not a
    # named entity, so the entity pass above never touches it). html.unescape
    # decodes it to a raw form feed inside the fallback -- the fallback must
    # strip that char again rather than let it back into the output.
    out = epub_builder.xhtmlify("<p>a&#12;b<b>")
    assert "\x0c" not in out
    ET.fromstring("<div>" + out + "</div>")     # must not raise


def test_bare_ampersand_is_escaped_instead_of_breaking_the_parse():
    out = epub_builder.xhtmlify('<a href="https://x/?v=1&t=122">link</a>')
    assert out == '<a href="https://x/?v=1&amp;t=122">link</a>'
    ET.fromstring("<div>" + out + "</div>")     # must not raise


def test_bare_ampersand_fix_does_not_double_escape_valid_entities():
    out = epub_builder.xhtmlify("<p>a &amp; b &lt; c</p>")
    assert out == "<p>a &amp; b &lt; c</p>"
    ET.fromstring("<div>" + out + "</div>")


def test_chapter_shows_transcript_error_message_when_summary_is_missing():
    strings = i18n.get_strings("de")
    week = epub_builder.group_by_week(
        [video("v1", "2026-08-19T10:00:00Z", summary=None, transcript_error="rate_limited")]
    )[0]
    xhtml = epub_builder.render_video(week["videos"][0], week, strings, "de", {}, set())
    ET.fromstring(xhtml)                          # must still parse as XML
    assert strings["transcript_rate_limited"] in xhtml


def test_each_video_document_is_well_formed_and_self_contained():
    week = epub_builder.group_by_week([
        video("v1", "2026-08-19T10:00:00Z", title='Quote " & <tag>'),
        video("v2", "2026-08-20T10:00:00Z"),
    ])[0]
    strings = i18n.get_strings("de")
    # newest first: v2 (Aug 20) precedes v1 (Aug 19)
    for index, marker in ((1, "Quote"), (0, "Title v2")):
        xhtml = epub_builder.render_video(week["videos"][index], week, strings, "de", {}, set())
        root = ET.fromstring(xhtml)             # must parse as XML, escaping and all
        heading = root.find(".//{http://www.w3.org/1999/xhtml}h1")
        assert marker in heading.text
        assert "KW 34" in xhtml                 # the week kicker keeps the context


def test_real_store_shaped_summary_keeps_its_markup_and_timestamp_link():
    # Regression for the whole-branch-review finding: every real stored
    # summary has an unescaped "&" in its "&t=" timestamp-link href (e.g.
    # "watch?v=ID&t=122"). The named-entity regex only rewrites "&name;"
    # forms, so a bare "&t=122" used to survive into ET.fromstring(), which
    # raised, and xhtmlify() fell back to escaping the *entire* fragment to
    # plain text -- stripping every <h3>, list, and link a summary had.
    week = epub_builder.group_by_week([raw_store_video("v1", "2026-08-19T10:00:00Z")])[0]
    xhtml = epub_builder.render_video(week['videos'][0], week, i18n.get_strings('de'), 'de', {}, set())
    ET.fromstring(xhtml)  # must still parse as XML
    assert "<h3>Intro</h3>" in xhtml
    assert '<a class="ts-link" href="https://www.youtube.com/watch?v=v1&amp;t=122">' in xhtml


def test_chapter_links_thumbnail_and_transcript_only_when_present():
    week = epub_builder.group_by_week(
        [video("v1", "2026-08-19T10:00:00Z", tags=["A", "B"])]
    )[0]
    plain = epub_builder.render_video(week['videos'][0], week, i18n.get_strings('de'), 'de', {}, set())
    assert "images/" not in plain and "transcript-v1.xhtml" not in plain

    rich = epub_builder.render_video(
        week["videos"][0], week, i18n.get_strings("de"), "de", {"v1": "images/v1.jpg"}, {"v1"})
    ET.fromstring(rich)                          # must parse as XML, incl. <img/>
    assert 'src="images/v1.jpg"' in rich
    assert "transcript-v1.xhtml" in rich
    assert "A &#183; B" in rich                  # tag separator must not be double-escaped


# ── Summaries go through the same sanitizer as the HTML render path ─────────
# Stored summaries are raw LLM output: about one in ten nests a list inside a
# paragraph, which is well-formed XML but invalid XHTML content -- epubcheck
# rejects it with RSC-005. nh3's HTML5 tree builder re-nests it correctly, and
# it also repairs mismatched tags that would otherwise cost a summary all its
# markup via xhtmlify()'s escape fallback.

def _summary_of(chapter_xhtml: str) -> str:
    m = re.search(r'<div class="summary">(.*?)</div>', chapter_xhtml, re.S)
    assert m, "no summary div in chapter"
    return m.group(1)


def _chapter_with_summary(summary: str) -> str:
    week = epub_builder.group_by_week([video("v1", "2026-08-19T10:00:00Z", summary=summary)])[0]
    return epub_builder.render_video(week["videos"][0], week, i18n.get_strings("de"), "de", {}, set())


def test_a_list_nested_in_a_paragraph_is_re_nested():
    out = _summary_of(_chapter_with_summary("<p>Einleitung<ul><li>Punkt</li></ul></p>"))
    assert "<ul>" in out and "<li>Punkt</li>" in out
    # No <ul> may open while a <p> is still unclosed.
    assert not re.search(r"<p>(?:(?!</p>).)*?<ul\b", out, re.S), out


def test_a_mismatched_tag_no_longer_costs_the_summary_its_markup():
    out = _summary_of(_chapter_with_summary("<h3>Titel</h3><p>Text<b>fett</p>"))
    assert "<h3>Titel</h3>" in out, out
    assert "&lt;h3&gt;" not in out


def test_disallowed_markup_is_still_removed():
    out = _summary_of(_chapter_with_summary('<p>ok</p><script>alert(1)</script>'))
    assert "script" not in out.lower()


# ── List content model ──────────────────────────────────────────────────────
# The LLM sometimes emits <ul><a href=...>...</a></ul> — an anchor as a direct
# child of the list, with no <li>. nh3 keeps it (that is how an HTML5 parser
# reads it) and it is well-formed XML, but epubcheck rejects it with RSC-005:
# a list may only contain <li>.

def test_an_anchor_directly_inside_a_list_is_wrapped_in_a_list_item():
    out = _summary_of(_chapter_with_summary(
        '<ul><a href="https://www.youtube.com/watch?v=X&amp;t=305" class="ts-link">05:05</a></ul>'))
    assert "05:05" in out
    assert not re.search(r"<(ul|ol)>\s*<a\b", out), out
    assert re.search(r"<li>\s*<a\b", out), out


def test_stray_text_inside_a_list_is_wrapped_too():
    out = _summary_of(_chapter_with_summary("<ul>loser Text<li>Punkt</li></ul>"))
    assert "loser Text" in out and "Punkt" in out
    assert not re.search(r"<ul>\s*[^<\s]", out), out


def test_a_well_formed_list_keeps_its_items():
    out = _summary_of(_chapter_with_summary("<ul><li>A</li><li>B</li></ul>"))
    assert out.count("<li>") == 2
    assert "<li>A</li>" in out and "<li>B</li>" in out


def test_a_nested_list_is_not_left_as_a_direct_child():
    out = _summary_of(_chapter_with_summary("<ul><li>A</li><ul><li>B</li></ul></ul>"))
    assert not re.search(r"<ul>\s*<ul\b", out), out
    assert "B" in out


def test_a_block_element_inside_an_inline_element_becomes_a_span():
    # <em><p>...</p></em> is well-formed XML and survives nh3, but a paragraph
    # may not sit inside phrasing content — epubcheck rejects it (RSC-005).
    out = _summary_of(_chapter_with_summary("<p><em><p>Text im Inline-Element</p></em></p>"))
    assert "Text im Inline-Element" in out
    assert not re.search(r"<(em|strong|a)\b[^>]*>\s*<(p|h3|ul|ol)\b", out), out


def test_a_paragraph_inside_an_unclosed_heading_is_hoisted_out():
    # Real case from the store: the model forgets </h3>, so an HTML5 parser
    # (and therefore nh3) makes the following paragraph a child of the
    # heading. A heading may only hold phrasing content -- epubcheck rejects
    # it with RSC-005, and the paragraph would render heading-sized.
    out = _summary_of(_chapter_with_summary(
        '<h3>Abschnitt <a href="https://www.youtube.com/watch?v=X&amp;t=1000">16:40</a>'
        '<p>Der eigentliche Absatz.</p>'))
    assert "Der eigentliche Absatz." in out
    assert not re.search(r"<h3\b[^>]*>(?:(?!</h3>).)*?<p\b", out, re.S), out
