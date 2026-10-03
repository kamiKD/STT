"""Groq Whisper transcription.

Sends 16 kHz mono PCM (wrapped in a WAV container) to Groq's
/audio/transcriptions endpoint. temperature=0 plus an optional prompt is what
gets you the "recognizes perfectly" behaviour: no hallucinated filler, stable
punctuation and casing.
"""

from __future__ import annotations

import io
import re
import wave

import numpy as np
import requests

ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"

# 16 kHz, mono, int16: what pcm_to_wav wraps the capture in.
BYTES_PER_SECOND = 32_000
MIN_TIMEOUT = 30
MAX_TIMEOUT = 900
# Slack over the upload+inference time for one second of audio.
TIMEOUT_SLACK_SECONDS = 15


def timeout_for(wav_bytes: bytes) -> int:
    """Seconds to allow for this much audio.

    A single flat timeout is wrong in both directions: it is far too generous
    for a two-second dictation and far too tight for the ten minutes
    config.py allows, which fails with a bogus "timed out" rather than a real
    error. Scale with the payload instead.
    """
    seconds = len(wav_bytes) / BYTES_PER_SECOND
    return int(min(MAX_TIMEOUT, max(MIN_TIMEOUT, seconds * 1.5 + TIMEOUT_SLACK_SECONDS)))


class TranscriptionError(RuntimeError):
    """Raised when the API call fails or returns nothing usable."""


def pcm_to_wav(pcm: np.ndarray, sample_rate: int = 16000) -> bytes:
    """Wrap raw int16 samples in a RIFF/WAVE container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.astype(np.int16).tobytes())
    return buf.getvalue()


# Corpus watermarks. Whisper emits these verbatim when it has nothing but noise
# to work with, and they recur take after take. They are never something the
# speaker said, so they are always safe to drop.
#
# Split in two on purpose: the bare tokens are matched whole, so dictating
# "go to amara.org" survives, while only the multi-word boilerplate is matched
# as a substring.
WATERMARKS_EXACT = (
    "amara.org",
    "www.amara.org",
    "castingwords.com",
    "www.castingwords.com",
)
WATERMARK_PHRASES = (
    "subtitles by",
    "transcription by",
    "transcribed by",
    "amara.org community",
)

# Bracketed stage directions, which the model writes instead of transcribing.
# These are annotations about the audio, not speech.
NON_SPEECH = re.compile(
    r"^[\[(]\s*(blank_?audio|silence|silencio|noise|noises|music|musica|"
    r"inaudible|unintelligible|no speech|applause|aplausos|laughing|riso|"
    r"blank|musical|sound)\s*[\])]$",
    re.IGNORECASE,
)


def _strip_hallucinations(text: str) -> str:
    """Drop what Whisper invents when there is no speech to transcribe.

    Deliberately narrow. An earlier version also dropped short common phrases
    such as "you", "thank you." and "bye.", which made a perfectly real
    one-word dictation come back as "No speech detected." Silence is already
    rejected upstream by the RMS gate in the recorder, so the job here is the
    recurring watermark and the bracketed annotation, nothing more.
    """
    stripped = str(text or "").strip()
    if not stripped:
        return ""
    lowered = stripped.lower()
    if lowered in WATERMARKS_EXACT:
        return ""
    if any(phrase in lowered for phrase in WATERMARK_PHRASES):
        return ""
    if NON_SPEECH.match(stripped):
        return ""
    return stripped


def transcribe(
    wav_bytes: bytes,
    api_key: str,
    model: str = "whisper-large-v3",
    language: str = "en",
    prompt: str = "",
    timeout: int | None = None,
) -> str:
    """Transcribe WAV bytes. Raises TranscriptionError on any failure.

    `timeout` defaults to a value scaled to the audio length; pass an int to
    override it.
    """
    if not api_key:
        raise TranscriptionError("Missing Groq API key (set GROQ_API_KEY).")
    if not wav_bytes:
        raise TranscriptionError("No audio captured.")

    if timeout is None:
        timeout = timeout_for(wav_bytes)
    audio_seconds = len(wav_bytes) / BYTES_PER_SECOND

    data = {
        "model": model,
        "temperature": "0",
        "response_format": "json",
    }
    if language and language != "auto":
        data["language"] = language
    if prompt.strip():
        data["prompt"] = prompt.strip()

    try:
        resp = requests.post(
            ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data=data,
            timeout=timeout,
        )
    except requests.Timeout as exc:
        raise TranscriptionError(
            f"Groq timed out after {timeout}s "
            f"({audio_seconds:.0f}s of audio). Check your connection."
        ) from exc
    except requests.RequestException as exc:
        raise TranscriptionError(f"Network error: {exc}") from exc

    if resp.status_code == 401:
        raise TranscriptionError("Groq rejected the API key (401).")
    if resp.status_code == 429:
        raise TranscriptionError("Groq rate limit hit. Try again shortly.")
    if not resp.ok:
        detail = resp.text[:200].replace("\n", " ")
        raise TranscriptionError(f"Groq error {resp.status_code}: {detail}")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise TranscriptionError("Unreadable response from Groq.") from exc

    text = _strip_hallucinations(payload.get("text", ""))
    if not text:
        raise TranscriptionError("No speech detected.")
    return text
