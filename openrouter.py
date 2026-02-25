"""OpenRouter API client for video summarization."""

import os
import re
from openai import OpenAI
from dotenv import load_dotenv

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

The transcript contains timestamp markers in [MM:SS] format at the start of each segment.
Include timestamp links to the relevant positions in the video using this exact HTML format:
  <a href="https://www.youtube.com/watch?v=VIDEO_ID&t=SECONDS" class="ts-link">MM:SS</a>
Replace VIDEO_ID with the video ID from the user message and SECONDS with the integer number of
seconds (e.g. [1:23] → t=83).
- For <h3> headings: place the timestamp link at the start of the heading.
- For <p> paragraphs and <li> items: place timestamp link(s) inline at the END of the
  relevant sentence, not at the beginning. For longer paragraphs, distribute 3–4 timestamps
  across the paragraph — one after each sentence that corresponds to a new segment."""


def build_client() -> OpenAI:
    base_url = os.environ.get("LLM_BASE_URL") or OPENROUTER_BASE_URL
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        if base_url == OPENROUTER_BASE_URL:
            raise ValueError("OPENROUTER_API_KEY (or LLM_API_KEY) is not set in the environment.")
        # Local backends (e.g. Ollama) don't require a real key
        api_key = "ollama"
    return OpenAI(base_url=base_url, api_key=api_key)


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
        max_tokens=4096,
    )
    content = response.choices[0].message.content.strip()
    # Some models (e.g. Gemma3) wrap the HTML in a markdown code fence; strip it.
    content = re.sub(r"^```[a-zA-Z]*\s*\n?(.*?)\n?```$", r"\1", content, flags=re.DOTALL).strip()
    return content
