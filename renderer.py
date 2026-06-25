"""Render the summary data to an HTML file using Jinja2."""

import base64
import gzip
import json
import os
import re
from datetime import date

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


def _sanitize_summary(html: str | None) -> str | None:
    """Strip malicious HTML from a summary fragment.

    Two-stage sanitization:
    1. nh3.clean() — allowlist-based HTML sanitizer; removes all tags/attributes
       not on the allowlist, strips javascript: URIs, and cleans event handlers.
    2. Trailing-tag fix — removes any trailing '<...' left by LLM truncation so
       the browser cannot consume subsequent HTML as an attribute value.
    """
    if not html:
        return html
    # Stage 1: allowlist-based XSS sanitization
    cleaned = nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        url_schemes=_ALLOWED_URL_SCHEMES,
        link_rel=None,  # preserve existing rel/class; do not override
    )
    # Stage 2: strip trailing incomplete tag from LLM truncation
    cleaned = re.sub(r"<[^>]*$", "", cleaned).rstrip()
    return cleaned if cleaned else None


def _strip_html_to_text(html: str | None) -> str:
    """Reduce an HTML fragment to plain whitespace-normalised text.

    Used to precompute a lightweight, lowercased full-text search field so the
    browser never has to strip the (heavy) summary HTML on every keystroke.
    """
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


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
    generated_date = date.today().strftime("%B %d, %Y")

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


def render_export_html(
    videos: list[dict],
    output_path: str,
    lang: str = i18n_module.DEFAULT_LANG,
    sync_url: str | None = None,
    show_embed: bool = True,
    compress: bool = True,
) -> None:
    """Render and write a self-contained export HTML file with embedded video data.

    The data is embedded as two parts: a lightweight ``index`` (everything except
    the heavy summary HTML, plus a precomputed lowercased ``search_text``) that
    drives filtering/sorting/search, and a ``summaries`` map (video_id -> HTML)
    consulted only when a card is rendered. The combined ``{index, summaries}``
    JSON is gzip+base64 embedded (``compress=True``, decompressed in-browser via
    ``DecompressionStream``); ``compress=False`` embeds it as a plain JSON string
    parsed with ``JSON.parse`` for browsers without ``DecompressionStream``.

    videos: list of dicts with keys:
        video_id, channel_id, channel_title, title,
        published_at (ISO str), published_at_display (str),
        duration (str), thumbnail_url (str),
        summary (str|None), summary_model (str|None),
        transcript_error (str|None)
    """
    lang = i18n_module.resolve_lang(lang)
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
    template = env.get_template(EXPORT_TEMPLATE_NAME)

    index: list[dict] = []
    summaries: dict[str, str] = {}
    for v in videos:
        summary = _sanitize_summary(v.get("summary"))
        vid = v["video_id"]
        if summary:
            summaries[vid] = summary
        entry = {k: val for k, val in v.items() if k != "summary"}
        entry["search_text"] = (
            f"{v.get('title') or ''} {_strip_html_to_text(summary)}".lower()
        )
        index.append(entry)

    raw = json.dumps({"index": index, "summaries": summaries}, ensure_ascii=False)

    data_b64 = None
    data_obj = None
    if compress:
        data_b64 = base64.b64encode(gzip.compress(raw.encode("utf-8"))).decode("ascii")
    else:
        # Embed the JSON directly as a JS object literal; escape </ so it cannot
        # break out of the <script> tag. Markup avoids double-escaping.
        data_obj = Markup(raw.replace("</", "<\\/"))

    # sync_url is operator-configured (not user content); wrap so autoescape preserves it
    safe_sync_url = Markup(sync_url) if sync_url else None

    html = template.render(
        compressed=compress,
        data_b64=data_b64,
        data_obj=data_obj,
        generated_date=date.today().strftime("%B %d, %Y"),
        total_videos=len(videos),
        default_lang=lang,
        sync_url=safe_sync_url,
        show_embed=show_embed,
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
