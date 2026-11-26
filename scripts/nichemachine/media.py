"""Looking at video on your own computer: what's in a file, how much is moving, where it cuts, contact sheets, loudness."""
from __future__ import annotations

import json
import re
import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import draw
from .util import ffmpeg, ffprobe, hms, run, say, warn

SAMPLE_FPS = 4            # looks per second when measuring motion
SMALL_W, SMALL_H = 160, 90


def probe(path: Path) -> dict:
    p = run([ffprobe(), "-v", "error", "-show_entries",
             "stream=codec_type,width,height,r_frame_rate,avg_frame_rate:stream_side_data=rotation:format=duration",
             "-of", "json", str(path)])
    d = json.loads(p.stdout or "{}")
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in d.get("streams", []))
    fps = 30.0
    for key in ("avg_frame_rate", "r_frame_rate"):
        fr = v.get(key) or "0/0"
        try:
            a, b = fr.split("/")
            if float(b) > 0 and float(a) > 0:
                fps = float(a) / float(b)
                break
        except Exception:
            pass
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    if abs(rot) in (90, 270):
        w, h = h, w
    return {"w": w, "h": h, "fps": fps, "dur": float(d.get("format", {}).get("duration") or 0), "audio": has_audio}


def extract_wav(path: Path, out: Path, start: float | None = None, dur: float | None = None, sr: int = 16000) -> Path:
    meta = out.with_name(out.name + ".source.json")
    if out.exists() and out.stat().st_size > 1000 and start is None:
        st = path.stat()
        identity = f"{path.resolve()}:{st.st_size}:{st.st_mtime_ns}:{sr}"
        try:
            if json.loads(meta.read_text(encoding="utf-8")).get("source") == identity:
                return out
        except (OSError, ValueError, AttributeError):
            pass
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", str(out)]
    run(cmd)
    if start is None:
        st = path.stat()
        identity = f"{path.resolve()}:{st.st_size}:{st.st_mtime_ns}:{sr}"
        meta.write_text(json.dumps({"source": identity}), encoding="utf-8")
    return out


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    return x, sr


def loudness(path: Path) -> float:
    p = subprocess.run([ffmpeg(), "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128=peak=true",
                        "-f", "null", "-"], capture_output=True, text=True, errors="replace")
    vals = re.findall(r"I:\s+(-?[\d.]+) LUFS", p.stderr)
    return float(vals[-1]) if vals else -99.0


def true_peak(path: Path) -> float:
    p = subprocess.run([ffmpeg(), "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128=peak=true",
                        "-f", "null", "-"], capture_output=True, text=True, errors="replace")
    vals = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", p.stderr)
    return float(vals[-1]) if vals else 0.0


def black_stretches(path: Path, min_s: float = 0.4) -> int:
    p = subprocess.run([ffmpeg(), "-hide_banner", "-nostats", "-i", str(path), "-vf",
                        f"blackdetect=d={min_s}:pix_th=0.06", "-an", "-f", "null", "-"],
                       capture_output=True, text=True, errors="replace")
    return len(re.findall(r"black_start", p.stderr))


# ------------------------------------------------------------------ motion, cuts and brightness

def profile(path: Path, start: float = 0.0, dur: float | None = None, cache: Path | None = None) -> dict:
    """Every 1/4 s: how much the picture moved, how different it is from the last look (a cut), how bright it is.
    One small decode of the whole file (160 x 90 grey); a two-hour recording takes a minute or two."""
    if cache and cache.exists():
        try:
            d = json.loads(cache.read_text(encoding="utf-8"))
            st = path.stat()
            identity = f"{path.resolve()}:{st.st_size}:{st.st_mtime_ns}"
            if d.get("start") == start and d.get("dur") == dur and d.get("source") == identity:
                return d
        except (OSError, ValueError, AttributeError):
            pass
    cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if dur:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-an", "-vf", f"fps={SAMPLE_FPS},scale={SMALL_W}:{SMALL_H}:flags=area,format=gray",
            "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    n = SMALL_W * SMALL_H
    motion, cut, bright = [], [], []
    prev = None
    prev_hist = None
    while True:
        buf = p.stdout.read(n)
        if not buf or len(buf) < n:
            break
        fr = np.frombuffer(buf, dtype=np.uint8).astype(np.float32).reshape(SMALL_H, SMALL_W)
        hist = np.histogram(fr, bins=32, range=(0, 256))[0].astype(np.float32)
        hist /= max(1.0, hist.sum())
        bright.append(float(fr.mean()))
        if prev is None:
            motion.append(0.0)
            cut.append(0.0)
        else:
            motion.append(float(np.mean(np.abs(fr - prev))))
            cut.append(float(0.5 * np.abs(hist - prev_hist).sum()))      # 0 = same colours, 1 = nothing alike
        prev, prev_hist = fr, hist
    p.wait()
    st = path.stat()
    identity = f"{path.resolve()}:{st.st_size}:{st.st_mtime_ns}"
    d = {"start": start, "dur": dur, "source": identity, "step": 1.0 / SAMPLE_FPS, "motion": [round(x, 2) for x in motion],
         "cut": [round(x, 3) for x in cut], "bright": [round(x, 1) for x in bright]}
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(d))
    return d


def hard_cuts(prof: dict, thresh: float = 0.42, min_gap: float = 1.0) -> list[float]:
    """Times (seconds from the profile start) where the picture changes completely: a cut, a respawn, a reload,
    a jump to a menu or a black screen."""
    step = prof["step"]
    cut = np.array(prof["cut"])
    bright = np.array(prof["bright"])
    out: list[float] = []
    for i in range(1, len(cut)):
        big = cut[i] >= thresh
        to_black = bright[i] < 14 <= bright[i - 1]
        if big or to_black:
            t = i * step
            if not out or t - out[-1] >= min_gap:
                out.append(round(t, 2))
    return out


# ------------------------------------------------------------------ contact sheets (for Claude to look at)

def frames_at(path: Path, times: list[float], width: int = 360) -> list[Image.Image]:
    imgs = []
    for t in times:
        p = subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(0, t):.3f}", "-i", str(path),
                            "-frames:v", "1", "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                           capture_output=True)
        try:
            from io import BytesIO
            imgs.append(Image.open(BytesIO(p.stdout)).convert("RGB"))
        except Exception:
            imgs.append(Image.new("RGB", (width, width * 9 // 16), (30, 30, 30)))
    return imgs


def contact_sheet(path: Path, out: Path, start: float, end: float, every: float = 1.0, cols: int = 6,
                  width: int = 320, label: str = "") -> list[Path]:
    """Grid(s) of frames with their times, at most 48 per image."""
    times = []
    t = start
    while t <= end + 1e-6:
        times.append(round(t, 2))
        t += every
    if not times:
        times = [start]
    sheets = []
    per = 48
    for k in range(0, len(times), per):
        ts = times[k:k + per]
        tiles = _segment_frames(path, ts, width)
        th = tiles[0].height if tiles else width * 9 // 16
        rows = (len(ts) + cols - 1) // cols
        head = 44 if label else 0
        sheet = Image.new("RGB", (cols * width, rows * (th + 26) + head), (16, 16, 16))
        d = ImageDraw.Draw(sheet)
        f = draw.font(22, bold=False)
        if label:
            d.text((10, 8), label, font=draw.font(26), fill=(255, 255, 255))
        for i, (tt, im) in enumerate(zip(ts, tiles)):
            x = (i % cols) * width
            y = head + (i // cols) * (th + 26)
            sheet.paste(im, (x, y + 26))
            d.text((x + 6, y + 2), hms(tt, 2), font=f, fill=(255, 214, 10))
        o = out if len(times) <= per else out.with_name(f"{out.stem}_{k // per + 1}{out.suffix}")
        o.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(o, quality=82)
        sheets.append(o)
    return sheets


def _segment_frames(path: Path, ts: list[float], width: int) -> list[Image.Image]:
    """Frames at evenly spaced times in one ffmpeg pass (much faster than one seek per frame)."""
    if len(ts) < 3:
        return frames_at(path, ts, width)
    step = ts[1] - ts[0]
    a = ts[0]
    dur = ts[-1] - a + step * 0.5
    cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{a:.3f}", "-i", str(path), "-t", f"{dur:.3f}",
           "-an", "-vf", f"fps=1/{step:.4f}:start_time=0,scale={width}:-2", "-f", "image2pipe", "-vcodec", "png", "-"]
    p = subprocess.run(cmd, capture_output=True)
    data = p.stdout
    imgs = []
    from io import BytesIO
    sig = b"\x89PNG\r\n\x1a\n"
    parts = data.split(sig)
    for part in parts[1:]:
        try:
            imgs.append(Image.open(BytesIO(sig + part)).convert("RGB"))
        except Exception:
            pass
    if len(imgs) < len(ts):
        warn("contact sheet: fewer frames than asked, filling the rest one by one")
        imgs += frames_at(path, ts[len(imgs):], width)
    return imgs[:len(ts)]


def cover(path: Path, t: float, out: Path) -> None:
    run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", str(path),
         "-frames:v", "1", "-q:v", "3", str(out)], check=False)
