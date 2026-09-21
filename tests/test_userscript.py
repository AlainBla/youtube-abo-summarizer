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


def _slot(chain, expr="m.rowSlotFor(anchor, body)"):
    """Build a duck-typed parent chain (innermost first) and ask for the slot.

    Each entry is {"id": ..., "tag": ...}; the last one's parent is the body.
    """
    code = (
        "const m = require(%s);\n"
        "const spec = %s;\n"
        "const body = { id: 'body', tagName: 'BODY', parentElement: null };\n"
        "let parent = body;\n"
        "const nodes = [];\n"
        "for (let i = spec.length - 1; i >= 0; i--) {\n"
        "  const n = { id: spec[i].id || '', tagName: spec[i].tag || 'DIV',\n"
        "              parentElement: parent, nextSibling: null };\n"
        "  nodes.unshift(n); parent = n;\n"
        "}\n"
        "const anchor = nodes[0];\n"
        "const slot = %s;\n"
        "console.log(JSON.stringify(slot ? { id: slot.parent.id, tag: slot.parent.tagName } : null));\n"
    ) % (json.dumps(SCRIPT), json.dumps(chain), expr)
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr[:2000]
    return json.loads(out.stdout)


def test_the_button_lands_in_the_action_row_when_there_is_one():
    assert _slot([
        {"tag": "SEGMENTED-LIKE-DISLIKE-BUTTON-VIEW-MODEL"},
        {"tag": "DIV"},
        {"id": "top-level-buttons-computed"},
        {"id": "actions"},
    ]) == {"id": "top-level-buttons-computed", "tag": "DIV"}


def test_a_chain_without_an_action_row_yields_no_slot():
    """The walk used to fall out at <body> and hand it back as the parent, and
    the chip was appended to the end of the document -- below the comments in
    tablet portrait, near the fold in landscape. It measures as visible there,
    so nothing ever escalated it to the floating corner."""
    assert _slot([
        {"tag": "LIKE-BUTTON-VIEW-MODEL"},
        {"id": "some-mobile-wrapper"},
        {"id": "app"},
    ]) is None


def test_an_anchor_sitting_directly_under_body_yields_no_slot():
    assert _slot([{"tag": "LIKE-BUTTON-VIEW-MODEL"}]) is None


# ── the verdict ───────────────────────────────────────────────────────────────
# A submission's outcome is read off the archive's own data-ingest-* attributes.
# Inferring it from the button's disabled flag was a race the script usually
# lost in a background tab, and reported a freshly queued video as one that was
# already in the archive.

def test_an_archive_that_states_its_verdict_is_recognised():
    assert _call("m.speaksVerdict({ingestResult: '', ingestId: ''})") is True


def test_an_archive_exported_before_v1_8_states_nothing():
    assert _call("m.speaksVerdict({})") is False
    assert _call("m.speaksVerdict(null)") is False


@pytest.mark.parametrize("result,expected", [
    ("queued", "queued"),
    ("already", "already"),
    ("failed", "failed"),
    ("logged-out", "logged-out"),
    ("", None),          # the box as rendered: no verdict yet, keep waiting
])
def test_the_verdict_for_this_video_is_read_back(result, expected):
    ds = json.dumps({"ingestId": "dQw4w9WgXcQ", "ingestResult": result})
    assert _call("m.ingestVerdict(%s, 'dQw4w9WgXcQ')" % ds) == expected


def test_a_verdict_about_another_video_is_not_this_ones():
    ds = json.dumps({"ingestId": "abcdefgh123", "ingestResult": "already"})
    assert _call("m.ingestVerdict(%s, 'dQw4w9WgXcQ')" % ds) is None


@pytest.mark.parametrize("value,saw_disabled,expected", [
    ("", True, "queued"),          # sent, answered, field cleared
    ("", False, "already"),        # cleared without a request going out
    ("dQw4w9WgXcQ", True, "failed"),
    ("dQw4w9WgXcQ", False, None),  # nothing has happened yet
])
def test_the_old_reading_still_works_on_an_old_archive(value, saw_disabled, expected):
    assert _call("m.legacyVerdict(%s, %s)" % (json.dumps(value), json.dumps(saw_disabled))) == expected
