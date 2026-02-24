"""Render the summary data to an HTML file using Jinja2."""

import json
import os
from datetime import date

from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = os.path.dirname(__file__)
TEMPLATE_NAME = "template.html.j2"
EXPORT_TEMPLATE_NAME = "export.html.j2"


def render_html(channels_data: list[dict], output_path: str) -> None:
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
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=False)
    template = env.get_template(TEMPLATE_NAME)

    total_videos = sum(len(ch["videos"]) for ch in channels_data)

    html = template.render(
        channels=channels_data,
        generated_date=date.today().strftime("%B %d, %Y"),
        total_videos=total_videos,
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


def render_export_html(videos: list[dict], output_path: str) -> None:
    """Render and write a self-contained export HTML file with embedded video data.

    videos: list of dicts with keys:
        video_id, channel_id, channel_title, title,
        published_at (ISO str), published_at_display (str),
        duration (str), thumbnail_url (str),
        summary (str|None), summary_model (str|None),
        transcript_error (str|None)
    """
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=False)
    template = env.get_template(EXPORT_TEMPLATE_NAME)

    # Escape </script> to prevent the JSON blob from breaking the script tag
    videos_json = json.dumps(videos, ensure_ascii=False).replace("</", "<\\/")

    html = template.render(
        videos_json=videos_json,
        generated_date=date.today().strftime("%B %d, %Y"),
        total_videos=len(videos),
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
