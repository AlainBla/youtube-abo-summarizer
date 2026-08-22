# EPUB-Ebook-Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein `ebook.py`-CLI, das gespeicherte Video-Zusammenfassungen als EPUB-3-Datei ausgibt — nach Kalenderwoche gegliedert, optional getrennt nach ungelesen/gelesen anhand des Sync-Server-Zustands eines Nutzers.

**Architecture:** `ebook.py` wählt Videos aus dem Store (dieselben Flags wie `export.py`, plus `--tag`/`--limit`) und lädt den Lesestatus lesend aus `sync-server/sync.db`. `epub_builder.py` gruppiert nach ISO-Woche, rendert XHTML über Jinja2-Templates in `ebook/` und packt alles mit `zipfile` zu einem EPUB 3. Keine neue Laufzeit-Abhängigkeit: EPUB ist ZIP + XHTML.

**Tech Stack:** Python 3.13, stdlib (`zipfile`, `sqlite3`, `urllib.request`, `xml.etree.ElementTree`, `datetime`), Jinja2, nh3 (bereits im Projekt), pytest.

**Spec:** `docs/plans/2026-08-22-ebook-export-design.md`

## Global Constraints

- **Keine neuen Laufzeit-Abhängigkeiten.** `requirements.txt` bleibt unverändert; alles läuft mit stdlib + Jinja2 + nh3.
- **Testlauf** (venv-Mismatch, siehe README): `env TMPDIR=$HOME/.cache/yt-tmp PYTHONPATH=$PWD/.venv/lib/python3.13/site-packages python -m pytest tests/ -q`
- **Kein Netzzugriff in Tests.** Der Thumbnail-Fetcher wird immer injiziert/gemockt.
- **Sprachen:** `de` (Default) und `en`, Strings ausschließlich über `i18n.py`. Keine Emojis.
- **Commits:** Conventional Commits, Footer `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **Default-Umfang:** `--limit 100`; `0` bedeutet unbegrenzt.
- **XHTML:** Jede erzeugte Datei muss als XML parsen — Reader lehnen ein Buch sonst komplett ab.

---

### Task 1: Store ohne Transkript-Ladung auslesen

`store.get_all_videos()` liest heute für **jedes** der ~4.900 Videos die Transkriptdatei von der Platte. Für ein Buch mit 100 Einträgen ist das reine Verschwendung; die Auswahl braucht nur Metadaten und Zusammenfassung.

**Files:**
- Modify: `store.py` (`get_all_videos`, `get_videos_since`)
- Test: `tests/test_store_metadata_only.py`

**Interfaces:**
- Consumes: nichts
- Produces: `store.get_all_videos(with_transcripts: bool = True) -> list[dict]`, `store.get_videos_since(since: datetime, with_transcripts: bool = True) -> list[dict]` — bei `False` ist `entry["transcript"]` immer `None`, alle anderen Felder unverändert.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store_metadata_only.py
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import store


def test_metadata_only_listing_skips_transcript_files(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "videos.db")
    monkeypatch.setattr(store, "TRANSCRIPTS_DIR", tmp_path / "transcripts")
    monkeypatch.setattr(store, "SUMMARIES_DIR", tmp_path / "summaries")
    store.init_db()
    store.add_video({
        "video_id": "v1", "channel_id": "UC1", "channel_title": "Chan",
        "title": "T", "published_at": "2026-01-01T00:00:00Z",
        "thumbnail_url": "https://i.ytimg.com/vi/v1/hq.jpg",
        "transcript": "hello transcript", "summary": "<p>sum</p>", "tags": [],
    })

    full = store.get_all_videos()
    assert full[0]["transcript"] == "hello transcript"

    lean = store.get_all_videos(with_transcripts=False)
    assert lean[0]["transcript"] is None
    assert lean[0]["summary"] == "<p>sum</p>"        # summary is still needed
    assert lean[0]["title"] == "T"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env TMPDIR=$HOME/.cache/yt-tmp PYTHONPATH=$PWD/.venv/lib/python3.13/site-packages python -m pytest tests/test_store_metadata_only.py -v`
Expected: FAIL — `get_all_videos() got an unexpected keyword argument 'with_transcripts'`

- [ ] **Step 3: Write minimal implementation**

In `store.py`, beide Reader um den Schalter erweitern (identisches Muster, `get_videos_since` analog):

```python
def get_all_videos(with_transcripts: bool = True) -> list[dict]:
    """Return all stored videos, newest first.

    with_transcripts=False skips reading the transcript files from disk --
    the caller only wants metadata and summaries, and reading thousands of
    transcripts for that is pure waste.
    """
    rows = _conn().execute("SELECT * FROM videos ORDER BY published_at DESC").fetchall()

    result = []
    for row in rows:
        d = dict(row)
        if with_transcripts:
            t_path = _resolve_transcript_path(d["video_id"], d.get("transcript_lang"))
            d["transcript"] = t_path.read_text(encoding="utf-8") if t_path else None
        else:
            d["transcript"] = None
        s_path = SUMMARIES_DIR / f"{d['video_id']}.html"
        d["summary"] = s_path.read_text(encoding="utf-8") if s_path.exists() else None
        d["tags"] = json.loads(d["tags"]) if d.get("tags") else []
        result.append(d)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env TMPDIR=$HOME/.cache/yt-tmp PYTHONPATH=$PWD/.venv/lib/python3.13/site-packages python -m pytest tests/test_store_metadata_only.py tests/test_store_transcript_lang.py -v`
Expected: PASS (der bestehende Store-Test darf sich nicht verändern — Default bleibt `True`)

- [ ] **Step 5: Commit**

```bash
git add store.py tests/test_store_metadata_only.py
git commit -m "perf(store): allow listing videos without reading transcripts"
```

---

### Task 2: Auswahl und CLI-Argumente

**Files:**
- Create: `ebook.py`
- Test: `tests/test_ebook_selection.py`

**Interfaces:**
- Consumes: `store.get_all_videos(with_transcripts=False)`, `store.get_videos_since(...)`
- Produces:
  - `ebook.select_videos(entries: list[dict], channel: str | None = None, videos: list[str] | None = None, tag: str | None = None, limit: int = 100) -> list[dict]` — filtert, sortiert `published_at` absteigend und schneidet auf `limit` (0 = unbegrenzt)
  - `ebook.parse_args(argv: list[str] | None = None) -> argparse.Namespace` mit den Feldern `hours, all, channel, videos, tag, limit, user, sync_db, read, thumbnails, transcripts, output, lang`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ebook_selection.py
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import ebook
from export_harness import video


def _entries(n):
    return [video("v%03d" % i, "2026-01-%02dT00:00:00Z" % (i + 1)) for i in range(n)]


def test_limit_keeps_the_newest_entries():
    picked = ebook.select_videos(_entries(10), limit=3)
    assert [v["video_id"] for v in picked] == ["v009", "v008", "v007"]


def test_limit_zero_keeps_everything():
    assert len(ebook.select_videos(_entries(10), limit=0)) == 10


def test_tag_and_channel_filters_apply_before_the_limit():
    entries = _entries(4)
    entries[0]["tags"] = ["Rust"]
    entries[1]["channel_id"] = "UC2"
    assert [v["video_id"] for v in ebook.select_videos(entries, tag="Rust", limit=100)] == ["v000"]
    assert [v["video_id"] for v in ebook.select_videos(entries, channel="UC2", limit=100)] == ["v001"]


def test_explicit_video_ids_win_over_order():
    picked = ebook.select_videos(_entries(5), videos=["v001", "v003"], limit=100)
    assert sorted(v["video_id"] for v in picked) == ["v001", "v003"]


def test_defaults():
    args = ebook.parse_args([])
    assert args.limit == 100 and args.read == "split" and args.lang == "de"
    assert args.thumbnails is True and args.transcripts is True


def test_hours_and_all_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        ebook.parse_args(["--hours", "24", "--all"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env TMPDIR=$HOME/.cache/yt-tmp PYTHONPATH=$PWD/.venv/lib/python3.13/site-packages python -m pytest tests/test_ebook_selection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ebook'`

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
"""Render stored video summaries into an EPUB 3 ebook.

Reads only from the local store (and optionally the sync server's database
for a user's read state) -- no YouTube and no LLM calls.
"""

import argparse
import os
import sys
from datetime import datetime

DEFAULT_LIMIT = 100
DEFAULT_SYNC_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync-server", "sync.db")


def select_videos(entries, channel=None, videos=None, tag=None, limit=DEFAULT_LIMIT):
    """Filter, order newest-first and cut to `limit` (0 = no limit).

    The cut happens before any grouping, so "the newest 100" means exactly
    that -- not "100 per week".
    """
    picked = entries
    if videos:
        wanted = set(videos)
        picked = [v for v in picked if v["video_id"] in wanted]
    if channel:
        picked = [v for v in picked if v.get("channel_id") == channel]
    if tag:
        picked = [v for v in picked if tag in (v.get("tags") or [])]
    picked = sorted(
        picked,
        key=lambda v: ((v.get("published_at") or ""), v.get("video_id") or ""),
        reverse=True,
    )
    return picked[:limit] if limit else picked


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Build an EPUB ebook from stored summaries.")
    window = parser.add_mutually_exclusive_group()
    window.add_argument("--hours", type=int, help="only videos published in the last N hours")
    window.add_argument("--all", action="store_true", help="all videos in the store")
    parser.add_argument("--channel", help="restrict to one channel ID")
    parser.add_argument("--videos", help="comma-separated video IDs")
    parser.add_argument("--tag", help="restrict to videos carrying this tag")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help="keep only the N newest videos (0 = no limit)")
    parser.add_argument("--user", help="email whose read state is taken from the sync database")
    parser.add_argument("--sync-db", default=DEFAULT_SYNC_DB, help="path to the sync server database")
    parser.add_argument("--read", choices=["split", "drop", "ignore"], default="split",
                        help="read videos: move to the back, drop them, or ignore the state")
    parser.add_argument("--no-thumbnails", dest="thumbnails", action="store_false",
                        help="do not download and embed thumbnails")
    parser.add_argument("--no-transcripts", dest="transcripts", action="store_false",
                        help="do not append transcripts")
    parser.add_argument("--output", help="output file (default: ebook_YYYY-MM-DD_HH-MM.epub)")
    parser.add_argument("--lang", choices=["de", "en"], default="de")
    return parser.parse_args(argv)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env TMPDIR=$HOME/.cache/yt-tmp PYTHONPATH=$PWD/.venv/lib/python3.13/site-packages python -m pytest tests/test_ebook_selection.py -v`
Expected: PASS (6 Tests)

- [ ] **Step 5: Commit**

```bash
git add ebook.py tests/test_ebook_selection.py
git commit -m "feat(ebook): add selection and CLI arguments"
```

---

### Task 3: Lesestatus aus der Sync-Datenbank

**Files:**
- Modify: `ebook.py`
- Test: `tests/test_ebook_read_state.py`

**Interfaces:**
- Consumes: `ebook.select_videos()`
- Produces:
  - `ebook.load_read_ids(sync_db: str, email: str) -> set[str]` — Video-IDs mit `type='read'` und `value=1`; wirft `SystemExit` mit Klartext, wenn Datei oder Nutzer fehlen
  - `ebook.partition_by_read(videos: list[dict], read_ids: set[str], mode: str) -> list[tuple[str, list[dict]]]` — Liste von `(part_key, videos)`; `part_key` ist `"unread"`, `"read"` oder `"all"`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ebook_read_state.py
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import ebook
from export_harness import video


def _sync_db(tmp_path, rows):
    path = tmp_path / "sync.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, created_at TEXT);
        CREATE TABLE video_state (user_id INTEGER, video_id TEXT, type TEXT, value INTEGER,
                                  updated_at TEXT, PRIMARY KEY (user_id, video_id, type));
    """)
    db.execute("INSERT INTO users (id, email, created_at) VALUES (1, 'a@b.com', '')")
    for vid, typ, val in rows:
        db.execute("INSERT INTO video_state VALUES (1, ?, ?, ?, '')", (vid, typ, val))
    db.commit()
    db.close()
    return str(path)


def test_only_videos_marked_read_are_returned(tmp_path):
    path = _sync_db(tmp_path, [("v1", "read", 1), ("v2", "read", 0), ("v3", "bookmark", 1)])
    assert ebook.load_read_ids(path, "a@b.com") == {"v1"}


def test_unknown_user_is_an_error_not_an_empty_set(tmp_path):
    path = _sync_db(tmp_path, [])
    with pytest.raises(SystemExit):
        ebook.load_read_ids(path, "nobody@example.com")


def test_missing_database_is_an_error(tmp_path):
    with pytest.raises(SystemExit):
        ebook.load_read_ids(str(tmp_path / "nope.db"), "a@b.com")


def _videos():
    return [video("v1", "2026-01-01T00:00:00Z"), video("v2", "2026-01-02T00:00:00Z")]


def test_split_puts_unread_first_and_keeps_both_parts():
    parts = ebook.partition_by_read(_videos(), {"v1"}, "split")
    assert [(k, [v["video_id"] for v in vs]) for k, vs in parts] == [
        ("unread", ["v2"]), ("read", ["v1"]),
    ]


def test_drop_removes_read_videos_entirely():
    parts = ebook.partition_by_read(_videos(), {"v1"}, "drop")
    assert [(k, [v["video_id"] for v in vs]) for k, vs in parts] == [("unread", ["v2"])]


def test_ignore_yields_one_undivided_part():
    parts = ebook.partition_by_read(_videos(), {"v1"}, "ignore")
    assert [k for k, _ in parts] == ["all"]
    assert len(parts[0][1]) == 2


def test_empty_parts_are_dropped():
    parts = ebook.partition_by_read(_videos(), set(), "split")
    assert [k for k, _ in parts] == ["unread"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env TMPDIR=$HOME/.cache/yt-tmp PYTHONPATH=$PWD/.venv/lib/python3.13/site-packages python -m pytest tests/test_ebook_read_state.py -v`
Expected: FAIL — `module 'ebook' has no attribute 'load_read_ids'`

- [ ] **Step 3: Write minimal implementation**

```python
import sqlite3


def load_read_ids(sync_db, email):
    """Video IDs this user has marked as read, straight from the sync database.

    Read-only. An unknown email is an error rather than an empty set -- a typo
    would otherwise silently produce a book in which nothing is marked read.
    """
    if not os.path.exists(sync_db):
        sys.exit(f"Error: sync database not found: {sync_db}")
    db = sqlite3.connect(f"file:{sync_db}?mode=ro", uri=True)
    try:
        row = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            sys.exit(f"Error: no sync user with email '{email}'.")
        rows = db.execute(
            "SELECT video_id FROM video_state WHERE user_id = ? AND type = 'read' AND value = 1",
            (row[0],),
        ).fetchall()
    finally:
        db.close()
    return {r[0] for r in rows}


def partition_by_read(videos, read_ids, mode):
    """Split into (part_key, videos) pairs according to --read."""
    if mode == "ignore" or not read_ids:
        parts = [("all", videos)] if mode == "ignore" else [
            ("unread", [v for v in videos if v["video_id"] not in read_ids]),
            ("read", [v for v in videos if v["video_id"] in read_ids]),
        ]
    else:
        unread = [v for v in videos if v["video_id"] not in read_ids]
        read = [v for v in videos if v["video_id"] in read_ids]
        parts = [("unread", unread)] if mode == "drop" else [("unread", unread), ("read", read)]
    return [(key, vs) for key, vs in parts if vs]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env TMPDIR=$HOME/.cache/yt-tmp PYTHONPATH=$PWD/.venv/lib/python3.13/site-packages python -m pytest tests/test_ebook_read_state.py -v`
Expected: PASS (7 Tests)

- [ ] **Step 5: Commit**

```bash
git add ebook.py tests/test_ebook_read_state.py
git commit -m "feat(ebook): read per-user read state from the sync database"
```

---

### Task 4: Gruppierung nach Kalenderwoche

**Files:**
- Create: `epub_builder.py`
- Test: `tests/test_ebook_weeks.py`

**Interfaces:**
- Consumes: nichts
- Produces: `epub_builder.group_by_week(videos: list[dict]) -> list[dict]` — je Eintrag `{"iso_year": int, "iso_week": int, "start": date, "end": date, "anchor": "w-2026-34", "videos": [...]}`, Wochen aufsteigend, Videos darin aufsteigend nach `published_at`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ebook_weeks.py
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import epub_builder
from export_harness import video


def test_videos_are_grouped_into_iso_weeks_in_chronological_order():
    weeks = epub_builder.group_by_week([
        video("b", "2026-08-19T10:00:00Z"),   # KW 34
        video("a", "2026-08-12T10:00:00Z"),   # KW 33
        video("c", "2026-08-21T10:00:00Z"),   # KW 34
    ])
    assert [(w["iso_year"], w["iso_week"]) for w in weeks] == [(2026, 33), (2026, 34)]
    assert [v["video_id"] for v in weeks[1]["videos"]] == ["b", "c"]


def test_week_boundaries_are_monday_to_sunday():
    weeks = epub_builder.group_by_week([video("a", "2026-08-19T10:00:00Z")])
    assert weeks[0]["start"].isoformat() == "2026-08-17"
    assert weeks[0]["end"].isoformat() == "2026-08-23"


def test_new_year_does_not_collapse_two_different_week_ones():
    # 2026-01-01 is in ISO week 1 of 2026; 2024-12-31 is in ISO week 1 of 2025.
    weeks = epub_builder.group_by_week([
        video("old", "2024-12-31T10:00:00Z"),
        video("new", "2026-01-01T10:00:00Z"),
    ])
    assert [(w["iso_year"], w["iso_week"]) for w in weeks] == [(2025, 1), (2026, 1)]
    assert len(weeks) == 2


def test_anchor_is_stable_and_filename_safe():
    weeks = epub_builder.group_by_week([video("a", "2026-08-19T10:00:00Z")])
    assert weeks[0]["anchor"] == "w-2026-34"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env TMPDIR=$HOME/.cache/yt-tmp PYTHONPATH=$PWD/.venv/lib/python3.13/site-packages python -m pytest tests/test_ebook_weeks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'epub_builder'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Turn selected videos into the files of an EPUB 3 archive."""

from datetime import date, datetime, timedelta


def _published_date(entry):
    """The publish date as a plain date; ISO strings may end in 'Z'."""
    raw = (entry.get("published_at") or "")[:19]
    return datetime.fromisoformat(raw).date()


def group_by_week(videos):
    """Group videos into ISO calendar weeks, oldest week first.

    The key is (iso_year, iso_week), never the week number alone: ISO week 1
    can start in December, so two different "week 1"s would otherwise merge.
    """
    buckets = {}
    for v in videos:
        d = _published_date(v)
        iso_year, iso_week, iso_weekday = d.isocalendar()
        key = (iso_year, iso_week)
        bucket = buckets.setdefault(key, {
            "iso_year": iso_year,
            "iso_week": iso_week,
            "start": d - timedelta(days=iso_weekday - 1),
            "end": d + timedelta(days=7 - iso_weekday),
            "anchor": "w-%04d-%02d" % (iso_year, iso_week),
            "videos": [],
        })
        bucket["videos"].append(v)

    weeks = [buckets[k] for k in sorted(buckets)]
    for w in weeks:
        w["videos"].sort(key=lambda v: (v.get("published_at") or "", v.get("video_id") or ""))
    return weeks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env TMPDIR=$HOME/.cache/yt-tmp PYTHONPATH=$PWD/.venv/lib/python3.13/site-packages python -m pytest tests/test_ebook_weeks.py -v`
Expected: PASS (4 Tests)

- [ ] **Step 5: Commit**

```bash
git add epub_builder.py tests/test_ebook_weeks.py
git commit -m "feat(ebook): group videos into ISO calendar weeks"
```

---

### Task 5: XHTML-Härtung und Kapitel-Rendering

Reader lehnen ein EPUB ab, sobald eine Datei kein wohlgeformtes XML ist. Die gespeicherten Zusammenfassungen sind HTML-Fragmente aus einem LLM — sie dürfen das Buch nicht sprengen.

**Files:**
- Modify: `epub_builder.py`
- Create: `ebook/book.css`, `ebook/chapter.xhtml.j2`
- Test: `tests/test_ebook_xhtml.py`

**Interfaces:**
- Consumes: `epub_builder.group_by_week()`
- Produces:
  - `epub_builder.xhtmlify(fragment: str | None) -> str` — wohlgeformtes XML-Fragment; unrettbare Eingabe wird escapet zurückgegeben
  - `epub_builder.render_chapter(week: dict, strings: dict, lang: str, images: dict[str, str], transcripts: set[str]) -> str` — vollständiges XHTML-Dokument eines Wochenkapitels; `images` bildet `video_id` auf den EPUB-internen Bildpfad ab, `transcripts` enthält die IDs mit Anhangseite

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ebook_xhtml.py
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import epub_builder
import i18n
from export_harness import video


def test_named_entities_are_replaced_by_numeric_ones():
    out = epub_builder.xhtmlify("<p>a&nbsp;b</p>")
    assert "&nbsp;" not in out
    ET.fromstring("<div>" + out + "</div>")


def test_unclosed_tag_is_escaped_instead_of_breaking_the_document():
    out = epub_builder.xhtmlify("<p>text<b>bold</p>")
    ET.fromstring("<div>" + out + "</div>")     # must not raise
    assert "bold" in out


def test_none_summary_becomes_empty_string():
    assert epub_builder.xhtmlify(None) == ""


def test_chapter_is_well_formed_and_carries_every_video():
    week = epub_builder.group_by_week([
        video("v1", "2026-08-19T10:00:00Z", title='Quote " & <tag>'),
        video("v2", "2026-08-20T10:00:00Z"),
    ])[0]
    xhtml = epub_builder.render_chapter(week, i18n.get_strings("de"), "de", {}, set())
    root = ET.fromstring(xhtml)                 # must parse as XML
    ids = [s.get("id") for s in root.iter("{http://www.w3.org/1999/xhtml}section")]
    assert "v-v1" in ids and "v-v2" in ids
    assert "KW 34" in xhtml


def test_chapter_links_thumbnail_and_transcript_only_when_present():
    week = epub_builder.group_by_week([video("v1", "2026-08-19T10:00:00Z")])[0]
    plain = epub_builder.render_chapter(week, i18n.get_strings("de"), "de", {}, set())
    assert "images/" not in plain and "transcript-v1.xhtml" not in plain

    rich = epub_builder.render_chapter(
        week, i18n.get_strings("de"), "de", {"v1": "images/v1.jpg"}, {"v1"})
    assert 'src="images/v1.jpg"' in rich
    assert "transcript-v1.xhtml" in rich
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env TMPDIR=$HOME/.cache/yt-tmp PYTHONPATH=$PWD/.venv/lib/python3.13/site-packages python -m pytest tests/test_ebook_xhtml.py -v`
Expected: FAIL — `module 'epub_builder' has no attribute 'xhtmlify'`

- [ ] **Step 3: Write minimal implementation**

`epub_builder.py`:

```python
import html
import os
import re
import xml.etree.ElementTree as ET

from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "ebook")

# Only these named entities can appear in stored summaries (nh3 escapes the
# rest); XML knows none of them except the five predefined ones.
_NAMED_ENTITY_RE = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#)([a-zA-Z][a-zA-Z0-9]*);")


def xhtmlify(fragment):
    """Return a fragment that is guaranteed to parse as XML.

    A single unclosed tag in one summary would make the whole book unreadable
    for strict readers, so an unparseable fragment is escaped into plain text
    rather than passed through.
    """
    if not fragment:
        return ""
    def numeric(match):
        entity = "&%s;" % match.group(1)
        decoded = html.unescape(entity)
        # Unknown entity: keep the literal text but make the ampersand legal XML.
        return "&#%d;" % ord(decoded) if decoded != entity else "&amp;%s;" % match.group(1)

    text = _NAMED_ENTITY_RE.sub(numeric, fragment)
    try:
        ET.fromstring("<div>" + text + "</div>")
        return text
    except ET.ParseError:
        return "<p>" + html.escape(re.sub(r"<[^>]*>", "", text)) + "</p>"


def _env():
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
    env.filters["xhtml"] = lambda s: Markup(xhtmlify(s))
    return env


def render_chapter(week, strings, lang, images, transcripts):
    template = _env().get_template("chapter.xhtml.j2")
    return template.render(week=week, t=strings, lang=lang, images=images, transcripts=transcripts)
```

`ebook/chapter.xhtml.j2` (jedes Tag geschlossen, `<img/>` selbstschließend — XHTML, nicht HTML):

```jinja
<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{{ lang }}" lang="{{ lang }}">
<head><title>{{ t.book_week }} {{ week.iso_week }}</title>
<link rel="stylesheet" type="text/css" href="book.css"/></head>
<body>
<h1>{{ t.book_week }} {{ week.iso_week }} &#183; {{ week.start.strftime('%d.%m.%Y') }}&#8211;{{ week.end.strftime('%d.%m.%Y') }}</h1>
{% for v in week.videos %}
<section id="v-{{ v.video_id }}" class="video">
  <h2>{{ v.title }}</h2>
  <p class="meta">{{ v.channel_title }} &#183; {{ v.published_at[:10] }}{% if v.duration %} &#183; {{ v.duration }}{% endif %}</p>
  {% if images.get(v.video_id) %}<p class="thumb"><img src="{{ images[v.video_id] }}" alt="{{ v.title }}"/></p>{% endif %}
  {% if v.tags %}<p class="tags">{{ v.tags|join(' &#183; ') }}</p>{% endif %}
  <div class="summary">{{ v.summary|xhtml }}</div>
  <p class="links"><a href="https://www.youtube.com/watch?v={{ v.video_id }}">{{ t.book_watch }}</a>{% if v.video_id in transcripts %} &#183; <a href="transcript-{{ v.video_id }}.xhtml">{{ t.book_transcript }}</a>{% endif %}</p>
</section>
{% endfor %}
</body>
</html>
```

`ebook/book.css`: schlichte Typografie, keine Farben (der Reader bestimmt sie):

```css
body { font-family: serif; line-height: 1.5; margin: 0 1em; }
h1 { font-size: 1.3em; margin: 1.2em 0 0.6em; }
h2 { font-size: 1.1em; margin: 1.4em 0 0.2em; }
.meta, .tags { font-size: 0.85em; font-style: italic; margin: 0.2em 0; }
.thumb img { max-width: 100%; }
.summary h3 { font-size: 1em; margin: 1em 0 0.3em; }
.links { font-size: 0.9em; margin: 0.6em 0 1.4em; }
```

Für den `Markup`-Import: `from markupsafe import Markup` oben ergänzen (kommt mit Jinja2).
`i18n.py` bekommt `book_week`, `book_watch`, `book_transcript` in `de` und `en`.

- [ ] **Step 4: Run test to verify it passes**

Run: `env TMPDIR=$HOME/.cache/yt-tmp PYTHONPATH=$PWD/.venv/lib/python3.13/site-packages python -m pytest tests/test_ebook_xhtml.py -v`
Expected: PASS (5 Tests)

- [ ] **Step 5: Commit**

```bash
git add epub_builder.py ebook/ i18n.py tests/test_ebook_xhtml.py
git commit -m "feat(ebook): render week chapters as well-formed XHTML"
```

---

### Task 6: EPUB packen

**Files:**
- Modify: `epub_builder.py`
- Create: `ebook/nav.xhtml.j2`, `ebook/content.opf.j2`, `ebook/toc.ncx.j2`, `ebook/title.xhtml.j2`, `ebook/transcript.xhtml.j2`
- Test: `tests/test_epub_structure.py`

**Interfaces:**
- Consumes: `render_chapter()`, `group_by_week()`
- Produces: `epub_builder.build_epub(parts: list[dict], output_path: str, title: str, lang: str, strings: dict, images: dict[str, bytes] | None = None, transcripts: dict[str, str] | None = None, book_id: str | None = None) -> None`; `parts` ist `[{"key": "unread", "title": "Ungelesen", "weeks": [...]}, ...]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_epub_structure.py
import os
import sys
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import epub_builder
import i18n
from export_harness import video

OPF_NS = {"opf": "http://www.idpf.org/2007/opf"}


def _book(tmp_path, **kwargs):
    weeks = epub_builder.group_by_week([
        video("v1", "2026-08-19T10:00:00Z"),
        video("v2", "2026-08-26T10:00:00Z"),
    ])
    parts = [{"key": "unread", "title": "Ungelesen", "weeks": weeks}]
    out = str(tmp_path / "book.epub")
    epub_builder.build_epub(parts, out, "Test Buch", "de", i18n.get_strings("de"),
                            book_id="urn:uuid:fixed", **kwargs)
    return out


def test_mimetype_is_the_first_entry_and_stored_uncompressed(tmp_path):
    with zipfile.ZipFile(_book(tmp_path)) as z:
        first = z.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"application/epub+zip"


def test_every_manifest_item_exists_and_every_content_file_is_manifested(tmp_path):
    with zipfile.ZipFile(_book(tmp_path)) as z:
        names = set(z.namelist())
        opf = ET.fromstring(z.read("OEBPS/content.opf"))
        hrefs = {i.get("href") for i in opf.findall(".//opf:manifest/opf:item", OPF_NS)}
        for href in hrefs:
            assert "OEBPS/" + href in names, href
        content = {n[len("OEBPS/"):] for n in names
                   if n.startswith("OEBPS/") and not n.endswith(".opf")}
        assert content == hrefs


def test_spine_starts_with_the_title_page_and_lists_every_chapter(tmp_path):
    with zipfile.ZipFile(_book(tmp_path)) as z:
        opf = ET.fromstring(z.read("OEBPS/content.opf"))
        ids = [i.get("idref") for i in opf.findall(".//opf:spine/opf:itemref", OPF_NS)]
    assert ids[0] == "title"
    assert "chap-w-2026-34" in ids and "chap-w-2026-35" in ids


def test_every_document_in_the_archive_parses_as_xml(tmp_path):
    with zipfile.ZipFile(_book(tmp_path)) as z:
        for name in z.namelist():
            if name.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                ET.fromstring(z.read(name))


def test_nav_lists_parts_and_weeks(tmp_path):
    with zipfile.ZipFile(_book(tmp_path)) as z:
        nav = z.read("OEBPS/nav.xhtml").decode("utf-8")
    assert "Ungelesen" in nav
    assert "chapter-w-2026-34.xhtml" in nav


def test_container_points_at_the_package_document(tmp_path):
    with zipfile.ZipFile(_book(tmp_path)) as z:
        container = ET.fromstring(z.read("META-INF/container.xml"))
    rootfile = container.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
    assert rootfile.get("full-path") == "OEBPS/content.opf"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env TMPDIR=$HOME/.cache/yt-tmp PYTHONPATH=$PWD/.venv/lib/python3.13/site-packages python -m pytest tests/test_epub_structure.py -v`
Expected: FAIL — `module 'epub_builder' has no attribute 'build_epub'`

- [ ] **Step 3: Write minimal implementation**

```python
import zipfile
from datetime import datetime, timezone

CONTAINER_XML = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""


def build_epub(parts, output_path, title, lang, strings, images=None,
               transcripts=None, book_id=None):
    """Write the EPUB 3 archive.

    Order matters: 'mimetype' must be the first entry and stored uncompressed,
    otherwise readers refuse the file.
    """
    images = images or {}
    transcripts = transcripts or {}
    env = _env()
    generated = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()
    book_id = book_id or "urn:uuid:" + generated.replace(":", "").replace("-", "")

    files = {}   # href inside OEBPS -> str | bytes
    files["book.css"] = open(os.path.join(TEMPLATE_DIR, "book.css"), encoding="utf-8").read()
    files["title.xhtml"] = env.get_template("title.xhtml.j2").render(
        title=title, lang=lang, t=strings, generated=generated[:10], parts=parts)

    image_hrefs = {}
    for video_id, blob in images.items():
        href = "images/%s.jpg" % video_id
        files[href] = blob
        image_hrefs[video_id] = href

    for part in parts:
        for week in part["weeks"]:
            href = "chapter-%s.xhtml" % week["anchor"]
            week["href"] = href
            files[href] = render_chapter(week, strings, lang, image_hrefs, set(transcripts))

    for video_id, text in transcripts.items():
        files["transcript-%s.xhtml" % video_id] = env.get_template("transcript.xhtml.j2").render(
            video_id=video_id, paragraphs=_transcript_paragraphs(text), lang=lang, t=strings,
            back_href=_chapter_href_for(parts, video_id))

    files["nav.xhtml"] = env.get_template("nav.xhtml.j2").render(parts=parts, lang=lang, t=strings, title=title)
    files["toc.ncx"] = env.get_template("toc.ncx.j2").render(parts=parts, book_id=book_id, title=title)
    files["content.opf"] = env.get_template("content.opf.j2").render(
        parts=parts, title=title, lang=lang, book_id=book_id, generated=generated,
        items=sorted(files.keys()), transcripts=sorted(transcripts.keys()), t=strings)

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER_XML)
        for href, payload in files.items():
            z.writestr("OEBPS/" + href, payload)
```

Die beiden Helfer:

```python
MEDIA_TYPES = {".xhtml": "application/xhtml+xml", ".css": "text/css",
               ".jpg": "image/jpeg", ".ncx": "application/x-dtbncx+xml"}


def _media_type(href):
    return MEDIA_TYPES[os.path.splitext(href)[1]]


def _item_id(href):
    """Manifest ID for a file: chapter-w-2026-34.xhtml -> chap-w-2026-34."""
    stem = os.path.splitext(os.path.basename(href))[0]
    return "chap-" + stem[len("chapter-"):] if stem.startswith("chapter-") else stem.replace(".", "-")


def _transcript_paragraphs(text):
    """Cut a raw transcript into readable paragraphs.

    Transcripts arrive as one long blob; blank lines are honoured where they
    exist, otherwise every 12 lines become a paragraph so an e-reader has
    something to break pages on.
    """
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(blocks) > 1:
        return [" ".join(b.split()) for b in blocks]
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return [" ".join(lines[i:i + 12]) for i in range(0, len(lines), 12)]


def _chapter_href_for(parts, video_id):
    """Where a transcript page links back to: chapter file plus video anchor."""
    for part in parts:
        for week in part["weeks"]:
            if any(v["video_id"] == video_id for v in week["videos"]):
                return "%s#v-%s" % (week["href"], video_id)
    return "nav.xhtml"
```

`ebook/content.opf.j2` — Manifest aus `items`, Spine `title`, dann Kapitel je Teil, dann Transkripte:

```jinja
<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">{{ book_id }}</dc:identifier>
    <dc:title>{{ title }}</dc:title>
    <dc:language>{{ lang }}</dc:language>
    <meta property="dcterms:modified">{{ generated }}</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
{% for href in items if href != 'nav.xhtml' %}    <item id="{{ item_id(href) }}" href="{{ href }}" media-type="{{ media_type(href) }}"/>
{% endfor %}  </manifest>
  <spine toc="toc">
    <itemref idref="title"/>
{% for part in parts %}{% for week in part.weeks %}    <itemref idref="chap-{{ week.anchor }}"/>
{% endfor %}{% endfor %}{% for vid in transcripts %}    <itemref idref="transcript-{{ vid }}"/>
{% endfor %}  </spine>
</package>
```

`_env()` bekommt dafür zwei Globals: `env.globals.update(item_id=_item_id, media_type=_media_type)`.

`ebook/nav.xhtml.j2` — EPUB-3-Inhaltsverzeichnis, Teile als äußere Ebene:

```jinja
<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{{ lang }}">
<head><title>{{ t.book_contents }}</title></head>
<body>
<nav epub:type="toc" id="toc"><h1>{{ t.book_contents }}</h1>
<ol>
{% for part in parts %}  <li>{{ part.title }}
    <ol>
{% for week in part.weeks %}      <li><a href="{{ week.href }}">{{ t.book_week }} {{ week.iso_week }} &#183; {{ week.start.strftime('%d.%m.%Y') }}</a></li>
{% endfor %}    </ol>
  </li>
{% endfor %}</ol>
</nav>
</body>
</html>
```

`ebook/toc.ncx.j2` — dieselbe Struktur flach als `navPoint`-Liste (`playOrder` fortlaufend ab 1) für ältere Reader; `ebook/title.xhtml.j2` — Titel, Erzeugungsdatum, Anzahl Videos je Teil; `ebook/transcript.xhtml.j2` — `<h1>{{ t.book_transcript }}</h1>`, ein `<p>` je Absatz aus `paragraphs`, darunter `<a href="{{ back_href }}">{{ t.book_back }}</a>`.

`i18n.py` braucht zusätzlich `book_contents` ("Inhalt"/"Contents").

- [ ] **Step 4: Run test to verify it passes**

Run: `env TMPDIR=$HOME/.cache/yt-tmp PYTHONPATH=$PWD/.venv/lib/python3.13/site-packages python -m pytest tests/test_epub_structure.py -v`
Expected: PASS (6 Tests)

- [ ] **Step 5: Commit**

```bash
git add epub_builder.py ebook/ tests/test_epub_structure.py
git commit -m "feat(ebook): package chapters into an EPUB 3 archive"
```

---

### Task 7: Thumbnails laden, zwischenspeichern, einbetten

**Files:**
- Modify: `ebook.py`
- Test: `tests/test_ebook_thumbnails.py`

**Interfaces:**
- Consumes: `build_epub(images=...)`
- Produces: `ebook.collect_thumbnails(videos: list[dict], cache_dir: str, fetch=None, max_bytes: int = 2_000_000) -> tuple[dict[str, bytes], int]` — Bilder je Video-ID plus Anzahl der Fehlschläge; `fetch(url) -> bytes` ist injizierbar und wird in Tests immer gestellt

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ebook_thumbnails.py
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import ebook
from export_harness import video


def test_images_are_fetched_once_and_cached_on_disk(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return b"\xff\xd8jpegdata"

    videos = [video("v1", "2026-01-01T00:00:00Z")]
    images, failed = ebook.collect_thumbnails(videos, str(tmp_path), fetch=fetch)
    assert images["v1"] == b"\xff\xd8jpegdata" and failed == 0

    again, _ = ebook.collect_thumbnails(videos, str(tmp_path), fetch=fetch)
    assert again["v1"] == b"\xff\xd8jpegdata"
    assert len(calls) == 1, "second run must come from the cache"


def test_a_failing_download_is_counted_and_skipped(tmp_path):
    def fetch(url):
        raise OSError("timeout")

    images, failed = ebook.collect_thumbnails(
        [video("v1", "2026-01-01T00:00:00Z")], str(tmp_path), fetch=fetch)
    assert images == {} and failed == 1


def test_oversized_images_are_skipped(tmp_path):
    images, failed = ebook.collect_thumbnails(
        [video("v1", "2026-01-01T00:00:00Z")], str(tmp_path),
        fetch=lambda url: b"x" * 10, max_bytes=5)
    assert images == {} and failed == 1


def test_non_https_urls_are_never_fetched(tmp_path):
    v = video("v1", "2026-01-01T00:00:00Z", thumbnail_url="http://example.com/a.jpg")
    def fetch(url):
        raise AssertionError("must not be called")
    images, failed = ebook.collect_thumbnails([v], str(tmp_path), fetch=fetch)
    assert images == {} and failed == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env TMPDIR=$HOME/.cache/yt-tmp PYTHONPATH=$PWD/.venv/lib/python3.13/site-packages python -m pytest tests/test_ebook_thumbnails.py -v`
Expected: FAIL — `module 'ebook' has no attribute 'collect_thumbnails'`

- [ ] **Step 3: Write minimal implementation**

```python
import urllib.request


def _default_fetch(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def collect_thumbnails(videos, cache_dir, fetch=None, max_bytes=2_000_000):
    """Return {video_id: jpeg bytes} plus the number of failures.

    Failures never abort the build -- a book without one thumbnail is still a
    book. Downloads are cached on disk so a rebuild works without network.
    """
    fetch = fetch or _default_fetch
    os.makedirs(cache_dir, exist_ok=True)
    images, failed = {}, 0
    for v in videos:
        url = v.get("thumbnail_url") or ""
        path = os.path.join(cache_dir, "%s.jpg" % v["video_id"])
        if os.path.exists(path):
            images[v["video_id"]] = open(path, "rb").read()
            continue
        if not url.startswith("https://"):
            failed += 1
            continue
        try:
            blob = fetch(url)
        except Exception:
            failed += 1
            continue
        if not blob or len(blob) > max_bytes:
            failed += 1
            continue
        with open(path, "wb") as f:
            f.write(blob)
        images[v["video_id"]] = blob
    return images, failed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env TMPDIR=$HOME/.cache/yt-tmp PYTHONPATH=$PWD/.venv/lib/python3.13/site-packages python -m pytest tests/test_ebook_thumbnails.py -v`
Expected: PASS (4 Tests)

- [ ] **Step 5: Commit**

```bash
git add ebook.py tests/test_ebook_thumbnails.py
git commit -m "feat(ebook): embed cached thumbnails, tolerating failures"
```

---

### Task 8: CLI verdrahten, i18n, Dokumentation

**Files:**
- Modify: `ebook.py` (`main()`), `i18n.py`, `README.md`, `CLAUDE.md`, `AGENTS.md`
- Test: `tests/test_ebook_cli.py`

**Interfaces:**
- Consumes: alles aus Task 2–7
- Produces: `ebook.main() -> None`; Exit 0 mit Meldung, wenn die Auswahl leer ist

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ebook_cli.py
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import ebook
from export_harness import video


def _stub_store(monkeypatch, entries):
    monkeypatch.setattr(ebook.store, "get_all_videos", lambda with_transcripts=True: entries)


def test_end_to_end_build_writes_a_readable_epub(tmp_path, monkeypatch):
    _stub_store(monkeypatch, [video("v1", "2026-08-19T10:00:00Z"),
                              video("v2", "2026-08-26T10:00:00Z")])
    out = tmp_path / "book.epub"
    monkeypatch.setattr(sys, "argv", ["ebook.py", "--all", "--no-thumbnails",
                                      "--no-transcripts", "--output", str(out)])
    ebook.main()
    with zipfile.ZipFile(out) as z:
        assert z.infolist()[0].filename == "mimetype"
        assert any(n.startswith("OEBPS/chapter-") for n in z.namelist())


def test_empty_selection_exits_zero_with_a_message(tmp_path, monkeypatch, capsys):
    _stub_store(monkeypatch, [])
    monkeypatch.setattr(sys, "argv", ["ebook.py", "--all", "--output", str(tmp_path / "x.epub")])
    with pytest.raises(SystemExit) as exc:
        ebook.main()
    assert exc.value.code == 0
    assert "No videos" in capsys.readouterr().out


def test_read_split_produces_two_parts(tmp_path, monkeypatch):
    _stub_store(monkeypatch, [video("v1", "2026-08-19T10:00:00Z"),
                              video("v2", "2026-08-26T10:00:00Z")])
    monkeypatch.setattr(ebook, "load_read_ids", lambda db, email: {"v1"})
    out = tmp_path / "book.epub"
    monkeypatch.setattr(sys, "argv", ["ebook.py", "--all", "--user", "a@b.com",
                                      "--no-thumbnails", "--no-transcripts",
                                      "--output", str(out)])
    ebook.main()
    with zipfile.ZipFile(out) as z:
        nav = z.read("OEBPS/nav.xhtml").decode("utf-8")
    assert "Ungelesen" in nav and "Gelesen" in nav
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env TMPDIR=$HOME/.cache/yt-tmp PYTHONPATH=$PWD/.venv/lib/python3.13/site-packages python -m pytest tests/test_ebook_cli.py -v`
Expected: FAIL — `module 'ebook' has no attribute 'main'`

- [ ] **Step 3: Write minimal implementation**

```python
from datetime import timedelta, timezone

import store
import i18n as i18n_module
import epub_builder

PART_TITLE_KEYS = {"unread": "book_part_unread", "read": "book_part_read", "all": "book_part_all"}


def main():
    args = parse_args()
    lang = i18n_module.resolve_lang(args.lang)
    strings = i18n_module.get_strings(lang)

    if args.hours:
        since = datetime.now(tz=timezone.utc) - timedelta(hours=args.hours)
        entries = store.get_videos_since(since, with_transcripts=False)
    else:
        entries = store.get_all_videos(with_transcripts=False)

    video_ids = [v.strip() for v in args.videos.split(",")] if args.videos else None
    selected = select_videos(entries, channel=args.channel, videos=video_ids,
                             tag=args.tag, limit=args.limit)
    if not selected:
        print("No videos to put in the book.")
        sys.exit(0)

    read_ids = load_read_ids(args.sync_db, args.user) if args.user else set()
    parts = []
    for key, videos in partition_by_read(selected, read_ids, args.read):
        parts.append({
            "key": key,
            "title": strings[PART_TITLE_KEYS[key]],
            "weeks": epub_builder.group_by_week(videos),
        })

    images, failed = ({}, 0)
    if args.thumbnails:
        images, failed = collect_thumbnails(selected, os.path.join("data", "thumbnails"))
        if failed:
            print(f"{failed} thumbnail(s) could not be fetched — building without them.")

    transcripts = {}
    if args.transcripts:
        for v in selected:
            path = store.get_llm_transcript_path(v["video_id"])
            if path:
                transcripts[v["video_id"]] = path.read_text(encoding="utf-8")

    output = args.output or f"ebook_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.epub"
    print(f"Building {output} — {len(selected)} video(s), {len(parts)} part(s).")
    epub_builder.build_epub(parts, output, strings["book_title"], lang, strings,
                            images=images, transcripts=transcripts)
    print("Done.")


if __name__ == "__main__":
    main()
```

`i18n.py` ergänzen (de/en): `book_title`, `book_part_unread` ("Ungelesen"/"Unread"), `book_part_read` ("Gelesen"/"Read"), `book_part_all` ("Videos"/"Videos"), `book_week` ("KW"/"Week"), `book_watch` ("Auf YouTube ansehen"/"Watch on YouTube"), `book_transcript` ("Transkript"/"Transcript"), `book_back` ("Zurück zum Kapitel"/"Back to chapter").

- [ ] **Step 4: Run the whole suite**

Run: `env TMPDIR=$HOME/.cache/yt-tmp PYTHONPATH=$PWD/.venv/lib/python3.13/site-packages python -m pytest tests/ -q --ignore=tests/test_openrouter_clean_response.py --ignore=tests/test_openrouter_prompt.py --ignore=tests/test_openrouter_timestamp_links.py --ignore=tests/test_openrouter_validate.py`
Expected: PASS — alle bisherigen Tests plus die neuen

- [ ] **Step 5: Dokumentation ergänzen**

- `README.md`: Abschnitt "Usage — ebook export" mit den Flags, Default `--limit 100`, `--user`/`--read`, Hinweis Send-to-Kindle.
- `CLAUDE.md`: Abschnitt "Ebook export" plus Zeilen für `ebook.py`, `epub_builder.py`, `ebook/` in der Architektur-Tabelle.
- `AGENTS.md`: die zwei Fallen — jede erzeugte Datei muss als XML parsen (`xhtmlify()` ist der Schutz), und `mimetype` muss unkomprimiert der erste ZIP-Eintrag sein.

- [ ] **Step 6: Commit**

```bash
git add ebook.py epub_builder.py ebook/ i18n.py tests/test_ebook_cli.py README.md CLAUDE.md AGENTS.md
git commit -m "feat(ebook): build EPUB books from stored summaries"
```

---

## Danach (bewusst nicht in diesem Plan)

- Versand per Mail / Send-to-Kindle (`--send-to`), Anhang statt HTML-Body in `send_mail.py`
- PDF aus denselben XHTML-Quellen
- Auswahl direkt im HTML-Archiv (Häkchen setzen, Liste exportieren)
- Cover-Bild statt reiner Textseite
- Sync-Zustand über die HTTP-API statt über `sync.db`, für Läufe auf einem anderen Host
