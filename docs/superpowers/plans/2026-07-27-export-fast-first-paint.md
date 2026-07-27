# Export Fast First Paint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein Export-Archiv mit mehreren tausend Videos zeigt die erste Seite sofort, statt erst nach dem Dekodieren der gesamten eingebetteten Daten.

**Architecture:** Der Export bleibt eine einzelne portable HTML-Datei. Die ersten 20 Karten werden beim Export als statisches HTML vorgerendert und stehen vor der Datenlast im Dokument. Die Daten selbst werden in einen Index-Blob (Metadaten) und Summary-Chunks zu je 50 Videos aufgeteilt; Chunks werden erst dekodiert, wenn eine Seite sie braucht, der Rest per Idle-Prefetch. Sync-Requests starten in einem kurzen Inline-Script vor der Datenlast.

**Tech Stack:** Python 3.11+, Jinja2, nh3, gzip+base64, Vanilla JS (DecompressionStream), pytest, Node (nur für Tests).

Spec: `docs/superpowers/specs/2026-07-27-export-fast-first-paint-design.md`

## Global Constraints

- Der Export bleibt **eine Datei**. Keine `fetch()`-Aufrufe auf Nachbardateien — unter `file://` blockiert.
- Keine neuen Laufzeit-Abhängigkeiten im Browser, kein Build-Schritt.
- Chunkgröße: **50** Videos (`renderer.EXPORT_CHUNK_SIZE`). Vorgerenderte erste Seite: **20** Karten (`renderer.EXPORT_FIRST_PAGE`), identisch mit `PAGE_SIZE` in `export.html.j2`.
- Default-Sortierung überall: `published_at` absteigend, Gleichstand nach `video_id` absteigend.
- `--no-compress` behält genau das heutige Verhalten (ein `{index, summaries}`-Objektliteral, kein Chunking); nur die vorgerenderte erste Seite kommt dort ebenfalls hinzu.
- Bestehendes Verhalten unverändert: Filter, Sortierung, Pagination, Suche, Read/Bookmark, Sync, i18n, `--thumbnail`, `--show-model`.
- Keine Emojis in Code, Commits, Doku.

### Testumgebung (einmalig einrichten)

Das Projekt-`.venv` hat weder `pip` noch `nh3`. Für Tests ein eigenes venv anlegen:

```bash
python3 -m venv /tmp/ytenv --system-site-packages
/tmp/ytenv/bin/pip install -q nh3
/tmp/ytenv/bin/python -c "import nh3, jinja2, pytest; print('ok')"
```

Alle `pytest`-Kommandos in diesem Plan laufen mit `/tmp/ytenv/bin/python -m pytest`.

**Vorbestehende Fehlschläge (nicht von dieser Änderung verursacht, ignorieren):**
`tests/test_openrouter_prompt.py` (kein `openai`-Modul), 9 Fehlschläge in `tests/test_transcripts.py`.
Baseline vor Beginn festhalten:

```bash
/tmp/ytenv/bin/python -m pytest tests -q --ignore=tests/test_openrouter_prompt.py 2>&1 | tail -3
```

Node muss vorhanden sein (`node --version`, v18+ wegen `DecompressionStream`). Tests, die Node brauchen, skippen sich selbst, wenn es fehlt.

---

## File Structure

| Datei | Verantwortung | Status |
|---|---|---|
| `renderer.py` | Sortierung, Index/Chunk-Aufteilung, Summary-Preview-Split, HTML-Escaping wie im JS, Template-Kontext | ändern |
| `export.html.j2` | Dokumentreihenfolge, Kartenmakro, Hydration-Script, früher Sync-Boot, Chunk-Cache im JS | ändern |
| `i18n.py` | zusätzliche Strings für das Kartenmakro | ändern |
| `tests/export_harness.py` | Test-Helfer: Export rendern, `<script>` extrahieren, in Node ausführen | neu |
| `tests/dom_stub.js` | Browser-Stubs, damit der Export-JS-Code in Node läuft | neu |
| `tests/test_export_chunks.py` | Aufteilung, Blob-Struktur, Dekodierung in Node | neu |
| `tests/test_export_prerender.py` | Drift-Test Jinja-Makro gegen `buildCard()` | neu |
| `README.md`, `CLAUDE.md`, `AGENTS.md` | Beschreibung des Ladeverhaltens | ändern |

---

## Task 1: Datenaufteilung im Renderer

**Files:**
- Modify: `renderer.py` (neue Konstanten und Hilfsfunktionen oberhalb von `render_export_html`)
- Test: `tests/test_export_chunks.py` (neu)

**Interfaces:**
- Consumes: `renderer._sanitize_summary(html) -> str | None` (existiert)
- Produces:
  - `renderer.EXPORT_CHUNK_SIZE = 50`, `renderer.EXPORT_FIRST_PAGE = 20`
  - `renderer._split_export_data(videos: list[dict], chunk_size: int = EXPORT_CHUNK_SIZE) -> tuple[list[dict], list[dict[str, str]]]`
  - `renderer._summary_preview(html: str) -> tuple[str, str]`
  - `renderer._esc_html(s) -> str`

- [ ] **Step 1: Write the failing test**

`tests/test_export_chunks.py`:

```python
"""Tests for the export data split (index + summary chunks)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from renderer import _split_export_data, _summary_preview, _esc_html


def _video(vid, published, summary="<p>one</p><p>two</p>"):
    return {
        "video_id": vid,
        "channel_id": "UC1",
        "channel_title": "Channel",
        "title": "Title " + vid,
        "published_at": published,
        "published_at_display": "January 01, 2026",
        "duration": "10:00",
        "thumbnail_url": "https://i.ytimg.com/vi/%s/hq.jpg" % vid,
        "summary": summary,
        "summary_model": None,
        "transcript_error": None,
        "tags": ["Tag"],
    }


def test_index_is_sorted_newest_first():
    videos = [
        _video("a", "2026-01-01T00:00:00Z"),
        _video("c", "2026-03-01T00:00:00Z"),
        _video("b", "2026-02-01T00:00:00Z"),
    ]
    index, _ = _split_export_data(videos)
    assert [v["video_id"] for v in index] == ["c", "b", "a"]


def test_ties_break_on_video_id_descending():
    videos = [_video("a", "2026-01-01T00:00:00Z"), _video("b", "2026-01-01T00:00:00Z")]
    index, _ = _split_export_data(videos)
    assert [v["video_id"] for v in index] == ["b", "a"]


def test_index_carries_no_summary():
    index, _ = _split_export_data([_video("a", "2026-01-01T00:00:00Z")])
    assert "summary" not in index[0]
    assert index[0]["title"] == "Title a"


def test_chunks_follow_index_order_and_size():
    videos = [_video("v%03d" % i, "2026-01-%02dT00:00:00Z" % (i + 1)) for i in range(7)]
    index, chunks = _split_export_data(videos, chunk_size=3)
    assert [len(c) for c in chunks] == [3, 3, 1]
    for pos, entry in enumerate(index):
        assert entry["video_id"] in chunks[pos // 3]


def test_every_summary_appears_exactly_once():
    videos = [_video("v%d" % i, "2026-01-01T00:00:00Z") for i in range(5)]
    _, chunks = _split_export_data(videos, chunk_size=2)
    seen = [vid for c in chunks for vid in c]
    assert sorted(seen) == sorted(v["video_id"] for v in videos)


def test_video_without_summary_is_absent_from_chunk_but_present_in_index():
    index, chunks = _split_export_data([_video("a", "2026-01-01T00:00:00Z", summary=None)])
    assert [v["video_id"] for v in index] == ["a"]
    assert chunks == [{}]


def test_empty_input_yields_no_chunks():
    assert _split_export_data([]) == ([], [])


def test_summaries_are_sanitized():
    videos = [_video("a", "2026-01-01T00:00:00Z", summary="<p>hi</p><script>evil()</script>")]
    _, chunks = _split_export_data(videos)
    assert "script" not in chunks[0]["a"]


def test_summary_preview_splits_at_first_paragraph():
    preview, rest = _summary_preview("<p>one</p><p>two</p>")
    assert preview == "<p>one</p>"
    assert rest == "<p>two</p>"


def test_summary_preview_without_paragraph_end_has_no_rest():
    preview, rest = _summary_preview("<h3>only</h3>")
    assert preview == "<h3>only</h3>"
    assert rest == ""


def test_esc_html_matches_js_escaping():
    # Mirrors escHtml() in export.html.j2: & < > " and nothing else.
    assert _esc_html('a&b<c>d"e\'f') == "a&amp;b&lt;c&gt;d&quot;e'f"
    assert _esc_html(None) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/tmp/ytenv/bin/python -m pytest tests/test_export_chunks.py -q`
Expected: FAIL — `ImportError: cannot import name '_split_export_data' from 'renderer'`

- [ ] **Step 3: Write minimal implementation**

In `renderer.py`, direkt oberhalb von `def render_export_html(` einfügen:

```python
EXPORT_CHUNK_SIZE = 50
EXPORT_FIRST_PAGE = 20


def _esc_html(s) -> str:
    """Escape exactly like escHtml() in export.html.j2 (& < > " and nothing else).

    Kept byte-compatible with the JS implementation so the pre-rendered first
    page and the JS-built cards produce identical markup.
    """
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _summary_preview(html: str) -> tuple[str, str]:
    """Split a summary into the first paragraph and the remainder.

    Mirrors the split in buildCard(): cut after the first '</p>', the rest is
    trimmed and hidden behind the "more" toggle.
    """
    cut = html.find("</p>")
    if cut == -1:
        return html, ""
    return html[: cut + 4], html[cut + 4 :].strip()


def _split_export_data(
    videos: list[dict], chunk_size: int = EXPORT_CHUNK_SIZE
) -> tuple[list[dict], list[dict[str, str]]]:
    """Sort newest-first, then split into a summary-free index and summary chunks.

    Chunk k holds the sanitized summaries of index positions
    [k*chunk_size, (k+1)*chunk_size), so the browser can decode only the chunks
    a rendered page actually needs. Videos without a summary occupy an index
    slot but no chunk entry.
    """
    ordered = sorted(
        videos,
        key=lambda v: ((v.get("published_at") or ""), v.get("video_id") or ""),
        reverse=True,
    )
    index: list[dict] = []
    chunks: list[dict[str, str]] = []
    for pos, v in enumerate(ordered):
        if pos % chunk_size == 0:
            chunks.append({})
        summary = _sanitize_summary(v.get("summary"))
        if summary:
            chunks[-1][v["video_id"]] = summary
        index.append({k: val for k, val in v.items() if k != "summary"})
    return index, chunks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/tmp/ytenv/bin/python -m pytest tests/test_export_chunks.py -q`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add renderer.py tests/test_export_chunks.py
git commit -m "feat(export): split export data into index and summary chunks"
```

---

## Task 2: Chunk-Blobs im Template, Lazy-Dekodierung im Browser

**Files:**
- Modify: `renderer.py:142-207` (`render_export_html`)
- Modify: `export.html.j2` — Datenzeile (`:471`), `loadData()`/`bootstrap()` (`:1497-1531`), `buildCard()` (`:1219`), `getSearchText()` (`:1285`), `renderPage()` (`:1362`)
- Create: `tests/export_harness.py`, `tests/dom_stub.js`
- Test: `tests/test_export_chunks.py` (erweitern)

**Interfaces:**
- Consumes: `renderer._split_export_data()`, `renderer.EXPORT_CHUNK_SIZE` (Task 1)
- Produces:
  - Template-Kontext: `index_b64: str | None`, `chunks_b64: list[str]`, `chunk_size: int`, `data_obj: Markup | None`, `compressed: bool`
  - JS: `gunzipB64(b64) -> Promise<any>`, `ensureChunk(k) -> Promise<object>`, `ensureChunks(videos) -> Promise<void>`, `chunksReady(videos) -> bool`, `getSummary(v) -> string|undefined`, `bootstrap() -> Promise<void>`
  - Jedes Index-Objekt bekommt beim Dekodieren `v._c` = Chunk-Nummer.
  - Test-Helfer: `tests/export_harness.py` mit `render_export(videos, **kwargs) -> str`, `extract_script(html) -> str`, `run_node(script, snippet) -> str`

- [ ] **Step 1: Write the failing test**

`tests/export_harness.py` (neu):

```python
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
    """Return the last inline <script> body — the export UI code plus data."""
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert scripts, "no inline script found in export HTML"
    return scripts[-1]


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
```

`tests/dom_stub.js` (neu):

```javascript
// Minimal browser stubs so the export UI script can run under Node.
// Node 18+ provides atob, Blob, DecompressionStream, Response, URL natively.
const __els = {};
function __el(id) {
  if (!__els[id]) {
    __els[id] = {
      id: id,
      value: '',
      textContent: '',
      innerHTML: '',
      placeholder: '',
      disabled: false,
      hidden: false,
      style: {},
      options: [{}, {}, {}, {}],
      classList: {add: function () {}, remove: function () {}, toggle: function () {}},
      addEventListener: function () {},
      appendChild: function () {},
      setAttribute: function () {},
      getAttribute: function () { return null; },
      querySelector: function () { return __el(id + '-child'); },
      querySelectorAll: function () { return []; },
      remove: function () {},
    };
  }
  return __els[id];
}

const __store = {};
globalThis.localStorage = {
  getItem: function (k) { return k in __store ? __store[k] : null; },
  setItem: function (k, v) { __store[k] = String(v); },
  removeItem: function (k) { delete __store[k]; },
};
globalThis.document = {
  cookie: '',
  documentElement: {},
  title: '',
  getElementById: __el,
  querySelector: function () { return __el('generic'); },
  querySelectorAll: function () { return []; },
  createElement: function () { return __el('created'); },
  addEventListener: function () {},
  body: __el('body'),
};
globalThis.window = {
  location: {protocol: 'https:', hostname: 'example.com', origin: 'https://example.com',
             pathname: '/x.html', search: '', hash: '', href: 'https://example.com/x.html'},
  addEventListener: function () {},
};
globalThis.location = globalThis.window.location;
globalThis.navigator = {languages: ['de']};
globalThis.alert = function () {};
globalThis.fetch = function () { return Promise.reject(new Error('no network in tests')); };
globalThis.requestAnimationFrame = function (cb) { setTimeout(cb, 0); };
globalThis.requestIdleCallback = function (cb) { setTimeout(cb, 0); };
globalThis.history = {replaceState: function () {}};
```

Zusätzliche Tests in `tests/test_export_chunks.py` (ans Ende anhängen):

```python
import json

import pytest

from export_harness import extract_script, node_available, render_export, run_node


def test_compressed_export_embeds_index_and_chunk_blobs():
    videos = [_video("v%03d" % i, "2026-01-01T00:%02d:00Z" % i) for i in range(7)]
    html = render_export(videos)
    assert "const INDEX_B64" in html
    assert "const SUM_B64" in html
    assert "const DATA_B64" not in html


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_browser_decodes_index_and_only_the_needed_chunk():
    videos = [_video("v%03d" % i, "2026-01-01T00:%02d:00Z" % i) for i in range(120)]
    html = render_export(videos)
    script = extract_script(html)
    out = run_node(script, """
      bootstrap().then(function () {
        console.log(JSON.stringify({
          videos: VIDEOS.length,
          firstId: VIDEOS[0].video_id,
          firstChunk: VIDEOS[0]._c,
          lastChunk: VIDEOS[VIDEOS.length - 1]._c,
          chunkCount: SUM_B64.length,
          decoded: CHUNKS.filter(Boolean).length,
          summary: getSummary(VIDEOS[0]),
        }));
      });
    """)
    data = json.loads(out.strip().splitlines()[-1])
    assert data["videos"] == 120
    assert data["firstId"] == "v119"          # newest first
    assert data["firstChunk"] == 0
    assert data["lastChunk"] == 2             # 120 videos / 50 per chunk
    assert data["chunkCount"] == 3
    assert data["decoded"] == 1               # only chunk 0 decoded for page 1
    assert "Summary of v119" in data["summary"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/tmp/ytenv/bin/python -m pytest tests/test_export_chunks.py -q`
Expected: FAIL — `assert "const INDEX_B64" in html` schlägt fehl (Template embeddet noch `DATA_B64`).

- [ ] **Step 3: Write minimal implementation**

**3a — `renderer.render_export_html()` ersetzen (ab `index: list[dict] = []` bis `data_obj = Markup(...)`):**

```python
    index, chunks = _split_export_data(videos)

    index_b64 = None
    chunks_b64: list[str] = []
    data_obj = None
    if compress:
        index_b64 = _gzip_b64(json.dumps(index, ensure_ascii=False))
        chunks_b64 = [_gzip_b64(json.dumps(c, ensure_ascii=False)) for c in chunks]
    else:
        # Old browsers without DecompressionStream: one plain object literal,
        # no chunking. Escape </ so it cannot break out of the <script> tag.
        summaries = {vid: html for chunk in chunks for vid, html in chunk.items()}
        raw = json.dumps({"index": index, "summaries": summaries}, ensure_ascii=False)
        data_obj = Markup(raw.replace("</", "<\\/"))
```

Dazu oberhalb (neben den anderen Hilfsfunktionen):

```python
def _gzip_b64(raw: str) -> str:
    return base64.b64encode(gzip.compress(raw.encode("utf-8"))).decode("ascii")
```

Und im `template.render(...)`-Aufruf `data_b64=data_b64` ersetzen durch:

```python
        index_b64=index_b64,
        chunks_b64=chunks_b64,
        chunk_size=EXPORT_CHUNK_SIZE,
```

**3b — `export.html.j2` Datenzeile (`:471`) ersetzen:**

```jinja
{% if compressed %}const INDEX_B64 = "{{ index_b64 }}";
const SUM_B64 = [{% for c in chunks_b64 %}"{{ c }}"{% if not loop.last %},{% endif %}{% endfor %}];
{% else %}const DATA_OBJ = {{ data_obj }};
const SUM_B64 = [];{% endif %}
const CHUNK_SIZE = {{ chunk_size }};
const CHUNKS = [];
const CHUNK_PROMISES = [];
```

**3c — `loadData()`/`bootstrap()` (`:1497-1531`) ersetzen durch:**

```javascript
// ── Data loading (index first, summary chunks on demand) ──────────────────────
async function gunzipB64(b64) {
  var bin = atob(b64);
  var bytes = new Uint8Array(bin.length);
  for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  if (typeof DecompressionStream === 'undefined') {
    throw new Error('DecompressionStream unsupported');
  }
  var stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
  return JSON.parse(await new Response(stream).text());
}

function ensureChunk(k) {
  if (CHUNKS[k]) return Promise.resolve(CHUNKS[k]);
  if (!CHUNK_PROMISES[k]) {
    CHUNK_PROMISES[k] = gunzipB64(SUM_B64[k]).then(function (map) {
      CHUNKS[k] = map;
      return map;
    });
  }
  return CHUNK_PROMISES[k];
}

function chunksReady(list) {
  return list.every(function (v) { return !!CHUNKS[v._c]; });
}

function ensureChunks(list) {
  var wanted = {};
  list.forEach(function (v) { wanted[v._c] = true; });
  return Promise.all(Object.keys(wanted).map(function (k) { return ensureChunk(+k); }));
}

// Summary HTML lives in the chunk that covers this video's index position.
function getSummary(v) {
  var map = CHUNKS[v._c];
  return map ? map[v.video_id] : undefined;
}

async function bootstrap() {
{% if compressed %}
  try {
    VIDEOS = await gunzipB64(INDEX_B64);
  } catch (e) {
    document.getElementById('grid').innerHTML =
      '<p class="no-videos">This archive needs a modern browser '
      + '(DecompressionStream support). Re-export with --no-compress for older browsers.</p>';
    return;
  }
  VIDEOS.forEach(function (v, i) { v._c = (i / CHUNK_SIZE) | 0; });
  await ensureChunk(0);   // covers the first page
{% else %}
  VIDEOS = DATA_OBJ.index || [];
  VIDEOS.forEach(function (v) { v._c = 0; });
  CHUNKS[0] = DATA_OBJ.summaries || {};
{% endif %}
  populateChannelFilter();
  populateTagFilter();
  applyLang(detectLang());
}

// Let the pre-rendered first page paint before touching the data blobs.
requestAnimationFrame(function () { bootstrap(); });
```

Die alte Zeile `bootstrap();` (`:1531`) entfällt — der `requestAnimationFrame`-Aufruf ersetzt sie.

**3d — Summary-Zugriffe umstellen:**

`export.html.j2:1219` in `buildCard()`:
```javascript
  const summary = getSummary(v);
```

`export.html.j2:1285` in `getSearchText()`:
```javascript
  const html = getSummary(v) || '';
```

**3e — `renderPage()` (`:1362`) am Anfang ergänzen, direkt nach `var s = I18N[currentLang];`:**

```javascript
  // Never render a card whose summary chunk is missing — it would look like a
  // video without a transcript. Keep the current DOM and re-render when ready.
  var wantedPage = Math.min(
    Math.max(1, page),
    Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  );
  var pendingStart = (wantedPage - 1) * PAGE_SIZE;
  var pending = filtered.slice(pendingStart, pendingStart + PAGE_SIZE);
  if (pending.length && !chunksReady(pending)) {
    document.getElementById('results-count').textContent = s.loadingData;
    ensureChunks(pending).then(function () { renderPage(page, skipScroll); });
    return;
  }
```

**3f — i18n-Key `loadingData` in beiden Sprachdicts in `export.html.j2` ergänzen** (neben `results:`):

```javascript
    loadingData: 'Daten werden geladen…',
```
```javascript
    loadingData: 'Loading data…',
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
/tmp/ytenv/bin/python -m pytest tests/test_export_chunks.py -q
/tmp/ytenv/bin/python -m pytest tests/test_renderer_sanitize.py tests/test_renderer_autoescape.py -q
```
Expected: PASS

Zusätzlich manuell gegen den echten Store prüfen:

```bash
/tmp/ytenv/bin/python export.py --all --output /tmp/chunked.html && ls -la /tmp/chunked.html
```

- [ ] **Step 5: Commit**

```bash
git add renderer.py export.html.j2 tests/export_harness.py tests/dom_stub.js tests/test_export_chunks.py
git commit -m "perf(export): load summaries as on-demand chunks instead of one blob"
```

---

## Task 3: Vorgerenderte erste Seite

**Files:**
- Modify: `renderer.py` (`render_export_html`: Kontext um `first_page`, `t`, `esch`-Filter)
- Modify: `i18n.py` (neuer Key `show_more`)
- Modify: `export.html.j2` (Kartenmakro, `#grid`-Inhalt, Sprachwechsel-Rebuild)
- Test: `tests/test_export_prerender.py` (neu)

**Interfaces:**
- Consumes: `renderer._split_export_data()`, `_summary_preview()`, `_esc_html()`, `EXPORT_FIRST_PAGE` (Task 1); `getSummary()` (Task 2)
- Produces:
  - Template-Kontext `first_page: list[dict]` — Index-Einträge der ersten 20 Videos, jeweils zusätzlich `summary_preview: Markup`, `summary_rest: Markup` (leer, wenn kein zweiter Teil)
  - Jinja-Filter `esch` (= `_esc_html`)
  - Jinja-Makro `card(v)` in `export.html.j2`

- [ ] **Step 1: Write the failing test**

`tests/test_export_prerender.py` (neu):

```python
"""The pre-rendered first page must match what buildCard() produces."""
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from export_harness import extract_script, node_available, render_export, run_node, video


def _normalize(html: str) -> str:
    """Collapse whitespace between tags so formatting differences do not matter."""
    return re.sub(r">\s+<", "><", html).strip()


def test_first_page_cards_are_in_the_html_before_the_data_blob():
    videos = [video("v%03d" % i, "2026-01-01T00:%02d:00Z" % i) for i in range(30)]
    html = render_export(videos)
    first_card = html.index('data-video-id="v029"')
    blob = html.index("const INDEX_B64")
    assert first_card < blob, "cards must precede the data blob so they paint first"
    # exactly one page of cards is pre-rendered
    assert html.count('class="video-card') == 20
    assert 'data-video-id="v010"' in html      # 20th newest
    assert 'data-video-id="v009"' not in html  # 21st is not pre-rendered


def test_pre_rendered_card_shows_summary_preview_and_toggle():
    html = render_export([video("v1", "2026-01-01T00:00:00Z")])
    assert "Summary of v1" in html
    assert "summary-details" in html


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_pre_rendered_markup_matches_buildCard_output():
    videos = [
        video("v1", "2026-03-01T00:00:00Z"),
        video("v2", "2026-02-01T00:00:00Z", tags=[], duration="", summary=None,
              transcript_error="country_blocked"),
        video("v3", "2026-01-01T00:00:00Z", title='Quote " & <tag>'),
    ]
    html = render_export(videos)
    script = extract_script(html)
    js_cards = run_node(script, """
      bootstrap().then(function () {
        console.log(JSON.stringify(VIDEOS.map(buildCard)));
      });
    """)
    built = json.loads(js_cards.strip().splitlines()[-1])

    grid = re.search(r'<div id="grid">(.*?)</div>\s*<!-- /grid -->', html, re.S)
    assert grid, "grid marker not found"
    pre_rendered = re.findall(r"<article .*?</article>", grid.group(1), re.S)
    assert len(pre_rendered) == len(built) == 3

    for pre, js in zip(pre_rendered, built):
        assert _normalize(pre) == _normalize(js)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/tmp/ytenv/bin/python -m pytest tests/test_export_prerender.py -q`
Expected: FAIL — `html.count('class="video-card')` ist 0, das Grid ist leer.

- [ ] **Step 3: Write minimal implementation**

**3a — `i18n.py`: in beide Sprachdicts aufnehmen**

```python
        "show_more": "▼ mehr",
```
```python
        "show_more": "▼ more",
```

**3b — `renderer.render_export_html()`: nach dem Aufteilen ergänzen**

```python
    # Pre-render the first page so the browser can paint before any blob is
    # decoded. Chunk 0 covers it because EXPORT_CHUNK_SIZE >= EXPORT_FIRST_PAGE.
    first_chunk = chunks[0] if chunks else {}
    first_page = []
    for entry in index[:EXPORT_FIRST_PAGE]:
        summary = first_chunk.get(entry["video_id"], "")
        preview, rest = _summary_preview(summary) if summary else ("", "")
        item = dict(entry)
        item["summary_preview"] = Markup(preview)
        item["summary_rest"] = Markup(rest)
        first_page.append(item)
```

Environment und Render-Aufruf:

```python
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
    env.filters["esch"] = lambda s: Markup(_esc_html(s))
```

und im `template.render(...)`:

```python
        first_page=first_page,
        t=i18n_module.get_strings(lang),
```

**3c — `export.html.j2`: Makro ganz oben in der Datei (vor `<!DOCTYPE`-Inhalt ist kein Platz, daher direkt nach der ersten Zeile des `<body>`-Blocks als `{% macro %}` definieren, gerendert wird es nur im Grid).**

Makro (Markup exakt wie `buildCard()`, deshalb ohne Zeilenumbrüche innerhalb der Tags):

```jinja
{%- macro card(v) -%}
<article class="video-card" data-video-id="{{ v.video_id|esch }}">
<div class="video-thumb">
{%- if show_embed -%}
<div class="video-embed-facade" onclick="loadEmbed(this, '{{ v.video_id|esch }}')">
{%- if v.thumbnail_url %}<img loading="lazy" src="{{ v.thumbnail_url|esch }}" alt="{{ v.title|esch }}">{% endif -%}
<button class="facade-play" aria-label="Play">&#9654;</button></div>
{%- else -%}
{%- if v.thumbnail_url %}<img src="{{ v.thumbnail_url|esch }}" alt="{{ v.title|esch }}">{% else %}<div class="no-thumb">{{ t.no_thumb }}</div>{% endif -%}
{%- endif -%}
</div>
<div class="video-body">
<div class="video-actions">
<button class="read-btn" onclick="toggleRead('{{ v.video_id|esch }}')">{{ t.read_btn }}</button>
<button class="bookmark-btn" onclick="toggleBookmark('{{ v.video_id|esch }}')">{{ t.bookmark_btn }}</button>
</div>
<a class="video-title" href="https://www.youtube.com/watch?v={{ v.video_id|esch }}" target="_blank" rel="noopener">{{ v.title|esch }}</a>
<div class="video-meta"><span class="channel-link" onclick="setChannelFilter('{{ v.channel_id|esch }}')">{{ v.channel_title|esch }}</span> &middot; {{ v.published_at_display|esch }}{% if v.duration %} &middot; {{ v.duration|esch }}{% endif %}{% if v.summary_model %}<span class="model-badge">{{ v.summary_model|esch }}</span>{% endif %}</div>
{%- if v.tags %}<div class="video-tags">{% for tag in v.tags %}<span class="tag-chip" onclick="setTagFilter('{{ tag|esch }}')">{{ tag|esch }}</span>{% endfor %}</div>{% endif -%}
<div class="video-summary">
{%- if v.summary_preview -%}
{{ v.summary_preview }}{% if v.summary_rest %}<button class="summary-toggle" onclick="toggleSummary(this)">{{ t.show_more }}</button><div class="summary-details" hidden>{{ v.summary_rest }}</div>{% endif %}
{%- elif v.transcript_error == 'ip_blocked' -%}
<p class="no-transcript ip-blocked">{{ t.transcript_ip_blocked }}</p>
{%- elif v.transcript_error == 'rate_limited' -%}
<p class="no-transcript rate-limited">{{ t.transcript_rate_limited }}</p>
{%- elif v.transcript_error == 'country_blocked' -%}
<p class="no-transcript country-blocked">{{ t.transcript_country_blocked }}</p>
{%- else -%}
<p class="no-transcript unavailable">{{ t.transcript_unavailable }}</p>
{%- endif -%}
</div>
</div>
</article>
{%- endmacro -%}
```

Hinweis für den Implementierer: Das Makro muss zeichengleiches Markup zu `buildCard()` liefern (Klassenreihenfolge, `&middot;`-Trenner, Attributreihenfolge). Der Test in Step 1 vergleicht normalisiert; bei Abweichung zeigt `pytest -vv` das Diff — dann `buildCard()` als Referenz nehmen und das Makro angleichen, **nicht** umgekehrt.

**3d — `export.html.j2`: Grid füllen (`:462`):**

```jinja
<div id="grid">{% for v in first_page %}{{ card(v) }}{% endfor %}</div>
<!-- /grid -->
```

**3e — `bootstrap()` um den Sprachfall ergänzen** (vor `applyLang(detectLang())`):

```javascript
  // The pre-rendered cards carry the export's default language. If the visitor
  // resolves to a different one, drop them so applyLang() rebuilds from scratch.
  var lang = detectLang();
  if (lang !== EMBEDDED_DEFAULT) document.getElementById('grid').innerHTML = '';
  applyLang(lang);
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
/tmp/ytenv/bin/python -m pytest tests/test_export_prerender.py tests/test_export_chunks.py -q
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add renderer.py i18n.py export.html.j2 tests/test_export_prerender.py
git commit -m "perf(export): pre-render the first page of cards into the shell"
```

---

## Task 4: Read/Bookmark-Zustand ohne Flackern

**Files:**
- Modify: `export.html.j2` (Inline-Script direkt nach `<!-- /grid -->`)
- Test: `tests/test_export_prerender.py` (erweitern)

**Interfaces:**
- Consumes: vorgerenderte Karten mit `data-video-id` (Task 3)
- Produces: nichts, was spätere Tasks konsumieren — das Script arbeitet eigenständig auf dem DOM.

- [ ] **Step 1: Write the failing test**

An `tests/test_export_prerender.py` anhängen:

```python
def test_hydration_script_runs_before_the_data_blob():
    html = render_export([video("v1", "2026-01-01T00:00:00Z")])
    hydrate = html.index("yt_read")
    blob = html.index("const INDEX_B64")
    assert hydrate < blob, "read/bookmark hydration must run before the data blob"
    assert "is-read" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/tmp/ytenv/bin/python -m pytest tests/test_export_prerender.py::test_hydration_script_runs_before_the_data_blob -q`
Expected: FAIL — `ValueError: substring not found` für `yt_read`.

- [ ] **Step 3: Write minimal implementation**

In `export.html.j2` direkt nach `<!-- /grid -->` einfügen:

```html
<script>
// Paint the pre-rendered cards with the stored read/bookmark state right away;
// waiting for the main script would show every card as unread for a moment.
(function () {
  function ids(key) {
    try { return new Set(JSON.parse(localStorage.getItem(key) || '[]')); }
    catch (e) { return new Set(); }
  }
  var read = ids('yt_read');
  var marked = ids('yt_bookmark');
  if (!read.size && !marked.size) return;
  var cards = document.querySelectorAll('#grid .video-card');
  for (var i = 0; i < cards.length; i++) {
    var id = cards[i].getAttribute('data-video-id');
    if (read.has(id)) {
      cards[i].classList.add('is-read');
      var rb = cards[i].querySelector('.read-btn');
      if (rb) rb.classList.add('is-active');
    }
    if (marked.has(id)) {
      cards[i].classList.add('is-bookmarked');
      var bb = cards[i].querySelector('.bookmark-btn');
      if (bb) bb.classList.add('is-active');
    }
  }
})();
</script>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/tmp/ytenv/bin/python -m pytest tests/test_export_prerender.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add export.html.j2 tests/test_export_prerender.py
git commit -m "perf(export): hydrate read/bookmark state on the pre-rendered cards"
```

---

## Task 5: Sync-Requests früh und parallel starten

**Files:**
- Modify: `export.html.j2` — neues Inline-Script nach der Hydration, `SYNC_URL`-Deklaration (`:479`), `initSync()` (`:803-864`), Aufrufstelle (`:1531-1535`)
- Test: `tests/test_export_sync_boot.py` (neu)

**Interfaces:**
- Consumes: nichts aus früheren Tasks
- Produces:
  - Globals `SYNC_URL` (jetzt im frühen Script deklariert) und `window.__syncBoot = {whoami: Promise, state: Promise} | undefined`
  - JS: `startSyncRequests() -> {whoami, state}|null`, `initSync()` (konsumiert `window.__syncBoot`)

- [ ] **Step 1: Write the failing test**

`tests/test_export_sync_boot.py` (neu):

```python
"""The sync requests must start before the data blob is parsed."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from export_harness import render_export, video

SYNC = "https://sync.example.com"


def test_sync_requests_are_fired_before_the_data_blob():
    html = render_export([video("v1", "2026-01-01T00:00:00Z")], sync_url=SYNC)
    boot = html.index("__syncBoot")
    blob = html.index("const INDEX_B64")
    assert boot < blob


def test_whoami_and_state_are_requested_in_parallel():
    html = render_export([video("v1", "2026-01-01T00:00:00Z")], sync_url=SYNC)
    early = html[: html.index("const INDEX_B64")]
    assert "/api/whoami" in early
    assert "/api/state" in early


def test_sync_url_is_declared_exactly_once():
    html = render_export([video("v1", "2026-01-01T00:00:00Z")], sync_url=SYNC)
    assert html.count("const SYNC_URL") == 1


def test_no_sync_boot_without_sync_url():
    html = render_export([video("v1", "2026-01-01T00:00:00Z")])
    assert "__syncBoot" not in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/tmp/ytenv/bin/python -m pytest tests/test_export_sync_boot.py -q`
Expected: FAIL — `ValueError: substring not found` für `__syncBoot`.

- [ ] **Step 3: Write minimal implementation**

**3a — Frühes Script in `export.html.j2`, direkt nach dem Hydration-Script aus Task 4:**

```html
{% if sync_url %}
<script>
// Fire the sync requests before the data blobs are parsed, so the round trip
// overlaps decoding. Both endpoints only need the token, so they run in
// parallel instead of whoami-then-state. initSync() consumes the promises once
// the index is ready — nothing touches the DOM from here.
const SYNC_URL = "{{ sync_url }}";
(function () {
  var m = window.location.hash.match(/[#&]session=([^&]+)/);
  if (m) {
    try { localStorage.setItem('yt_sync_token', decodeURIComponent(m[1])); } catch (e) {}
    try {
      history.replaceState(null, '', window.location.pathname + window.location.search);
    } catch (e) {}  // file:// may throw SecurityError
  }
  var token = '';
  try { token = localStorage.getItem('yt_sync_token') || ''; } catch (e) {}
  if (!token) return;
  var opts = {headers: {'Authorization': 'Bearer ' + token}};
  window.__syncBoot = {
    whoami: fetch(SYNC_URL + '/api/whoami', opts).catch(function () { return null; }),
    state: fetch(SYNC_URL + '/api/state', opts).catch(function () { return null; })
  };
})();
</script>
{% endif %}
```

**3b — `export.html.j2:478-480` löschen** (die zweite `const SYNC_URL`-Deklaration im Hauptscript; sie würde als erneute lexikalische Deklaration im selben globalen Scope einen `SyntaxError` auslösen):

```jinja
{% if sync_url %}
const SYNC_URL = "{{ sync_url }}";
{% endif %}
```

**3c — `initSync()` (`:803-864`) ersetzen:**

```javascript
// Re-issue both sync requests (used after 'online' events; the initial pair is
// fired by the early inline script).
function startSyncRequests() {
  var token = getSyncToken();
  if (!token) return null;
  var opts = {headers: {'Authorization': 'Bearer ' + token}};
  return {
    whoami: fetch(SYNC_URL + '/api/whoami', opts).catch(function () { return null; }),
    state: fetch(SYNC_URL + '/api/state', opts).catch(function () { return null; })
  };
}

function initSync() {
  var token = getSyncToken();
  if (!token) {
    showSyncLoggedOut();
    return;
  }
  if (!window.__syncBoot) window.__syncBoot = startSyncRequests();
  if (!window.__syncBoot) {
    showSyncLoggedOut();
    return;
  }
  var boot = window.__syncBoot;
  var s = I18N[currentLang || 'de'];
  updateSyncStatus(s.syncStatusSyncing);

  boot.whoami
    .then(function (r) {
      if (!r) throw new Error('offline');
      if (r.status === 401) {
        localStorage.removeItem('yt_sync_token');
        showSyncLoggedOut();
        return null;
      }
      return r.json();
    })
    .then(function (data) {
      if (!data) return;
      showSyncLoggedIn(data.email);
      if (data.can_ingest) showIngestUI();
      return boot.state.then(function (r) {
        if (!r) throw new Error('offline');
        if (r.status === 401) {
          localStorage.removeItem('yt_sync_token');
          showSyncLoggedOut();
          return null;
        }
        return r.json();
      });
    })
    .then(function (serverData) {
      if (!serverData) return;
      applyServerState(serverData);
      applyFiltersAndSort(currentPage);
      updateSyncStatus(
        I18N[currentLang || 'de'].syncStatusSynced +
        ' — ' + new Date().toLocaleTimeString()
      );
    })
    .catch(function () {
      updateSyncStatus(I18N[currentLang || 'de'].syncStatusFailed);
    });
}
```

**3d — Aufrufstellen (`:1531-1535`) anpassen:** `initSync()` wandert ans Ende von `bootstrap()` (nach `applyLang(lang)`), damit `applyServerState()` niemals auf ein leeres `VIDEOS` trifft. Am Dateiende bleibt:

```javascript
{% if sync_url %}
setTimeout(warnIfInsecure, 0);
window.addEventListener('online', function () {
  if (getSyncToken()) { window.__syncBoot = startSyncRequests(); initSync(); }
});
document.addEventListener('visibilitychange', function () {
  if (document.visibilityState === 'visible') pullServerState();
});
window.addEventListener('focus', pullServerState);
setInterval(pullServerState, 5 * 60 * 1000);
{% endif %}
```

In `bootstrap()` ans Ende:

```javascript
{% if sync_url %}
  initSync();
{% endif %}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
/tmp/ytenv/bin/python -m pytest tests/test_export_sync_boot.py tests/test_export_prerender.py tests/test_export_chunks.py -q
```
Expected: PASS

Zusätzlich JS-Syntax beider Varianten prüfen:

```bash
/tmp/ytenv/bin/python - <<'EOF'
import sys, os, subprocess, tempfile
sys.path.insert(0, 'tests'); sys.path.insert(0, '.')
from export_harness import render_export, extract_script, video
for sync in (None, "https://sync.example.com"):
    for comp in (True, False):
        html = render_export([video("v1", "2026-01-01T00:00:00Z")], sync_url=sync, compress=comp)
        js = extract_script(html)
        p = tempfile.mktemp(suffix=".js")
        open(p, "w").write(js)
        r = subprocess.run(["node", "--check", p], capture_output=True, text=True)
        print("sync=%s compress=%s -> %s" % (bool(sync), comp, "OK" if r.returncode == 0 else r.stderr[:200]))
EOF
```
Expected: viermal `OK`

- [ ] **Step 5: Commit**

```bash
git add export.html.j2 tests/test_export_sync_boot.py
git commit -m "perf(export): start sync requests before decoding the data blobs"
```

---

## Task 6: Suche über alle Chunks, Idle-Prefetch

**Files:**
- Modify: `export.html.j2` — `applyFiltersAndSort()` (`:1291`), neue `ensureAllChunks()`/`prefetchChunks()`, `bootstrap()`
- Test: `tests/test_export_chunks.py` (erweitern)

**Interfaces:**
- Consumes: `ensureChunk()`, `chunksReady()`, `getSummary()` (Task 2)
- Produces: `ensureAllChunks() -> Promise<void>`, `allChunksReady() -> bool`, `prefetchChunks()`

- [ ] **Step 1: Write the failing test**

An `tests/test_export_chunks.py` anhängen:

```python
@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_search_waits_for_every_chunk_then_matches_summary_text():
    videos = [_video("v%03d" % i, "2026-01-01T00:%02d:00Z" % i) for i in range(120)]
    videos[0]["summary"] = "<p>needle in the oldest video</p>"
    html = render_export(videos)
    script = extract_script(html)
    out = run_node(script, """
      bootstrap().then(function () {
        document.getElementById('search').value = 'needle';
        applyFiltersAndSort();
        return ensureAllChunks().then(function () {
          applyFiltersAndSort();
          console.log(JSON.stringify({
            decoded: CHUNKS.filter(Boolean).length,
            hits: filtered.map(function (v) { return v.video_id; }),
          }));
        });
      });
    """)
    data = json.loads(out.strip().splitlines()[-1])
    assert data["decoded"] == 3       # every chunk decoded for the search
    assert data["hits"] == ["v000"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/tmp/ytenv/bin/python -m pytest tests/test_export_chunks.py -q -k search`
Expected: FAIL — `node failed: ReferenceError: ensureAllChunks is not defined`

- [ ] **Step 3: Write minimal implementation**

**3a — Nach `ensureChunks()` in `export.html.j2` ergänzen:**

```javascript
function allChunksReady() {
  for (var k = 0; k < SUM_B64.length; k++) if (!CHUNKS[k]) return false;
  return true;
}

function ensureAllChunks() {
  var all = [];
  for (var k = 0; k < SUM_B64.length; k++) all.push(ensureChunk(k));
  return Promise.all(all);
}

// Pull the remaining chunks in while the browser is idle, so a search later on
// usually finds everything already decoded.
function prefetchChunks() {
  var idle = window.requestIdleCallback || function (cb) { return setTimeout(cb, 50); };
  var next = 0;
  function step() {
    while (next < SUM_B64.length && CHUNKS[next]) next++;
    if (next >= SUM_B64.length) return;
    ensureChunk(next).then(function () { idle(step); });
  }
  idle(step);
}
```

**3b — `applyFiltersAndSort()` am Anfang ergänzen, direkt nach `const q = ...`:**

```javascript
  // A text search reads summary text, which lives in the chunks. Matching on a
  // partial set would silently drop hits, so wait for all of them instead.
  if (q && !allChunksReady()) {
    document.getElementById('results-count').textContent = I18N[currentLang].loadingData;
    ensureAllChunks().then(function () { applyFiltersAndSort(page); });
    return;
  }
```

**3c — In `bootstrap()` ganz am Ende ergänzen (nach `initSync()`):**

```javascript
  prefetchChunks();
```

Hinweis: `test_browser_decodes_index_and_only_the_needed_chunk` aus Task 2 erwartet
weiterhin `decoded == 1`. Das bleibt korrekt, weil `prefetchChunks()` über
`requestIdleCallback`/`setTimeout` läuft und damit erst nach der Microtask-Kette
feuert, in der der Test seine Ausgabe schreibt. Schlägt der Test nach dieser
Änderung trotzdem fehl, ist der Prefetch versehentlich synchron — dann den
Prefetch reparieren, nicht die Erwartung aufweichen.

- [ ] **Step 4: Run tests to verify they pass**

```bash
/tmp/ytenv/bin/python -m pytest tests/test_export_chunks.py -q
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add export.html.j2 tests/test_export_chunks.py
git commit -m "feat(export): search across all summary chunks with idle prefetch"
```

---

## Task 7: Benchmark, manuelle Prüfung, Dokumentation

**Files:**
- Create: `/tmp/bench_export.py` (Wegwerf-Skript, nicht committen)
- Modify: `README.md` (Abschnitt "Export archive"), `CLAUDE.md` (Zeile zu `export.py`/`export.html.j2`/`renderer.py`), `AGENTS.md` (Abschnitt "Renderer / templates")

**Interfaces:**
- Consumes: alles aus Task 1-6
- Produces: nichts

- [ ] **Step 1: Synthetisches Archiv bauen und messen**

`/tmp/bench_export.py`:

```python
"""Render a synthetic large archive to compare load behaviour."""
import sys, time
sys.path.insert(0, '.')
import renderer

N = 3000
videos = [{
    "video_id": "vid%06d" % i,
    "channel_id": "UC%03d" % (i % 40),
    "channel_title": "Channel %03d" % (i % 40),
    "title": "Video number %d about a topic" % i,
    "published_at": "2026-%02d-%02dT%02d:00:00Z" % (i % 12 + 1, i % 28 + 1, i % 24),
    "published_at_display": "January 01, 2026",
    "duration": "12:34",
    "thumbnail_url": "https://i.ytimg.com/vi/vid%06d/hq.jpg" % i,
    "summary": "<p>Intro paragraph for video %d.</p>" % i + "<p>Body sentence. </p>" * 40,
    "summary_model": None,
    "transcript_error": None,
    "tags": ["Tag A", "Tag B", "Tag %d" % (i % 15)],
} for i in range(N)]

t0 = time.time()
renderer.render_export_html(videos, "/tmp/bench_new.html")
print("rendered in %.1fs" % (time.time() - t0))
import os
print("size: %.1f MB" % (os.path.getsize("/tmp/bench_new.html") / 1e6))
```

Run:
```bash
/tmp/ytenv/bin/python /tmp/bench_export.py
```

- [ ] **Step 2: Im Browser vergleichen**

Alte Version zum Vergleich erzeugen:

```bash
git stash && /tmp/ytenv/bin/python /tmp/bench_export.py && mv /tmp/bench_new.html /tmp/bench_old.html && git stash pop
/tmp/ytenv/bin/python /tmp/bench_export.py
python3 -m http.server 8765 --directory /tmp &
```

Beide Dateien in einem Browser mit gedrosselter CPU (DevTools, 4x slowdown) laden und die Zeit bis zur ersten sichtbaren Karte notieren. Erwartung: die neue Datei zeigt Karten, bevor die alte überhaupt etwas anzeigt. Server danach beenden.

- [ ] **Step 3: Funktionsprüfung von Hand**

An der neuen Datei durchgehen und abhaken:
- Filter Kanal / Tag / Gelesen / Merken / Datum
- Sortierung: alle vier Optionen
- Pagination vor/zurück, Sprung auf letzte Seite (lädt spätere Chunks nach)
- Suche nach einem Wort, das nur in einem späten Video vorkommt
- Read/Bookmark umschalten, Seite neu laden — Zustand bleibt, kein Flackern
- Sprachumschaltung de/en
- Mit `--sync-url` gegen den echten Sync-Server: Login, State-Sync, Ingest-Button
- `--no-compress`-Variante lädt und funktioniert
- `--thumbnail`-Variante zeigt statische Bilder

- [ ] **Step 4: Dokumentation aktualisieren**

`README.md`, Abschnitt "Export archive": beschreiben, dass die erste Seite vorgerendert ausgeliefert wird, dass Metadaten und Summaries in getrennten Blobs stecken und Summaries chunkweise (50 Videos) nachgeladen werden, dass die Suche auf alle Chunks wartet und `--no-compress` weiterhin ein einzelnes Objektliteral einbettet.

`CLAUDE.md`: die Zeilen zu `renderer.py`, `export.html.j2` und der Absatz über die eingebetteten Daten im Abschnitt "Export archive" entsprechend anpassen.

`AGENTS.md`, Abschnitt "Renderer / templates": Stichpunkte ergänzen — `_split_export_data()` als Quelle der Sortier- und Chunkreihenfolge, das Jinja-Makro `card()` muss zu `buildCard()` passen (Test `tests/test_export_prerender.py`), `getSummary()` statt direktem `SUMMARIES`-Zugriff.

- [ ] **Step 5: Volle Testsuite und Commit**

```bash
/tmp/ytenv/bin/python -m pytest tests -q --ignore=tests/test_openrouter_prompt.py 2>&1 | tail -5
```
Expected: dieselben 9 vorbestehenden Fehlschläge in `tests/test_transcripts.py`, sonst alles grün.

```bash
git add README.md CLAUDE.md AGENTS.md
git commit -m "docs(export): describe the chunked loading and pre-rendered first page"
```

---

## Nachtrag: bekannte Grenzen

- Die vorgerenderte Seite trägt die Export-Default-Sprache; abweichende Browsersprachen sehen kurz die Default-Sprache, bis `applyLang()` neu baut.
- Das Jinja-Makro und `buildCard()` sind zwei Implementierungen desselben Markups. `tests/test_export_prerender.py` hält sie zusammen — bei Änderungen an einer Seite immer beide anfassen.
- Chunking gilt nur im komprimierten Pfad; `--no-compress` lädt weiterhin alles auf einmal.
