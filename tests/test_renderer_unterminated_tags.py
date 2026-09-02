"""Tests for _close_unterminated_ts_tags — <a ...> that never reaches its '>'.

The href may even be closed correctly; what is missing is the '>' that would end
the tag, so the parser keeps reading the rest of the sentence as attributes and
the text never reaches the card.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from renderer import _close_unterminated_ts_tags as close
from renderer import sanitize_summary

URL = "https://www.youtube.com/watch?v=X&t="


def test_closed_href_without_the_closing_bracket():
    html = f'<p>Ein Satz <a href="{URL}1:02". Die Nutzung von <code>x</code></p>'
    assert close(html) == (
        f'<p>Ein Satz <a href="{URL}62" class="ts-link">1:02</a>. Die Nutzung von <code>x</code></p>'
    )


def test_seconds_value_keeps_its_prose():
    html = f'<p>Ein Satz <a href="{URL}214". Ein hoher Wert <a href="{URL}300" class="ts-link">5:00</a></p>'
    assert close(html) == (
        f'<p>Ein Satz <a href="{URL}214" class="ts-link">3:34</a>. Ein hoher Wert '
        f'<a href="{URL}300" class="ts-link">5:00</a></p>'
    )


def test_truncated_class_attribute_is_dropped():
    html = f'<p>Ein Satz <a href="{URL}1:03" class="ts-link<h3>Titel</h3>'
    assert close(html) == f'<p>Ein Satz <a href="{URL}63" class="ts-link">1:03</a><h3>Titel</h3>'


def test_second_url_pasted_into_the_class_attribute_is_dropped():
    html = f'<p>X <a href="{URL}660" class://www.youtube.com/watch?v=X&t=11:10<h3>T</h3>'
    assert close(html) == f'<p>X <a href="{URL}660" class="ts-link">11:00</a><h3>T</h3>'


def test_orphaned_label_after_the_attributes_is_dropped():
    """The model's own "93:00" is dropped; 5580 s is written as 1:33:00."""
    html = f'<p>X <a href="{URL}5580" class="ts-link": 93:00<h3>T</h3>'
    assert close(html) == f'<p>X <a href="{URL}5580" class="ts-link">1:33:00</a><h3>T</h3>'


def test_missing_equals_sign_in_the_query():
    html = f'<p>X <a href="https://www.youtube.com/watch?v=X&t7:53<h3>T</h3>'
    assert close(html) == (
        '<p>X <a href="https://www.youtube.com/watch?v=X&t=473" class="ts-link">7:53</a><h3>T</h3>'
    )


def test_prose_with_a_colon_word_is_not_eaten():
    html = f'<p>X <a href="{URL}120". Fazit: Das war es <h3>T</h3>'
    assert close(html) == (
        f'<p>X <a href="{URL}120" class="ts-link">2:00</a>. Fazit: Das war es <h3>T</h3>'
    )


def test_well_formed_anchor_is_untouched():
    html = f'<p>X <a href="{URL}64" class="ts-link">1:04</a>.</p>'
    assert close(html) == html


def test_tag_without_a_timestamp_is_left_alone():
    html = '<p>X <a href="https://example.com" class="x<h3>T</h3>'
    assert close(html) == html


def test_sanitizer_recovers_the_swallowed_sentence():
    html = f'<p>Ein Satz <a href="{URL}1:02". Diese Prosa war verschluckt <b>x</b></p>'
    out = sanitize_summary(html)
    assert "Diese Prosa war verschluckt" in out
