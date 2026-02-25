"""Fetch and clean YouTube video transcripts."""

import os
from urllib.parse import urlparse, urlunparse

from dotenv import load_dotenv
from youtube_transcript_api import (
    YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled,
    IpBlocked, RequestBlocked, VideoUnplayable, CouldNotRetrieveTranscript,
)
from youtube_transcript_api.proxies import GenericProxyConfig

load_dotenv()

_langs_env = os.getenv("TRANSCRIPT_LANGS", "de,en")
PREFERRED_LANGS = [l.strip() for l in _langs_env.split(",") if l.strip()]


def _make_api(proxy_url: str | None) -> YouTubeTranscriptApi:
    cfg = GenericProxyConfig(http_url=proxy_url, https_url=proxy_url) if proxy_url else None
    return YouTubeTranscriptApi(proxy_config=cfg)


_FALLBACK_COUNTRY = os.getenv("PROXY_FALLBACK_COUNTRY", "DE").upper()


def _country_proxy_url(proxy_url: str, country: str) -> str | None:
    """Derive a country-pinned Webshare proxy URL by appending -COUNTRY to the username."""
    try:
        p = urlparse(proxy_url)
        if not p.username or f"-{country}" in p.username.upper():
            return None
        netloc = f"{p.username}-{country}:{p.password}@{p.hostname}:{p.port}"
        return urlunparse(p._replace(netloc=netloc))
    except Exception:
        return None


_proxy_url = os.getenv("WEBSHARE_PROXY_URL")
_api = _make_api(_proxy_url)
_fallback_api = _make_api(_country_proxy_url(_proxy_url, _FALLBACK_COUNTRY)) if _proxy_url else None


def _fetch(api: YouTubeTranscriptApi, video_id: str, preferred_langs: list[str]) -> tuple[str | None, str | None]:
    """Single attempt to fetch a transcript using the given API instance."""
    try:
        transcript_list = api.list(video_id)

        for lang in preferred_langs:
            for generated in (False, True):
                try:
                    if generated:
                        t = transcript_list.find_generated_transcript([lang])
                    else:
                        t = transcript_list.find_manually_created_transcript([lang])
                    return _to_text(t.fetch()), None
                except NoTranscriptFound:
                    continue

        t = next(iter(transcript_list))
        return _to_text(t.fetch()), None

    except IpBlocked:
        print(f"    [BLOCKED] YouTube blockiert diese IP für Transkript-Anfragen (video_id={video_id}).")
        return None, "ip_blocked"
    except RequestBlocked:
        print(f"    [BLOCKED] Anfrage von YouTube abgelehnt (Rate Limit?) für video_id={video_id}.")
        return None, "rate_limited"
    except (NoTranscriptFound, TranscriptsDisabled):
        return None, "unavailable"
    except VideoUnplayable as e:
        reason_lower = (e.reason or "").lower()
        if any(kw in reason_lower for kw in ("country", "region")):
            return None, "country_blocked"
        print(f"    [INFO] Video nicht abspielbar für video_id={video_id}: {e}")
        return None, "unavailable"
    except CouldNotRetrieveTranscript as e:
        print(f"    [ERROR] {type(e).__name__} für video_id={video_id}: {e}")
        return None, "unavailable"


def get_transcript(video_id: str, preferred_langs: list[str] = PREFERRED_LANGS) -> tuple[str | None, str | None]:
    """Return (transcript_text, error_reason).

    error_reason is None on success, otherwise one of:
      "ip_blocked", "rate_limited", "unavailable", "country_blocked"

    On country_blocked: retries once with a Germany-pinned Webshare proxy if available.
    """
    text, reason = _fetch(_api, video_id, preferred_langs)
    if reason == "country_blocked":
        if _fallback_api is not None:
            print(f"    [RETRY] Video geo-gesperrt, versuche {_FALLBACK_COUNTRY}-Proxy für video_id={video_id}.")
            text, reason = _fetch(_fallback_api, video_id, preferred_langs)
            if reason != "country_blocked":
                return text, reason
        print(f"    [BLOCKED] Video in dieser Region gesperrt (country_blocked) für video_id={video_id}.")
    return text, reason


def _to_text(entries) -> str:
    """Format transcript with one [MM:SS] timestamp marker every ~30 seconds."""
    INTERVAL = 30
    result = []
    current_start = None
    current_texts = []

    for entry in entries:
        if current_start is None or entry.start >= current_start + INTERVAL:
            if current_texts:
                s = int(current_start)
                result.append(f"[{s // 60}:{s % 60:02d}] {' '.join(current_texts)}")
            current_start = entry.start
            current_texts = [entry.text]
        else:
            current_texts.append(entry.text)

    if current_texts and current_start is not None:
        s = int(current_start)
        result.append(f"[{s // 60}:{s % 60:02d}] {' '.join(current_texts)}")

    return "\n".join(result)
