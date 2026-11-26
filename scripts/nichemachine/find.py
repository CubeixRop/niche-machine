"""Finding things in your gameplay: stretches where you're moving (commentary backgrounds), every attempt at a
stunt, and the loud, funny or wild moments of a whole play session."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

import numpy as np

from . import listen, media
from .util import APP, WORK, hms, read_json, say, slug, videos_in, warn, write_json

CACHE = WORK / "_cache"
USED = APP / "used_backgrounds.json"


def _cache_for(video: Path, what: str) -> Path:
    st = video.stat()
    # Include the resolved path, size and nanosecond mtime. Same-name files and
    # edits with an unchanged size must never reuse another recording's profile.
    identity = f"{video.resolve()}\0{st.st_size}\0{st.st_mtime_ns}".encode("utf-8", "surrogatepass")
    digest = hashlib.sha256(identity).hexdigest()[:16]
    return CACHE / f"{slug(video.stem, 30)}_{digest}_{what}.json"


def prof(video: Path) -> dict:
    c = _cache_for(video, "profile")
    if not c.exists():
        say(f"  looking through {video.name}…")
    return media.profile(video, cache=c)


# ------------------------------------------------------------------ commentary: where are you actually moving?

def moving(paths: list[Path], length: float, n: int = 5, avoid_used: bool = True) -> list[dict]:
    """The best stretches of `length` seconds: steady motion (driving, flying, running), no cuts, no menus, no black.
    Stretches already used for another Short are skipped, so every Short gets a fresh background."""
    used = read_json(USED, {}) if avoid_used else {}
    cands = []
    for v in paths:
        p = prof(v)
        step = p["step"]
        mot = np.array(p["motion"])
        bright = np.array(p["bright"])
        cut = np.array(p["cut"])
        win = int(length / step)
        if len(mot) < win + 2:
            continue
        bad = (bright < 22) | (cut > 0.42)
        # menus and pauses barely move; driving moves a lot but not chaotically
        k = np.ones(win)
        med = np.array([np.median(mot[i:i + win]) for i in range(0, len(mot) - win, 4)])
        lo = np.array([np.percentile(mot[i:i + win], 10) for i in range(0, len(mot) - win, 4)])
        badc = np.convolve(bad.astype(float), k, mode="valid")[::4][:len(med)]
        score = med + 0.8 * lo - 30 * badc
        for j in np.argsort(-score)[: n * 6]:
            a = j * 4 * step
            key = f"{v}"
            if any(abs(a - u) < length for u in used.get(key, [])):
                continue
            if any(c["file"] == str(v) and abs(c["a"] - a) < length for c in cands):
                continue
            cands.append({"file": str(v), "a": round(float(a), 2), "b": round(float(a + length), 2),
                          "score": round(float(score[j]), 2), "motion": round(float(med[j]), 2)})
    cands.sort(key=lambda c: -c["score"])
    return cands[:n]


def mark_used(file: str, a: float) -> None:
    used = read_json(USED, {})
    used.setdefault(file, []).append(round(a, 2))
    write_json(USED, used)


# ------------------------------------------------------------------ stunts: every attempt

def attempts(path: Path, out_dir: Path, min_len: float = 2.0, min_try: float = 5.0) -> list[dict]:
    """A folder of clips: one clip = one attempt. One long recording: split where the picture changes completely
    (a restart, a respawn, a reload, a black screen). Writes attempts.json and a contact sheet per 8 attempts
    (the start and the last four seconds of each) so Claude can see which one landed."""
    vids = videos_in(path)
    out_dir.mkdir(parents=True, exist_ok=True)
    found = []
    if len(vids) > 1:
        for v in vids:
            d = media.probe(v)["dur"]
            if d >= min_len:
                found.append({"file": str(v), "a": 0.0, "b": round(d, 2)})
    else:
        v = vids[0]
        p = prof(v)
        dur = media.probe(v)["dur"]
        cuts = [0.0] + media.hard_cuts(p) + [dur]
        bright = np.array(p["bright"])
        step = p["step"]
        for a, b in zip(cuts, cuts[1:]):
            # skip the black or loading stretch right after a cut
            a2 = a + 0.25
            if b - a2 < min_len:
                continue
            seg = bright[int(a2 / step): max(int(a2 / step) + 1, int(b / step))]
            if len(seg) and float(np.median(seg)) < 24:
                continue                    # a black fail screen or a loading screen, not an attempt
            if found and b - a2 < min_try and a2 - found[-1]["b"] < 1.0:
                found[-1]["b"] = round(b - 0.1, 2)   # a few seconds right after a cut (the WASTED / FAILED
                continue                             # screen fading in) belong to the try before, not a new one
            found.append({"file": str(v), "a": round(a2, 2), "b": round(b - 0.1, 2)})
    for i, f in enumerate(found, 1):
        f["n"] = i
    write_json(out_dir / "attempts.json", found)
    # contact sheets: one row per attempt
    from PIL import Image, ImageDraw
    from . import draw
    per = 8
    sheets = []
    for k in range(0, len(found), per):
        rows = found[k:k + per]
        tw, th = 240, 135
        cols = 6
        sheet = Image.new("RGB", (cols * tw + 150, len(rows) * (th + 8)), (16, 16, 16))
        d = ImageDraw.Draw(sheet)
        for r, f in enumerate(rows):
            a, b = f["a"], f["b"]
            ts = [a + 0.3] + [max(a, b - x) for x in (4, 3, 2, 1, 0.2)]
            ims = media.frames_at(Path(f["file"]), ts, tw)
            y = r * (th + 8)
            d.text((8, y + 40), f"#{f['n']}", font=draw.font(40), fill=(255, 214, 10))
            d.text((8, y + 92), f"{b - a:.0f}s", font=draw.font(24, bold=False), fill=(200, 200, 200))
            for c, im in enumerate(ims):
                sheet.paste(im.resize((tw, th)), (150 + c * tw, y))
        o = out_dir / f"attempts_{k // per + 1:02d}.jpg"
        sheet.save(o, quality=80)
        sheets.append(str(o))
    say(f"  {len(found)} attempts → {out_dir / 'attempts.json'}")
    for s in sheets:
        say(f"  look: {s}")
    return found


# ------------------------------------------------------------------ moments: a whole session

def scan(video: Path, out_dir: Path, chunk_min: float = 10.0, max_candidates: int | None = None,
         clip_min_seconds: float = 15.0, clip_max_seconds: float = 45.0,
         analyze_audio: bool = True, analyze_transcript: bool = True,
         transcript_data: dict | None = None) -> dict:
    """Listen to the whole session (your mic and the game), measure loudness against your own normal and how much
    the picture changes, then write transcript chunks and ranked candidate clips for the dashboard."""
    if max_candidates is not None and not 1 <= int(max_candidates) <= 50:
        raise ValueError("max_candidates must be between 1 and 50")
    clip_min_seconds, clip_max_seconds = float(clip_min_seconds), float(clip_max_seconds)
    if not 5 <= clip_min_seconds <= clip_max_seconds <= 45:
        raise ValueError("Clip length must be between 5 and 45 seconds, with minimum no greater than maximum")
    if not analyze_audio and not analyze_transcript:
        raise ValueError("Enable audio analysis, transcript analysis, or both")
    out_dir.mkdir(parents=True, exist_ok=True)
    source = media.probe(video)
    wav = None
    if analyze_audio:
        if not source["audio"]:
            raise RuntimeError("Audio analysis was selected, but this recording has no audio track.")
        wav = media.extract_wav(video, out_dir / "audio.wav")
        en = listen.energy(wav)
        if not len(en["z"]):
            raise RuntimeError("The recording's audio track is empty or could not be decoded.")
        hop = en["hop"]
        z = np.array(en["z"])
        dur = en["duration"]
    else:
        hop = 0.25
        dur = float(source["dur"])
        z = np.zeros(max(1, int(dur / hop)))
    if analyze_transcript:
        if transcript_data is None:
            if wav is None:
                wav = media.extract_wav(video, out_dir / "audio.wav")
            say("  listening for transcript (this takes a few minutes per hour)…")
            transcript_data = listen.transcribe(wav, out_dir / "transcript")
        tr = transcript_data
        segs = listen.segments(tr)
        write_json(out_dir / "transcript.json", tr)
    else:
        tr, segs = {}, []
        write_json(out_dir / "transcript.json", tr)
    if not source["w"]:
        warn("This file has no video track; scan will use audio and transcript cues only. Pass a video recording to include visual-motion analysis.")
    p = prof(video)
    mot = np.array(p["motion"])
    cut = np.array(p["cut"])
    n = len(z)
    # one hype curve: loud against your normal + a burst of motion + laughs and shouts in the transcript
    m_on = np.interp(np.arange(n) * hop, np.arange(len(mot)) * p["step"], mot) if len(mot) else np.zeros(n)
    m_z = (m_on - np.median(m_on)) / (np.median(np.abs(m_on - np.median(m_on))) * 1.4826 + 1e-6)
    hype = (np.clip(z, 0, 6) / 6 * 0.6 if analyze_audio else np.zeros(n))
    if source["w"]:
        hype += np.clip(m_z, 0, 6) / 6 * 0.25
    for s in segs:
        a, b = int(s["s"] / hop), int(s["e"] / hop) + 1
        if s["kind"] in ("laugh", "shout"):
            hype[a:b] += 0.35
        elif s["kind"] == "speech" and listen.HYPE.search(s["text"]):
            hype[a:b] += 0.15
    if len(hype):
        kernel = min(8, len(hype))
        hype = np.convolve(hype, np.ones(kernel) / kernel, mode="same")
    p97, p92 = float(np.percentile(hype, 97)), float(np.percentile(hype, 92))
    # top moments: peaks at least 25 s apart
    order = np.argsort(-hype)
    peaks = []
    suppressed = np.zeros(len(hype), dtype=bool)
    spacing = max(1, int(25 / hop))
    for i in order:
        if suppressed[i]:
            continue
        t = i * hop
        if hype[i] < max(p92, 0.08):
            break
        peaks.append(t)
        suppressed[max(0, i - spacing):min(len(suppressed), i + spacing + 1)] = True
        if max_candidates is not None and len(peaks) >= int(max_candidates):
            break
    peaks.sort()
    cands = []
    for t in peaks:
        peak_index = min(len(hype) - 1, max(0, int(t / hop)))
        event_floor = max(p92, float(hype[peak_index]) * 0.45)
        left_limit = max(0, peak_index - int(clip_max_seconds * 0.7 / hop))
        right_limit = min(len(hype) - 1, peak_index + int(clip_max_seconds * 0.3 / hop))
        left, right = peak_index, peak_index
        while left > left_limit and hype[left - 1] >= event_floor:
            left -= 1
        while right < right_limit and hype[right + 1] >= event_floor:
            right += 1
        activity_length = max(0.0, (right - left + 1) * hop)
        clip_length = max(clip_min_seconds, min(clip_max_seconds, activity_length + 4.0))
        if dur < clip_min_seconds:
            continue
        a = max(0.0, min(t - clip_length * 0.7, dur - clip_length))
        b = min(dur, a + clip_length)
        if b - a + 0.05 < clip_min_seconds:
            continue
        cands.append({"peak": round(t, 1), "a": round(a, 1), "b": round(b, 1),
                      "duration": round(b - a, 1), "hype": round(float(hype[peak_index]), 3)})
    write_json(out_dir / "candidates.json", cands)
    # chunk files: transcript lines with 🔥 marks
    chunks = []
    k = 0
    t0 = 0.0
    while t0 < dur:
        t1 = min(dur, t0 + chunk_min * 60)
        lines = [f"# Chunk {k + 1:02d} · {hms(t0)}–{hms(t1)} of {video.name}", "",
                 "Each line: time, the hype mark (🔥🔥 top 3 %, 🔥 top 8 % of this session), what was heard.", ""]
        for s in segs:
            if s["s"] < t0 or s["s"] >= t1:
                continue
            a, b = int(s["s"] / hop), max(int(s["s"] / hop) + 1, int(s["e"] / hop))
            h = float(np.max(hype[a:b])) if b > a else 0.0
            mark = "🔥🔥" if h >= p97 else "🔥" if h >= p92 else "  "
            tag = f"[{s['kind']}] " if s["kind"] != "speech" else ""
            lines.append(f"{hms(s['s'])} {mark} {tag}{s['text']}")
        silent_peaks = [c for c in cands if t0 <= c["peak"] < t1]
        if silent_peaks:
            lines += ["", "Hot seconds (loud or wild on screen, even with nobody talking):"]
            lines += [f"- {hms(c['peak'])} (hype {c['hype']})" for c in silent_peaks]
        f = out_dir / f"chunk_{k + 1:02d}.md"
        f.write_text("\n".join(lines) + "\n")
        chunks.append(str(f))
        k += 1
        t0 = t1
    info = {"file": str(video), "duration": round(dur, 1), "chunks": chunks, "candidates": len(cands),
            "clip_min_seconds": clip_min_seconds, "clip_max_seconds": clip_max_seconds}
    write_json(out_dir / "scan.json", info)
    say(f"  {hms(dur)} of gameplay · {len(segs)} lines heard · {len(cands)} hot moments · {len(chunks)} chunk(s)")
    for c in chunks:
        say(f"  read: {c}")
    return info
