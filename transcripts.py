"""Fetch and clean YouTube video transcripts."""

from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled

PREFERRED_LANGS = ["de", "en"]


def get_transcript(video_id: str, preferred_langs: list[str] = PREFERRED_LANGS) -> str | None:
    """Return the transcript as plain text, or None if unavailable."""
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        # Try preferred languages first (manual, then generated)
        for lang in preferred_langs:
            for generated in (False, True):
                try:
                    if generated:
                        t = transcript_list.find_generated_transcript([lang])
                    else:
                        t = transcript_list.find_manually_created_transcript([lang])
                    return _to_text(t.fetch())
                except NoTranscriptFound:
                    continue

        # Fall back to whatever is available
        t = next(iter(transcript_list))
        return _to_text(t.fetch())

    except (NoTranscriptFound, TranscriptsDisabled):
        return None


def _to_text(entries) -> str:
    return " ".join(entry["text"] for entry in entries)
