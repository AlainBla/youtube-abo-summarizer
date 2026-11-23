"""OpenRouter API client for video summarization."""

import os
import json
import re
import sys
from openai import OpenAI
from dotenv import load_dotenv

import renderer
import tags as tag_vocab

load_dotenv()

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Override either variable to point at a different OpenAI-compatible backend.
# LLM_BASE_URL — e.g. http://localhost:11434/v1 for a local Ollama instance.
# LLM_API_KEY  — omit (or leave empty) for backends that need no key.

_summary_lang = os.environ.get("SUMMARY_LANG", "German")

SYSTEM_PROMPT = f"""You are an assistant that summarizes YouTube videos based on their transcripts.
Always write the summary in {_summary_lang}, regardless of the transcript language.
Structure your response in clean HTML using these elements only (no full document tags):
- <h3> for section headings
- <p> for paragraphs
- <ul>/<li> for bullet points — only when the content is genuinely a list or enumeration

Summary structure:
1. A short introductory <p> (2–3 sentences) stating the topic and main thesis.
2. One <h3> section per major topic, strictly in chronological order. Cover the full runtime of
   the video — do not skip large portions. Scale the number of sections to video length:
   short videos (<15 min): 2–3 sections; medium (15–45 min): 4–6 sections; long (>45 min): 6–10 sections.
   Each section body is written as flowing prose in one or more <p> elements — not bullet points.
   Only use <ul>/<li> if the content is an actual enumeration (e.g. a list of steps or items).
3. A concluding <h3> with a short <p> (2–3 sentences) summarising the overall message.

Name the person speaking. The user message carries the channel as
<channel>...</channel>: use that name — "SpeckObst erklärt…", "Alex Ziskind zeigt…" — unless
the transcript itself gives a more specific one (the presenter introduces themselves, or is
addressed by name), in which case use that. Never fall back to a generic label: no "der
Creator", no "the creator", no "der YouTuber", no "der Host", no "der Sprecher". When no
channel is given and the transcript names nobody, write around it ("im Video wird gezeigt…").

The video is newer than your training data. Product names, model names and version numbers
in it — AI models above all — may well be ones you have never heard of. Take them as the
transcript states them and never replace them with an older or better-known one: a "Qwen 3.8"
stays Qwen 3.8, not Qwen 2.5, and a "Claude Fable 5.2" stays Claude Fable 5.2, not Claude 3.5
Sonnet. Never write that the video "means" or "refers to" a different model than the one it
names. Automatic captions garble such names, so you may fix the spelling ("Clode" → "Claude",
"Quen" → "Qwen") and restore a dot a version number lost ("Quen 38" → "Qwen 3.8", "Fable 52" →
"Fable 5.2" when the video says 5.2 elsewhere), but keep every number the speaker said.

The transcript contains timestamp markers at the start of each segment: [M:SS] in the first
hour, [H:MM:SS] after it. Include timestamp links to the relevant positions in the video using
this exact HTML format:
  <a href="https://www.youtube.com/watch?v=VIDEO_ID&t=SECONDS" class="ts-link">M:SS</a>
Replace VIDEO_ID with the video ID from the user message and SECONDS with the integer number of
seconds (e.g. [1:23] → t=83, [1:02:03] → t=3723), and show the marker itself as the label.
Take every timestamp from a marker in the transcript: never a rounded or estimated time, and
never one after the last marker — the video ends there. Cover the whole transcript, to its
last marker, in order.
- For <h3> headings: place the timestamp link at the end of the heading.
- For <p> paragraphs and <li> items: place each timestamp link directly after the sentence
  it belongs to — not at the end of the paragraph. Each sentence that covers a new transcript
  segment gets its own timestamp immediately following the full stop. For longer paragraphs,
  this means 3–4 timestamps are distributed across the paragraph at the sentence level, not
  grouped at the end.

After the HTML summary, append exactly one line in this format:
<!-- tags: Tag1, Tag2, Tag3 -->
Choose 2–3 tags for the video and take them **only** from the list below. Write each
one exactly as it appears there — same wording, same spelling, German. Never invent a
tag, never translate one, never add a variation, no hashtags, no quotes. Name the
entries that describe what the video is about, most fitting first — only three are
kept. Prefer the general entry over none: a game title belongs under its genre, a
company under its field. If only one entry fits, name one. If none fits, leave the
line empty after "tags:".

{tag_vocab.prompt_block()}"""


def build_client() -> OpenAI:
    base_url = os.environ.get("LLM_BASE_URL") or OPENROUTER_BASE_URL
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        if base_url == OPENROUTER_BASE_URL:
            raise ValueError("OPENROUTER_API_KEY (or LLM_API_KEY) is not set in the environment.")
        # Local backends (e.g. Ollama) don't require a real key
        api_key = "ollama"
    return OpenAI(base_url=base_url, api_key=api_key)


# Block-level tags that may legitimately follow a timestamp link. When the model
# closes an anchor with one of these, the tag is kept after the inserted </a>;
# anything else (</article>, </small>, ...) is dropped as pure noise.
_STRUCTURAL_TAGS = frozenset(
    {"p", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "div"}
)

# A timestamp link: opening <a ... t=SECS ...>, the visible MM:SS label, and
# whatever the model used to close it. The closing group tolerates a stray "</"
# prefix and any tag name, because both show up in real model output.
_TS_LINK_RE = re.compile(
    r'(?P<head><a\b[^>]*?\bt=)(?P<secs>[0-9:]*)(?P<tail>[^>]*>)'
    r'(?P<label>[^<]*)'
    r'(?:</\s*)?</(?P<closing>[a-zA-Z][a-zA-Z0-9]*)>'
)


def _label_seconds(label: str) -> int | None:
    """Convert a MM:SS or HH:MM:SS timestamp label to seconds, or None."""
    parts = label.strip().split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        return None
    nums = [int(p) for p in parts]
    if len(parts) == 2:
        return nums[0] * 60 + nums[1]
    return nums[0] * 3600 + nums[1] * 60 + nums[2]


def _fix_timestamp_links(html: str) -> str:
    """Repair the two ways models break timestamp links.

    1. The `t=` value is the visible label's digits rather than a second count
       ("02:02" becoming t=202 or t=2 instead of t=122). The label reliably comes
       from a real transcript marker, so it is authoritative and `t=` is derived
       from it.
    2. The anchor is closed with the wrong tag (`</p>`, `</h3>`, `</article>`) or
       with a stray `</` before `</a>`, which leaves the link open and makes the
       browser swallow the rest of the card into it.
    3. `class="ts-link"` is missing, so the templates' timestamp styling does not
       apply and the link renders as a default blue underlined browser link.

    Links whose label is not a parseable timestamp are left alone apart from the
    closing tag and the class.
    """

    def fix(m: re.Match) -> str:
        label = m.group("label")
        secs = _label_seconds(label)
        t = str(secs) if secs is not None else m.group("secs")
        opening = f'{m.group("head")}{t}{m.group("tail")}'
        if "ts-link" not in opening:
            opening = f'{opening[:-1].rstrip()} class="ts-link">'
        closing = m.group("closing")
        suffix = "" if closing == "a" or closing not in _STRUCTURAL_TAGS else f"</{closing}>"
        return f"{opening}{label}</a>{suffix}"

    return _TS_LINK_RE.sub(fix, html)


# A </p> that is followed by running text instead of another block element or
# the end of the fragment. Models sometimes close the paragraph right after a
# timestamp link, before the sentence-final period; the browser then ends the
# paragraph there and every following sentence becomes its own block starting
# with a dot.
_STRAY_P_END_RE = re.compile(r"[ \t]*</p>(?P<gap>\s*)(?=(?P<next>[^\s<]))")

# Two or more paragraph ends in a row: the browser turns the extras into empty
# paragraphs, which show up as stray vertical gaps between cards' sections.
_REPEATED_P_END_RE = re.compile(r"</p>(?:\s*</p>)+")


def _drop_stray_paragraph_ends(html: str) -> str:
    """Remove </p> tags that close a paragraph in the middle of a sentence.

    A paragraph end is genuine when the next thing in the fragment is another
    tag or nothing at all. Anything else — a period, a comma, the next word —
    means the model closed too early, so the tag is dropped and the sentence
    stays in one paragraph. Whitespace around the dropped tag is kept, except
    before punctuation, where it would leave an orphaned space. A run of
    consecutive </p> tags collapses to one — paragraphs cannot nest, so the
    extras only produce empty paragraphs in the browser.

    Must run after `_fix_timestamp_links()`, which relies on a trailing </p> to
    recognise an anchor the model closed with the wrong tag.
    """

    def drop(m: re.Match) -> str:
        return "" if m.group("next") in ".,;:!?)" else m.group("gap")

    return _REPEATED_P_END_RE.sub("</p>", _STRAY_P_END_RE.sub(drop, html))


# A heading start repeated before the first one was closed, on the same line:
# "<h3>Titel <h3><a ...>2:13</a></h3>" or "<h3>Fazit <h3>" (the opener typed
# where the closer belonged). The inner start tag is dropped.
_REPEATED_HEADING_START_RE = re.compile(
    r"(?P<head><h(?P<level>[1-6])\b[^>]*>(?:(?!</?h[1-6]\b|<p\b)[^\n])*?)"
    r"\s*<h(?P=level)\s*>",
    re.IGNORECASE,
)

# One heading up to the next block start (or the end of the fragment): that is
# as far as the browser would carry an unclosed heading.
_HEADING_SEGMENT_RE = re.compile(
    r"<h(?P<level>[1-6])\b(?P<attrs>[^>]*)>"
    r"(?P<body>.*?)"
    r"(?=<(?:p|ul|ol|li|div|blockquote|table|h[1-6])\b|\Z)",
    re.DOTALL | re.IGNORECASE,
)

# What models write where </hN> belongs: </p>, a garbled </h… ("</hoster",
# </h3> run into the next word), or an inline closer with no opener.
_HEADING_JUNK_CLOSER_RE = re.compile(
    r"</p\s*>|</h(?![1-6]\s*>)[^<>\s]*>?|</(?P<inline>i|b|em|strong|span|u|small)\s*>",
    re.IGNORECASE,
)
_HEADING_CLOSER_RE = re.compile(r"</h[1-6]\s*>", re.IGNORECASE)


def _close_heading(m: re.Match) -> str:
    level, attrs, body = m["level"], m["attrs"], m["body"]
    opener, closer = f"<h{level}{attrs}>", f"</h{level}>"
    real = _HEADING_CLOSER_RE.search(body)
    if real:
        # Closed — the browser accepts any level. Normalise the level and drop
        # a </p> typed just before it ("</a></p></h3>").
        head = re.sub(r"(?:\s*</p\s*>)+\s*$", "", body[: real.start()])
        return f"{opener}{head}{closer}{body[real.end():]}"
    for junk in _HEADING_JUNK_CLOSER_RE.finditer(body):
        tag = junk["inline"]
        if tag and re.search(rf"<{tag}\b", body[: junk.start()], re.IGNORECASE):
            continue  # a real inline closer
        return f"{opener}{body[: junk.start()].rstrip()}{closer}{body[junk.end():]}"
    stripped = body.rstrip()
    if not stripped:
        return body  # an empty heading the model opened by mistake: drop it
    return f"{opener}{stripped}{closer}{body[len(stripped):]}"


def _fix_heading_closers(html: str) -> str:
    """Close every heading the model left open.

    Models end a heading with </p>, with a garbled "</hoster", with an inline
    closer that has no opener, or type <h3> where </h3> belongs. The browser
    ignores all of those, so the heading stays open and the next paragraph is
    parsed inside it — inheriting the heading style, which in the archive is
    `text-transform: uppercase` (znzYWHPv1WY: three paragraphs in capitals).

    Each heading is taken up to the next block start; without a real closer in
    that stretch, the first junk closer is replaced by </hN>, or </hN> is put
    at its end. A closer of the wrong level is normalised; the browser would
    accept it anyway.

    Must run after `_fix_timestamp_links()`, which turns an anchor closed with
    </p> into "</a></p>" — that </p> is then the heading's closer.
    """
    def merge(m: re.Match) -> str:
        rest = m.string[m.end():m.end() + 1]
        return m["head"] + ("" if not rest or rest.isspace() else " ")

    html = _REPEATED_HEADING_START_RE.sub(merge, html)
    return _HEADING_SEGMENT_RE.sub(_close_heading, html)


def repair_summary_html(html: str) -> str:
    """Run every purely textual repair over a summary fragment. No API calls.

    Order matters: a JSON-wrapped response is unwrapped first (its escaped
    newlines would otherwise look like text to the paragraph pass), then anchors
    whose href the model never closed are rebuilt — until they are, the text
    they swallowed is still inside an attribute value and invisible to every
    pass below; the same holds for a tag that never reaches its '>'. Anchors
    the model left without a label then get one from their href. Timestamp links come next (that pass consumes the wrong closing
    tags), then headings closed with </p>, stray paragraph ends after that, and deduplication last —
    its block regex would otherwise stop at a stray </p> and miss duplicate
    links in the rest of the paragraph.
    """
    unwrapped = _unwrap_json_response(html)
    return _dedup_timestamps(
        _drop_stray_paragraph_ends(
            _fix_heading_closers(
                _fix_timestamp_links(
                    renderer._relabel_unlabelled_ts_links(
                        renderer._close_labelless_ts_links(
                            renderer._close_unterminated_ts_tags(
                                renderer._repair_broken_ts_links(unwrapped)
                            )
                        )
                    )
                )
            )
        )
    )


def _dedup_timestamps(html: str) -> str:
    """Within each <p> and <li>, remove duplicate timestamp links with the same t= value.

    The LLM sometimes assigns the same transcript segment (same t= seconds) to
    multiple sentences in a paragraph, producing repeated identical links. Only
    the first occurrence of each t= value within a block element is kept.
    """
    # The leading whitespace goes with the link: dropping the anchor but keeping
    # its space would leave "... erreichen . Es wird" behind.
    ts_re = re.compile(r'[ \t]*<a\b[^>]*\bt=(\d+)[^>]*>.*?</a>', re.DOTALL)

    def dedup_block(m):
        seen: set[str] = set()
        def keep_first(tm: re.Match) -> str:
            t = tm.group(1)
            if t in seen:
                return ''
            seen.add(t)
            return tm.group(0)
        return ts_re.sub(keep_first, m.group(0))

    return re.sub(r'<(?:p|li)\b[^>]*>.*?</(?:p|li)>', dedup_block, html, flags=re.DOTALL)


# A response that is a JSON object rather than the requested HTML fragment:
# {"summary": "<div>...", "tags": [...]}. Matched leniently, because the tag
# extraction in _parse_tags() cuts the response at the <!-- tags: --> comment
# and takes the object's closing quote and brace with it.
_JSON_SUMMARY_RE = re.compile(r'\A\s*\{\s*"summary"\s*:\s*"(?P<body>.*)\Z', re.DOTALL)


def _unwrap_json_response(content: str) -> str:
    """Return the HTML fragment from a model response wrapped in a JSON object.

    Stored unwrapped, such a summary keeps every quote and newline backslash-
    escaped, so the sanitizer sees one long text node instead of markup and the
    card renders as literal source. Anything that is not a JSON object with a
    "summary" string is returned unchanged.
    """
    stripped = content.strip()
    if not stripped.startswith("{"):
        return content
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        summary = parsed.get("summary")
        return summary if isinstance(summary, str) else content
    m = _JSON_SUMMARY_RE.match(stripped)
    if not m:
        return content
    # Truncated object: drop a closing quote and brace if they survived, then
    # decode the escapes by hand — json.loads() needs a complete string literal.
    body = re.sub(r'"\s*,?\s*\}?\s*\Z', "", m.group("body"))
    for escape, char in (("\\n", "\n"), ("\\t", "\t"), ("\\r", "\r"), ('\\"', '"'), ("\\/", "/")):
        body = body.replace(escape, char)
    return body.replace("\\\\", "\\")


def _parse_tags(content: str) -> tuple[str, list[str]]:
    """Extract <!-- tags: ... --> comment from end of content.

    Returns (html_without_comment, tags_list). Tags list is [] if not found.
    """
    m = re.search(r'<!--\s*tags:\s*([^>]+?)-->', content, re.IGNORECASE)
    if not m:
        return content, []
    tags = [t.strip() for t in m.group(1).split(',') if t.strip()]
    html = content[:m.start()].rstrip()
    return html, tags


class SummaryRejected(Exception):
    """The model returned output that is unusable as a summary.

    Raised instead of returning garbage, so callers can store no summary at all
    and let `repair.py` retry later.
    """


# 30 consecutive repetitions of the same token. Models occasionally collapse
# into a repetition loop ("our our our ...") and run until max_tokens. Scanned
# against the whole summary store: no legitimate summary trips this.
_REPETITION_RE = re.compile(r"\b(\w+)(\s+\1){29,}")


def _validate_summary(html: str, finish_reason: str | None) -> None:
    """Raise SummaryRejected if the model output is degenerate or truncated."""
    if finish_reason == "length":
        raise SummaryRejected(
            f"Summary hit the {_MAX_OUTPUT}-token output cap and is truncated"
        )
    m = _REPETITION_RE.search(html)
    if m:
        raise SummaryRejected(
            f"Summary degenerated into a repetition loop ({m.group(1)!r} repeated)"
        )
    # A well-formed summary is a sequence of <p>/<h3> blocks and always ends on
    # a closing '>'. A model that stops mid-sentence (finish_reason "stop", not
    # "length" — e.g. 0AZv6Jp4LmQ, gemma-3-27b-it) leaves trailing prose after
    # the last tag, which the length check above does not catch.
    stripped = html.rstrip()
    if stripped and not stripped.endswith(">"):
        raise SummaryRejected(
            "Summary ends mid-sentence, not on a closing tag — looks truncated"
        )


def _clean_response(content: str) -> tuple[str, list[str]]:
    """Turn a raw model response into (summary_html, tags).

    A response wrapped in a JSON object is unwrapped first, so the tag comment
    and the fences below are looked for in the HTML itself.

    Tags are extracted next: some models put the <!-- tags: ... --> comment
    *after* the closing code fence, others inside it. Opening and closing fence
    are therefore stripped independently — extracting the tags may already have
    taken the closing fence with it.
    """
    html, tags = _parse_tags(_unwrap_json_response(content).strip())
    html = re.sub(r"^```[a-zA-Z]*\s*\n?", "", html)
    html = re.sub(r"\n?```\s*$", "", html)
    return html.strip(), tags


# A segment marker: [M:SS], [H:MM:SS], or the minute-only long form older
# transcripts carry past the first hour ("[220:13]").
_MARKER_RE = re.compile(r"\[(?:(\d+):)?(\d+):(\d{2})\]")


def _marker_seconds(m: re.Match) -> int:
    return int(m[1] or 0) * 3600 + int(m[2]) * 60 + int(m[3])


def _normalize_markers(transcript: str) -> str:
    """Rewrite minute-only markers past the first hour as [H:MM:SS].

    Transcripts stored before transcripts._marker() say "[220:13]"; a model
    reads that as no time at all (see there). Applied to every transcript
    before it reaches the model, so a re-summarize of an old one benefits too.
    """
    def fix(m: re.Match) -> str:
        secs = _marker_seconds(m)
        if m[1] or secs < 3600:
            return m[0]
        return f"[{renderer._seconds_to_label(secs)}]"

    return _MARKER_RE.sub(fix, transcript)


def _transcript_duration(transcript: str) -> int | None:
    """Return total seconds from the last timestamp marker in the transcript, or None."""
    last = None
    for last in _MARKER_RE.finditer(transcript):
        pass
    return _marker_seconds(last) if last else None


# How far past the last marker a link may point. The last segment runs on
# after its marker for up to one marker interval (120 s on long videos).
_LINK_SLACK_SECONDS = 180

_ANY_TS_LINK_RE = re.compile(
    r'[ \t]*<a\b[^>]*\bt=(?P<secs>\d+)[^>]*>[^<]*</a>', re.IGNORECASE
)


def _links_past_end(html: str, duration: int | None) -> int:
    """How many timestamp links point past the end of the video (0 when unknown)."""
    if not duration:
        return 0
    limit = duration + _LINK_SLACK_SECONDS
    return sum(1 for m in _ANY_TS_LINK_RE.finditer(html) if int(m["secs"]) > limit)


def _drop_links_past_end(html: str, duration: int | None) -> str:
    """Remove timestamp links that point past the end of the video.

    No marker lies there, so the model invented the time — znzYWHPv1WY, a
    3:44 h video, got links up to 6:30:00. A link that jumps nowhere is worse
    than none; the sentence it followed stays.
    """
    if not duration:
        return html
    limit = duration + _LINK_SLACK_SECONDS
    dropped = 0

    def drop(m: re.Match) -> str:
        nonlocal dropped
        if int(m["secs"]) <= limit:
            return m[0]
        dropped += 1
        return ""

    html = _ANY_TS_LINK_RE.sub(drop, html)
    if dropped:
        print(f"  [warn] dropped {dropped} timestamp link(s) past the video's end "
              f"({renderer._seconds_to_label(duration)})", file=sys.stderr)
    return html


def _format_duration(seconds: int) -> str:
    """Same M:SS / H:MM:SS shape the timestamp labels use."""
    return renderer._seconds_to_label(seconds)


_CONTEXT_LIMIT = 131_072   # model's hard context-window limit (tokens)
_MAX_OUTPUT    = 16_384    # max_tokens we request for the final summary
_CHUNK_OUTPUT  = 2_048     # max_tokens per intermediate chunk summary
_OVERHEAD      = 1_500     # tokens reserved for system prompt + message wrapper
# Chars budget per chunk (4 chars ≈ 1 token; conservative).
_CHUNK_MAX_CHARS = (_CONTEXT_LIMIT - _CHUNK_OUTPUT - _OVERHEAD) * 4  # ≈ 510 k

_CHUNK_SYSTEM_PROMPT = (
    "You are summarizing one segment of a YouTube video transcript as an intermediate step.\n"
    "Extract the key points, facts, arguments, and events from this segment.\n"
    "Write concise bullet points or short paragraphs in plain text — no HTML.\n"
    "Preserve the [M:SS] / [H:MM:SS] timestamp markers exactly as written for every major point\n"
    "so they can be used later.\n"
    "This partial summary will be combined with summaries of other segments.\n"
    "The video is newer than your training data: keep product names, model names and version\n"
    "numbers exactly as the transcript states them (spelling of a garbled caption may be fixed,\n"
    "the numbers never), and never swap one for an older or better-known one."
)


def _channel_line(channel: str | None) -> str:
    """The channel as its own delimited element, or nothing at all.

    Nothing rather than an empty element: <channel></channel> would read as a
    name the model is expected to fill in, and it does not have one either.
    """
    return f"Channel: <channel>{channel}</channel>\n" if channel else ""


def _published_line(published_at: str | None) -> str:
    """The publication date (YYYY-MM-DD), or nothing when it is unknown.

    It tells the model that the video postdates its training data, which is
    what keeps it from "correcting" a model name it has never seen into an
    older one it knows.
    """
    day = (published_at or "")[:10]
    return f"Video published: {day}\n" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else ""


def _build_user_message(
    video_id: str, title: str, transcript: str, channel: str | None = None,
    published_at: str | None = None,
) -> str:
    """Build the user message for the LLM, wrapping untrusted inputs in delimiters.

    Channel, title and transcript come from YouTube and are potentially
    attacker-controlled. XML-style delimiters make the boundary between
    instructions and data explicit, reducing the effectiveness of prompt
    injection via crafted video metadata.
    """
    duration = _transcript_duration(transcript)
    duration_line = f"Video duration: {_format_duration(duration)}\n" if duration else ""
    return (
        f"Video ID: {video_id}\n"
        f"{_published_line(published_at)}"
        f"{duration_line}"
        f"{_channel_line(channel)}"
        f"Video title: <title>{title}</title>\n\n"
        f"Transcript (with timestamps):\n"
        f"<transcript>\n{transcript}\n</transcript>"
    )


def _split_transcript_chunks(transcript: str) -> list[str]:
    """Split a long transcript into chunks that each fit within _CHUNK_MAX_CHARS.

    Splits at line boundaries so segment markers are never broken.
    Returns a single-element list when the transcript is short enough.
    """
    if len(transcript) <= _CHUNK_MAX_CHARS:
        return [transcript]
    lines = transcript.splitlines(keepends=True)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        if current_len + len(line) > _CHUNK_MAX_CHARS and current:
            chunks.append("".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line)
    if current:
        chunks.append("".join(current))
    return chunks


def _summarize_chunk(
    client: OpenAI,
    model: str,
    video_id: str,
    title: str,
    chunk: str,
    chunk_index: int,
    total_chunks: int,
    published_at: str | None = None,
) -> str:
    """Return a plain-text key-point summary of one transcript chunk."""
    user_msg = (
        f"Video ID: {video_id}\n"
        f"{_published_line(published_at)}"
        f"Video title: <title>{title}</title>\n"
        f"Transcript segment {chunk_index + 1} of {total_chunks}:\n"
        f"<transcript>\n{chunk}\n</transcript>"
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _CHUNK_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=_CHUNK_OUTPUT,
    )
    choice = response.choices[0]
    if choice.message.content is None:
        raise ValueError(f"Chunk {chunk_index + 1}/{total_chunks}: model returned no content")
    text = choice.message.content.strip()
    # Repetition loops are checked here too — a degenerate chunk would poison the
    # synthesis pass. A `length` finish_reason is deliberately tolerated: the
    # 2048-token chunk budget is tight enough that legitimate key points may hit
    # it, and truncated key points still contribute usefully to the reduce step.
    m = _REPETITION_RE.search(text)
    if m:
        raise SummaryRejected(
            f"Chunk {chunk_index + 1}/{total_chunks} degenerated into a "
            f"repetition loop ({m.group(1)!r} repeated)"
        )
    return text


def _build_synthesis_message(
    video_id: str, title: str, transcript: str, chunk_summaries: list[str],
    channel: str | None = None, published_at: str | None = None,
) -> str:
    """Build the synthesis user message from ordered chunk summaries.

    The final prose comes out of this pass, so it needs the channel as much as
    the single-pass message does.
    """
    duration = _transcript_duration(transcript)
    duration_line = f"Video duration: {_format_duration(duration)}\n" if duration else ""
    parts = [
        f"Video ID: {video_id}\n",
        _published_line(published_at),
        f"{duration_line}",
        _channel_line(channel),
        f"Video title: <title>{title}</title>\n\n",
        "The transcript was too long for a single pass. "
        "The following are key-point summaries of consecutive transcript segments, in order. "
        "Each preserves [M:SS] / [H:MM:SS] timestamps that you should use for timestamp links.\n\n",
    ]
    for i, summary in enumerate(chunk_summaries, 1):
        parts.append(f'<segment_summary index="{i}">\n{summary}\n</segment_summary>\n\n')
    return "".join(parts)


def summarize_video(
    video_id: str, title: str, transcript: str, model: str, channel: str | None = None,
    published_at: str | None = None,
) -> tuple[str, list[str]]:
    """Return an (HTML-fragment summary, tags list) tuple for the video.

    When the transcript exceeds the model's context window the function
    automatically falls back to a two-pass map-reduce strategy:
      1. Map  — summarise each chunk into plain-text key points.
      2. Reduce — synthesise those partial summaries into the final HTML.
    """
    client = build_client()
    transcript = _normalize_markers(transcript)
    chunks = _split_transcript_chunks(transcript)

    if len(chunks) == 1:
        user_message = _build_user_message(video_id, title, transcript, channel=channel,
                                           published_at=published_at)
    else:
        chunk_summaries = [
            _summarize_chunk(client, model, video_id, title, chunk, i, len(chunks),
                             published_at=published_at)
            for i, chunk in enumerate(chunks)
        ]
        user_message = _build_synthesis_message(video_id, title, transcript, chunk_summaries,
                                                channel=channel, published_at=published_at)

    duration = _transcript_duration(transcript)
    html, raw_tags = _final_pass(client, model, user_message)
    late = _links_past_end(html, duration)
    if late:
        # Sampling is random: a second run of the same input mostly keeps to the
        # timeline (19 of 20 stretched summaries did in a test). One retry only,
        # and the first result stands if the retry fails or is no better.
        print(f"  [warn] {late} timestamp link(s) past the video's end "
              f"({renderer._seconds_to_label(duration)}), retrying once", file=sys.stderr)
        try:
            retry_html, retry_tags = _final_pass(client, model, user_message)
        except (SummaryRejected, ValueError) as exc:
            print(f"  [warn] retry failed ({exc}), keeping the first summary", file=sys.stderr)
        else:
            if _links_past_end(retry_html, duration) < late:
                html, raw_tags = retry_html, retry_tags
    # The prompt asks for vocabulary tags; canonicalize() is what enforces it.
    # What the model still invents is counted for review, never stored.
    kept_tags, rejected_tags = tag_vocab.canonicalize(raw_tags)
    tag_vocab.record_candidates(rejected_tags)
    return _drop_links_past_end(html, duration), kept_tags


def _final_pass(client: OpenAI, model: str, user_message: str) -> tuple[str, list[str]]:
    """One call for the final HTML summary: validated and repaired, raw tags."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        max_tokens=_MAX_OUTPUT,
    )
    choice = response.choices[0]
    if choice.message.content is None:
        finish_reason = getattr(choice, "finish_reason", "unknown")
        raise ValueError(f"Model returned no content (finish_reason={finish_reason!r})")
    html, raw_tags = _clean_response(choice.message.content)
    _validate_summary(html, getattr(choice, "finish_reason", None))
    # Repair links before deduping — dedup compares t= values, which are only
    # meaningful once they have been recomputed from the labels.
    return repair_summary_html(html), raw_tags
