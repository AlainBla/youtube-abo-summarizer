# Export UI Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a date filter, descriptive labels for all dropdowns, and flags in the language selector to `export.html.j2`.

**Architecture:** All changes are contained in `export.html.j2`. No backend or Python changes needed. The template is rendered once at export time and shipped as a self-contained HTML file; all filtering/sorting is pure client-side JS. There are no automated tests for this file — verification is done by running `python export.py` and inspecting the output in a browser.

**Tech Stack:** Jinja2 template, vanilla JS, embedded CSS, HTML5 `<input type="date">`.

---

### Task 1: Add CSS for control groups, labels, and date input

**Files:**
- Modify: `export.html.j2` (CSS section, inside `<style>`, after the `.controls-bar select` block around line 56)

**Step 1: Add the new CSS rules**

Insert the following block immediately after the `.controls-bar select:focus, .lang-select:focus { border-color: #555; }` rule (around line 68):

```css
    /* ── Control groups (label + select/input pairs) ── */
    .control-group {
      display: flex;
      align-items: center;
      gap: 0.35rem;
    }
    .ctrl-label {
      font-size: 0.8rem;
      color: #666;
      white-space: nowrap;
    }
    .controls-bar input[type="date"] {
      background: #1a1a1a;
      border: 1px solid #2a2a2a;
      border-radius: 6px;
      color: #e0e0e0;
      font-size: 0.9rem;
      padding: 0.45rem 0.5rem;
      outline: none;
      cursor: pointer;
      color-scheme: dark;
    }
    .controls-bar input[type="date"]:focus { border-color: #555; }
    .controls-bar input[type="date"]::-webkit-calendar-picker-indicator { filter: invert(0.7); }
```

Note: `color-scheme: dark` makes the native date picker use dark UI in supporting browsers. The `filter: invert(0.7)` makes the calendar icon visible on a dark background.

**Step 2: Verify CSS is syntactically correct**

Open `export.html.j2` in an editor and confirm the new block is inside `<style>…</style>` and has matching braces. No test runner — just a visual check.

---

### Task 2: Restructure the HTML controls bar

**Files:**
- Modify: `export.html.j2` (HTML section, `<div class="controls-bar">…</div>`, lines 287–312)

**Step 1: Replace the controls bar HTML**

Replace the entire `<div class="controls-bar">…</div>` block with the following. Note the changes:
- Each `<select>` is wrapped in a `<div class="control-group">` together with a `<label>`.
- The date filter `<input type="date">` is added as the first grouped control (after the search input).
- Default option text for channel-filter, tag-filter, read-filter, and bookmark-filter changes from "Alle Kanäle"/"Alle Tags"/"Alle Videos" to just "Alle" (the label provides the context).
- The `<select id="sort">` options are unchanged (already descriptive).

```html
<div class="controls-bar">
  <input type="search" id="search" placeholder="Suchen&hellip;" autocomplete="off">
  <div class="control-group">
    <label class="ctrl-label" for="date-filter" id="label-date">Ab</label>
    <input type="date" id="date-filter">
  </div>
  <div class="control-group">
    <label class="ctrl-label" for="channel-filter" id="label-channel">Kanal</label>
    <select id="channel-filter">
      <option value="">Alle</option>
    </select>
  </div>
  <div class="control-group">
    <label class="ctrl-label" for="tag-filter" id="label-tag">Tag</label>
    <select id="tag-filter">
      <option value="">Alle</option>
    </select>
  </div>
  <div class="control-group">
    <label class="ctrl-label" for="read-filter" id="label-read">Gelesen</label>
    <select id="read-filter">
      <option value="">Alle</option>
      <option value="unread">Nur ungelesen</option>
      <option value="read">Nur gelesen</option>
    </select>
  </div>
  <div class="control-group">
    <label class="ctrl-label" for="bookmark-filter" id="label-bookmark">Merken</label>
    <select id="bookmark-filter">
      <option value="">Alle</option>
      <option value="bookmarked">Nur gemerkt</option>
    </select>
  </div>
  <div class="control-group">
    <label class="ctrl-label" for="sort" id="label-sort">Sortierung</label>
    <select id="sort">
      <option value="date-desc">Neueste zuerst</option>
      <option value="date-asc">&Auml;lteste zuerst</option>
      <option value="channel">Kanal A&ndash;Z</option>
      <option value="title">Titel A&ndash;Z</option>
    </select>
  </div>
  <span class="results-count" id="results-count"></span>
  <span class="results-count" id="key-hint" style="color:#444">j/k &mdash; vor/zur&uuml;ck</span>
</div>
```

**Step 2: Update the language selector to include flags**

Find the `<select id="lang-select">` block (around line 280) and replace its options:

Old:
```html
      <option value="de">Deutsch</option>
      <option value="en">English</option>
```

New:
```html
      <option value="de">&#x1F1E9;&#x1F1EA; Deutsch</option>
      <option value="en">&#x1F1EC;&#x1F1E7; English</option>
```

(Using HTML entities for flag emoji to avoid encoding issues: `&#x1F1E9;&#x1F1EA;` = 🇩🇪, `&#x1F1EC;&#x1F1E7;` = 🇬🇧.)

---

### Task 3: Update the I18N dicts

**Files:**
- Modify: `export.html.j2` (JS section, `const I18N = { de: {…}, en: {…} };`, lines 330–395)

**Step 1: Add label keys and update "Alle" values in the `de` dict**

In the `de` object, add the following new keys after `allVideos`:

```javascript
    allVideos: 'Alle',
```

Change the values of these existing keys:
```javascript
    allChannels: 'Alle',
    allTags: 'Alle',
    allVideos: 'Alle',
```

Add new label keys (insert after `allVideos`):
```javascript
    labelFrom: 'Ab',
    labelChannel: 'Kanal',
    labelTag: 'Tag',
    labelRead: 'Gelesen',
    labelBookmark: 'Merken',
    labelSort: 'Sortierung',
```

**Step 2: Apply the same changes to the `en` dict**

Change existing values:
```javascript
    allChannels: 'All',
    allTags: 'All',
    allVideos: 'All',
```

Add new label keys:
```javascript
    labelFrom: 'From',
    labelChannel: 'Channel',
    labelTag: 'Tag',
    labelRead: 'Read',
    labelBookmark: 'Bookmark',
    labelSort: 'Sort',
```

---

### Task 4: Extend `applyLang()` to update label text

**Files:**
- Modify: `export.html.j2` (JS `applyLang` function, around line 469)

**Step 1: Find the existing label-update block in `applyLang()`**

The function currently updates the first option of `channel-filter` and `tag-filter`:
```javascript
  document.querySelector('#channel-filter option[value=""]').textContent = s.allChannels;
  document.querySelector('#tag-filter option[value=""]').textContent = s.allTags;
```

**Step 2: Add label updates after the read-filter/bookmark-filter/sort blocks**

After the `// sort` block (which updates `sortOpts[0..3].textContent`), add:

```javascript
  // control labels
  document.getElementById('label-date').textContent = s.labelFrom;
  document.getElementById('label-channel').textContent = s.labelChannel;
  document.getElementById('label-tag').textContent = s.labelTag;
  document.getElementById('label-read').textContent = s.labelRead;
  document.getElementById('label-bookmark').textContent = s.labelBookmark;
  document.getElementById('label-sort').textContent = s.labelSort;
```

---

### Task 5: Add date filter logic and event listener

**Files:**
- Modify: `export.html.j2` (JS `applyFiltersAndSort()` function, around line 631; event listeners, around line 744)

**Step 1: Read the date filter value in `applyFiltersAndSort()`**

At the top of `applyFiltersAndSort()`, after the existing `const bookmarkFilter = …` line, add:

```javascript
  const dateFrom = document.getElementById('date-filter').value; // "YYYY-MM-DD" or ""
```

**Step 2: Add the date filter check inside `VIDEOS.filter()`**

In the filter callback, after the `bookmarkFilter` check and before the `if (q)` search check, add:

```javascript
    if (dateFrom && v.published_at.slice(0, 10) < dateFrom) return false;
```

`v.published_at` is an ISO datetime string (e.g. `"2026-02-27T14:30:00"`). Slicing the first 10 characters gives `"YYYY-MM-DD"` which compares lexicographically correctly against the date input value.

**Step 3: Wire the event listener**

In the event listener block at the bottom (around line 749), add after the existing `bookmark-filter` listener:

```javascript
document.getElementById('date-filter').addEventListener('change', applyFiltersAndSort);
```

---

### Task 6: Verify and commit

**Step 1: Generate a test export**

```bash
python export.py --all --output /tmp/test_export.html
```

Open `/tmp/test_export.html` in a browser and verify:
- Controls bar shows: search input, then labelled groups "Ab [date picker] | Kanal [dropdown] | Tag [dropdown] | Gelesen [dropdown] | Merken [dropdown] | Sortierung [dropdown]"
- Language selector shows 🇩🇪 Deutsch / 🇬🇧 English
- Switching language updates all label texts
- Entering a date in the "Ab" field filters videos correctly
- read-filter and bookmark-filter default options say "Alle" (not "Alle Videos")
- Groups stay visually paired when the bar wraps on narrow viewports

**Step 2: Commit**

```bash
git add export.html.j2
git commit -m "feat(export): add date filter, descriptive dropdown labels, and flags in lang selector

- 'Published after' date input filters videos client-side
- Each filter/sort dropdown now has a visible label (Kanal, Tag, Gelesen, Merken, Sortierung)
- Language selector shows flag emoji (🇩🇪 Deutsch / 🇬🇧 English)
- Default option text for read/bookmark filters simplified to 'Alle' (label provides context)
- All label strings added to both de/en I18N dicts

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```
