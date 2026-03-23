# Original-Language Transcript + Manual DE/EN Fallback — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Always fetch and store the transcript in the video's original language; also fetch and store a manually created DE/EN transcript as a second file when available; feed the LLM the manual file if present, otherwise the original.

**Architecture:** `transcripts.py` gains a new `get_manual_transcript()` function and `get_transcript()` is reworked to detect original language via auto-generated transcripts. `store.py` gains a `transcript_lang` DB column, two path-resolution helpers, and updated write/prune functions. `collect.py`, `repair.py`, and `summarize.py` are updated to use the new APIs.

**Tech Stack:** Python 3.11+, `youtube_transcript_api`, SQLite via `sqlite3`, `pathlib.Path`, `pytest` + `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-03-23-original-language-transcript-design.md`

---

## File Map

| File | Change |
|---|---|
| `transcripts.py` | Rework `_fetch()` → `_fetch_original()`; update `get_transcript()` to 3-tuple; add `get_manual_transcript()` |
| `store.py` | Add `transcript_lang` migration; add `_resolve_transcript_path()`, `get_llm_transcript_path()`; update `get_video()`, `get_videos_since()`, `get_all_videos()`, `add_video()`, `update_video_with_summary()`, `prune_older_than()` |
| `collect.py` | Update `_process_single_video()` and bulk loop to 3-tuple + manual fetch + lang-suffixed filenames |
| `repair.py` | Update 3-tuple unpack, file write path, `transcript_lang=` kwarg, LLM input via `get_llm_transcript_path()` |
| `summarize.py` | Minimal: unpack 3-tuple and discard `_lang` |
| `tests/test_transcripts.py` | New: tests for `get_transcript()` original-language logic and `get_manual_transcript()` |
| `tests/test_store_transcript_lang.py` | New: tests for `_resolve_transcript_path()`, `get_llm_transcript_path()`, `has_transcript`, `has_manual_transcript`, `prune_older_than()` |

---

## Task 1: Rework `transcripts.py`

**Files:**
- Modify: `transcripts.py`
- Create: `tests/test_transcripts.py`

### Background

`_fetch(api, video_id, preferred_langs)` currently iterates `preferred_langs` and takes the first matching transcript. It must be replaced with `_fetch_original(api, video_id)` which:
1. Lists all transcripts via `api.list(video_id)`.
2. Finds the first auto-generated transcript to discover `orig_lang`.
3. Tries `find_manually_created_transcript([orig_lang])` first (better quality).
4. Falls back to `find_generated_transcript([orig_lang])`.
5. Falls back to `next(iter(transcript_list))` if no generated exists at all.
6. Returns `(text, lang_code, error_reason)`.

The new `_fetch_manual(api, video_id, preferred_langs)` returns `(text, lang_code)` or `(None, None)` — no error return, no retry.

Public `get_transcript()` keeps the existing ip_blocked / country_blocked proxy-retry logic, but calls `_fetch_original` instead of `_fetch`. `get_manual_transcript()` calls `_fetch_manual` on the default `_api` only (no retry needed).

The module-level `PREFERRED_LANGS` constant is no longer used by the main path but may be kept for backward-compat (it currently also appears as a default parameter in `get_transcript()`).

- [ ] **Step 1: Create test file with failing tests**

```python
# tests/test_transcripts.py
"""Tests for transcripts.get_transcript() (original-language) and get_manual_transcript()."""
import pytest
from unittest.mock import MagicMock, patch


def _entry(start=0.0, text="Hello world"):
    e = MagicMock()
    e.start = start
    e.text = text
    return e


def _make_transcript(lang, is_generated, text="content"):
    t = MagicMock()
    t.language_code = lang
    t.is_generated = is_generated
    t.fetch.return_value = [_entry(0.0, text)]
    return t


def _transcript_list(*transcripts):
    tl = MagicMock()
    # Use side_effect (factory) so each iter() call gets a fresh iterator.
    # return_value would share one exhausted iterator across multiple for-loops.
    tl.__iter__ = MagicMock(side_effect=lambda: iter(transcripts))
    def find_generated(langs):
        from youtube_transcript_api import NoTranscriptFound
        for t in transcripts:
            if t.is_generated and t.language_code in langs:
                return t
        raise NoTranscriptFound("", langs, [])
    def find_manual(langs):
        from youtube_transcript_api import NoTranscriptFound
        for t in transcripts:
            if not t.is_generated and t.language_code in langs:
                return t
        raise NoTranscriptFound("", langs, [])
    tl.find_generated_transcript = find_generated
    tl.find_manually_created_transcript = find_manual
    return tl


class TestGetTranscriptOriginalLanguage:
    def _call(self, transcript_list):
        import transcripts as tr
        with patch.object(tr._api, "list", return_value=transcript_list):
            return tr.get_transcript("vid123")

    def test_returns_three_tuple(self):
        tl = _transcript_list(_make_transcript("ja", is_generated=True))
        text, lang, err = self._call(tl)
        assert lang == "ja"
        assert err is None
        assert text is not None

    def test_prefers_manual_in_original_lang(self):
        # Both manual-ja and generated-ja exist — manual wins
        manual_ja = _make_transcript("ja", is_generated=False, text="manual")
        gen_ja = _make_transcript("ja", is_generated=True, text="generated")
        tl = _transcript_list(gen_ja, manual_ja)
        text, lang, err = self._call(tl)
        assert lang == "ja"
        assert "manual" in text

    def test_falls_back_to_generated_when_no_manual(self):
        gen_ja = _make_transcript("ja", is_generated=True, text="auto")
        tl = _transcript_list(gen_ja)
        text, lang, err = self._call(tl)
        assert lang == "ja"
        assert "auto" in text

    def test_falls_back_to_first_transcript_when_no_generated(self):
        # Only manually created, no auto-generated → take first
        manual_en = _make_transcript("en", is_generated=False, text="english manual")
        tl = _transcript_list(manual_en)
        text, lang, err = self._call(tl)
        assert lang == "en"
        assert text is not None


class TestGetManualTranscript:
    def _call(self, transcript_list, preferred=None):
        import transcripts as tr
        kwargs = {"preferred_langs": preferred} if preferred else {}
        with patch.object(tr._api, "list", return_value=transcript_list):
            return tr.get_manual_transcript("vid123", **kwargs)

    def test_returns_manual_de_when_available(self):
        manual_de = _make_transcript("de", is_generated=False, text="deutsch")
        gen_ja = _make_transcript("ja", is_generated=True)
        tl = _transcript_list(gen_ja, manual_de)
        text, lang = self._call(tl)
        assert lang == "de"
        assert "deutsch" in text

    def test_returns_manual_en_when_only_en_available(self):
        manual_en = _make_transcript("en", is_generated=False, text="english")
        gen_ja = _make_transcript("ja", is_generated=True)
        tl = _transcript_list(gen_ja, manual_en)
        text, lang = self._call(tl)
        assert lang == "en"

    def test_returns_none_none_when_no_manual(self):
        gen_ja = _make_transcript("ja", is_generated=True)
        tl = _transcript_list(gen_ja)
        text, lang = self._call(tl)
        assert text is None
        assert lang is None

    def test_prefers_de_over_en(self):
        manual_de = _make_transcript("de", is_generated=False)
        manual_en = _make_transcript("en", is_generated=False)
        gen_ja = _make_transcript("ja", is_generated=True)
        tl = _transcript_list(gen_ja, manual_de, manual_en)
        text, lang = self._call(tl, preferred=["de", "en"])
        assert lang == "de"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/alain/repos/youtube-abo-summarizer
python -m pytest tests/test_transcripts.py -v 2>&1 | head -40
```

Expected: errors like `TypeError: cannot unpack non-iterable` (3-tuple not returned yet) or `AttributeError` (function doesn't exist yet).

- [ ] **Step 3: Implement the changes in `transcripts.py`**

Replace `_fetch()` with `_fetch_original()` and add `_fetch_manual()`:

```python
def _fetch_original(api: YouTubeTranscriptApi, video_id: str) -> tuple[str | None, str | None, str | None]:
    """Fetch transcript in the video's original language. Returns (text, lang_code, error)."""
    try:
        transcript_list = api.list(video_id)

        # Convert to list once so we can iterate multiple times safely.
        all_transcripts = list(transcript_list)

        # Find original language via the first auto-generated transcript
        orig_lang = None
        for t in all_transcripts:
            if t.is_generated:
                orig_lang = t.language_code
                break

        if orig_lang is None:
            # No auto-generated transcript found — take first available
            t = all_transcripts[0]
            return _to_text(t.fetch()), t.language_code, None

        # Prefer manually created transcript in original language (higher quality)
        try:
            t = transcript_list.find_manually_created_transcript([orig_lang])
            return _to_text(t.fetch()), t.language_code, None
        except NoTranscriptFound:
            pass

        # Fall back to auto-generated
        t = transcript_list.find_generated_transcript([orig_lang])
        return _to_text(t.fetch()), t.language_code, None

    except IpBlocked:
        print(f"    [BLOCKED] YouTube blockiert diese IP für Transkript-Anfragen (video_id={video_id}).")
        return None, None, "ip_blocked"
    except RequestBlocked:
        print(f"    [BLOCKED] Anfrage von YouTube abgelehnt (Rate Limit?) für video_id={video_id}.")
        return None, None, "rate_limited"
    except (NoTranscriptFound, TranscriptsDisabled):
        return None, None, "unavailable"
    except VideoUnplayable as e:
        reason_lower = (e.reason or "").lower()
        if any(kw in reason_lower for kw in ("country", "region")):
            return None, None, "country_blocked"
        print(f"    [INFO] Video nicht abspielbar für video_id={video_id}: {e}")
        return None, None, "unavailable"
    except CouldNotRetrieveTranscript as e:
        print(f"    [ERROR] {type(e).__name__} für video_id={video_id}: {e}")
        return None, None, "unavailable"
    except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError) as e:
        print(f"    [ERROR] Proxy/Netzwerkfehler für video_id={video_id}: {e}")
        return None, None, "unavailable"


def _fetch_manual(api: YouTubeTranscriptApi, video_id: str, preferred_langs: list[str]) -> tuple[str | None, str | None]:
    """Fetch the best available manually created transcript in preferred_langs.

    Returns (text, lang_code) or (None, None) if none found.
    Errors are treated as "not found" — absence of a manual transcript is not an error.
    """
    try:
        transcript_list = api.list(video_id)
        for lang in preferred_langs:
            try:
                t = transcript_list.find_manually_created_transcript([lang])
                return _to_text(t.fetch()), t.language_code
            except NoTranscriptFound:
                continue
    except Exception:
        pass
    return None, None
```

Then update `get_transcript()`:

```python
def get_transcript(video_id: str) -> tuple[str | None, str | None, str | None]:
    """Return (transcript_text, lang_code, error_reason).

    Always returns the transcript in the video's original language.
    error_reason is None on success, otherwise one of:
      "ip_blocked", "rate_limited", "unavailable", "country_blocked"
    """
    text, lang, reason = _fetch_original(_api, video_id)
    if reason == "ip_blocked" and _fallback_api is not None:
        print(f"    [RETRY] IP geblockt, versuche Proxy für video_id={video_id}.")
        text, lang, reason = _fetch_original(_fallback_api, video_id)
    if reason == "country_blocked":
        if _fallback_api is not None:
            print(f"    [RETRY] Video geo-gesperrt, versuche {_FALLBACK_COUNTRY}-Proxy für video_id={video_id}.")
            text, lang, reason = _fetch_original(_fallback_api, video_id)
            if reason != "country_blocked":
                return text, lang, reason
        print(f"    [BLOCKED] Video in dieser Region gesperrt (country_blocked) für video_id={video_id}.")
    return text, lang, reason


def get_manual_transcript(video_id: str, preferred_langs: list[str] = PREFERRED_LANGS) -> tuple[str | None, str | None]:
    """Return (transcript_text, lang_code) for the best manually created DE/EN transcript.

    Returns (None, None) if no manual transcript is available in preferred_langs.
    """
    return _fetch_manual(_api, video_id, preferred_langs)
```

Also remove the old `_fetch()` function entirely.

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_transcripts.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add transcripts.py tests/test_transcripts.py
git commit -m "feat(transcripts): always fetch original language; add get_manual_transcript()"
```

---

## Task 2: Update `store.py`

**Files:**
- Modify: `store.py`
- Create: `tests/test_store_transcript_lang.py`

### Background

Changes needed:
- Add `transcript_lang TEXT` to `_MIGRATIONS`.
- Add `_resolve_transcript_path(video_id, transcript_lang)` → private helper returning first existing path among `<id>.<lang>.txt`, `<id>.txt`.
- Add `get_llm_transcript_path(video_id)` → public helper reading `transcript_lang` from DB, returning first existing path among `<id>.de.txt`, `<id>.en.txt`, `<id>.<transcript_lang>.txt`, `<id>.txt`.
- Update `get_video()`: `has_transcript` uses `_resolve_transcript_path`; add `has_manual_transcript` flag.
- Update `get_videos_since()` and `get_all_videos()`: replace hardcoded `<id>.txt` with `_resolve_transcript_path`.
- Update `add_video()`: accept `transcript_lang` key; write transcript to `<id>.<lang>.txt` instead of `<id>.txt`; store `transcript_lang` in DB.
- Update `update_video_with_summary()`: accept `transcript_lang=` kwarg; write to `<id>.<lang>.txt`; update `transcript_lang` in DB.
- Update `prune_older_than()`: glob `<id>.*.txt` + delete `<id>.txt` instead of only `<id>.txt`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_store_transcript_lang.py
"""Tests for store.py transcript_lang support."""
import json
import pytest
from datetime import datetime, timezone
from pathlib import Path


@pytest.fixture
def store_env(tmp_path, monkeypatch):
    """Patch store module to use a temporary directory."""
    import store
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "videos.db")
    monkeypatch.setattr(store, "TRANSCRIPTS_DIR", tmp_path / "transcripts")
    monkeypatch.setattr(store, "SUMMARIES_DIR", tmp_path / "summaries")
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "summaries").mkdir()
    return tmp_path


def _base_entry(vid_id="vid1", lang="ja"):
    return {
        "video_id": vid_id,
        "channel_id": "ch1",
        "channel_title": "Channel",
        "title": "Title",
        "published_at": "2026-01-01T00:00:00+00:00",
        "thumbnail_url": "https://example.com/thumb.jpg",
        "duration": "PT5M",
        "summary_model": None,
        "transcript": "Hello world",
        "transcript_lang": lang,
        "summary": None,
        "transcript_error": None,
        "tags": [],
        "collected_at": datetime.now(tz=timezone.utc).isoformat(),
    }


class TestAddVideo:
    def test_writes_lang_suffixed_transcript(self, store_env, monkeypatch):
        import store
        store.add_video(_base_entry("vid1", "ja"))
        assert (store_env / "transcripts" / "vid1.ja.txt").exists()
        assert not (store_env / "transcripts" / "vid1.txt").exists()

    def test_stores_transcript_lang_in_db(self, store_env, monkeypatch):
        import store
        store.add_video(_base_entry("vid1", "ja"))
        v = store.get_video("vid1")
        assert v["transcript_lang"] == "ja"


class TestGetVideo:
    def test_has_transcript_true_for_lang_suffixed_file(self, store_env):
        import store
        store.add_video(_base_entry("vid1", "ja"))
        v = store.get_video("vid1")
        assert v["has_transcript"] is True

    def test_has_transcript_true_for_legacy_txt(self, store_env):
        import store
        # Simulate legacy entry: no transcript_lang in DB, but <id>.txt exists
        entry = _base_entry("vid1", "ja")
        entry["transcript_lang"] = None
        entry["transcript"] = None
        store.add_video(entry)
        (store_env / "transcripts" / "vid1.txt").write_text("legacy", encoding="utf-8")
        v = store.get_video("vid1")
        assert v["has_transcript"] is True

    def test_has_manual_transcript_true_when_de_file_exists(self, store_env):
        import store
        store.add_video(_base_entry("vid1", "ja"))
        (store_env / "transcripts" / "vid1.de.txt").write_text("deutsch", encoding="utf-8")
        v = store.get_video("vid1")
        assert v["has_manual_transcript"] is True

    def test_has_manual_transcript_false_when_no_manual_file(self, store_env):
        import store
        store.add_video(_base_entry("vid1", "ja"))
        v = store.get_video("vid1")
        assert v["has_manual_transcript"] is False


class TestGetLlmTranscriptPath:
    def test_returns_de_when_de_file_exists(self, store_env):
        import store
        store.add_video(_base_entry("vid1", "ja"))
        de_path = store_env / "transcripts" / "vid1.de.txt"
        de_path.write_text("deutsch", encoding="utf-8")
        result = store.get_llm_transcript_path("vid1")
        assert result == de_path

    def test_returns_orig_lang_when_no_manual(self, store_env):
        import store
        store.add_video(_base_entry("vid1", "ja"))
        result = store.get_llm_transcript_path("vid1")
        assert result == store_env / "transcripts" / "vid1.ja.txt"

    def test_returns_legacy_txt_when_no_lang_in_db(self, store_env):
        import store
        entry = _base_entry("vid1", "ja")
        entry["transcript_lang"] = None
        entry["transcript"] = None
        store.add_video(entry)
        legacy = store_env / "transcripts" / "vid1.txt"
        legacy.write_text("legacy", encoding="utf-8")
        result = store.get_llm_transcript_path("vid1")
        assert result == legacy


class TestPruneOlderThan:
    def test_deletes_lang_suffixed_and_manual_files(self, store_env):
        import store
        entry = _base_entry("vid1", "ja")
        entry["published_at"] = "2020-01-01T00:00:00+00:00"
        store.add_video(entry)
        de_path = store_env / "transcripts" / "vid1.de.txt"
        de_path.write_text("deutsch", encoding="utf-8")
        store.prune_older_than(days=1)
        assert not (store_env / "transcripts" / "vid1.ja.txt").exists()
        assert not de_path.exists()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_store_transcript_lang.py -v 2>&1 | head -40
```

Expected: mostly `KeyError: 'transcript_lang'` or `AssertionError` failures.

- [ ] **Step 3: Implement all `store.py` changes**

**Add to `_MIGRATIONS`:**
```python
("transcript_lang", "ALTER TABLE videos ADD COLUMN transcript_lang TEXT"),
```

**Add private helper (after the `_conn()` function):**
```python
def _resolve_transcript_path(video_id: str, transcript_lang: str | None) -> Path | None:
    """Return the path to the original-language transcript file, or None if not found.

    Checks <id>.<lang>.txt first, then falls back to legacy <id>.txt.
    """
    if transcript_lang:
        p = TRANSCRIPTS_DIR / f"{video_id}.{transcript_lang}.txt"
        if p.exists():
            return p
    legacy = TRANSCRIPTS_DIR / f"{video_id}.txt"
    return legacy if legacy.exists() else None
```

**Add public helper (after `_resolve_transcript_path`):**
```python
def get_llm_transcript_path(video_id: str) -> Path | None:
    """Return the best transcript path for LLM input.

    Priority: <id>.de.txt → <id>.en.txt → <id>.<transcript_lang>.txt → <id>.txt.
    Returns None if no transcript file exists.
    """
    row = _conn().execute(
        "SELECT transcript_lang FROM videos WHERE video_id = ?", (video_id,)
    ).fetchone()
    transcript_lang = row["transcript_lang"] if row else None

    for lang in ["de", "en"]:
        p = TRANSCRIPTS_DIR / f"{video_id}.{lang}.txt"
        if p.exists():
            return p
    return _resolve_transcript_path(video_id, transcript_lang)
```

**Update `get_video()`** — replace the two `has_*` lines:
```python
t_path = _resolve_transcript_path(video_id, d.get("transcript_lang"))
d["has_transcript"] = t_path is not None
d["has_manual_transcript"] = any(
    (TRANSCRIPTS_DIR / f"{video_id}.{lang}.txt").exists()
    for lang in ["de", "en"]
)
d["has_summary"] = (SUMMARIES_DIR / f"{video_id}.html").exists()
```

**Update `get_videos_since()` and `get_all_videos()`** — replace hardcoded `t_path`:
```python
t_path = _resolve_transcript_path(d["video_id"], d.get("transcript_lang"))
d["transcript"] = t_path.read_text(encoding="utf-8") if t_path else None
```

**Update `add_video()`** — add `transcript_lang` to INSERT and change file write.

> **Important:** The existing `add_video()` wraps the INSERT in `try/except sqlite3.IntegrityError: return False`. Preserve that guard — the snippet below shows only the changed parts.

```python
# In INSERT: add transcript_lang column and value
c.execute(
    """INSERT INTO videos
       (video_id, channel_id, channel_title, title, published_at,
        thumbnail_url, duration, summary_model, transcript_error, tags,
        transcript_lang, collected_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    (
        entry["video_id"], entry["channel_id"], entry["channel_title"],
        entry["title"], entry["published_at"], entry["thumbnail_url"],
        entry.get("duration"), entry.get("summary_model"),
        entry.get("transcript_error"), tags_json,
        entry.get("transcript_lang"), entry["collected_at"],
    ),
)
# ... keep existing except sqlite3.IntegrityError: return False block ...

# File write: use lang-suffixed name if lang known, else legacy
if entry.get("transcript"):
    lang = entry.get("transcript_lang")
    fname = f"{entry['video_id']}.{lang}.txt" if lang else f"{entry['video_id']}.txt"
    (TRANSCRIPTS_DIR / fname).write_text(entry["transcript"], encoding="utf-8")
```

**Update `update_video_with_summary()`** — add `transcript_lang=` kwarg:
```python
def update_video_with_summary(
    video_id: str,
    transcript: str | None,
    summary: str | None,
    transcript_error: str | None,
    summary_model: str | None = None,
    tags: list[str] | None = None,
    transcript_lang: str | None = None,
) -> None:
    tags_json = json.dumps(tags) if tags else None
    with _conn() as c:
        c.execute(
            """UPDATE videos
               SET transcript_error = ?, summary_model = ?, tags = ?,
                   transcript_lang = COALESCE(?, transcript_lang)
               WHERE video_id = ?""",
            (transcript_error, summary_model, tags_json, transcript_lang, video_id),
        )
    if transcript is not None:
        lang = transcript_lang
        fname = f"{video_id}.{lang}.txt" if lang else f"{video_id}.txt"
        (TRANSCRIPTS_DIR / fname).write_text(transcript, encoding="utf-8")
    if summary is not None:
        (SUMMARIES_DIR / f"{video_id}.html").write_text(summary, encoding="utf-8")
```

**Update `prune_older_than()`** — glob all language-variant files:
```python
for vid_id in video_ids:
    # Delete all language-variant transcript files
    for p in TRANSCRIPTS_DIR.glob(f"{vid_id}.*.txt"):
        p.unlink(missing_ok=True)
    (TRANSCRIPTS_DIR / f"{vid_id}.txt").unlink(missing_ok=True)
    (SUMMARIES_DIR / f"{vid_id}.html").unlink(missing_ok=True)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_store_transcript_lang.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 6: Add test for `get_all_videos()` reading lang-suffixed transcript**

Add to `tests/test_store_transcript_lang.py`:

```python
class TestGetAllVideos:
    def test_reads_lang_suffixed_transcript(self, store_env):
        import store
        store.add_video(_base_entry("vid1", "ja"))
        videos = store.get_all_videos()
        assert len(videos) == 1
        assert videos[0]["transcript"] == "Hello world"

    def test_reads_legacy_txt_transcript(self, store_env):
        import store
        entry = _base_entry("vid1", "ja")
        entry["transcript_lang"] = None
        entry["transcript"] = None
        store.add_video(entry)
        (store_env / "transcripts" / "vid1.txt").write_text("legacy text", encoding="utf-8")
        videos = store.get_all_videos()
        assert videos[0]["transcript"] == "legacy text"
```

Run:
```bash
python -m pytest tests/test_store_transcript_lang.py -v
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add store.py tests/test_store_transcript_lang.py
git commit -m "feat(store): add transcript_lang column, path helpers, update write/prune"
```

---

## Task 3: Update `collect.py`

**Files:**
- Modify: `collect.py`

### Background

There are two nearly identical code paths:
1. `_process_single_video()` (~lines 110–168) — used for `--video` flag
2. The bulk loop (~lines 240–300) — used for channel/subscription processing

Both need the same three changes:
- **Existing-transcript branch**: replace hardcoded `<id>.txt` read with `store.get_llm_transcript_path(vid_id)`.
- **New-transcript branch**: unpack 3-tuple from `tr.get_transcript()`; write to `<id>.<lang>.txt`; call `tr.get_manual_transcript()` if `lang not in ["de", "en"]`.
- **Store calls**: pass `transcript_lang=lang` to `update_video_with_summary()` and `transcript_lang` key to `add_video()`.

**Who writes what:** `collect.py` does NOT write the original transcript file directly — it passes `transcript=` and `transcript_lang=` to `store.add_video()` / `store.update_video_with_summary()`, and those functions write `<id>.<lang>.txt`. The spec's `collect.py` pseudocode shows an explicit file write; that is illustrative and is superseded by the store-side write implemented in Task 2. Do NOT add an explicit `path.write_text()` call for the original transcript in `collect.py`.

`collect.py` IS responsible for writing the **manual** transcript file directly (since store has no concept of a second transcript). So `collect.py` only needs to:
1. Unpack the 3-tuple from `get_transcript()`.
2. Call `get_manual_transcript()` and write the manual file itself.
3. Use `store.get_llm_transcript_path()` for the LLM input.
4. Pass `transcript_lang=lang` through to store calls.

**Variable naming convention:** `transcript` holds the raw original-language text (used for the `if transcript` guard and passed to `store`). `llm_input` holds the text actually sent to the LLM (from `get_llm_transcript_path()`, which may be the manual file). These are always read from `get_llm_transcript_path()` immediately before the summarize call — do not pass `transcript` directly to `summarize_video()`.

- [ ] **Step 1: Update `_process_single_video()`**

Replace the transcript-fetch block (roughly lines 124–141):

```python
# Fetch transcript only if not already stored
lang = None
if existing and existing["has_transcript"]:
    llm_path = store.get_llm_transcript_path(vid_id)
    transcript = llm_path.read_text(encoding="utf-8") if llm_path else None
    lang = existing.get("transcript_lang")
    transcript_error = existing.get("transcript_error")
else:
    transcript, lang, transcript_error = tr.get_transcript(vid_id)
    if not transcript:
        if not transcript_error or transcript_error == "unavailable":
            print("    No transcript available.")
        elif transcript_error == "country_blocked":
            print("    Video in dieser Region gesperrt — kein Transkript.")
    elif lang not in ["de", "en"]:
        # Fetch manual DE/EN transcript as a second file
        manual, manual_lang = tr.get_manual_transcript(vid_id)
        if manual:
            (store.TRANSCRIPTS_DIR / f"{vid_id}.{manual_lang}.txt").write_text(
                manual, encoding="utf-8"
            )

# Use LLM-optimal transcript (manual DE/EN if available, else original)
if transcript and (not existing or not existing["has_summary"]):
    llm_path = store.get_llm_transcript_path(vid_id)
    llm_input = llm_path.read_text(encoding="utf-8") if llm_path else transcript
    print(f"    Summarizing via {model}...")
    summary, tags = openrouter.summarize_video(vid_id, vid_title, llm_input, model)
```

Update the `store.update_video_with_summary()` call to pass `transcript_lang`:
```python
store.update_video_with_summary(
    vid_id,
    transcript if not existing["has_transcript"] else None,
    summary,
    transcript_error,
    model if summary else existing.get("summary_model"),
    tags=tags,
    transcript_lang=lang,
)
```

Update the `store.add_video()` call to include `transcript_lang`:
```python
return store.add_video({
    ...
    "transcript_lang": lang,
    ...
})
```

- [ ] **Step 2: Apply identical changes to the bulk loop**

The bulk loop (roughly lines 248–300) has the same structure. Apply the same changes:
- Replace hardcoded `<id>.txt` read.
- Unpack 3-tuple from `tr.get_transcript()`.
- Call `tr.get_manual_transcript()` and write manual file.
- Pass `transcript_lang=lang` to store calls.

- [ ] **Step 3: Run the existing test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS (collect.py has no unit tests, but regressions in other modules would surface here).

- [ ] **Step 4: Commit**

```bash
git add collect.py
git commit -m "feat(collect): use original-language transcript, fetch manual DE/EN when available"
```

---

## Task 4: Update `repair.py` and `summarize.py`

**Files:**
- Modify: `repair.py`
- Modify: `summarize.py`

### `repair.py` changes

The transcript-fetch block (roughly lines 121–133) unpacks a 2-tuple. Update to 3-tuple and pass `transcript_lang` to store.

> **Important:** `store.update_video_with_summary()` (updated in Task 2) already writes the transcript file internally when `transcript=` is provided and `transcript_lang=` is set. Do NOT add a separate explicit `path.write_text()` call in `repair.py` — it would write the file twice (harmless but redundant, and inconsistent with collect.py). The store call is the single source of truth for writing transcript files.

```python
transcript, lang, t_error = tr.get_transcript(vid_id)
time.sleep(2)
if transcript:
    n_transcript_ok += 1
    store.update_video_with_summary(vid_id, transcript, None, t_error,
                                    transcript_lang=lang)
else:
    print(f"    Still unavailable: {t_error or 'unavailable'}")
    store.update_video_with_summary(vid_id, None, None, t_error)
    n_transcript_fail += 1
    continue
```

The summarize step (roughly line 143) currently uses `transcript` directly. Replace with LLM-optimal path so the manual DE/EN file is used if it exists:

```python
if needs_summary or needs_transcript:
    print(f"    Summarizing via {model}...")
    llm_path = store.get_llm_transcript_path(vid_id)
    llm_input = llm_path.read_text(encoding="utf-8") if llm_path else transcript
    summary, tags = openrouter.summarize_video(vid_id, vid_title, llm_input, model)
    store.update_video_with_summary(
        vid_id, None, summary, t_error, summary_model=model, tags=tags
    )
    n_summarized += 1
```

Also update the initial `transcript = entry["transcript"]` line (line 119). After Task 2 the bulk-read functions return transcript text from `_resolve_transcript_path`, so this still works. No change needed there — but note the variable is used in `if transcript is None` guard (line 136), which remains correct.

### `summarize.py` changes

Line 163 — unpack 3-tuple and discard lang:

```python
transcript, _lang, transcript_error = tr.get_transcript(vid_id)
```

- [ ] **Step 1: Update `repair.py`** as described above.

- [ ] **Step 2: Update `summarize.py`** — change the one-line unpack at line 163.

- [ ] **Step 3: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 4: Smoke-test the import chain**

```bash
python -c "import collect; import repair; import summarize; print('imports OK')"
```

Expected: `imports OK`

- [ ] **Step 5: Commit**

```bash
git add repair.py summarize.py
git commit -m "fix(repair,summarize): unpack 3-tuple from get_transcript(); use get_llm_transcript_path()"
```

---

## Done

After all four tasks pass, the pipeline:

1. Always fetches the original-language transcript and stores it as `<id>.<lang>.txt`
2. For non-DE/EN originals, also fetches a manually created DE/EN transcript and stores it as `<id>.de.txt` or `<id>.en.txt`
3. The LLM summarizes from the manual DE/EN file when available, otherwise from the original
4. Legacy `<id>.txt` files continue to work throughout
