"""The free voice: Kokoro (Apache 2.0), running on your own computer. No account, no key, no cost.
Or your own voice: record the script, drop the file in, and it is used as it is."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from .util import MODELS, say, settings

KOKORO_MODEL = "kokoro-v1.0.int8.onnx"
KOKORO_VOICES = "voices-v1.0.bin"
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/{name}"

_engine = None


def _kokoro():
    global _engine
    if _engine is None:
        try:
            from kokoro_onnx import Kokoro
        except ImportError:
            raise SystemExit("The free voice isn't installed. Run: nm setup")
        m, v = MODELS / KOKORO_MODEL, MODELS / KOKORO_VOICES
        if not m.exists() or not v.exists():
            raise SystemExit("The free voice model isn't downloaded yet. Run: nm setup")
        _engine = Kokoro(str(m), str(v))
    return _engine


def voices() -> list[str]:
    return list(_kokoro().get_voices())


def without_notes(text: str) -> str:
    """The script without note lines (a line starting with #) and [stage notes]; {pic:id} cues stay."""
    t = re.sub(r"^[ \t]*#.*$", " ", text, flags=re.M)
    return re.sub(r"\[[^\]]*\]", " ", t)


def spoken(text: str) -> str:
    """What the voice should read: the script without picture cues, note lines or stage notes.
    A # inside a sentence ("the #1 car") is read, only a line that starts with # is a note."""
    t = re.sub(r"\{pic:[^}]*\}", " ", without_notes(text))
    return re.sub(r"\s+", " ", t).strip()


def synth(text: str, out: Path, voice: str | None = None, speed: float | None = None, gap: float = 0.18) -> Path:
    """Sentence by sentence (steadier timing), with a short breath between sentences. 24 kHz mono WAV."""
    import soundfile as sf
    cfg = settings()
    voice = voice or cfg["voice"]
    speed = float(speed or cfg["voice_speed"])
    k = _kokoro()
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    chunks = []
    sr = 24000
    for i, s in enumerate(sents):
        lang = "en-gb" if voice.startswith("b") else "en-us"
        audio, sr = k.create(s, voice=voice, speed=speed, lang=lang)
        chunks.append(np.asarray(audio, dtype=np.float32))
        if i + 1 < len(sents):
            chunks.append(np.zeros(int(sr * gap), dtype=np.float32))
    y = np.concatenate(chunks) if chunks else np.zeros(sr, dtype=np.float32)
    peak = float(np.max(np.abs(y))) or 1.0
    y = y * min(1.0, 0.89 / peak)
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), y, sr)
    say(f"  voice: {len(y) / sr:.1f} s ({voice}, speed {speed})")
    return out
