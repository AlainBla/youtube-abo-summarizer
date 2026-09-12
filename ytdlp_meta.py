"""Quota-free video metadata via yt-dlp.

Every YouTube Data API lookup is charged against a project-wide daily quota
that the scheduled collect runs already spend on subscriptions and playlist
pages. On-demand ingest was the casualty: a single videos().list costs one
unit, but once the day's 10 000 are gone the button fails with quotaExceeded
anyway. yt-dlp reads the watch page instead -- no OAuth token, no API key, no
quota -- and returns the same dict shape as
``youtube_client.get_video_by_id()``, so callers cannot tell which path
answered.

Failure is always ``None``, never an exception: the caller falls back to the
API, and a fallback is only useful if it is reached.
"""

import json
import os
import subprocess
import sys

# A watch page fetch is a couple of requests; through a proxy it can crawl.
# Generous, but bounded -- the ingest worker runs under cron every minute.
_TIMEOUT = 120


def _iso_duration(seconds) -> str | None:
    """Format seconds as an ISO 8601 duration ("PT1H2M3S").

    The store keeps the API's raw ISO string, and collect._is_short(),
    export._fmt_duration() and the ebook all parse that form. yt-dlp reports
    float seconds, so the conversion happens here rather than leaving two
    duration dialects in the store.
    """
    if seconds is None:
        return None
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return None
    if total < 0:
        return None

    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    out = "PT"
    if hours:
        out += f"{hours}H"
    if minutes:
        out += f"{minutes}M"
    if secs or not (hours or minutes):
        out += f"{secs}S"
    return out


def _published_at(info: dict) -> str | None:
    """Return the publish time as "YYYY-MM-DDTHH:MM:SSZ".

    A premiered video's release_timestamp is what the API reports as
    publishedAt, so it wins over the upload timestamp. upload_date is the last
    resort and carries no time of day -- midnight UTC is a placeholder, good
    enough for the day-granularity date filters the archive offers.
    """
    from datetime import datetime, timezone

    for key in ("release_timestamp", "timestamp"):
        ts = info.get(key)
        if ts:
            try:
                return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            except (TypeError, ValueError, OSError):
                continue

    date = info.get("upload_date")
    if date and len(str(date)) == 8 and str(date).isdigit():
        d = str(date)
        return f"{d[0:4]}-{d[4:6]}-{d[6:8]}T00:00:00Z"
    return None


def _run(url: str, proxy: str | None) -> dict | None:
    """Run yt-dlp once and return the parsed JSON, or None on any failure."""
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--dump-single-json",
        "--skip-download",
        "--no-playlist",
        "--no-warnings",
        "--quiet",
    ]
    if proxy:
        cmd += ["--proxy", proxy]
    cmd.append(url)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"    [yt-dlp] Aufruf fehlgeschlagen: {exc}", file=sys.stderr)
        return None

    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        if err:
            print(f"    [yt-dlp] {err[-1]}", file=sys.stderr)
        return None

    try:
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        print("    [yt-dlp] Antwort war kein JSON.", file=sys.stderr)
        return None


def get_video_metadata(video_id: str, no_proxy: bool = False) -> dict | None:
    """Metadata for one video, without spending API quota.

    Tries a direct fetch first and retries once through WEBSHARE_PROXY_URL --
    the same order transcripts.py uses, and for the same reason: the server's
    own IP is the one YouTube blocks, but a working IP should not pay for the
    proxy. Returns None if neither attempt produced usable metadata.
    """
    # The full watch URL, never the bare ID: IDs starting with "-" are read as
    # a flag by yt-dlp's own argument parser.
    url = f"https://www.youtube.com/watch?v={video_id}"

    info = _run(url, None)
    if info is None and not no_proxy:
        proxy = os.getenv("WEBSHARE_PROXY_URL")
        if proxy:
            print("    [yt-dlp] Direkter Abruf fehlgeschlagen — Wiederholung über den Proxy.", file=sys.stderr)
            info = _run(url, proxy)
    if not info:
        return None

    channel_id = info.get("channel_id")
    title = info.get("title")
    published_at = _published_at(info)
    if not channel_id or not title or not published_at:
        print("    [yt-dlp] Unvollständige Metadaten.", file=sys.stderr)
        return None

    return {
        "video_id": video_id,
        "title": title,
        "published_at": published_at,
        # Built, not taken from info["thumbnail"]: yt-dlp hands back a .webp URL
        # with a query string, while ebook.collect_thumbnails() and the export
        # cards expect the API's plain JPEG.
        "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        "duration": _iso_duration(info.get("duration")),
        "channel_id": channel_id,
        "channel_title": info.get("channel") or info.get("uploader") or "",
    }
