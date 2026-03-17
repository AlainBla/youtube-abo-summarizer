"""Render the summary data to an HTML file using Jinja2."""

import json
import os
import re
from datetime import date

from jinja2 import Environment, FileSystemLoader

import i18n as i18n_module

TEMPLATE_DIR = os.path.dirname(__file__)
TEMPLATE_NAME = "template.html.j2"
EXPORT_TEMPLATE_NAME = "export.html.j2"


def _sanitize_summary(html: str | None) -> str | None:
    """Strip any trailing incomplete HTML tag from a summary fragment.

    LLM output can be cut off mid-tag (e.g. ending with '<a href="' due to a
    token limit).  When such a fragment is injected into the DOM via innerHTML
    or output verbatim by a Jinja2 template, the unclosed attribute quote causes
    the browser to consume all subsequent HTML as the attribute value, making
    the next card appear nested inside the broken one.

    This strips any trailing '<...' that has no closing '>'.
    """
    if not html:
        return html
    cleaned = re.sub(r"<[^>]*$", "", html).rstrip()
    return cleaned if cleaned else None


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
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=False)
    template = env.get_template(TEMPLATE_NAME)

    total_videos = sum(len(ch["videos"]) for ch in channels_data)
    generated_date = date.today().strftime("%B %d, %Y")

    # Sanitize summaries to prevent truncated LLM output from breaking HTML structure
    for ch in channels_data:
        for v in ch["videos"]:
            v["summary"] = _sanitize_summary(v.get("summary"))

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
) -> None:
    """Render and write a self-contained export HTML file with embedded video data.

    videos: list of dicts with keys:
        video_id, channel_id, channel_title, title,
        published_at (ISO str), published_at_display (str),
        duration (str), thumbnail_url (str),
        summary (str|None), summary_model (str|None),
        transcript_error (str|None)
    """
    lang = i18n_module.resolve_lang(lang)
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=False)
    template = env.get_template(EXPORT_TEMPLATE_NAME)

    # Sanitize summaries to prevent truncated LLM output from breaking HTML structure,
    # then escape </script> to prevent the JSON blob from breaking the script tag.
    sanitized = [{**v, "summary": _sanitize_summary(v.get("summary"))} for v in videos]
    videos_json = json.dumps(sanitized, ensure_ascii=False).replace("</", "<\\/")

    html = template.render(
        videos_json=videos_json,
        generated_date=date.today().strftime("%B %d, %Y"),
        total_videos=len(videos),
        default_lang=lang,
        sync_url=sync_url,
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
