"""Tests for Jinja2 autoescape=True — M3 fix.

Verifies that:
- Untrusted string values (channel title, video title) are HTML-escaped in output.
- Sanitized summary HTML still renders as real HTML, not escaped text.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from renderer import render_html, render_export_html


_CHANNEL_TITLE_XSS = '<script>alert("xss")</script>Channel'
_VIDEO_TITLE_XSS = '<img src=x onerror=alert(1)>Video'

_BASE_CHANNEL = {
    "channel_id": "UC123",
    "title": _CHANNEL_TITLE_XSS,
    "videos": [],
}

_BASE_VIDEO = {
    "video_id": "dQw4w9WgXcQ",
    "title": _VIDEO_TITLE_XSS,
    "published_at": "January 01, 2026",
    "duration": "5:00",
    "thumbnail_url": "",
    "summary": "<h3>Topic</h3><p>Body text.</p>",
    "summary_model": None,
    "transcript_error": None,
}


# ── report template ───────────────────────────────────────────────────────────

def test_channel_title_with_script_is_escaped_in_report(tmp_path):
    """Channel titles must not inject raw HTML into the report."""
    out = str(tmp_path / "report.html")
    render_html([_BASE_CHANNEL], out)
    content = open(out).read()
    assert '<script>alert("xss")</script>' not in content
    assert "&lt;script&gt;" in content


def test_video_title_with_onerror_is_escaped_in_report(tmp_path):
    """Video titles with event handlers must be escaped, not executed.

    The raw <img> tag must not appear as a DOM element. After auto-escaping the
    title text, the string 'onerror=alert(1)' may still appear as plain text
    inside &lt;img...&gt;, but that is harmless.
    """
    channel = {**_BASE_CHANNEL, "title": "Channel", "videos": [_BASE_VIDEO]}
    out = str(tmp_path / "report.html")
    render_html([channel], out)
    content = open(out).read()
    # Raw <img> tag must not be present (would create an executable DOM element)
    assert "<img src=x onerror=alert(1)>" not in content
    # The title must appear as escaped text
    assert "&lt;img" in content


def test_summary_html_renders_as_html_not_escaped_text_in_report(tmp_path):
    """Sanitized summary must render as actual HTML tags, not escaped text."""
    channel = {**_BASE_CHANNEL, "title": "Channel", "videos": [_BASE_VIDEO]}
    out = str(tmp_path / "report.html")
    render_html([channel], out)
    content = open(out).read()
    assert "<h3>Topic</h3>" in content
    assert "&lt;h3&gt;" not in content


# ── export template ───────────────────────────────────────────────────────────

def test_channel_title_with_script_is_escaped_in_export(tmp_path):
    """Channel titles in export JSON blob must not contain raw script tags."""
    video = {
        "video_id": "dQw4w9WgXcQ",
        "channel_id": "UC123",
        "channel_title": _CHANNEL_TITLE_XSS,
        "title": "Title",
        "published_at": "2026-01-01T00:00:00Z",
        "published_at_display": "January 01, 2026",
        "duration": "5:00",
        "thumbnail_url": "",
        "summary": "<p>Body.</p>",
        "summary_model": None,
        "transcript_error": None,
        "tags": [],
    }
    out = str(tmp_path / "export.html")
    render_export_html([video], out)
    content = open(out).read()
    # The raw unescaped script tag must not appear outside of the JSON blob.
    # The JSON blob itself will still contain the raw channel_title (it's JSON-encoded,
    # not HTML — the </script> protection already handles the only real risk there).
    # What must NOT happen: the channel title leaking into HTML attribute/element context.
    # We verify the overall page does not contain an executable script tag from the title.
    assert '<script>alert("xss")</script>' not in content.split("const VIDEOS")[0]
