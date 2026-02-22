"""Fetch and clean YouTube video transcripts."""

import os

from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled, IpBlocked, RequestBlocked
from youtube_transcript_api.proxies import GenericProxyConfig

load_dotenv()

PREFERRED_LANGS = ["de", "en"]

_proxy_url = os.getenv("WEBSHARE_PROXY_URL")
_proxy_config = GenericProxyConfig(http_url=_proxy_url, https_url=_proxy_url) if _proxy_url else None
_api = YouTubeTranscriptApi(proxy_config=_proxy_config)


def get_transcript(video_id: str, preferred_langs: list[str] = PREFERRED_LANGS) -> tuple[str | None, str | None]:
    """Return (transcript_text, error_reason).

    error_reason is None on success, otherwise one of:
      "ip_blocked", "rate_limited", "unavailable"
    """
    try:
        transcript_list = _api.list(video_id)

        # Try preferred languages first (manual, then generated)
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

        # Fall back to whatever is available
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
