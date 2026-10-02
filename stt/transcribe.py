"""Groq Whisper transcription.

Sends 16 kHz mono PCM (wrapped in a WAV container) to Groq's
/audio/transcriptions endpoint. temperature=0 plus an optional prompt is what
gets you the "recognizes perfectly" behaviour: no hallucinated filler, stable
punctuation and casing.
"""

from __future__ import annotations

import io
import wave

import numpy as np
import requests

ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"


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


def _strip_hallucinations(text: str) -> str:
    """Whisper sometimes emits these for silence or noise."""
    junk = {
        "you",
        "thank you.",
        "thanks for watching!",
        "bye.",
        "subtitles by the amara.org community",
        "amara.org",
    }
    stripped = text.strip()
    if stripped.lower() in junk:
        return ""
    return stripped


def transcribe(
    wav_bytes: bytes,
    api_key: str,
    model: str = "whisper-large-v3",
    language: str = "en",
    prompt: str = "",
    timeout: int = 60,
) -> str:
    """Transcribe WAV bytes. Raises TranscriptionError on any failure."""
    if not api_key:
        raise TranscriptionError("Missing Groq API key (set GROQ_API_KEY).")
    if not wav_bytes:
        raise TranscriptionError("No audio captured.")

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
        raise TranscriptionError("Groq timed out. Check your connection.") from exc
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
