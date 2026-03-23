# Design: Original-Language Transcript + Manual DE/EN Fallback

**Date:** 2026-03-23
**Status:** Approved

## Goal

Always fetch the transcript in the video's original language. Additionally, if a manually
created German or English transcript exists, fetch and store it as a second file. The LLM
receives the manual DE/EN transcript when available, otherwise the original.

## File Convention

```
data/transcripts/<video_id>.<orig_lang>.txt   — original language (e.g. abc123.ja.txt)
data/transcripts/<video_id>.<manual_lang>.txt — manual DE/EN (e.g. abc123.de.txt)
data/transcripts/<video_id>.txt               — legacy format, still recognised
```

Two files may exist for the same video. Backward compatibility: existing `<id>.txt` files
(without language suffix) remain valid throughout.

## Components

### `transcripts.py`

**`get_transcript(video_id)`** — reworked to always return the original language:

1. Call `transcript_list = api.list(video_id)`.
2. Find the first auto-generated transcript to determine the original language code.
3. Try to find a manually created transcript in that same language code.
4. Fall back to the auto-generated transcript.
5. Return `(text, lang_code, error_reason)` — adds `lang_code` (string or None) to the
   existing tuple.

Proxy/retry logic (ip_blocked, country_blocked) remains unchanged.

**`get_manual_transcript(video_id, preferred_langs=["de", "en"])`** — new function:

1. Call `transcript_list = api.list(video_id)`.
2. For each lang in `preferred_langs`: try `find_manually_created_transcript([lang])`.
3. Return `(text, lang_code)` if found, `(None, None)` otherwise.
4. No error return needed — absence of a manual transcript is not an error.
5. No proxy retry needed (same network conditions as `get_transcript`).

**Collision when original lang is `de` or `en`:** if `get_transcript()` returns
`lang_code` that is already in `preferred_langs`, the caller skips the
`get_manual_transcript()` call entirely. The original file already uses the preferred
language, so a second fetch would be redundant and might overwrite a manually created
transcript with an auto-generated one.

### `store.py`

**New DB column:** `transcript_lang TEXT` — stores the original transcript's language
code. Added to `_MIGRATIONS` list.

**`_resolve_transcript_path(video_id, transcript_lang)`** — private helper used by bulk
readers:
- Returns the first existing path in: `<id>.<transcript_lang>.txt` → `<id>.txt`.
- Used by `get_videos_since` and `get_all_videos` to load transcript text, replacing the
  current hardcoded `<id>.txt`.

**`get_llm_transcript_path(video_id)`** — new public helper for the summarization input:
- Reads `transcript_lang` from DB for the video.
- Returns the first existing path in:
  `<id>.de.txt` → `<id>.en.txt` → `<id>.<transcript_lang>.txt` → `<id>.txt`.

**`has_transcript` logic** (in `get_video()`):
- True when `transcript_lang IS NOT NULL` in DB and `_resolve_transcript_path()` exists,
  OR when the legacy `<id>.txt` exists (for pre-existing rows where `transcript_lang` is
  NULL).

**`has_manual_transcript` flag** (in `get_video()`):
- Scan for `<id>.de.txt`, `<id>.en.txt` in `TRANSCRIPTS_DIR`; True if any exists.

**`get_videos_since()` and `get_all_videos()`:**
- Replace hardcoded `TRANSCRIPTS_DIR / f"{vid_id}.txt"` with
  `_resolve_transcript_path(vid_id, d.get("transcript_lang"))` so that newly-named
  language-suffixed files are loaded correctly.

**`update_video_with_summary()`** — gains `transcript_lang=` kwarg (stored in DB).

**`add_video()`** — gains `transcript_lang=` key in the data dict.

**`prune_older_than()`:**
- For each video to be pruned, delete all files matching `<id>.*.txt` (glob) plus the
  legacy `<id>.txt` in `TRANSCRIPTS_DIR`, as well as the summary `<id>.html`.
- Replaces the current hardcoded single-file deletion.

### `collect.py`

Changes applied at both the single-video path (`--video`) and the bulk path.

**Transcript fetch (new video or missing transcript):**
```python
transcript, lang, transcript_error = tr.get_transcript(vid_id)
if transcript:
    path = store.TRANSCRIPTS_DIR / f"{vid_id}.{lang}.txt"
    path.write_text(transcript, encoding="utf-8")
```

**Read existing transcript (video already has transcript in store):**
```python
llm_path = store.get_llm_transcript_path(vid_id)
transcript = llm_path.read_text(encoding="utf-8")
lang = existing.get("transcript_lang")
transcript_error = existing.get("transcript_error")
```
Replaces the current hardcoded `(store.TRANSCRIPTS_DIR / f"{vid_id}.txt").read_text()`.

**Manual transcript fetch** (only when original lang is NOT already in preferred langs):
```python
if lang not in ["de", "en"]:
    manual, manual_lang = tr.get_manual_transcript(vid_id)
    if manual:
        path = store.TRANSCRIPTS_DIR / f"{vid_id}.{manual_lang}.txt"
        path.write_text(manual, encoding="utf-8")
```

**LLM input:**
```python
llm_path = store.get_llm_transcript_path(vid_id)
llm_input = llm_path.read_text(encoding="utf-8")
summary, tags = openrouter.summarize_video(vid_id, vid_title, llm_input, model)
```

**Skip logic** — unchanged: `has_transcript AND has_summary` → skip entirely. A video
with an existing transcript but no manual file is not re-processed by collect; use
`repair.py` for backfill.

### `repair.py`

`get_transcript()` now returns a 3-tuple; update accordingly:

```python
transcript, lang, t_error = tr.get_transcript(vid_id)
if transcript:
    path = store.TRANSCRIPTS_DIR / f"{vid_id}.{lang}.txt"
    path.write_text(transcript, encoding="utf-8")
    store.update_video_with_summary(vid_id, transcript, None, t_error,
                                    transcript_lang=lang)
```

The `transcript` variable passed to `openrouter.summarize_video()` in repair.py should
be read via `store.get_llm_transcript_path(vid_id).read_text()` after the fetch, so the
LLM gets the manual DE/EN file if one exists.

### `summarize.py` (legacy)

Minimal update — unpack 3-tuple and discard `lang`:

```python
transcript, _lang, transcript_error = tr.get_transcript(vid_id)
```

## Data Flow

```
collect.py
  └─ tr.get_transcript(id)             → (text, lang, error)
       writes: <id>.<lang>.txt
  └─ tr.get_manual_transcript(id)      → (text, lang) | (None, None)
       writes: <id>.<lang>.txt          (skipped if lang already in ["de","en"])
  └─ store.get_llm_transcript_path(id)
       returns: <id>.de.txt | <id>.en.txt | <id>.<orig_lang>.txt | <id>.txt
  └─ openrouter.summarize_video(id, title, llm_input, model)
```

## Out of Scope

- `repair.py` backfill of manual transcripts for existing videos (separate task).
- Export/report UI: no changes (transcript files are not surfaced to the UI).

## Backward Compatibility

- Existing `<id>.txt` files: recognised via fallback in `_resolve_transcript_path()`,
  `get_llm_transcript_path()`, and `has_transcript` logic.
- `transcript_lang` column: added via migration; NULL for pre-existing rows. When NULL,
  `_resolve_transcript_path()` skips the lang-suffixed check and falls back to `<id>.txt`.
