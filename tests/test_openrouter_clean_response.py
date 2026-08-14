"""Tests for _clean_response — code-fence stripping and tag extraction."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import openrouter


def test_plain_html_untouched():
    html, tags = openrouter._clean_response("<h3>A</h3>\n<p>B</p>")
    assert html == "<h3>A</h3>\n<p>B</p>"
    assert tags == []


def test_fence_without_tags_is_stripped():
    html, tags = openrouter._clean_response("```html\n<p>B</p>\n```")
    assert html == "<p>B</p>"
    assert tags == []


def test_tags_comment_inside_the_fence():
    """The tags comment sits before the closing fence — leading fence must go."""
    raw = "```html\n<p>B</p>\n<!-- tags: Alpha, Beta -->\n```"
    html, tags = openrouter._clean_response(raw)
    assert "```" not in html
    assert html == "<p>B</p>"
    assert tags == ["Alpha", "Beta"]


def test_tags_comment_after_the_fence():
    """The tags comment trails the closing fence — both fences must go."""
    raw = "```html\n<p>B</p>\n```\n<!-- tags: Alpha, Beta -->"
    html, tags = openrouter._clean_response(raw)
    assert "```" not in html
    assert html == "<p>B</p>"
    assert tags == ["Alpha", "Beta"]


def test_tags_without_fence():
    html, tags = openrouter._clean_response("<p>B</p>\n<!-- tags: Alpha -->")
    assert html == "<p>B</p>"
    assert tags == ["Alpha"]


def test_bare_fence_without_language():
    html, _ = openrouter._clean_response("```\n<p>B</p>\n```")
    assert html == "<p>B</p>"


def test_backticks_inside_body_are_kept():
    """Only leading/trailing fences are stripped, not inline code markers."""
    raw = "<p>Use ```npm install``` here</p>"
    html, _ = openrouter._clean_response(raw)
    assert html == raw
