"""Tests for _fix_timestamp_links — wrong t= arithmetic and broken anchor closings."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import openrouter
from openrouter import _fix_timestamp_links as fix


def link(t, label, closing="a"):
    return (
        f'<a href="https://www.youtube.com/watch?v=abc123&t={t}" '
        f'class="ts-link">{label}</{closing}>'
    )


# ── t= recomputed from the label ─────────────────────────────────────────────

def test_correct_link_is_untouched():
    html = f"<p>Text {link(122, '02:02')}</p>"
    assert fix(html) == html


def test_label_digits_used_as_seconds():
    """"02:02" became t=202 — the model concatenated the digits."""
    assert fix(f"<p>{link(202, '02:02')}</p>") == f"<p>{link(122, '02:02')}</p>"


def test_minutes_only_used_as_seconds():
    """"02:35" became t=2 — the model wrote only the minute number."""
    assert fix(f"<p>{link(2, '02:35')}</p>") == f"<p>{link(155, '02:35')}</p>"


def test_colon_inside_t_parameter():
    """t=1:01 is not a valid YouTube offset and jumps to the video start."""
    assert fix(f"<p>{link('1:01', '1:01')}</p>") == f"<p>{link(61, '1:01')}</p>"


def test_hour_labels():
    assert fix(f"<p>{link(10500, '1:45:00')}</p>") == f"<p>{link(6300, '1:45:00')}</p>"


def test_unparseable_label_keeps_its_t_value():
    html = f"<p>{link(300, 'hier')}</p>"
    assert fix(html) == html


# ── anchor closing tags ──────────────────────────────────────────────────────

def test_stray_slash_before_closing_anchor():
    """The </</a> shape that broke fIvklgYNq3Y."""
    html = '<p>Text <a href="https://www.youtube.com/watch?v=abc123&t=120" class="ts-link">02:00</</a>.</p>'
    out = fix(html)
    assert "</</" not in out
    assert out.count("</a>") == 1


def test_anchor_closed_with_article_drops_the_bogus_tag():
    """The </article> shape that broke jr1qTGOxc1k."""
    out = fix(f"<p>{link(45, '0:45', closing='article')}</p>")
    assert out == f"<p>{link(45, '0:45')}</p>"
    assert "article" not in out


def test_anchor_closed_with_h3_keeps_the_heading_close():
    """<h3>Titel <a ...>0:31</h3> must not lose its </h3>."""
    out = fix(f"<h3>Titel {link(31, '0:31', closing='h3')}")
    assert out == f"<h3>Titel {link(31, '0:31')}</h3>"


def test_anchor_closed_with_p_keeps_the_paragraph_close():
    out = fix(f"<p>Text {link(31, '0:31', closing='p')}")
    assert out == f"<p>Text {link(31, '0:31')}</p>"


def test_multiple_links_in_one_paragraph():
    html = f"<p>A {link(202, '02:02')} B {link(405, '04:05')}</p>"
    out = fix(html)
    assert out == f"<p>A {link(122, '02:02')} B {link(245, '04:05')}</p>"


def test_non_timestamp_anchors_are_left_alone():
    html = '<p>Siehe <a href="https://example.com">die Quelle</a>.</p>'
    assert fix(html) == html


def test_dedup_runs_on_corrected_values():
    """Two links whose labels are equal must dedup even if their t= differed."""
    html = f"<p>A {link(202, '02:02')} B {link(2, '02:02')}</p>"
    out = openrouter._dedup_timestamps(fix(html))
    assert out.count("<a ") == 1
