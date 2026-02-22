"""Fetch and clean YouTube video transcripts."""

from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled, IpBlocked, RequestBlocked

PREFERRED_LANGS = ["de", "en"]

_api = YouTubeTranscriptApi()


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
    return " ".join(entry.text for entry in entries)
