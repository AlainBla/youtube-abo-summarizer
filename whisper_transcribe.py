"""Transcribe a video ourselves when YouTube has no captions for it.

Some channels switch captions off entirely -- not even the automatic track
exists, so youtube-transcript-api and yt-dlp both come back empty. For those
the audio is downloaded with yt-dlp, cut into pieces with ffmpeg and sent
through a speech-to-text model on OpenRouter (/audio/transcriptions, Whisper by
default). The segments come back with start times, and are formatted by
transcripts._to_text() into the same "[M:SS]" markers a YouTube transcript
gets, so the summarize prompt can link timestamps exactly as it always does.

Three things the first attempt got wrong, kept here so they stay fixed:
  - The original audio track has to be asked for. A video with AI dubbing
    carries one audio track per language, and a plain bitrate filter picked
    the Tamil dub of an English podcast. yt-dlp marks the original with
    language_preference >= 10.
  - The audio is cut, never re-encoded: re-encoding an hour to Opus took
    3.5 minutes of CPU on the server, stream copy takes seconds.
  - Cutting is not optional. Upstream providers stop at about 60 seconds of
    processing per request and uploads at 25 MB; one ten-minute piece took 61
    seconds once. Ten minutes per piece, with a retry, stays under both.
"""
import csv
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import proxies
from transcripts import _to_text

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/whisper-large-v3"
CHUNK_SECONDS = 600
_DOWNLOAD_TIMEOUT = 900
_SPLIT_TIMEOUT = 300
_ATTEMPTS = 3
# Under data/, never /tmp: the audio of an hour is tens of megabytes, and the
# server's disk is the one with room to spare. Deleted on the way out, and
# whisper_collect.py removes what a killed run left behind.
WORK_DIR = Path(__file__).parent / "data"

# Original track first, at a low bitrate (speech needs no more, and a small
# file uploads faster); a video without dubs has no language_preference, so
# the plain filters behind it still match.
AUDIO_FORMAT = (
    "bestaudio[language_preference>=10][abr<=80]"
    "/bestaudio[language_preference>=10]"
    "/bestaudio[abr<=80]"
    "/bestaudio"
)

# Providers answer with a code ("en") or a name ("English"). transcript_lang
# names the stored file and drives get_llm_transcript_path()'s de -> en
# priority, so only codes may reach the store; an unknown name becomes None
# and the transcript is stored as the plain <id>.txt.
_LANGUAGE_NAMES = {
    "english": "en", "german": "de", "french": "fr", "spanish": "es",
    "italian": "it", "dutch": "nl", "portuguese": "pt", "polish": "pl",
    "russian": "ru", "japanese": "ja", "chinese": "zh", "korean": "ko",
}


def whisper_model() -> str:
    return os.environ.get("WHISPER_MODEL") or DEFAULT_MODEL


def language_code(language: str | None) -> str | None:
    """A provider's language answer as an ISO 639-1 code, or None."""
    if not language:
        return None
    lang = language.strip().lower()
    if len(lang) == 2 and lang.isalpha():
        return lang
    return _LANGUAGE_NAMES.get(lang)


def _ffmpeg() -> str | None:
    """ffmpeg from PATH, else the static binary shipped by imageio-ffmpeg.

    The server has no ffmpeg package and no sudo; imageio-ffmpeg installs a
    static build into the virtualenv.
    """
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    return imageio_ffmpeg.get_ffmpeg_exe()


def _download_once(url: str, workdir: Path, proxy: str | None) -> Path | None:
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", AUDIO_FORMAT,
        "--no-playlist", "--no-warnings", "--quiet", "--no-part",
        "-o", str(workdir / "audio.%(ext)s"),
    ]
    if proxy:
        cmd += ["--proxy", proxy]
    cmd.append(url)
    # Never print the exception or yt-dlp's output unredacted: TimeoutExpired
    # renders the whole command line, proxy URL and its password included.
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_DOWNLOAD_TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"    [whisper] Download nach {_DOWNLOAD_TIMEOUT} s abgebrochen.", file=sys.stderr)
        return None
    except OSError as exc:
        print(f"    [whisper] Download fehlgeschlagen: {type(exc).__name__}", file=sys.stderr)
        return None
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        if err:
            line = err[-1].replace(proxy, proxies.redact(proxy)) if proxy else err[-1]
            print(f"    [whisper] {line}", file=sys.stderr)
        return None
    files = sorted(workdir.glob("audio.*"))
    return files[0] if files else None


def download_audio(video_id: str, workdir: Path, no_proxy: bool = False) -> Path | None:
    """The video's original audio track in workdir, or None.

    Direct first, then each proxy in proxies.proxy_chain(), as ytdlp_meta does.
    The full watch URL, never the bare ID: an ID starting with "-" would be
    read as a flag.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    path = _download_once(url, workdir, None)
    for proxy in proxies.proxy_chain(no_proxy=no_proxy):
        if path is not None:
            break
        print(f"    [whisper] Direkter Download fehlgeschlagen — Wiederholung über "
              f"{proxies.redact(proxy)}.", file=sys.stderr)
        path = _download_once(url, workdir, proxy)
    return path


def split_audio(audio: Path, workdir: Path, chunk_seconds: int = CHUNK_SECONDS) -> list[tuple[Path, float]]:
    """Cut audio into pieces of about chunk_seconds; returns (path, start offset).

    Stream copy, so the cuts fall on packet boundaries rather than exactly on
    the second; the offsets are therefore read back from ffmpeg's segment list
    instead of being assumed as multiples of chunk_seconds.
    """
    ffmpeg = _ffmpeg()
    if ffmpeg is None:
        raise RuntimeError("ffmpeg nicht gefunden (pip install imageio-ffmpeg)")
    listing = workdir / "chunks.csv"
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(audio), "-vn", "-c", "copy",
        "-f", "segment", "-segment_time", str(chunk_seconds),
        "-reset_timestamps", "1",
        "-segment_list", str(listing), "-segment_list_type", "csv",
        str(workdir / f"chunk%03d{audio.suffix}"),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=_SPLIT_TIMEOUT)
    return parse_segment_list(listing.read_text(encoding="utf-8"), workdir)


def parse_segment_list(text: str, workdir: Path) -> list[tuple[Path, float]]:
    """ffmpeg's csv segment list ("name,start,end" per line) as (path, start)."""
    chunks = []
    for row in csv.reader(text.splitlines()):
        if len(row) >= 2 and row[0]:
            chunks.append((workdir / row[0], float(row[1])))
    return chunks


def _is_noise(text: str) -> bool:
    """A segment that is only a music marker ("♪ ♪", "Music") carries nothing."""
    stripped = text.strip().strip("♪").strip()
    return not stripped or stripped.lower() in {"music", "[music]", "(music)"}


def merge_segments(pieces: list[tuple[float, list[dict]]]) -> list[SimpleNamespace]:
    """Segments of all pieces on one timeline: each start shifted by its piece's offset."""
    entries = []
    for offset, segments in pieces:
        for seg in segments:
            text = (seg.get("text") or "").strip()
            if _is_noise(text):
                continue
            entries.append(SimpleNamespace(start=offset + float(seg.get("start") or 0), text=text))
    return entries


def _client():
    """A client for OpenRouter itself, whatever LLM_BASE_URL says.

    openrouter.py's client may point at a local Ollama, which has no
    transcription endpoint; speech-to-text always goes to OpenRouter.
    """
    from openai import OpenAI
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY fehlt")
    return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=key, timeout=180)


def transcribe_chunk(client, path: Path, model: str) -> dict:
    """One piece through the transcription endpoint, with retries; the response as a dict."""
    last = None
    for attempt in range(_ATTEMPTS):
        try:
            with open(path, "rb") as f:
                result = client.audio.transcriptions.create(
                    model=model, file=f,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                )
            return result.model_dump() if hasattr(result, "model_dump") else dict(result)
        except Exception as exc:  # noqa: BLE001 - provider timeouts surface as several types
            last = exc
            print(f"    [whisper] {path.name}: Versuch {attempt + 1} fehlgeschlagen: {exc}",
                  file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"{path.name}: {last}")


def transcribe_video(video_id: str, no_proxy: bool = False,
                     model: str | None = None) -> tuple[str | None, str | None, float]:
    """(transcript text with [M:SS] markers, language code, cost in USD).

    Text is None when nothing usable came back. Raises on a failure that says
    nothing about the video (no API key, ffmpeg missing, provider down), so the
    caller can tell "try again later" from "this video cannot be done".
    """
    model = model or whisper_model()
    client = _client()
    WORK_DIR.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="whisper-", dir=WORK_DIR) as tmp:
        workdir = Path(tmp)
        audio = download_audio(video_id, workdir, no_proxy=no_proxy)
        if audio is None:
            return None, None, 0.0
        chunks = split_audio(audio, workdir)
        pieces = []
        cost = 0.0
        language = None
        for path, offset in chunks:
            response = transcribe_chunk(client, path, model)
            cost += float((response.get("usage") or {}).get("cost") or 0)
            language = language or response.get("language")
            pieces.append((offset, response.get("segments") or []))
        entries = merge_segments(pieces)
        if not entries:
            return None, None, cost
        return _to_text(entries), language_code(language), cost
