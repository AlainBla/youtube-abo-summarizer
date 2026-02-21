"""Render the summary data to an HTML file using Jinja2."""

import os
from datetime import date

from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = os.path.dirname(__file__)
TEMPLATE_NAME = "template.html.j2"


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
