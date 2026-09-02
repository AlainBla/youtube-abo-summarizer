"""Tests for _relabel_unlabelled_ts_links — anchors that show no timestamp.

Some links carry the sentence's period as their text, or nothing at all:
`<a href="...&t=1:20" class="ts-link">.</a>`. The card then shows a dot (or an
invisible link) instead of "1:20", and a colon-form t= is ignored by YouTube, so
the link jumps to the start of the video.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from renderer import _relabel_unlabelled_ts_links as relabel
from renderer import sanitize_summary

URL = "https://www.youtube.com/watch?v=X&t="


def test_period_label_with_colon_form_href():
    html = f'<p>Ein Satz <a href="{URL}1:20" class="ts-link">.</a> Der nächste.</p>'
    assert relabel(html) == (
        f'<p>Ein Satz <a href="{URL}80" class="ts-link">1:20</a>. Der nächste.</p>'
    )


def test_period_label_with_seconds_href_keeps_the_href():
    html = f'<p>Ein Satz <a href="{URL}45" class="ts-link">.</a></p>'
    assert relabel(html) == f'<p>Ein Satz <a href="{URL}45" class="ts-link">0:45</a>.</p>'


def test_comma_label():
    html = f'<p>Ein Satz <a href="{URL}135" class="ts-link">,</a> wobei mehr folgt.</p>'
    assert relabel(html) == (
        f'<p>Ein Satz <a href="{URL}135" class="ts-link">2:15</a>, wobei mehr folgt.</p>'
    )


def test_empty_label_gets_one_without_inventing_punctuation():
    html = f'<p>Ein Satz <a href="{URL}3720" class="ts-link"></a></p>'
    assert relabel(html) == f'<p>Ein Satz <a href="{URL}3720" class="ts-link">1:02:00</a></p>'


def test_real_label_is_untouched():
    html = f'<p>Ein Satz <a href="{URL}64" class="ts-link">1:04</a>.</p>'
    assert relabel(html) == html


def test_prose_link_without_a_timestamp_is_untouched():
    html = '<p>Siehe <a href="https://example.com/page">.</a></p>'
    assert relabel(html) == html


def test_sanitizer_applies_it():
    html = f'<p>Ein Satz <a href="{URL}1:37" class="ts-link">.</a></p>'
    out = sanitize_summary(html)
    assert ">1:37</a>." in out
    assert "t=97" in out
