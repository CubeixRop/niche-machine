"""Listening on your own computer with whisper.cpp: the transcript of a play session (laughs, screams, what you
said) and word-by-word timings for captions. Plus how loud each moment is against your own normal."""
from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import wave
from pathlib import Path

import numpy as np

from .util import MODELS, run, say, settings, which

EXTRA_MODEL_DIRS = [Path.home() / ".cache/hyperframes/whisper/models", Path("/opt/homebrew/share/whisper-cpp"),
                    Path("/usr/local/share/whisper-cpp"), Path.home() / ".clip-farm/models"]

LAUGH = re.compile(r"\b(laugh\w*|chuckl\w*|giggl\w*)\b", re.I)
SHOUT = re.compile(r"\b(scream\w*|shout\w*|yell\w*|gasp\w*|cheer\w*|groan\w*)\b", re.I)
MUSIC = re.compile(r"♪|\bmusic\b|\bsinging\b", re.I)
HYPE = re.compile(r"\b(no way|oh my god|what the|let'?s go|bro|dude|holy|yo+|wait|run|help|nah|stop)\b", re.I)


def model_path(name: str | None = None) -> Path:
    name = name or settings()["whisper_model"]
    fn = f"ggml-{name}.bin"
    for d in [MODELS] + EXTRA_MODEL_DIRS:
        p = d / fn
        if p.exists() and p.stat().st_size > 10_000_000:
            return p
    return MODELS / fn


def whisper_exe() -> str:
    exe = which("whisper-cli") or which("whisper-cpp")
    if not exe:
        raise SystemExit("whisper-cli is missing. Run: nm setup")
    return exe


def _threads() -> int:
    return max(2, min(8, (os.cpu_count() or 4) - 2))


def transcribe(wav: Path, out_base: Path) -> dict:
    out_json = out_base.with_suffix(".json")
    fingerprint = _file_fingerprint(wav)
    meta = out_base.with_name(out_base.name + ".source.json")
    if out_json.exists() and out_json.stat().st_size > 50:
        try:
            if json.loads(meta.read_text(encoding="utf-8")).get("fingerprint") == fingerprint:
                return json.loads(out_json.read_bytes().decode("utf-8", "replace"))
        except (OSError, ValueError, AttributeError):
            pass
    mp = model_path()
    if not mp.exists():
        raise SystemExit(f"Whisper model missing ({mp.name}). Run: nm setup")
    cmd = [whisper_exe(), "-m", str(mp), "-f", str(wav), "-l", "en", "-oj", "-of", str(out_base),
           "-bs", "1", "-bo", "1", "-mc", "0", "-t", str(_threads()), "-np", "-pp"]
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace")
    last = -10
    for line in p.stderr:
        m = re.search(r"progress\s*=\s*(\d+)%", line)
        if m:
            v = int(m.group(1))
            if v >= last + 20:
                say(f"  listening… {v}%")
                last = v
    if p.wait() != 0 or not out_json.exists():
        raise RuntimeError("whisper-cli failed on the audio")
    result = json.loads(out_json.read_bytes().decode("utf-8", "replace"))
    meta.write_text(json.dumps({"fingerprint": fingerprint}), encoding="utf-8")
    return result


def _file_fingerprint(path: Path) -> str:
    st = path.stat()
    h = hashlib.sha256()
    h.update(f"{st.st_size}:{st.st_mtime_ns}".encode())
    with path.open("rb") as f:
        h.update(f.read(1024 * 1024))
        if st.st_size > 1024 * 1024:
            f.seek(max(0, st.st_size - 1024 * 1024))
            h.update(f.read(1024 * 1024))
    return h.hexdigest()


def segments(tr: dict) -> list[dict]:
    """[{s, e, text, kind}] kind: speech | laugh | shout | music | sound"""
    out = []
    prev = None
    for seg in tr.get("transcription") or []:
        s = seg["offsets"]["from"] / 1000.0
        e = seg["offsets"]["to"] / 1000.0
        txt = re.sub(r"\s+", " ", seg.get("text") or "").strip()
        if not txt:
            continue
        kind = "speech"
        bare = re.sub(r"^[\-\s]+", "", txt)
        if MUSIC.search(bare) and ("♪" in bare or re.fullmatch(r"[\(\[].*[\)\]]", bare)):
            kind = "music"
        elif re.fullmatch(r"[\(\[\*].*[\)\]\*]", bare):
            kind = "laugh" if LAUGH.search(bare) else "shout" if SHOUT.search(bare) else "sound"
        if kind == "speech":
            if prev and bare.lower() == prev.lower() and out and s - out[-1]["e"] < 5:
                out[-1]["e"] = e
                continue
            prev = bare
        out.append({"s": round(s, 2), "e": round(max(e, s + 0.2), 2), "text": bare, "kind": kind})
    return out


def energy(wav: Path, hop: float = 0.25) -> dict:
    """Loudness in dB per 0.25 s and how far above your own normal each moment is (z-score over ~5 minutes)."""
    with wave.open(str(wav), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        step = int(sr * hop)
        out = np.empty(n // step + 1, dtype=np.float32)
        k = 0
        block = step * 400
        while True:
            raw = w.readframes(block)
            if not raw:
                break
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            m = len(x) // step
            if m:
                r = np.sqrt(np.mean(x[: m * step].reshape(m, step) ** 2, axis=1) + 1e-10)
                out[k:k + m] = 20 * np.log10(r)
                k += m
    db = out[:k]
    base, spread = _rolling_stats(db, int(300 / hop))
    z = (db - base) / np.maximum(spread, 1.5)
    return {"hop": hop, "db": db.round(2).tolist(), "z": z.round(2).tolist(), "duration": n / sr}


def _rolling_stats(x: np.ndarray, block: int):
    n = len(x)
    centers, meds, mads = [], [], []
    for s in range(0, n, max(1, block // 4)):
        seg = x[max(0, s - block // 2): s + block // 2]
        seg = seg[seg > -70]
        if len(seg) < 10:
            continue
        med = float(np.median(seg))
        centers.append(s)
        meds.append(med)
        mads.append(float(np.median(np.abs(seg - med))) * 1.4826)
    if not centers:
        return np.full(n, -40.0), np.full(n, 6.0)
    idx = np.arange(n)
    return np.interp(idx, centers, meds), np.interp(idx, centers, mads)


# ------------------------------------------------------------------ word timings (captions)

def _split_points(wav: Path, max_len: float = 8.0, min_len: float = 2.5) -> list[tuple[float, float]]:
    """Windows of at most ~8 s cut at the quietest moments: whisper's word timings drift on long stretches."""
    with wave.open(str(wav), "rb") as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    hop = int(sr * 0.02)
    m = len(x) // hop
    if m == 0:
        return [(0.0, len(x) / sr)]
    db = 20 * np.log10(np.sqrt(np.mean(x[: m * hop].reshape(m, hop) ** 2, axis=1)) + 1e-10)
    db = np.convolve(db, np.ones(10) / 10, mode="same")
    total = len(x) / sr
    out, start = [], 0.0
    while total - start > max_len:
        lo, hi = int((start + min_len) / 0.02), int((start + max_len) / 0.02)
        cut = (lo + int(np.argmin(db[lo:hi]))) * 0.02
        out.append((start, cut))
        start = cut
    out.append((start, total))
    return out


def _words_from_json(d: dict, offset: float) -> list[dict]:
    words: list[dict] = []
    for seg in d.get("transcription") or []:
        for tok in seg.get("tokens") or []:
            t = tok.get("text") or ""
            if t.startswith("[_") or t.startswith("<|") or not t.strip():
                continue
            t0 = tok["offsets"]["from"] / 1000.0
            t1 = tok["offsets"]["to"] / 1000.0
            dtw = tok.get("t_dtw")
            if dtw is not None and dtw >= 0:
                t0 = dtw / 100.0
            if t.startswith(" ") or not words:
                words.append({"w": t.strip(), "s": t0 + offset, "e": t1 + offset})
            else:
                words[-1]["w"] += t
                words[-1]["e"] = t1 + offset
    return words


def words(wav: Path, prompt: str | None = None) -> list[dict]:
    """Word-by-word timings for a short piece of audio (whisper.cpp, DTW alignment, window by window).
    `prompt`: the script, when we know what was said (a voice-over), so names come out spelled right."""
    mp = model_path()
    name = mp.name.replace("ggml-", "").replace(".bin", "")
    wins = _split_points(wav)
    parts = []
    for i, (a, b) in enumerate(wins):
        pw = wav.with_name(f"{wav.stem}_w{i:02d}.wav")
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i",
             str(wav), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(pw)])
        parts.append((pw, a))
    files = []
    for pw, _ in parts:
        files += ["-f", str(pw)]
    cmd = [whisper_exe(), "-m", str(mp)] + files + ["-l", "en", "-ojf", "-np", "-t", str(_threads()), "-nfa",
                                                     "--dtw", name, "-mc", "0"]
    if prompt:
        cmd += ["--prompt", prompt[:600]]
    p = run(cmd, check=False, timeout=900)
    if p.returncode != 0:
        run([c for c in cmd if c not in ("-nfa", "--dtw", name)], timeout=900)
    out: list[dict] = []
    for pw, a in parts:
        j = Path(str(pw) + ".json")
        if j.exists():
            out += _words_from_json(json.loads(j.read_bytes().decode("utf-8", "replace")), a)
        for f in (pw, j):
            try:
                f.unlink()
            except Exception:
                pass
    clean = []
    in_tag = False
    for w in out:
        tx = w["w"]
        if tx.startswith(("(", "[", "*")):
            in_tag = True
        if in_tag:
            if tx.endswith((")", "]", "*")):
                in_tag = False
            continue
        if "♪" in tx or not re.search(r"[A-Za-z0-9]", tx):
            continue
        clean.append(w)
    for i, w in enumerate(clean):
        if i + 1 < len(clean) and clean[i + 1]["s"] > w["s"]:
            w["e"] = min(max(w["e"], w["s"] + 0.08), clean[i + 1]["s"])
        w["e"] = max(w["e"], w["s"] + 0.08)
    return clean


def respell(words_: list[dict], script: str) -> list[dict]:
    """When we know the script (our own voice-over), put its spelling on the timed words: GTA 6, not 'GTA six' or
    'GTA VI'. The two word lists are aligned as sequences; where whisper heard the same number of words differently,
    the script's spelling wins, except that whisper's digits beat a number the script spells out for the voice
    ('twelfth' is read, '12th' is shown)."""
    import difflib
    sw = script.split()
    if not sw or not words_:
        return words_

    def norm(x):
        return re.sub(r"[^a-z0-9]", "", x.lower())

    a = [norm(w["w"]) for w in words_]
    b = [norm(x) for x in sw]
    out = [dict(w) for w in words_]
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for k in range(i2 - i1):
                heard, written = out[i1 + k]["w"], sw[j1 + k]
                if not b[j1 + k]:
                    continue
                if tag == "replace" and re.search(r"\d", heard) and not re.search(r"\d", written):
                    continue
                out[i1 + k]["w"] = written
    return out
