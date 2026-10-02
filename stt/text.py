"""Text folding shared by the voice-command matcher.

A spoken app name, a window title and a folder name all have to be compared the
same way: lowercase, no accents, no punctuation. One implementation keeps the
thresholds in voice_command and windows honest about each other.
"""

from __future__ import annotations

import re
import unicodedata


def strip_accents(text: str) -> str:
    """'Música' -> 'Musica'. Windows filenames do not carry the accent."""
    decomposed = unicodedata.normalize("NFKD", str(text))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize(text: str) -> str:
    """Lowercase, drop accents and punctuation: 'Google Chrome!' -> 'google chrome'."""
    plain = strip_accents(text).lower()
    cleaned = re.sub(r"[^\w\s]", " ", plain)
    return re.sub(r"\s+", " ", cleaned).strip()