# Design: Export UI Improvements

Date: 2026-02-28

## Scope

Four targeted improvements to `export.html.j2`:

1. Date filter (published-after)
2. Descriptive labels before every filter/sort dropdown
3. Flags in the language selector

No backend changes required — all changes are contained in `export.html.j2`.

---

## 1. Date filter

A single `<input type="date" id="date-filter">` in the controls bar.

- Label: "Ab:" (DE) / "From:" (EN) — added to `I18N` dicts.
- When set, `applyFiltersAndSort()` excludes any video where `v.published_at.slice(0, 10) < filterValue`.
- Empty value = no date filter.
- No cookie persistence (archive files are typically opened fresh).
- Styled to match the existing dark-theme inputs: `background:#1a1a1a; border:1px solid #2a2a2a; color:#e0e0e0; border-radius:6px; padding:.45rem .75rem`.
- `applyLang()` updates the label text when language changes.

## 2. Descriptive labels before dropdowns

Every dropdown (channel, tag, read, bookmark, sort, date) gets a paired `<label>` element.

- Each label+select pair is wrapped in `<div class="control-group">` with `display:flex; align-items:center; gap:.35rem` so they stay together on line-wrap.
- Label style: `font-size:.8rem; color:#666; white-space:nowrap`.
- Default option text for read-filter and bookmark-filter changes from "Alle Videos" to "Alle" (the label provides the context).
- New `I18N` keys added for each label:
  - `labelChannel`, `labelTag`, `labelRead`, `labelBookmark`, `labelSort`, `labelFrom`
  - DE: "Kanal", "Tag", "Gelesen", "Merken", "Sortierung", "Ab"
  - EN: "Channel", "Tag", "Read", "Bookmark", "Sort", "From"
- `applyLang()` is extended to update each label's `textContent`.

## 3. Language selector flags

Option text updated:
- `🇩🇪 Deutsch`
- `🇬🇧 English`

Flag emoji render natively in `<option>` elements across all modern browsers. The collapsed select shows the selected option's full text including the flag. No additional CSS needed.

---

## Implementation plan

All changes in `export.html.j2`:

1. Add `.control-group` CSS rule.
2. Add `input[type="date"]` CSS rule (dark-theme, same style as `input[type="search"]`).
3. Update HTML controls bar: wrap each select (and the new date input) in a `.control-group` div with its `<label>`.
4. Update flag option text in `<select id="lang-select">`.
5. Add `I18N` label keys to both `de` and `en` dicts.
6. Extend `applyLang()` to update label text nodes and the date-filter label.
7. Add date-filter logic in `applyFiltersAndSort()`.
8. Wire `change` event listener for `#date-filter`.
