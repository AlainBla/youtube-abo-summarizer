"""Tests for _close_labelless_ts_links — anchors opened but never closed.

The model writes a complete opening tag and then just continues the sentence:
`<a href="...&t=18" class="ts-link">. Es erschien auf PS2 …`. There is no label
and no </a>, so the browser turns the rest of the paragraph into one long link
and the card shows whole sentences in link styling instead of "0:18".
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from renderer import _close_labelless_ts_links as close
from renderer import sanitize_summary

URL = "https://www.youtube.com/watch?v=X&t="


def test_anchor_without_label_or_closing_tag_is_closed():
    html = f'<p>Ein Satz <a href="{URL}18" class="ts-link">. Zweiter Satz.</p>'
    assert close(html) == (
        f'<p>Ein Satz <a href="{URL}18" class="ts-link">0:18</a>. Zweiter Satz.</p>'
    )


def test_colon_form_becomes_seconds():
    html = f'<p>Ein Satz <a href="{URL}1:10" class="ts-link">. Weiter.</p>'
    assert close(html) == (
        f'<p>Ein Satz <a href="{URL}70" class="ts-link">1:10</a>. Weiter.</p>'
    )


def test_two_open_anchors_in_a_row_are_both_closed():
    html = (
        f'<p>Eins <a href="{URL}18" class="ts-link">. Zwei '
        f'<a href="{URL}24" class="ts-link">. Drei.</p>'
    )
    assert close(html) == (
        f'<p>Eins <a href="{URL}18" class="ts-link">0:18</a>. Zwei '
        f'<a href="{URL}24" class="ts-link">0:24</a>. Drei.</p>'
    )


def test_a_properly_closed_anchor_is_untouched():
    html = f'<p>Ein Satz <a href="{URL}64" class="ts-link">1:04</a>.</p>'
    assert close(html) == html


def test_an_anchor_whose_label_is_a_period_is_left_to_the_relabel_pass():
    """`<a …>.</a>` closes properly; _relabel_unlabelled_ts_links owns it."""
    html = f'<p>Ein Satz <a href="{URL}45" class="ts-link">.</a></p>'
    assert close(html) == html


def test_an_empty_anchor_that_closes_is_left_alone():
    html = f'<p>Ein Satz <a href="{URL}45" class="ts-link"></a></p>'
    assert close(html) == html


def test_a_bracketed_label_is_untouched():
    html = f'<p>Ein Satz <a href="{URL}35" class="ts-link">[0:35]</a>.</p>'
    assert close(html) == html


def test_a_link_that_is_not_a_timestamp_is_untouched():
    html = '<p>Siehe <a href="https://example.com/page">die Seite</a>.</p>'
    assert close(html) == html


def test_the_sanitizer_no_longer_swallows_the_sentence():
    html = (
        f'<p>Erster Satz <a href="{URL}18" class="ts-link">. Dieser Satz war ein Link '
        f'<a href="{URL}24" class="ts-link">. Und dieser auch.</p>'
    )
    out = sanitize_summary(html)
    assert ">0:18</a>" in out
    assert ">0:24</a>" in out
    assert "Dieser Satz war ein Link" in out
    # the prose must sit outside the anchors, not inside them
    assert 'class="ts-link">. Dieser' not in out


def test_a_label_closed_with_the_wrong_tag_is_left_to_the_link_repair_pass():
    """`<a …>1:04</p>` has a usable label; openrouter's _fix_timestamp_links
    normalises the closing tag. Inserting a second label here would double it."""
    html = f'<p>Ein Satz <a href="{URL}64" class="ts-link">1:04</p>. Weiter.</p>'
    assert close(html) == html
