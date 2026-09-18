"""The YouTube userscript's URL handling.

The script itself only drives a page, but everything it does hangs off two
pure functions: which video the current YouTube URL is, and what to hand the
archive. Both are exported for Node when there is no document.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from export_harness import node_available

REPO = os.path.dirname(os.path.dirname(__file__))
SCRIPT = os.path.join(REPO, "userscript", "yt-ingest.user.js")

pytestmark = pytest.mark.skipif(not node_available(), reason="node not installed")


def _call(expr):
    code = "const m = require(%s); console.log(JSON.stringify(%s));" % (json.dumps(SCRIPT), expr)
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr[:2000]
    return json.loads(out.stdout)


def test_the_metadata_block_declares_both_pages():
    with open(SCRIPT, encoding="utf-8") as f:
        head = f.read(2000)
    assert "// @match        https://www.youtube.com/*" in head
    assert "@grant        GM_openInTab" in head
    assert "yt.html*" in head, "the archive @match line must be there to be edited"


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/live/dQw4w9WgXcQ?feature=share", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
])
def test_the_video_id_is_found_wherever_youtube_puts_it(url, expected):
    assert _call("m.videoIdFromUrl(%s)" % json.dumps(url)) == expected


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/",
    "https://www.youtube.com/feed/subscriptions",
    "https://www.youtube.com/@somechannel",
    "https://www.youtube.com/watch?v=tooshort",
    "not a url at all",
])
def test_pages_without_a_video_yield_nothing(url):
    assert _call("m.videoIdFromUrl(%s)" % json.dumps(url)) is None


def test_an_id_starting_with_a_dash_survives():
    """collect.py needed --video=<id> for exactly this reason."""
    assert _call('m.videoIdFromUrl("https://www.youtube.com/watch?v=-wQ6JCz1s2Y")') == "-wQ6JCz1s2Y"


def test_the_archive_is_addressed_by_hash_not_by_the_deep_link_parameter():
    """'?v=ID' is the archive's own single-video view; using it would show the
    video instead of queueing it."""
    url = _call('m.archiveUrlFor("dQw4w9WgXcQ")')
    assert url.endswith("#ingest=dQw4w9WgXcQ")
    assert "?v=" not in url


def test_the_archive_side_reads_back_what_the_youtube_side_wrote():
    assert _call('m.ingestIdFromHash("#ingest=dQw4w9WgXcQ")') == "dQw4w9WgXcQ"
    assert _call('m.ingestIdFromHash("#session=abc&ingest=dQw4w9WgXcQ")') == "dQw4w9WgXcQ"


@pytest.mark.parametrize("hash_", ["", "#", "#ingest=", "#ingest=short", "#other=dQw4w9WgXcQ"])
def test_a_hash_without_a_usable_id_queues_nothing(hash_):
    assert _call("m.ingestIdFromHash(%s)" % json.dumps(hash_)) is None
