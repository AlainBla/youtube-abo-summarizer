# Video Title Filter Design

## Overview
Add the ability to filter out videos based on title patterns before they are processed and stored. This prevents unwanted content (e.g., sponsored videos, advertisements) from entering the database.

## Configuration
Add to `.env`:
```
VIDEO_TITLE_FILTERS=\(Werbung\)|Anzeige|Sponsor.*paid
```

- Comma-separated regex patterns
- Case-insensitive matching (`re.IGNORECASE`)
- Empty or missing variable means no filtering

## Implementation

### 1. collect.py
Location: Main loop (~line 264) — after receiving video list, before transcript fetch.

Pseudo-code:
```python
import re

def _should_filter_title(title: str) -> bool:
    patterns = os.environ.get("VIDEO_TITLE_FILTERS", "")
    if not patterns:
        return False
    try:
        for pattern in patterns.split(","):
            if re.search(pattern.strip(), title, re.IGNORECASE):
                return True
    except re.error as e:
        print(f"Invalid regex in VIDEO_TITLE_FILTERS: {e}", file=sys.stderr)
        sys.exit(1)
    return False
```

In the video loop:
```python
for video in videos:
    if _should_filter_title(video["title"]):
        print(f"    → Titel ignoriert (Filter match)")
        continue
    # ... rest of processing
```

### 2. repair.py
**Do not apply filter** — repair is for targeted re-operations, user explicitly requests specific videos.

### 3. .env.example
Add documentation:
```
# Optional: regex patterns to filter video titles (case-insensitive, comma-separated)
# Matching videos are skipped during collect. Example: \(Werbung\)|Anzeige|Sponsor.*paid
VIDEO_TITLE_FILTERS=
```

## Edge Cases
- Empty/None title: allow (no match)
- Invalid regex: catch `re.error`, print error, exit with code 1
- Video already in store with filter added later: not affected (existing entries remain)

## Testing
1. Title "(Werbung) Mein Video" → skipped
2. Title "WERBUNG: Test" → skipped (case-insensitive)
3. Title "Video mit Anzeige" → skipped
4. Title "Normales Video" → processed
5. Empty VIDEO_TITLE_FILTERS → all processed

## Files to Modify
1. `collect.py` — add filter logic
2. `.env.example` — add documentation
