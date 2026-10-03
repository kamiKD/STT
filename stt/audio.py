"""Microphone capture for push-to-talk.

Uses sounddevice's callback-based RawInputStream so recording never blocks the
UI thread: the callback only appends raw int16 blocks to a list and keeps a
rolling RMS level for the overlay meter.
"""

from __future__ import annotations

import math
import threading
from collections import deque

import numpy as np
import sounddevice as sd

BLOCK = 1024

# How far past max_seconds the buffer may grow. The caller's timer stops the
# recorder at max_seconds, so this is never reached in practice; it exists only
# so a wedged timer cannot grow the buffer without bound.
MEMORY_SLACK_SECONDS = 10


class Recorder:
    """Single-channel 16 kHz mono recorder with a live level meter."""

    def __init__(self, sample_rate: int = 16000, max_seconds: int = 120):
        self.sample_rate = int(sample_rate)
        self.max_seconds = int(max_seconds)
        self._lock = threading.Lock()
        self._chunks: list[np.ndarray] = []
        self._frames = 0
        self._stream: sd.RawInputStream | None = None
        self._levels: deque[float] = deque(maxlen=40)
        self._active = False
        self._truncated = False
        self._frozen = False
        self._frames_cap = self._hard_cap()

    def _hard_cap(self) -> int:
        return self.sample_rate * (self.max_seconds + MEMORY_SLACK_SECONDS)

    # ------------------------------------------------------------------ state
    @property
    def is_recording(self) -> bool:
        return self._active

    @property
    def truncated(self) -> bool:
        """True once the recording ran into the max_seconds limit."""
        return self._truncated

    def level(self) -> float:
        """Smoothed RMS in 0..1, for the overlay waveform."""
        if not self._levels:
            return 0.0
        return min(1.0, sum(self._levels) / len(self._levels) * 12.0)

    @property
    def elapsed(self) -> float:
        return self._frames / float(self.sample_rate)

    # ----------------------------------------------------------------- stream
    def list_devices(self) -> list[dict]:
        out = []
        for idx, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0:
                out.append(
                    {
                        "index": idx,
                        "name": dev.get("name", f"Input {idx}"),
                        "channels": dev.get("max_input_channels", 0),
                        "default": dev.get("default_samplerate", 0),
                    }
                )
        return out

    def default_input(self) -> str | None:
        try:
            # sounddevice raises if no device exists at all; treat as "no input".
            return sd.default.device[0]
        except Exception:
            return None

    def start(self, device=None) -> None:
        with self._lock:
            if self._active:
                return
            self._chunks = []
            self._frames = 0
            self._levels.clear()
            self._active = True
            self._truncated = False
            self._frozen = False
            self._frames_cap = self._hard_cap()
            try:
                stream = sd.RawInputStream(
                    samplerate=self.sample_rate,
                    blocksize=BLOCK,
                    device=device,
                    dtype="int16",
                    channels=1,
                    callback=self._callback,
                )
                stream.start()
            except Exception:
                self._active = False
                raise
            self._stream = stream

    def stop(self) -> np.ndarray:
        """Stop capture and return the recorded samples (may be empty)."""
        with self._lock:
            stream = self._stream
            self._stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        with self._lock:
            self._active = False
            chunks = self._chunks
            self._chunks = []
            self._truncated = False
            self._frozen = False
        if not chunks:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(chunks)

    def abort(self) -> None:
        self.stop()

    # ---------------------------------------------------------------- helpers
    def _callback(self, indata, frames, time_info, status) -> None:
        if not self._active:
            return
        block = np.frombuffer(indata, dtype=np.int16).copy()
        with self._lock:
            if self._frozen:
                return
            self._chunks.append(block)
            self._frames += len(block)
            self._levels.append(self._rms(block))
            if self._frames >= self.sample_rate * self.max_seconds:
                # Past the limit: flag it, but keep recording and keep owning
                # the stream. Clearing _active here used to orphan an open
                # RawInputStream (nothing would ever close it) and threw away
                # the whole take, because the caller's max timer keys off
                # is_recording. stop() is the only thing that closes a stream.
                self._truncated = True
            if self._frames >= self._frames_cap:
                # Backstop for a wedged caller timer: stop growing the buffer,
                # stay active so stop() still runs and still frees the device.
                self._frozen = True

    @staticmethod
    def _rms(block: np.ndarray) -> float:
        if block.size == 0:
            return 0.0
        x = block.astype(np.float32) / 32768.0
        return float(math.sqrt(float(np.mean(np.square(x)))))
