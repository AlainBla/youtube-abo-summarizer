"""OpenRouter API client for video summarization."""

import os
import re
from openai import OpenAI

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

SYSTEM_PROMPT = """You are an assistant that summarizes YouTube videos based on their transcripts.
Always write the summary in German, regardless of the transcript language.
Structure your response in clean HTML using these elements only (no full document tags):
- <h3> for section headings
- <p> for paragraphs
- <ul>/<li> for bullet points
Keep it concise: a short overview paragraph, key points as bullets, and a one-sentence takeaway.

The transcript contains timestamp markers in [MM:SS] format at the start of each segment.
For each section heading and each bullet point, include a timestamp link to the corresponding
position in the video using this exact HTML format:
  <a href="https://www.youtube.com/watch?v=VIDEO_ID&t=SECONDS" class="ts-link">MM:SS</a>
Replace VIDEO_ID with the video ID from the user message and SECONDS with the integer number of
seconds (e.g. [1:23] → t=83). Place the link at the start of the relevant heading or list item."""


def build_client() -> OpenAI:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is not set in the environment.")
    return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)


def summarize_video(video_id: str, title: str, transcript: str, model: str) -> str:
    """Return an HTML-fragment summary of the video."""
    client = build_client()
    user_message = f"Video ID: {video_id}\nVideo title: {title}\n\nTranscript (with timestamps):\n{transcript}"
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )
    content = response.choices[0].message.content.strip()
    # Some models (e.g. Gemma3) wrap the HTML in a markdown code fence; strip it.
    content = re.sub(r"^```[a-zA-Z]*\s*\n?(.*?)\n?```$", r"\1", content, flags=re.DOTALL).strip()
    return content
