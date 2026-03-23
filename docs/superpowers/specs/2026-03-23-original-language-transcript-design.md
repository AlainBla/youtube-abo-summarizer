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
(without language suffix) remain valid and are treated as the original transcript.

## Components

### `transcripts.py`

**`get_transcript(video_id)`** — reworked to always return the original language:

1. Call `transcript_list = api.list(video_id)`.
2. Find the auto-generated transcript (first one that `is_generated == True`) to determine
   the original language code.
3. Try to find a manually created transcript in that same language code.
4. Fall back to the auto-generated transcript.
5. Return `(text, lang_code, error_reason)` — adds `lang_code` to the existing tuple.

Proxy/retry logic (ip_blocked, country_blocked) remains unchanged.

**`get_manual_transcript(video_id, preferred_langs=["de", "en"])`** — new function:

1. Call `transcript_list = api.list(video_id)`.
2. For each lang in `preferred_langs`: try `find_manually_created_transcript([lang])`.
3. Return `(text, lang_code)` if found, `(None, None)` otherwise.
4. No error return needed — absence of a manual transcript is not an error.
5. No proxy retry needed (same network conditions as `get_transcript`).

### `store.py`

**New DB column:** `transcript_lang TEXT` — stores the original transcript's language code.
Added to `_MIGRATIONS` list.

**`has_transcript` logic** (in `get_video()`):
- Check `transcript_lang IS NOT NULL` in DB.
- Check file exists: `<id>.<lang>.txt`, fallback to `<id>.txt`.

**`has_manual_transcript` flag** (in `get_video()`):
- Scan for `<id>.de.txt`, `<id>.en.txt` (in preferred order) in `TRANSCRIPTS_DIR`.

**`get_llm_transcript_path(video_id)`** — new helper:
- Check `<id>.de.txt` → `<id>.en.txt` → `<id>.<transcript_lang>.txt` → `<id>.txt`.
- Returns the first path that exists.

**`update_video_with_summary()`** — gains `transcript_lang=` kwarg (stored in DB).

**`add_video()`** — gains `transcript_lang=` key in the data dict.

### `collect.py`

Changes applied at both the single-video path (`--video`) and the bulk path.

**Transcript fetch:**
```python
transcript, lang, transcript_error = tr.get_transcript(vid_id)
if transcript:
    path = store.TRANSCRIPTS_DIR / f"{vid_id}.{lang}.txt"
    path.write_text(transcript, encoding="utf-8")
```

**Manual transcript fetch** (only when a fresh transcript was just fetched or no manual
file exists yet):
```python
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

**Skip logic** — unchanged: `has_transcript AND has_summary` → skip entirely.

**Existing videos without manual transcript:** not retroactively fetched during collect.
Use `repair.py` to backfill (out of scope for this change).

## Data Flow

```
collect.py
  └─ tr.get_transcript(id)        → (text, lang, error)
       writes: <id>.<lang>.txt
  └─ tr.get_manual_transcript(id) → (text, lang) | (None, None)
       writes: <id>.<lang>.txt    (e.g. <id>.de.txt)
  └─ store.get_llm_transcript_path(id)
       returns: <id>.de.txt | <id>.en.txt | <id>.<orig_lang>.txt | <id>.txt
  └─ openrouter.summarize_video(id, title, llm_input, model)
```

## Out of Scope

- `repair.py` backfill of manual transcripts for existing videos (separate task).
- `summarize.py` (legacy all-in-one): only minimal update — unpack 3-tuple from
  `get_transcript()`, discard `lang_code`.
- Export/report UI: no changes (transcript files are not surfaced to the UI).

## Backward Compatibility

- Existing `<id>.txt` files: recognised as before via fallback in `get_video()` and
  `get_llm_transcript_path()`.
- `transcript_lang` column: added via migration; NULL for pre-existing rows (treated as
  legacy, `<id>.txt` path used).
- `repair.py` callers of `get_transcript()`: must unpack 3-tuple (minor update required).
