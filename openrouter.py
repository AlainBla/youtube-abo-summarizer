"""OpenRouter API client for video summarization."""

import os
from openai import OpenAI

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

SYSTEM_PROMPT = """You are an assistant that summarizes YouTube videos based on their transcripts.
Write the summary in the same language as the transcript.
Structure your response in clean HTML using these elements only (no full document tags):
- <h3> for section headings
- <p> for paragraphs
- <ul>/<li> for bullet points
Keep it concise: a short overview paragraph, key points as bullets, and a one-sentence takeaway."""


def build_client() -> OpenAI:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is not set in the environment.")
    return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)


def summarize_video(title: str, transcript: str, model: str) -> str:
    """Return an HTML-fragment summary of the video."""
    client = build_client()
    user_message = f"Video title: {title}\n\nTranscript:\n{transcript}"
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )
    return response.choices[0].message.content.strip()
