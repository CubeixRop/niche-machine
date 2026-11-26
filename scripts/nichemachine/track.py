"""An overlay track: transparent pictures shown at set times in one box of the frame. Each track becomes one
image list (ffconcat) that ffmpeg lays over the video in a single pass."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from .util import FPS


class Track:
    def __init__(self, name: str, folder: Path, x: int, y: int, w: int, h: int):
        self.name = name
        self.folder = folder / name
        self.folder.mkdir(parents=True, exist_ok=True)
        for f in self.folder.glob("*.png"):
            f.unlink()
        self.x, self.y, self.w, self.h = x, y, w, h
        self.events: list[tuple[float, float, Path]] = []
        self._k = 0
        self.blank = self.folder / "blank.png"
        Image.new("RGBA", (w, h), (0, 0, 0, 0)).save(self.blank)

    def _save(self, img: Image.Image) -> Path:
        if img.size != (self.w, self.h):
            fr = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
            fr.alpha_composite(img.convert("RGBA"), ((self.w - img.width) // 2, (self.h - img.height) // 2))
            img = fr
        p = self.folder / f"f_{self._k:05d}.png"
        self._k += 1
        img.save(p, compress_level=1)
        return p

    def add(self, t0: float, t1: float, img: Image.Image | Path) -> None:
        p = img if isinstance(img, Path) else self._save(img)
        if t1 - t0 > 0.01:
            self.events.append((t0, t1, p))

    def add_anim(self, t0: float, t1: float, frames: list[Image.Image], hold: Image.Image | None = None,
                 out_frames: list[Image.Image] | None = None) -> None:
        """Frames at 30 fps from t0, then `hold` (or the last frame) until t1, then optional exit frames."""
        dt = 1.0 / FPS
        t = t0
        out_frames = out_frames or []
        hold_end = t1 - dt * len(out_frames)
        for fr in frames:
            if t + dt > hold_end:
                break
            self.add(t, t + dt, fr)
            t += dt
        last = hold if hold is not None else (frames[-1] if frames else None)
        if last is not None and hold_end > t:
            self.add(t, hold_end, last)
            t = hold_end
        for fr in out_frames:
            self.add(t, t + dt, fr)
            t += dt

    def add_png(self, t0: float, t1: float, path: Path) -> None:
        self.events.append((t0, t1, path))

    def write(self, duration: float) -> Path | None:
        if not self.events:
            return None
        ev = sorted(self.events, key=lambda e: e[0])
        lines = ["ffconcat version 1.0"]
        t = 0.0
        for s, e, p in ev:
            s = max(s, t)
            e = min(e, duration)
            if e - s < 0.005:
                continue
            if s > t + 0.005:
                lines += [f"file '{self.blank}'", f"duration {s - t:.4f}"]
                t = s
            lines += [f"file '{p}'", f"duration {e - t:.4f}"]
            t = e
        if t < duration:
            lines += [f"file '{self.blank}'", f"duration {duration - t + 0.5:.4f}"]
        lines += [f"file '{self.blank}'"]
        lst = self.folder / "track.ffconcat"
        lst.write_text("\n".join(lines) + "\n")
        return lst
