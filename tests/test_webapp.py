"""The export installs as a web app and reads offline (webapp.py).

The service worker is exercised in Node against a fake fetch and Cache API:
network first, the cached copy only when the network fails, one cache entry
for every ?v= deep link, and the .meta.json sidecar never touched -- a worker
that answered it from cache would freeze the update banner.
"""
import json
import os
import struct
import subprocess
import sys
import tempfile
import zlib

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import renderer
import webapp
from export_harness import extract_script, node_available, run_node, video


def _render(tmp_path, name="yt.html", **kw):
    path = str(tmp_path / name)
    renderer.render_export_html([video("v1", "2026-01-01T00:00:00Z")], path, **kw)
    return path, open(path, encoding="utf-8").read()


# ── Icons ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("size", [32, 192])
def test_icon_is_a_valid_png_of_the_right_size(size):
    data = webapp.icon_png(size)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h, depth, colour = struct.unpack(">IIBB", data[16:26])
    assert (w, h, depth, colour) == (size, size, 8, 2)
    idat = data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8]
    assert len(zlib.decompress(idat)) == size * (1 + 3 * size)


def test_icon_has_background_and_glyph():
    data = webapp.icon_png(64)
    idat = data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8]
    pixels = zlib.decompress(idat)
    row = 32 * (1 + 3 * 64)
    corner = tuple(pixels[1:4])
    centre = tuple(pixels[row + 1 + 3 * 34: row + 1 + 3 * 34 + 3])
    assert corner == webapp.ICON_BG
    assert centre == webapp.ICON_FG


# ── Files next to the export ──────────────────────────────────────────────────

def test_export_writes_manifest_worker_and_icons(tmp_path):
    path, html = _render(tmp_path)
    for suffix in (".webmanifest", ".sw.js", ".meta.json"):
        assert os.path.exists(path + suffix)
    for size in webapp.ICON_SIZES:
        assert (tmp_path / webapp.ICON_NAME.format(size=size)).exists()
    m = json.load(open(path + ".webmanifest"))
    assert m["start_url"] == "yt.html" and m["id"] == "yt.html"
    assert m["display"] == "standalone"
    assert {i["sizes"] for i in m["icons"]} == {"192x192", "512x512"}
    assert '<link rel="manifest" href="yt.html.webmanifest">' in html
    assert 'rel="apple-touch-icon" href="archive-app-icon-180.png"' in html


def test_full_archive_installs_as_a_separate_app(tmp_path):
    p1, _ = _render(tmp_path, "yt.html")
    p2, _ = _render(tmp_path, "yt.full.html", app="full")
    m1, m2 = (json.load(open(p + ".webmanifest")) for p in (p1, p2))
    assert m1["id"] != m2["id"]
    assert m1["name"] != m2["name"]


def test_no_app_writes_nothing_and_links_nothing(tmp_path):
    path, html = _render(tmp_path, app=None)
    assert not os.path.exists(path + ".webmanifest")
    assert not os.path.exists(path + ".sw.js")
    assert not list(tmp_path.glob("archive-app-icon-*"))
    assert 'rel="manifest"' not in html
    assert "const PWA = null;" in html


def test_existing_icons_are_kept(tmp_path):
    icon = tmp_path / webapp.ICON_NAME.format(size=512)
    icon.write_bytes(b"keep")
    _render(tmp_path)
    assert icon.read_bytes() == b"keep"


def test_export_cli_no_app_flag():
    import export
    sys.argv = ["export.py", "--no-app"]
    try:
        assert export.parse_args().no_app is True
    finally:
        sys.argv = ["pytest"]


# ── Registration in the page ──────────────────────────────────────────────────

FAKE_SW = """
globalThis.__registered = [];
globalThis.navigator.serviceWorker = {register: function (url, opts) {
  __registered.push([url, opts.scope]); return Promise.resolve();
}};
"""


@pytest.mark.skipif(not node_available(), reason="node not installed")
@pytest.mark.parametrize("protocol,expected", [("https:", 1), ("file:", 0)])
def test_page_registers_worker_over_http_only(tmp_path, protocol, expected):
    _, html = _render(tmp_path)
    script = FAKE_SW + f"globalThis.window.location.protocol = '{protocol}';\n" + extract_script(html)
    out = run_node(script, "console.log(JSON.stringify(__registered));")
    reg = json.loads(out.strip().splitlines()[-1])
    assert len(reg) == expected
    if expected:
        assert reg[0] == ["yt.html.sw.js", "./yt.html"]


# ── The service worker itself ─────────────────────────────────────────────────

SW_HARNESS = r"""
var handlers = {};
var store = {};
var network = {up: true, calls: []};
globalThis.self = {
  location: {origin: 'https://example.com', href: 'https://example.com/yt.html.sw.js'},
  addEventListener: function (t, f) { handlers[t] = f; },
  skipWaiting: function () {}, clients: {claim: function () { return Promise.resolve(); }},
};
globalThis.caches = {
  open: function () { return Promise.resolve({put: function (k, v) { store[k] = v; return Promise.resolve(); }}); },
  match: function (k) { return Promise.resolve(store[k]); },
};
globalThis.Response = {error: function () { return {error: true}; }};
globalThis.fetch = function (req) {
  network.calls.push(req.url);
  if (!network.up) return Promise.reject(new TypeError('offline'));
  return Promise.resolve({ok: true, body: 'fresh:' + req.url, clone: function () { return {body: this.body, cached: true}; }});
};
function request(url) {
  var answered = null, waits = [];
  handlers.fetch({request: {method: 'GET', url: url},
                  respondWith: function (p) { answered = p; },
                  waitUntil: function (p) { waits.push(p); }});
  if (!answered) return Promise.resolve('passthrough');
  return answered.then(function (r) { return Promise.all(waits).then(function () { return r; }); });
}
"""


def _run_sw(snippet):
    sw = webapp.service_worker_js("yt.html")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "run.js")
        with open(path, "w") as f:
            f.write(SW_HARNESS + sw + snippet)
        out = subprocess.run(["node", path], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_worker_is_network_first_and_falls_back_offline():
    data = _run_sw("""
    (async function () {
      var online = await request('https://example.com/yt.html?v=abc');
      network.up = false;
      var offline = await request('https://example.com/yt.html');
      var deep = await request('https://example.com/yt.html?v=other');
      console.log(JSON.stringify({online: online.body, offline: offline.body,
                                  deep: deep.body, keys: Object.keys(store)}));
    })();
    """)
    assert data["online"] == "fresh:https://example.com/yt.html?v=abc"
    # Offline, any deep link gets the one cached page.
    assert data["offline"] == data["deep"] == data["online"]
    assert data["keys"] == ["https://example.com/yt.html"]


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_worker_leaves_sidecar_sync_and_other_pages_alone():
    data = _run_sw("""
    (async function () {
      var r = [];
      for (var u of ['https://example.com/yt.html.meta.json',
                     'https://example.com/sync/api/state',
                     'https://example.com/yt.full.html',
                     'https://other.example/yt.html']) r.push(await request(u));
      console.log(JSON.stringify(r));
    })();
    """)
    assert data == ["passthrough"] * 4


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_worker_offline_without_cache_fails_like_the_browser():
    data = _run_sw("""
    (async function () {
      network.up = false;
      var r = await request('https://example.com/yt.html');
      console.log(JSON.stringify(r));
    })();
    """)
    assert data == {"error": True}
