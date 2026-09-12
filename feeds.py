"""Channel video discovery through YouTube's public RSS feed — no API quota.

Every scheduled collect run used to spend API quota per channel: one unit to
look up the uploads playlist (now derived, see youtube_client.uploads_playlist_id)
and one per playlistItems page. At ~100 subscriptions and a half-hourly
schedule that exhausts the project's daily 10 000 units, and the first thing
to die is the on-demand ingest button, which shares the same budget.

youtube.com/feeds/videos.xml?channel_id=UC... carries the same newest-first
list of uploads for free, without a token. It is capped at about 15 entries,
which is the one thing this module must be honest about: when the window may
reach past the oldest entry, it returns None ("cannot answer") instead of a
list that quietly omits videos. The caller then pays for the API for that one
channel — correctness first, quota second.
"""

import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

_ATOM = "{http://www.w3.org/2005/Atom}"
_YT = "{http://www.youtube.com/xml/schemas/2015}"
_MEDIA = "{http://search.yahoo.com/mrss/}"

_TIMEOUT = 30
# Google fronts answer an empty User-Agent with 403.
_UA = "Mozilla/5.0 (compatible; youtube-abo-summarizer/1.0)"


def _fetch(url: str, proxy: str | None = None, timeout: int = _TIMEOUT) -> bytes:
    """Fetch a URL, optionally through a proxy. Raises on any failure."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
        with opener.open(req, timeout=timeout) as resp:
            return resp.read()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _fetch_feed(channel_id: str, no_proxy: bool = False) -> bytes | None:
    """Feed bytes for a channel, or None. Direct first, one retry via proxy.

    Same order and reasoning as transcripts.py and ytdlp_meta.py: the server's
    own IP is the one that gets blocked, but a healthy IP should not pay the
    proxy's latency.
    """
    url = FEED_URL.format(channel_id=channel_id)
    try:
        return _fetch(url)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        if no_proxy:
            print(f"    [rss] {channel_id}: {exc}", file=sys.stderr)
            return None
        proxy = os.getenv("WEBSHARE_PROXY_URL")
        if not proxy:
            print(f"    [rss] {channel_id}: {exc}", file=sys.stderr)
            return None

    try:
        return _fetch(url, proxy=proxy)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"    [rss] {channel_id} (über Proxy): {exc}", file=sys.stderr)
        return None


def _parse(payload: bytes):
    try:
        return ET.fromstring(payload)
    except ET.ParseError as exc:
        print(f"    [rss] Feed nicht lesbar: {exc}", file=sys.stderr)
        return None


def _published(entry) -> datetime | None:
    """Publish time of an entry.

    <published>, never <updated>: an edited title or description bumps
    <updated>, which would present a years-old video as new on the next run.
    """
    node = entry.find(f"{_ATOM}published")
    if node is None or not node.text:
        return None
    try:
        return datetime.fromisoformat(node.text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _entry_to_video(entry) -> dict | None:
    vid_node = entry.find(f"{_YT}videoId")
    title_node = entry.find(f"{_ATOM}title")
    published = _published(entry)
    if vid_node is None or not vid_node.text or published is None:
        return None
    video_id = vid_node.text.strip()
    return {
        "video_id": video_id,
        "title": (title_node.text if title_node is not None and title_node.text else ""),
        "published_at": published.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        # The feed offers hqdefault; the API path and ytdlp_meta both store
        # mqdefault, and the store should not hold two thumbnail dialects.
        "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
    }


def get_new_videos_rss(channel_id: str, since: datetime, no_proxy: bool = False) -> list[dict] | None:
    """Videos published after `since`, or None if the feed cannot answer.

    None means "fall back to the API": the fetch failed, the feed was empty or
    unparseable, or every entry it holds is newer than `since` — in that last
    case the ~15-entry cap may have cut off videos that also belong in the
    window, and a short answer would silently lose them.

    An empty list is a real answer: the feed was read, nothing is new.
    """
    payload = _fetch_feed(channel_id, no_proxy=no_proxy)
    if payload is None:
        return None
    root = _parse(payload)
    if root is None:
        return None

    entries = root.findall(f"{_ATOM}entry")
    if not entries:
        # An empty feed and a channel whose uploads are hidden look alike here,
        # and so does a feed served as an error page with a 200.
        print(f"    [rss] {channel_id}: Feed ohne Einträge.", file=sys.stderr)
        return None

    since_utc = since.astimezone(timezone.utc)
    videos = []
    for entry in entries:
        published = _published(entry)
        if published is None or published <= since_utc:
            continue
        video = _entry_to_video(entry)
        if video:
            videos.append(video)

    if len(videos) == len(entries):
        print(
            f"    [rss] {channel_id}: alle {len(entries)} Einträge liegen im Fenster — "
            "Feed womöglich abgeschnitten.",
            file=sys.stderr,
        )
        return None

    return videos


def get_channel(channel_id: str, no_proxy: bool = False) -> dict | None:
    """Resolve a UC... channel ID to {channel_id, title} without the API.

    channels().list costs a quota unit for what the feed states in its own
    <title>. Only useful for IDs; handles and URLs still need resolving
    through the API.
    """
    payload = _fetch_feed(channel_id, no_proxy=no_proxy)
    if payload is None:
        return None
    root = _parse(payload)
    if root is None:
        return None

    title_node = root.find(f"{_ATOM}title")
    title = title_node.text if title_node is not None and title_node.text else None
    if not title:
        author = root.find(f"{_ATOM}author/{_ATOM}name")
        title = author.text if author is not None and author.text else None
    if not title:
        return None
    return {"channel_id": channel_id, "title": title}
