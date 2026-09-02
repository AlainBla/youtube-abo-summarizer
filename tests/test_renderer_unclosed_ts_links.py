"""Tests for _repair_broken_ts_links — anchors whose href was never closed.

The model writes `<a href="...&t=1:02` and simply continues the sentence: no
closing quote, no '>', no link text. An HTML parser then swallows everything up
to the next quote into the attribute, so whole sentences disappear from the
rendered card.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from renderer import _repair_broken_ts_links as repair
from renderer import sanitize_summary

URL = "https://www.youtube.com/watch?v=X&t="


def test_href_left_open_mid_sentence_is_rebuilt():
    html = f'<p>Erster Satz <a href="{URL}1:02. Zweiter Satz.</p>'
    assert repair(html) == (
        f'<p>Erster Satz <a href="{URL}62" class="ts-link">1:02</a>. Zweiter Satz.</p>'
    )


def test_bogus_paragraph_closer_and_dot_are_folded_back():
    html = f'<p>Ein Satz <a href="{URL}1:34</p class="ts-link">.</a></p>'
    assert repair(html) == f'<p>Ein Satz <a href="{URL}94" class="ts-link">1:34</a>.</p>'


def test_bogus_anchor_closer_with_attributes():
    html = f'<p>Ein Satz <a href="{URL}1:32</a class="ts-link">.</a></p>'
    assert repair(html) == f'<p>Ein Satz <a href="{URL}92" class="ts-link">1:32</a>.</p>'


def test_plain_truncated_anchor_still_repaired():
    html = f'<p>Ein Satz <a href="{URL}1:04</a>.</p>'
    assert repair(html) == f'<p>Ein Satz <a href="{URL}64" class="ts-link">1:04</a>.</p>'


def test_hour_length_timestamp():
    html = f'<p>Ein Satz <a href="{URL}1:02:03. Weiter.</p>'
    assert repair(html) == (
        f'<p>Ein Satz <a href="{URL}3723" class="ts-link">1:02:03</a>. Weiter.</p>'
    )


def test_valid_link_is_untouched():
    html = f'<p>Ein Satz <a href="{URL}64" class="ts-link">1:04</a>.</p>'
    assert repair(html) == html


def test_swallowed_prose_survives_sanitizing():
    html = (
        f'<p>Erster Satz <a href="{URL}1:02. Diese Prosa wurde bisher verschluckt '
        f'<a href="{URL}1:34</p class="ts-link">.</a></p>'
    )
    out = sanitize_summary(html)
    assert "Diese Prosa wurde bisher verschluckt" in out
    assert out.count('class="ts-link"') == 2
