# Video Title Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SSKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add regex-based video title filtering to collect.py to skip videos matching configurable patterns before processing.

**Architecture:** New filter function reads `VIDEO_TITLE_FILTERS` from environment, compiles regex patterns once, and checks each video title case-insensitively before transcript fetch.

**Tech Stack:** Python `re` module, `os.environ`

---

### Task 1: Add filter function to collect.py

**Files:**
- Modify: `collect.py:38-40` (imports) and `collect.py:260-275` (video loop)

- [ ] **Step 1: Add import for `re`**

At line ~38 (after existing imports), add:
```python
import re
```

- [ ] **Step 2: Add filter function after imports**

After the imports section (around line 40), add:
```python
def _should_filter_title(title: str) -> tuple[bool, str]:
    """Check if title matches any VIDEO_TITLE_FILTERS pattern.
    
    Returns (should_filter, matched_pattern) tuple.
    """
    patterns = os.environ.get("VIDEO_TITLE_FILTERS", "")
    if not patterns:
        return False, ""
    
    for pattern in patterns.split(","):
        pattern = pattern.strip()
        if not pattern:
            continue
        try:
            if re.search(pattern, title, re.IGNORECASE):
                return True, pattern
        except re.error as e:
            print(f"Invalid regex in VIDEO_TITLE_FILTERS: {e}", file=sys.stderr)
            sys.exit(1)
    return False, ""
```

- [ ] **Step 3: Add filter check in video loop**

In the video loop (around line 268, after `vid_title = video["title"]`), add:
```python
if _should_filter_title(vid_title)[0]:
    print(f"    → Titel ignoriert (Filter match: '{_should_filter_title(vid_title)[1]}')")
    continue
```

- [ ] **Step 4: Commit**

```bash
git add collect.py
git commit -m "feat(collect): add VIDEO_TITLE_FILTERS for skipping videos by title regex"
```

---

### Task 2: Update .env.example with documentation

**Files:**
- Modify: `.env.example:42-43` (end of file)

- [ ] **Step 1: Add filter documentation**

Add at end of `.env.example`:
```bash
# Optional: regex patterns to filter video titles (case-insensitive, comma-separated)
# Matching videos are skipped during collect. Example: \(Werbung\)|Anzeige|Sponsor.*paid
VIDEO_TITLE_FILTERS=
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "docs(.env.example): add VIDEO_TITLE_FILTERS documentation"
```

---

### Task 3: Test the implementation

**Files:**
- Test: Manual testing with sample patterns

- [ ] **Step 1: Create test pattern in .env**

Add to your `.env`:
```
VIDEO_TITLE_FILTERS=\(Werbung\),Anzeige
```

- [ ] **Step 2: Run collect to verify filter works**

Run a small collect and check that videos with "(Werbung" or "Anzeige" in title are skipped.

- [ ] **Step 3: Verify filter is case-insensitive**

The regex with `re.IGNORECASE` should match "(werbung", "WERBUNG", etc.

- [ ] **Step 4: Test invalid regex handling**

Set `VIDEO_TITLE_FILTERS=[invalid` and run collect — should exit with error message.

- [ ] **Step 5: Commit any test changes (optional)**

---

## Summary

This adds 3 tasks:
1. Add filter function to collect.py
2. Document in .env.example
3. Manual testing

**Files modified:**
- `collect.py` — add `_should_filter_title()` function and filter check
- `.env.example` — add documentation
