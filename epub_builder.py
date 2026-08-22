"""Turn selected videos into the files of an EPUB 3 archive."""

import html
import os
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "ebook")

# Only these named entities can appear in stored summaries (nh3 escapes the
# rest); XML knows none of them except the five predefined ones.
_NAMED_ENTITY_RE = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#)([a-zA-Z][a-zA-Z0-9]*);")


def _published_date(entry):
    """The publish date as a plain date; ISO strings may end in 'Z'."""
    raw = (entry.get("published_at") or "")[:19]
    return datetime.fromisoformat(raw).date()


def group_by_week(videos):
    """Group videos into ISO calendar weeks, oldest week first.

    The key is (iso_year, iso_week), never the week number alone: ISO week 1
    can start in December, so two different "week 1"s would otherwise merge.
    """
    buckets = {}
    for v in videos:
        d = _published_date(v)
        iso_year, iso_week, iso_weekday = d.isocalendar()
        key = (iso_year, iso_week)
        bucket = buckets.setdefault(key, {
            "iso_year": iso_year,
            "iso_week": iso_week,
            "start": d - timedelta(days=iso_weekday - 1),
            "end": d + timedelta(days=7 - iso_weekday),
            "anchor": "w-%04d-%02d" % (iso_year, iso_week),
            "videos": [],
        })
        bucket["videos"].append(v)

    weeks = [buckets[k] for k in sorted(buckets)]
    for w in weeks:
        w["videos"].sort(key=lambda v: (v.get("published_at") or "", v.get("video_id") or ""))
    return weeks


def xhtmlify(fragment):
    """Return a fragment that is guaranteed to parse as XML.

    A single unclosed tag in one summary would make the whole book unreadable
    for strict readers, so an unparseable fragment is escaped into plain text
    rather than passed through.
    """
    if not fragment:
        return ""

    def numeric(match):
        entity = "&%s;" % match.group(1)
        decoded = html.unescape(entity)
        if decoded == entity:
            # Unknown entity: keep the literal text but make the ampersand legal XML.
            return "&amp;%s;" % match.group(1)
        # A handful of HTML5 entities (e.g. &NotEqualTilde;) decode to more
        # than one codepoint; emit a numeric reference per codepoint.
        return "".join("&#%d;" % ord(c) for c in decoded)

    text = _NAMED_ENTITY_RE.sub(numeric, fragment)
    try:
        ET.fromstring("<div>" + text + "</div>")
        return text
    except ET.ParseError:
        return "<p>" + html.escape(re.sub(r"<[^>]*>", "", text)) + "</p>"


def _env():
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
    env.filters["xhtml"] = lambda s: Markup(xhtmlify(s))
    return env


def render_chapter(week, strings, lang, images, transcripts):
    """Render one ISO-week chapter as a complete, well-formed XHTML document."""
    template = _env().get_template("chapter.xhtml.j2")
    return template.render(
        week=week, t=strings, lang=lang, images=images, transcripts=transcripts
    )
