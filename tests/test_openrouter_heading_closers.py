"""Tests for _fix_heading_closers — a heading the model closed with </p>.

"<h3>Fazit <a ...>6:00</a></p>" leaves the heading open: </p> without an open
paragraph is ignored by the browser, so the next <p> is parsed *inside* the
<h3> and inherits the heading style — in the archive that is
`text-transform: uppercase`, i.e. whole paragraphs in capitals (znzYWHPv1WY).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from openrouter import _fix_heading_closers as fix
from openrouter import repair_summary_html


LINK = '<a href="https://www.youtube.com/watch?v=X&t=360" class="ts-link">6:00</a>'


def test_heading_closed_with_p_gets_its_own_closer():
    html = f"<h3>Fazit {LINK}</p>\n<p>Text.</p>"
    assert fix(html) == f"<h3>Fazit {LINK}</h3>\n<p>Text.</p>"


def test_every_level_is_matched_to_its_own_closer():
    assert fix("<h2>A</p><h4>B</p>") == "<h2>A</h2><h4>B</h4>"


def test_correct_heading_is_untouched():
    html = f"<h3>Fazit {LINK}</h3>\n<p>Text.</p>"
    assert fix(html) == html


def test_paragraph_after_a_correct_heading_keeps_its_closer():
    html = "<h3>A</h3><p>x</p><h3>B</p><p>y</p>"
    assert fix(html) == "<h3>A</h3><p>x</p><h3>B</h3><p>y</p>"


def test_heading_with_attributes():
    assert fix('<h3 id="x">A</p>') == '<h3 id="x">A</h3>'


def test_p_before_the_real_closer_is_dropped():
    # 3BrtAyb9MO8: "</a></p></h3>" — must not become "</h3></h3>".
    html = f"<h3>Fazit {LINK}</p></h3>\n<p>Text.</p>"
    assert fix(html) == f"<h3>Fazit {LINK}</h3>\n<p>Text.</p>"


def test_garbled_closer_run_into_the_next_word():
    # SwzLhvhljyQ: "</hoster<p>" — an unknown end tag the browser ignores,
    # leaving the heading open over the following paragraph.
    html = f"<h3>Milka {LINK}</hoster<p>Das Produkt.</p>"
    assert fix(html) == f"<h3>Milka {LINK}</h3><p>Das Produkt.</p>"


def test_garbled_closer_with_bracket():
    assert fix("<h3>A</hh3>\n<p>x</p>") == "<h3>A</h3>\n<p>x</p>"


def test_heading_whose_anchor_was_also_closed_with_p():
    # _fix_timestamp_links turns "<a ...>6:00</p>" into "<a ...>6:00</a></p>";
    # the heading pass must then see that </p> as the heading's closer.
    html = '<h3>Fazit <a href="https://www.youtube.com/watch?v=X&t=360" class="ts-link">6:00</p>\n<p>Text.</p>'
    assert repair_summary_html(html) == f"<h3>Fazit {LINK}</h3>\n<p>Text.</p>"


def test_full_pass_repairs_the_reported_summary_shape():
    html = (
        f"<h3>Geopolitik {LINK}</p>\n<p>Satz eins. {LINK}</p>\n\n"
        f"<h3>Fazit {LINK}</h3>\n<p>Ende.</p>"
    )
    assert repair_summary_html(html) == (
        f"<h3>Geopolitik {LINK}</h3>\n<p>Satz eins. {LINK}</p>\n\n"
        f"<h3>Fazit {LINK}</h3>\n<p>Ende.</p>"
    )


def test_inline_closer_without_opener():
    # 1MoILpIEvKM: "</a></i>" — the browser ignores </i>, the heading stays open.
    html = f"<h3>Trends {LINK}</i>\n<p>x</p>"
    assert fix(html) == f"<h3>Trends {LINK}</h3>\n<p>x</p>"


def test_inline_closer_with_opener_is_kept():
    html = "<h3><em>A</em> B</p>\n<p>x</p>"
    assert fix(html) == "<h3><em>A</em> B</h3>\n<p>x</p>"


def test_opener_typed_instead_of_closer():
    # OgHiOCGOmNk: "<h3>Fazit des Duells <h3>" — the second heading never closes.
    assert fix("<h3>Fazit des Duells <h3>\n<p>x</p>") == "<h3>Fazit des Duells</h3>\n<p>x</p>"


def test_repeated_opener_before_the_link_is_merged():
    # xZDWPL0KRec: "<h3>Titel <h3><a ...>2:13</a></h3>".
    assert fix(f"<h3>Titel <h3>{LINK}</h3>\n<p>x</p>") == f"<h3>Titel {LINK}</h3>\n<p>x</p>"


def test_wrong_level_closer_is_normalised():
    assert fix(f"<h3>A {LINK}</h5>\n<p>x</p>") == f"<h3>A {LINK}</h3>\n<p>x</p>"


def test_heading_spanning_lines_is_untouched():
    html = "<h3>\nA\n</h3>\n<p>x</p>"
    assert fix(html) == html


def test_unclosed_heading_at_the_end_is_closed():
    assert fix("<p>x</p>\n<h3>Fazit  ") == "<p>x</p>\n<h3>Fazit</h3>  "


def test_clean_summary_is_untouched():
    html = f"<h3>A {LINK}</h3>\n<p>x {LINK}</p>\n\n<h3>B</h3>\n<ul><li>y</li></ul>"
    assert fix(html) == html
