"""Tests for _unwrap_json_response — models answering with a JSON object.

Some responses arrive as {"summary": "<div>...", "tags": [...]} instead of raw
HTML. Stored that way, every tag and href stays backslash-escaped and the whole
summary renders as literal text.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from openrouter import _unwrap_json_response as unwrap
from openrouter import _clean_response, repair_summary_html


HTML = '<p>Ein Satz <a href="https://www.youtube.com/watch?v=X&t=64" class="ts-link">1:04</a>.</p>'


def test_plain_html_is_untouched():
    assert unwrap(HTML) == HTML


def test_complete_json_object_is_unwrapped():
    payload = '{\n  "summary": "<p>Ein Satz.</p>\\n<h3>Titel</h3>",\n  "tags": ["A"]\n}'
    assert unwrap(payload) == "<p>Ein Satz.</p>\n<h3>Titel</h3>"


def test_truncated_json_object_is_unwrapped():
    """_parse_tags cuts the response at the <!-- tags: --> comment, which takes
    the JSON object's closing quote and brace with it."""
    payload = '{\n  "summary": "<p>Ein Satz <a href=\\"https://x/?v=1&t=64\\">1:04</a>.</p>\\n</div>\\n\\n'
    assert unwrap(payload) == '<p>Ein Satz <a href="https://x/?v=1&t=64">1:04</a>.</p>\n</div>\n\n'


def test_json_object_without_a_summary_key_is_untouched():
    payload = '{"foo": 1}'
    assert unwrap(payload) == payload


def test_clean_response_unwraps_and_still_extracts_tags():
    payload = '{"summary": "<p>Ein Satz.</p>\\n<!-- tags: Alpha, Beta -->"}'
    html, tags = _clean_response(payload)
    assert html == "<p>Ein Satz.</p>"
    assert tags == ["Alpha", "Beta"]


def test_repair_pass_unwraps_a_stored_json_summary():
    stored = '{\n  "summary": "<p>Ein Satz <a href=\\"https://www.youtube.com/watch?v=X&t=64\\" class=\\"ts-link\\">1:04</a>.</p>\\n'
    assert repair_summary_html(stored) == (
        '<p>Ein Satz <a href="https://www.youtube.com/watch?v=X&t=64" class="ts-link">1:04</a>.</p>\n'
    )
