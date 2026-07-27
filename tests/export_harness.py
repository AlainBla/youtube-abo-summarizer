"""Helpers for exercising the export template's JS in Node."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import renderer

HERE = os.path.dirname(__file__)
DOM_STUB = os.path.join(HERE, "dom_stub.js")


def node_available() -> bool:
    return shutil.which("node") is not None


def render_export(videos: list[dict], **kwargs) -> str:
    """Render an export archive to a temp file and return its HTML."""
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "export.html")
        renderer.render_export_html(videos, out, **kwargs)
        with open(out, encoding="utf-8") as f:
            return f.read()


def extract_script(html: str) -> str:
    """Return the inline <script> bodies concatenated in document order.

    export.html.j2 now emits the UI code and the (large) data blob as two
    separate <script> tags so the browser can parse/execute the UI code
    before tokenizing the multi-MB base64 payload. Classic <script> tags in
    a document share one global lexical environment and run in source order,
    so concatenating their bodies is a faithful model of that for Node.
    """
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert scripts, "no inline script found in export HTML"
    return "\n".join(scripts)


def run_node(script: str, snippet: str) -> str:
    """Run DOM stubs + export script + snippet in Node, return stdout."""
    with open(DOM_STUB, encoding="utf-8") as f:
        stub = f.read()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "run.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(stub + "\n" + script + "\n" + snippet + "\n")
        proc = subprocess.run(
            ["node", path], capture_output=True, text=True, timeout=60
        )
        if proc.returncode != 0:
            raise AssertionError("node failed:\n" + proc.stderr[:2000])
        return proc.stdout


def video(vid, published, **over):
    """A minimal export video record."""
    v = {
        "video_id": vid,
        "channel_id": "UC1",
        "channel_title": "Channel One",
        "title": "Title " + vid,
        "published_at": published,
        "published_at_display": "January 01, 2026",
        "duration": "10:00",
        "thumbnail_url": "https://i.ytimg.com/vi/%s/hq.jpg" % vid,
        "summary": "<p>Summary of %s</p><p>More detail</p>" % vid,
        "summary_model": None,
        "transcript_error": None,
        "tags": ["Tag A"],
    }
    v.update(over)
    return v
