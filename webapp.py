"""Make an export archive installable as a web app (PWA) with offline reading.

Next to every ``<page>.html`` the export writes

  ``<page>.html.webmanifest``  name, icons, ``display: standalone``
  ``<page>.html.sw.js``        a service worker scoped to exactly this page
  ``archive-app-icon-*.png``   the icons, shared by every page in the directory

The service worker is network first: the page always comes from the server
when it can, and the copy it caches is only served when the network fails.
That keeps it out of the way of the update banner and of every re-export,
which a cache-first worker would hide behind a stale page. It handles nothing
but the page itself and its manifest and icons -- the ``.meta.json`` sidecar
and the sync server's API go to the network untouched, so polling and sync
behave exactly as without it.

Each page gets its own worker and manifest (scope ``/yt.html`` vs.
``/yt.full.html``), so the personal view and the full archive install as two
separate apps and never answer for each other. Nothing here matters for a
``file://`` archive: browsers register no service worker there, and the page
only tries over http(s).
"""
import json
import os
import struct
import zlib

ICON_SIZES = (180, 192, 512)
ICON_NAME = "archive-app-icon-{size}.png"
BACKGROUND = "#0f0f0f"           # the archive's body background
ICON_BG = (15, 15, 15)           # BACKGROUND as RGB
ICON_FG = (229, 57, 53)          # a red close to the archive's accents

MANIFEST_SUFFIX = ".webmanifest"
SW_SUFFIX = ".sw.js"


# ── Icon ──────────────────────────────────────────────────────────────────────

def _png(width: int, height: int, rgb_rows) -> bytes:
    """Encode 8-bit RGB rows as a PNG, without any imaging library."""
    raw = b"".join(b"\x00" + bytes(row) for row in rgb_rows)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def icon_png(size: int) -> bytes:
    """A red play triangle on the archive's dark background, full bleed.

    Full bleed with the glyph well inside the middle 60 % means the same image
    works as an ordinary and as a maskable icon (Android crops to a circle or
    squircle; iOS rounds the corners itself). 2x2 supersampling smooths the
    triangle's edges.
    """
    s = float(size)
    # Triangle pointing right, centred slightly right of the middle so it
    # looks centred (its visual weight sits left of its bounding box).
    ax, ay = 0.38 * s, 0.30 * s
    bx, by = 0.38 * s, 0.70 * s
    cx, cy = 0.72 * s, 0.50 * s

    def inside(x: float, y: float) -> bool:
        d1 = (x - bx) * (ay - by) - (ax - bx) * (y - by)
        d2 = (x - cx) * (by - cy) - (bx - cx) * (y - cy)
        d3 = (x - ax) * (cy - ay) - (cx - ax) * (y - ay)
        neg = d1 < 0 or d2 < 0 or d3 < 0
        pos = d1 > 0 or d2 > 0 or d3 > 0
        return not (neg and pos)

    offsets = ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75))
    rows = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            hits = sum(inside(px + ox, py + oy) for ox, oy in offsets)
            for c in range(3):
                row.append((ICON_FG[c] * hits + ICON_BG[c] * (4 - hits)) // 4)
        rows.append(row)
    return _png(size, size, rows)


# ── Manifest and service worker ───────────────────────────────────────────────

# (name, short_name) per kind of page. The personal view and the full archive
# install side by side, so their names must tell them apart.
APP_NAMES = {
    "archive": ("YouTube-Archiv", "YT-Archiv"),
    "full": ("YouTube-Archiv (alle)", "YT-Alle"),
}


def manifest_json(page: str, name: str, short_name: str, lang: str) -> str:
    icons = [
        {"src": ICON_NAME.format(size=size), "sizes": f"{size}x{size}",
         "type": "image/png", "purpose": "any maskable"}
        for size in ICON_SIZES if size != 180
    ]
    return json.dumps({
        "id": page,
        "name": name,
        "short_name": short_name,
        "lang": lang,
        "start_url": page,
        # The whole directory, so the header's link between the personal view
        # and the full archive stays inside the app window.
        "scope": "./",
        "display": "standalone",
        "background_color": BACKGROUND,
        "theme_color": BACKGROUND,
        "icons": icons,
    }, ensure_ascii=False, indent=2)


SW_TEMPLATE = """\
// Service worker for {page} -- written by export.py (webapp.py), do not edit.
// Network first: the page comes from the server whenever it can, the cached
// copy only when the network fails. Only the page, its manifest and its icons
// are handled; the .meta.json sidecar and the sync API go to the network
// untouched.
const PAGE = {page_js};
const CACHE = {cache_js};
const ASSETS = {assets_js};

self.addEventListener('install', function () {{ self.skipWaiting(); }});

self.addEventListener('activate', function (event) {{
  event.waitUntil(self.clients.claim());
}});

self.addEventListener('fetch', function (event) {{
  var req = event.request;
  if (req.method !== 'GET') return;
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  var name = url.pathname.split('/').pop();
  if (name !== PAGE && ASSETS.indexOf(name) === -1) return;
  // One copy per file: ?v=ID deep links all share the page's cache entry.
  var key = new URL(name, self.location.href).href;
  event.respondWith(
    fetch(req).then(function (resp) {{
      if (resp.ok) {{
        var copy = resp.clone();
        event.waitUntil(caches.open(CACHE).then(function (c) {{ return c.put(key, copy); }}));
      }}
      return resp;
    }}).catch(function () {{
      return caches.match(key).then(function (hit) {{ return hit || Response.error(); }});
    }})
  );
}});
"""


def service_worker_js(page: str) -> str:
    assets = [page + MANIFEST_SUFFIX] + [ICON_NAME.format(size=s) for s in ICON_SIZES]
    return SW_TEMPLATE.format(
        page=page,
        page_js=json.dumps(page),
        cache_js=json.dumps("archive-app:" + page),
        assets_js=json.dumps(assets),
    )


# ── Writing ───────────────────────────────────────────────────────────────────

def write_app_files(output_path: str, lang: str, kind: str = "archive") -> dict:
    """Write manifest, service worker and (if missing) icons next to output_path.

    Returns the URLs the page needs, all relative to the page itself.
    """
    directory = os.path.dirname(os.path.abspath(output_path))
    page = os.path.basename(output_path)
    name, short_name = APP_NAMES[kind]

    for size in ICON_SIZES:
        path = os.path.join(directory, ICON_NAME.format(size=size))
        # Constant images: drawing a 512 px icon in pure Python takes about a
        # second, so an existing file is kept rather than redrawn every run.
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(icon_png(size))
            os.replace(tmp, path)

    with open(output_path + MANIFEST_SUFFIX, "w", encoding="utf-8") as f:
        f.write(manifest_json(page, name, short_name, lang))
    with open(output_path + SW_SUFFIX, "w", encoding="utf-8") as f:
        f.write(service_worker_js(page))

    return {
        "manifest_url": page + MANIFEST_SUFFIX,
        "sw_url": page + SW_SUFFIX,
        "scope": "./" + page,
        "apple_icon": ICON_NAME.format(size=180),
        "title": short_name,
    }
