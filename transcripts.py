"""Fetch and clean YouTube video transcripts."""

import os
from urllib.parse import urlparse, urlunparse

from dotenv import load_dotenv
import requests.exceptions
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

if _proxy_url:
    from urllib.parse import urlparse as _up
    _p = _up(_proxy_url)
    print(f"[transcripts] Proxy: {_p.scheme}://{_p.hostname}:{_p.port} (user={_p.username})", flush=True)
else:
    print("[transcripts] Kein Proxy konfiguriert — direkte Verbindung.", flush=True)


def _fetch_original(api: YouTubeTranscriptApi, video_id: str) -> tuple[str | None, str | None, str | None]:
    """Fetch transcript in the video's original language. Returns (text, lang_code, error)."""
    try:
        transcript_list = api.list(video_id)
        # Convert to list once so we can iterate multiple times safely
        all_transcripts = list(transcript_list)

        # Find original language via the first auto-generated transcript
        orig_lang = None
        for t in all_transcripts:
            if t.is_generated:
                orig_lang = t.language_code
                break

        if orig_lang is None:
            # No auto-generated transcript — take first available
            t = all_transcripts[0]
            return _to_text(t.fetch()), t.language_code, None

        # Prefer manually created transcript in original language (higher quality)
        try:
            t = transcript_list.find_manually_created_transcript([orig_lang])
            return _to_text(t.fetch()), t.language_code, None
        except NoTranscriptFound:
            pass

        # Fall back to auto-generated
        t = transcript_list.find_generated_transcript([orig_lang])
        return _to_text(t.fetch()), t.language_code, None

    except IpBlocked:
        print(f"    [BLOCKED] YouTube blockiert diese IP für Transkript-Anfragen (video_id={video_id}).")
        return None, None, "ip_blocked"
    except RequestBlocked:
        print(f"    [BLOCKED] Anfrage von YouTube abgelehnt (Rate Limit?) für video_id={video_id}.")
        return None, None, "rate_limited"
    except (NoTranscriptFound, TranscriptsDisabled):
        return None, None, "unavailable"
    except VideoUnplayable as e:
        reason_lower = (e.reason or "").lower()
        if any(kw in reason_lower for kw in ("country", "region")):
            return None, None, "country_blocked"
        print(f"    [INFO] Video nicht abspielbar für video_id={video_id}: {e}")
        return None, None, "unavailable"
    except CouldNotRetrieveTranscript as e:
        print(f"    [ERROR] {type(e).__name__} für video_id={video_id}: {e}")
        return None, None, "unavailable"
    except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError) as e:
        print(f"    [ERROR] Proxy/Netzwerkfehler für video_id={video_id}: {e}")
        return None, None, "unavailable"


def _fetch_manual(api: YouTubeTranscriptApi, video_id: str, preferred_langs: list[str]) -> tuple[str | None, str | None]:
    """Fetch the best available manually created transcript in preferred_langs.
    Returns (text, lang_code) or (None, None) if none found.
    Errors treated as not found.
    """
    try:
        transcript_list = api.list(video_id)
        for lang in preferred_langs:
            try:
                t = transcript_list.find_manually_created_transcript([lang])
                return _to_text(t.fetch()), t.language_code
            except NoTranscriptFound:
                continue
    except Exception:
        pass
    return None, None


def get_transcript(video_id: str) -> tuple[str | None, str | None, str | None]:
    """Return (transcript_text, lang_code, error_reason).

    Always returns the transcript in the video's original language.
    error_reason is None on success, otherwise one of:
      "ip_blocked", "rate_limited", "unavailable", "country_blocked"
    """
    text, lang, reason = _fetch_original(_api, video_id)
    if reason == "ip_blocked" and _fallback_api is not None:
        print(f"    [RETRY] IP geblockt, versuche Proxy für video_id={video_id}.")
        text, lang, reason = _fetch_original(_fallback_api, video_id)
    if reason == "country_blocked":
        if _fallback_api is not None:
            print(f"    [RETRY] Video geo-gesperrt, versuche {_FALLBACK_COUNTRY}-Proxy für video_id={video_id}.")
            text, lang, reason = _fetch_original(_fallback_api, video_id)
            if reason != "country_blocked":
                return text, lang, reason
        print(f"    [BLOCKED] Video in dieser Region gesperrt (country_blocked) für video_id={video_id}.")
    return text, lang, reason


def get_manual_transcript(video_id: str, preferred_langs: list[str] = PREFERRED_LANGS) -> tuple[str | None, str | None]:
    """Return (transcript_text, lang_code) for the best manually created DE/EN transcript.
    Returns (None, None) if no manual transcript is available in preferred_langs.
    """
    return _fetch_manual(_api, video_id, preferred_langs)


def _to_text(entries) -> str:
    """Format transcript with [MM:SS] timestamp markers.

    The marker interval scales with video length to keep token count manageable:
      < 30 min  → every 30 s
      30–120 min → every 60 s
      > 120 min  → every 120 s
    """
    entries = list(entries)
    if not entries:
        return ""

    total_seconds = entries[-1].start
    if total_seconds < 1800:
        INTERVAL = 30
    elif total_seconds < 7200:
        INTERVAL = 60
    else:
        INTERVAL = 120

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
