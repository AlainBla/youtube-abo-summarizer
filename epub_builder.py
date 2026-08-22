"""Turn selected videos into the files of an EPUB 3 archive."""

import functools
import html
import os
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone

from jinja2 import Environment, FileSystemLoader

import renderer
from markupsafe import Markup

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "ebook")

# Package-document boilerplate that never varies between books.
CONTAINER_XML = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""

MEDIA_TYPES = {
    ".xhtml": "application/xhtml+xml",
    ".css": "text/css",
    ".jpg": "image/jpeg",
    ".ncx": "application/x-dtbncx+xml",
}

# Only these named entities can appear in stored summaries (nh3 escapes the
# rest); XML knows none of them except the five predefined ones.
_NAMED_ENTITY_RE = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#)([a-zA-Z][a-zA-Z0-9]*);")

# A bare "&" that isn't part of one of the five predefined XML entities or a
# numeric reference is not legal XML character data. Stored summaries are
# full of these -- every timestamp link's href reads
# "watch?v=ID&t=122" -- and the named-entity pass above only rewrites
# "&name;" forms, so it leaves a bare "&t=122" untouched. Left alone, that
# single stray "&" makes ET.fromstring() raise below and xhtmlify() falls
# back to escaping the whole fragment to plain text, discarding all markup
# and every link. Runs after the named-entity pass so it never touches an
# "&" that pass already turned into a numeric reference.
_BARE_AMPERSAND_RE = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#)")

# XML 1.0 Char production (https://www.w3.org/TR/xml/#charsets): everything
# outside these ranges -- e.g. raw control chars like form feed, which HTML
# tolerates -- is not legal character data and makes ET.fromstring() raise.
# Built via chr() rather than literal \uXXXX escapes to keep this file plain
# ASCII. Stripped up front so neither the pass-through path nor the escape
# fallback below can leak an invalid char into the emitted markup.
_INVALID_XML_CHAR_RE = re.compile(
    "[^\t\n\r%s-%s%s-%s%s-%s]"
    % (chr(0x20), chr(0xD7FF), chr(0xE000), chr(0xFFFD), chr(0x10000), chr(0x10FFFF))
)


def _published_date(entry):
    """The publish date as a plain date; ISO strings may end in 'Z'."""
    raw = (entry.get("published_at") or "")[:19]
    return datetime.fromisoformat(raw).date()


def group_by_week(videos, newest_first=True):
    """Group videos into ISO calendar weeks, newest week first by default.

    The key is (iso_year, iso_week), never the week number alone: ISO week 1
    can start in December, so two different "week 1"s would otherwise merge.

    A digest is read starting with what just came in, so both the weeks and
    the videos inside them run newest to oldest. Pass newest_first=False for
    chronological order, e.g. when a book is meant to be read as a history.
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

    weeks = [buckets[k] for k in sorted(buckets, reverse=newest_first)]
    for w in weeks:
        w["videos"].sort(key=lambda v: (v.get("published_at") or "", v.get("video_id") or ""),
                         reverse=newest_first)
    return weeks


def xhtmlify(fragment):
    """Return a fragment that is guaranteed to parse as XML.

    A single unclosed tag in one summary would make the whole book unreadable
    for strict readers, so an unparseable fragment is escaped into plain text
    rather than passed through.
    """
    if not fragment:
        return ""

    # Strip XML-invalid characters first so both the pass-through path below
    # and the escape fallback are built from clean input -- otherwise a
    # control char surviving into the fallback would still make the "always
    # well-formed" guarantee false.
    fragment = _INVALID_XML_CHAR_RE.sub("", fragment)

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
    text = _BARE_AMPERSAND_RE.sub("&amp;", text)
    try:
        ET.fromstring("<div>" + text + "</div>")
        return text
    except ET.ParseError:
        # Strip tags rather than repair them -- a stray "<" can swallow text
        # up to the next ">", which is acceptable for this last-resort path
        # only. Resolve entities from the original (pre-numeric-conversion)
        # fragment before re-escaping, so a valid "&nbsp;" isn't first turned
        # into "&#160;" above and then double-escaped into "&amp;#160;" here.
        # html.unescape() can itself produce a char the earlier strip was
        # meant to rule out (e.g. a literal "&#12;" numeric reference in the
        # input decodes to a raw form-feed) -- the named-entity regex above
        # never touches numeric references, so this is the only place that
        # sees the decoded char; strip invalid chars again after decoding.
        plain = re.sub(r"<[^>]*>", "", fragment)
        resolved = _INVALID_XML_CHAR_RE.sub("", html.unescape(plain))
        return "<p>" + html.escape(resolved) + "</p>"


_BLOCK_TAGS = ("p", "ul", "ol", "h3")


def _hoist_blocks_out_of_headings(root):
    """Move block content out of a heading and put it after the heading.

    The model forgets </h3> often enough to matter: an HTML5 parser then
    nests everything up to the next heading inside it. A heading may only
    hold phrasing content, so epubcheck rejects the book -- and a whole
    paragraph would render heading-sized. Hoisting restores what the model
    meant, rather than flattening the paragraph into the title.
    """
    for parent in list(root.iter()):
        for heading in list(parent):
            if heading.tag != "h3":
                continue
            blocks = [child for child in list(heading) if child.tag in _BLOCK_TAGS]
            if not blocks:
                continue
            position = list(parent).index(heading)
            for offset, block in enumerate(blocks, start=1):
                heading.remove(block)
                parent.insert(position + offset, block)


def _normalize_lists(fragment):
    """Wrap anything that is not an <li> but sits directly in a list.

    The model emits `<ul><a href="...">12:34</a></ul>` often enough to matter:
    an HTML5 parser keeps it there (so nh3 does too) and it is well-formed
    XML, but a list may only contain list items — epubcheck rejects the book
    with RSC-005. Runs on xhtmlify()'s output, which is guaranteed parseable;
    anything unexpected is returned untouched rather than risking the book.
    """
    if not fragment or not any(t in fragment for t in ("<ul", "<ol", "<h3")):
        return fragment
    try:
        root = ET.fromstring("<div>" + fragment + "</div>")
    except ET.ParseError:
        return fragment

    _hoist_blocks_out_of_headings(root)

    for lst in root.iter():
        if lst.tag not in ("ul", "ol"):
            continue
        for position, child in enumerate(list(lst)):
            if child.tag == "li":
                continue
            item = ET.Element("li")
            lst.remove(child)
            item.append(child)
            lst.insert(position, item)
        if lst.text and lst.text.strip():
            item = ET.Element("li")
            item.text = lst.text
            lst.text = None
            lst.insert(0, item)

    out = ET.tostring(root, encoding="unicode")
    return out[len("<div>"):-len("</div>")] if out.startswith("<div>") else out


@functools.lru_cache(maxsize=1)
def _env():
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
    env.filters["xhtml"] = lambda s: Markup(_normalize_lists(xhtmlify(renderer.sanitize_summary(s))))
    env.globals.update(item_id=_item_id, media_type=_media_type)
    return env


def render_video(v, week, strings, lang, images, transcripts):
    """Render one video as a complete, well-formed XHTML document.

    `week` supplies the kicker line above the title, so a reader who lands on
    a single chapter still sees which calendar week it belongs to.
    """
    template = _env().get_template("video.xhtml.j2")
    return template.render(
        v=v, week=week, t=strings, lang=lang, images=images, transcripts=transcripts
    )


def _media_type(href):
    ext = os.path.splitext(href)[1]
    if ext not in MEDIA_TYPES:
        raise ValueError(f"unsupported EPUB asset type: {href}")
    return MEDIA_TYPES[ext]


def _item_id(href):
    """Manifest ID for a file: chapter-w-2026-34.xhtml -> chap-w-2026-34.

    Two cases need special handling beyond the generic stem-based ID:
    - Image hrefs ("images/<video_id>.jpg") get an "img-" prefix, because an
      XML ID may not start with a digit and YouTube video IDs can.
    - "toc.ncx" gets the fixed id "toc-ncx" so the OPF spine's toc="toc-ncx"
      attribute (which must reference the NCX by its manifest id) resolves
      instead of dangling -- splitting the extension off "toc.ncx" the way
      every other file is handled would otherwise collapse it to "toc".
    """
    if href.startswith("images/"):
        return "img-" + os.path.splitext(os.path.basename(href))[0]
    if href == "toc.ncx":
        return "toc-ncx"
    stem = os.path.splitext(os.path.basename(href))[0]
    if stem.startswith("chapter-"):
        return "chap-" + stem[len("chapter-"):]
    return stem.replace(".", "-")


# Longest paragraph a transcript page may contain. Stored transcripts are a
# handful of very long lines -- a 60-minute video arrives as ~6 lines -- so
# chunking by line count alone leaves single paragraphs of 25k+ characters:
# a wall of text with no page-break opportunities on an e-ink reader.
MAX_PARAGRAPH_CHARS = 1200

# Sentence end followed by whitespace. Splitting here keeps the terminator
# with its sentence, so paragraphs never start mid-thought.
_SENTENCE_END_RE = re.compile(r"(?<=[.!?\u2026])\s+")


def _split_long_block(block):
    """Cut an over-long block into paragraphs at sentence boundaries.

    A single sentence longer than the limit is emitted whole: dropping text
    would be worse than one oversized paragraph.
    """
    if len(block) <= MAX_PARAGRAPH_CHARS:
        return [block]

    paragraphs = []
    current = ""
    for sentence in _SENTENCE_END_RE.split(block):
        if current and len(current) + 1 + len(sentence) > MAX_PARAGRAPH_CHARS:
            paragraphs.append(current)
            current = sentence
        else:
            current = sentence if not current else current + " " + sentence
    if current:
        paragraphs.append(current)
    return paragraphs


def _transcript_paragraphs(text):
    """Cut a raw transcript into readable paragraphs.

    Transcripts arrive as one long blob; blank lines are honoured where they
    exist, otherwise every 12 lines become a paragraph so an e-reader has
    something to break pages on. Invalid XML characters are stripped up
    front for the same reason xhtmlify() strips them: raw transcript text
    goes straight into an XHTML document via autoescaping, which escapes
    "&"/"<"/">" but not control characters that ET.fromstring() would still
    reject.
    """
    text = _INVALID_XML_CHAR_RE.sub("", text)
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(blocks) > 1:
        joined = [" ".join(b.split()) for b in blocks]
    else:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        joined = [" ".join(lines[i:i + 12]) for i in range(0, len(lines), 12)]

    paragraphs = []
    for block in joined:
        paragraphs.extend(_split_long_block(block))
    return paragraphs


def _chapter_href_for(parts, video_id):
    """Where a transcript page links back to: chapter file plus video anchor."""
    for part in parts:
        for week in part["weeks"]:
            for v in week["videos"]:
                if v["video_id"] == video_id:
                    return v.get("href", "nav.xhtml")
    return "nav.xhtml"


def _covered_date_range(parts):
    """Earliest and latest publish date across every video in the book.

    (None, None) when the book somehow has no videos -- callers must guard
    against that rather than assume a range always exists.
    """
    dates = [_published_date(v) for part in parts for week in part["weeks"] for v in week["videos"]]
    return (min(dates), max(dates)) if dates else (None, None)


def build_epub(parts, output_path, title, lang, strings, images=None,
                transcripts=None, book_id=None):
    """Write the EPUB 3 archive.

    Order matters: 'mimetype' must be the first entry and stored uncompressed,
    otherwise readers refuse the file.
    """
    images = images or {}
    transcripts = transcripts or {}
    env = _env()
    # EPUB 3 requires dcterms:modified in the exact "CCYY-MM-DDThh:mm:ssZ"
    # form -- isoformat()'s "+00:00" offset is well-formed XML (so it would
    # slip past a well-formedness check) but fails epubcheck (RSC-005).
    generated = datetime.now(tz=timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    book_id = book_id or "urn:uuid:" + str(uuid.uuid4())

    date_from, date_to = _covered_date_range(parts)

    files = {}  # href inside OEBPS -> str | bytes
    with open(os.path.join(TEMPLATE_DIR, "book.css"), encoding="utf-8") as f:
        files["book.css"] = f.read()
    files["title.xhtml"] = env.get_template("title.xhtml.j2").render(
        title=title, lang=lang, t=strings, generated=generated[:10], parts=parts,
        date_from=date_from, date_to=date_to)

    image_hrefs = {}
    for video_id, blob in images.items():
        href = "images/%s.jpg" % video_id
        files[href] = blob
        image_hrefs[video_id] = href

    # One document per video: an e-reader's next-chapter jump then moves video
    # by video and every video starts on a fresh page. Weeks stay the grouping
    # level in the table of contents. A video ID is unique across the book (the
    # parts are disjoint), so it alone keys the file.
    for part in parts:
        for week in part["weeks"]:
            for v in week["videos"]:
                href = "video-%s.xhtml" % v["video_id"]
                v["href"] = href
                files[href] = render_video(v, week, strings, lang, image_hrefs,
                                           set(transcripts))
            week["href"] = week["videos"][0]["href"] if week["videos"] else "nav.xhtml"

    for video_id, text in transcripts.items():
        files["transcript-%s.xhtml" % video_id] = env.get_template("transcript.xhtml.j2").render(
            video_id=video_id, paragraphs=_transcript_paragraphs(text), lang=lang, t=strings,
            back_href=_chapter_href_for(parts, video_id))

    files["nav.xhtml"] = env.get_template("nav.xhtml.j2").render(
        parts=parts, lang=lang, t=strings, title=title)
    files["toc.ncx"] = env.get_template("toc.ncx.j2").render(
        parts=parts, book_id=book_id, title=title, t=strings)
    files["content.opf"] = env.get_template("content.opf.j2").render(
        parts=parts, title=title, lang=lang, book_id=book_id, generated=generated,
        items=sorted(files.keys()), transcripts=sorted(transcripts.keys()), t=strings)

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER_XML)
        for href, payload in files.items():
            z.writestr("OEBPS/" + href, payload)
