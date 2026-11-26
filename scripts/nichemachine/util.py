"""Shared helpers: folders, settings, running tools, time formats, JSON."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
APP = Path(os.environ.get("NICHE_HOME", HOME / ".niche-machine"))       # its own Python, models, settings
WORK = Path(os.environ.get("NICHE_WORK", HOME / "NicheMachine"))         # projects and finished Shorts
MODELS = APP / "models"
VENV = APP / "venv"
SETTINGS = APP / "settings.json"
SKILL_DIR = Path(__file__).resolve().parents[2]                          # .../niche-machine
PROJECTS = WORK / "projects"
SHORTS = WORK / "shorts"

W, H = 1080, 1920          # every Short is 1080 x 1920
FPS = 30

DEFAULTS = {
    "whisper_model": "small.en",
    "voice": "am_michael",          # the free voice (Kokoro). `nm voices` lists them all
    "voice_speed": 1.08,
    "hashtags": ["#GTA6", "#GTAVI", "#gaming", "#shorts"],
    "game_volume_under_voice": -22,  # dB: gameplay sound under a commentary voice
    "max_short_s": 59,
}

MODES = ["commentary", "compare", "stunts", "myths", "moments", "rank", "skit"]


def settings() -> dict:
    s = dict(DEFAULTS)
    if SETTINGS.exists():
        try:
            s.update(json.loads(SETTINGS.read_text()))
        except Exception:
            pass
    return s


def save_settings(s: dict) -> None:
    APP.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(s, indent=2))


def say(msg: str) -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    print(f"! {msg}", file=sys.stderr, flush=True)


def which(name: str) -> str | None:
    venv_bin = VENV / ("Scripts" if os.name == "nt" else "bin") / (name + (".exe" if os.name == "nt" and not name.lower().endswith(".exe") else ""))
    if venv_bin.exists():
        return str(venv_bin)
    p = shutil.which(name)
    if p:
        return p
    if os.name == "nt":
        # winget packages commonly install under these locations before the
        # current process has inherited the updated PATH.
        roots = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
                 Path(os.environ.get("LOCALAPPDATA", HOME / "AppData/Local"))]
        if name.lower() in ("ffmpeg", "ffprobe"):
            candidates = [r / "WinGet/Links" / f"{name}.exe" for r in roots]
            candidates += [r / "Microsoft/WinGet/Links" / f"{name}.exe" for r in roots]
            for pth in candidates:
                if pth.exists(): return str(pth)
    for d in ("/opt/homebrew/bin", "/usr/local/bin"):
        if Path(d, name).exists(): return str(Path(d, name))
    if os.name == "nt":
        bundled = MODELS / (name if name.lower().endswith(".exe") else name + ".exe")
        if bundled.exists(): return str(bundled)
    return None


def ffmpeg() -> str:
    exe = which("ffmpeg")
    if not exe:
        raise SystemExit("ffmpeg is missing. Run: nm setup")
    return exe


def ffprobe() -> str:
    exe = which("ffprobe")
    if not exe:
        raise SystemExit("ffprobe is missing. Run: nm setup")
    return exe


def run(cmd: list[str], check: bool = True, capture: bool = True, timeout: float | None = None,
        env: dict | None = None) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    if env:
        e.update(env)
    p = subprocess.run([str(c) for c in cmd], capture_output=capture, text=True, errors="replace",
                       timeout=timeout, env=e)
    if check and p.returncode != 0:
        tail = (p.stderr or p.stdout or "").strip().splitlines()[-8:]
        raise RuntimeError(f"{Path(str(cmd[0])).name} failed ({p.returncode}):\n  " + "\n  ".join(tail))
    return p


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(path)


def hms(sec: float, decimals: int = 0) -> str:
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec - h * 3600 - m * 60
    s_txt = f"{s:0{3 + decimals}.{decimals}f}" if decimals else f"{int(s):02d}"
    return f"{h}:{m:02d}:{s_txt}" if h else f"{m}:{s_txt}"


def parse_t(v) -> float:
    """'1:02:03', '62:03', '3723', 3723.5, '1:02:03.4' -> seconds."""
    if v is None:
        raise ValueError("missing time")
    if isinstance(v, (int, float)):
        return float(v)
    v = str(v).strip()
    if re.fullmatch(r"\d+(\.\d+)?", v):
        return float(v)
    parts = v.split(":")
    if not 2 <= len(parts) <= 3:
        raise ValueError(f"bad time: {v!r}")
    nums = [float(p) for p in parts]
    while len(nums) < 3:
        nums.insert(0, 0.0)
    return nums[0] * 3600 + nums[1] * 60 + nums[2]


def slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", str(text).lower()).strip("-")
    return (s[:n].rstrip("-")) or "short"


def today() -> str:
    return time.strftime("%Y-%m-%d")


def resolve(path: str | Path, base: Path | None = None) -> Path:
    """A path from a plan: absolute, ~, or relative to the project folder. A path typed on the command line
    (no base) is made absolute from where the command runs, so a plan that stores it still finds it later."""
    p = Path(os.path.expanduser(str(path)))
    if not p.is_absolute():
        p = (base / p) if base is not None else p.absolute()
    return p


VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".ts"}


def videos_in(path: Path) -> list[Path]:
    """A file, or every video in a folder and its subfolders (one subfolder per session or per game is common)."""
    path = Path(path)
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    return sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXT
                  and not any(part.startswith(".") for part in p.relative_to(path).parts))


class Timer:
    def __init__(self):
        self.t0 = time.time()

    def secs(self) -> float:
        return time.time() - self.t0

    @staticmethod
    def fmt(sec: float) -> str:
        m, s = divmod(int(round(sec)), 60)
        return f"{m} min {s:02d} s" if m else f"{s} s"
