"""Render the summary data to an HTML file using Jinja2."""

import base64
import gzip
import json
import os
import re
from datetime import datetime, timezone

import nh3

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

import i18n as i18n_module

TEMPLATE_DIR = os.path.dirname(__file__)
TEMPLATE_NAME = "template.html.j2"
EXPORT_TEMPLATE_NAME = "export.html.j2"


_ALLOWED_TAGS: frozenset[str] = frozenset({"h3", "p", "ul", "ol", "li", "a", "strong", "em"})
_ALLOWED_ATTRS: dict[str, set[str]] = {"a": {"href", "class"}}
_ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"https"})

# Matches timestamp anchors the LLM emitted with the M:SS (or H:MM:SS) value
# baked into the href and no closing quote or link text before </a>, e.g.
# <a href="https://www.youtube.com/watch?v=VID&t=1:04</a>. The unclosed href
# attribute would otherwise consume all following markup up to the next '"',
# hiding entire paragraphs. Also tolerates the ampersand already being encoded
# as &amp; in case nh3.clean has already touched the input.
_BROKEN_TS_LINK_RE = re.compile(
    r'<a\s+href="(https?://[^"<>\s]*?t=)(\d+(?::\d{2}){1,2})</a>',
    re.IGNORECASE,
)


def _colon_time_to_seconds(t: str) -> int:
    seconds = 0
    for part in t.split(":"):
        seconds = seconds * 60 + int(part)
    return seconds


def _repair_broken_ts_links(html: str) -> str:
    """Rebuild timestamp anchors truncated to `<a href="...t=M:SS</a>`."""
    def repair(m: re.Match) -> str:
        url_prefix = m.group(1)
        display = m.group(2)
        return f'<a href="{url_prefix}{_colon_time_to_seconds(display)}" class="ts-link">{display}</a>'
    return _BROKEN_TS_LINK_RE.sub(repair, html)


def _sanitize_summary(html: str | None) -> str | None:
    """Strip malicious HTML from a summary fragment.

    Three-stage sanitization:
    1. Broken-timestamp-anchor repair — rebuilds <a href="...t=M:SS</a> tags
       that the LLM emitted without the closing quote and link text.
    2. nh3.clean() — allowlist-based HTML sanitizer; removes all tags/attributes
       not on the allowlist, strips javascript: URIs, and cleans event handlers.
    3. Trailing-tag fix — removes any trailing '<...' left by LLM truncation so
       the browser cannot consume subsequent HTML as an attribute value.
    """
    if not html:
        return html
    # Stage 1: repair LLM-truncated timestamp anchors
    repaired = _repair_broken_ts_links(html)
    # Stage 2: allowlist-based XSS sanitization
    cleaned = nh3.clean(
        repaired,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        url_schemes=_ALLOWED_URL_SCHEMES,
        link_rel=None,  # preserve existing rel/class; do not override
    )
    # Stage 3: strip trailing incomplete tag from LLM truncation
    cleaned = re.sub(r"<[^>]*$", "", cleaned).rstrip()
    return cleaned if cleaned else None


def sanitize_summary(html: str | None) -> str | None:
    """Public entry point for the summary sanitizer (see _sanitize_summary).

    The ebook export must run stored summaries through exactly the same
    allowlist and repair pass as the HTML render path: raw LLM output nests
    lists inside paragraphs often enough that an unsanitised book fails
    epubcheck, and nh3's HTML5 tree builder is what re-nests them.
    """
    return _sanitize_summary(html)


def _report_meta(lang: str, generated_date: str, total_videos: int, num_channels: int) -> str:
    if lang == "de":
        vids = f"{total_videos} Video{'s' if total_videos != 1 else ''}"
        chans = f"{num_channels} Kanal{'en' if num_channels != 1 else ''}"
        return f"Generiert {generated_date} \u2014 {vids} in {chans}"
    vids = f"{total_videos} video{'s' if total_videos != 1 else ''}"
    chans = f"{num_channels} channel{'s' if num_channels != 1 else ''}"
    return f"Generated {generated_date} \u2014 {vids} across {chans}"


def render_html(
    channels_data: list[dict],
    output_path: str,
    lang: str = i18n_module.DEFAULT_LANG,
) -> None:
    """Render and write the HTML summary file.

    channels_data: [
        {
            "channel_id": str,
            "title": str,
            "videos": [
                {
                    "video_id": str,
                    "title": str,
                    "published_at": str,
                    "duration": str,       # formatted e.g. "12:34" or "1:02:03", may be ""
                    "thumbnail_url": str,
                    "summary": str | None,
                }
            ]
        }
    ]
    """
    lang = i18n_module.resolve_lang(lang)
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
    template = env.get_template(TEMPLATE_NAME)

    total_videos = sum(len(ch["videos"]) for ch in channels_data)
    generated_date = datetime.now().strftime(GENERATED_STAMP_FORMAT)

    # Sanitize summaries, then mark as Markup so autoescape does not re-escape them
    for ch in channels_data:
        for v in ch["videos"]:
            sanitized = _sanitize_summary(v.get("summary"))
            v["summary"] = Markup(sanitized) if sanitized is not None else None

    html = template.render(
        channels=channels_data,
        generated_date=generated_date,
        total_videos=total_videos,
        lang=lang,
        t=i18n_module.get_strings(lang),
        meta_line=_report_meta(lang, generated_date, total_videos, len(channels_data)),
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


# Shown in the page header and title of both the report and the export. Local
# time, with hour and minute so several runs on the same day stay tellable apart.
GENERATED_STAMP_FORMAT = "%B %d, %Y %H:%M"

EXPORT_CHUNK_SIZE = 50
EXPORT_FIRST_PAGE = 20

# Sidecar next to the exported HTML, polled by the open page to notice that a
# newer archive has been deployed. Kept tiny (a handful of fields) so a tab can
# ask for it every few minutes without cost.
EXPORT_MANIFEST_SUFFIX = ".meta.json"


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


def _gzip_b64(raw: str) -> str:
    return base64.b64encode(gzip.compress(raw.encode("utf-8"))).decode("ascii")


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


def _export_manifest(index: list[dict]) -> dict:
    """Describe this export run for the update poll in the browser.

    ``index`` is the newest-first list from ``_split_export_data()``, so its
    first entry is the newest video. ``generated_at`` is the field the page
    actually compares -- the counts only decide the wording of the banner.
    """
    newest = index[0] if index else {}
    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "video_count": len(index),
        "newest_id": newest.get("video_id"),
        "newest_published_at": newest.get("published_at"),
    }


def render_export_html(
    videos: list[dict],
    output_path: str,
    lang: str = i18n_module.DEFAULT_LANG,
    sync_url: str | None = None,
    show_embed: bool = True,
    compress: bool = True,
) -> None:
    """Render and write a self-contained export HTML file with embedded video data.

    The data is embedded as a lightweight ``index`` (everything except the heavy
    summary HTML, via ``_split_export_data()``) plus a series of summary
    "chunks" (video_id -> HTML), each covering ``EXPORT_CHUNK_SIZE`` index
    positions. ``compress=True`` (default) gzip+base64 encodes the index and
    each chunk separately so the browser only has to decompress the chunk(s)
    a rendered page actually needs (decoded in-browser via
    ``DecompressionStream``). ``compress=False`` embeds one plain
    ``{index, summaries}`` JSON object (no chunking) for browsers without
    ``DecompressionStream``.

    Alongside the HTML a manifest sidecar ``<output_path>.meta.json`` is written
    (after the HTML, never before it). The page embeds the same manifest and
    polls the sidecar every few minutes, so a tab left open notices a newer
    export and offers a reload.

    videos: list of dicts with keys:
        video_id, channel_id, channel_title, title,
        published_at (ISO str), published_at_display (str),
        duration (str), thumbnail_url (str),
        summary (str|None), summary_model (str|None),
        transcript_error (str|None)
    """
    lang = i18n_module.resolve_lang(lang)
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
    env.filters["esch"] = lambda s: Markup(_esc_html(s))
    template = env.get_template(EXPORT_TEMPLATE_NAME)

    index, chunks = _split_export_data(videos)

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

    # sync_url is operator-configured (not user content); wrap so autoescape preserves it
    safe_sync_url = Markup(sync_url) if sync_url else None

    manifest = _export_manifest(index)
    manifest_path = output_path + EXPORT_MANIFEST_SUFFIX
    # The page resolves the sidecar relative to its own URL, so the basename is
    # all it needs for the usual /dir/file.html and /dir/ (index) deployments.
    # An extensionless rewrite without a trailing slash would resolve it one
    # directory up -- serve the archive under its real path if that applies.
    manifest_url = os.path.basename(manifest_path)

    html = template.render(
        compressed=compress,
        index_b64=index_b64,
        chunks_b64=chunks_b64,
        chunk_size=EXPORT_CHUNK_SIZE,
        data_obj=data_obj,
        generated_date=datetime.now().strftime(GENERATED_STAMP_FORMAT),
        total_videos=len(videos),
        default_lang=lang,
        sync_url=safe_sync_url,
        show_embed=show_embed,
        first_page=first_page,
        first_page_size=EXPORT_FIRST_PAGE,
        manifest_json=Markup(json.dumps(manifest, ensure_ascii=False).replace("</", "<\\/")),
        manifest_url=Markup(json.dumps(manifest_url)),
        t=i18n_module.get_strings(lang),
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    # Written after the HTML: a manifest that lands first would, for the moment
    # in between, announce videos the served archive does not contain yet.
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)
