"""Everything drawn on top of the gameplay, with Pillow: captions, titles, labels, attempt tags, verdict stamps,
the ranking board and pop-up pictures. No special ffmpeg build needed."""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

HEAVY = [
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/Library/Fonts/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "C:/Windows/Fonts/ariblk.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
REGULAR = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
YELLOW = (255, 214, 10, 255)
WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)
RED = (230, 32, 42, 255)
GREEN = (24, 196, 92, 255)
INK = (14, 14, 14, 255)

_cache: dict = {}


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    if key in _cache:
        return _cache[key]
    for p in (HEAVY if bold else REGULAR):
        if Path(p).exists():
            _cache[key] = ImageFont.truetype(p, size)
            return _cache[key]
    _cache[key] = ImageFont.load_default(size)
    return _cache[key]


def _wrap(text: str, f, max_w: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if f.getlength(t) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _fit_lines(text: str, size: int, max_w: int, max_lines: int, min_size: int = 40, bold: bool = True):
    while True:
        f = font(size, bold)
        lines = _wrap(text, f, max_w)
        if len(lines) <= max_lines or size <= min_size:
            return f, lines, size
        size -= 4


def _shadowed(img: Image.Image, radius: int = 10, alpha: int = 130, dy: int = 8) -> Image.Image:
    a = img.split()[3]
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sh.putalpha(a.point(lambda v: v * alpha // 255))
    sh = sh.filter(ImageFilter.GaussianBlur(radius))
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.alpha_composite(sh, (0, dy) if dy < img.size[1] else (0, 0))
    out.alpha_composite(img)
    return out


def _canvas(w: int, h: int) -> Image.Image:
    return Image.new("RGBA", (w, h), (0, 0, 0, 0))


# ------------------------------------------------------------------ captions (word by word, middle of the screen)

def clean_word(w: str) -> str:
    w = re.sub(r"[\"“”…]", "", w)
    w = re.sub(r"[.,;:]+$", "", w)
    return w.upper()


def group_words(words: list[dict], max_words: int = 3, max_chars: int = 16) -> list[list[dict]]:
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        txt = clean_word(w["w"])
        if not txt:
            continue
        ww = dict(w, t=txt)
        if cur:
            gap = w["s"] - cur[-1]["e"]
            chars = sum(len(x["t"]) for x in cur) + len(txt) + len(cur)
            if len(cur) >= max_words or chars > max_chars or gap > 0.4 or re.search(r"[.!?,]$", cur[-1]["w"]):
                groups.append(cur)
                cur = []
        cur.append(ww)
    if cur:
        groups.append(cur)
    return groups


CAP_W, CAP_H = 1080, 300


def caption_img(parts: list[tuple[str, tuple]], size: int) -> Image.Image:
    f = font(size)
    space = f.getlength(" ")
    widths = [f.getlength(t) for t, _ in parts]
    total = sum(widths) + space * (len(parts) - 1)
    img = _canvas(CAP_W, CAP_H)
    d = ImageDraw.Draw(img)
    x = (CAP_W - total) / 2
    asc, desc = f.getmetrics()
    y = (CAP_H - (asc + desc)) / 2
    stroke = max(6, size // 10)
    for (t, col), w in zip(parts, widths):
        d.text((x, y), t, font=f, fill=col, stroke_width=stroke, stroke_fill=BLACK)
        x += w + space
    return _shadowed(img, 6, 150, 7)


def caption_size(texts: list[str], start: int = 104, max_w: int = 990) -> int:
    size = start
    while size > 44:
        if font(size).getlength(" ".join(texts)) + size * 0.25 <= max_w:
            return size
        size -= 4
    return size


def caption_events(words: list[dict], folder: Path, duration: float) -> list[tuple[float, float, Path]]:
    """One PNG per caption state (the spoken word in yellow). Returns [(start, end, png)]."""
    folder.mkdir(parents=True, exist_ok=True)
    groups = group_words(words)
    events = []
    k = 0
    for gi, g in enumerate(groups):
        size = caption_size([w["t"] for w in g])
        g_end = g[-1]["e"] + 0.25
        if gi + 1 < len(groups):
            g_end = min(g_end, groups[gi + 1][0]["s"])
        for wi, w in enumerate(g):
            parts = [(x["t"], YELLOW if j == wi else WHITE) for j, x in enumerate(g)]
            p = folder / f"cap_{k:04d}.png"
            caption_img(parts, size).save(p, compress_level=1)
            k += 1
            s = g[0]["s"] if wi == 0 else w["s"]
            e = g[wi + 1]["s"] if wi + 1 < len(g) else g_end
            events.append((max(0.0, s), min(duration, max(s + 0.04, e)), p))
    return events


# ------------------------------------------------------------------ titles, labels, tags

def title_card(text: str, max_w: int = 960, size: int = 68, max_lines: int = 3, fill=WHITE, ink=INK) -> Image.Image:
    """White rounded box, black heavy type: the line that makes people stop scrolling."""
    f, lines, size = _fit_lines(text.strip(), size, max_w - 80, max_lines, 40)
    asc, desc = f.getmetrics()
    lh = int((asc + desc) * 1.08)
    w = int(max(f.getlength(l) for l in lines)) + 80
    h = lh * len(lines) + 52
    img = _canvas(w + 40, h + 40)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((20, 14, w + 20, h + 14), 28, fill=fill)
    y = 14 + 26
    for l in lines:
        d.text((20 + (w - f.getlength(l)) / 2, y), l, font=f, fill=ink)
        y += lh
    return _shadowed(img, 10, 120, 8)


def outline_text(text: str, size: int = 92, max_w: int = 1000, color=WHITE, max_lines: int = 2) -> Image.Image:
    f, lines, size = _fit_lines(text.strip().upper(), size, max_w, max_lines, 44)
    asc, desc = f.getmetrics()
    lh = int((asc + desc) * 1.02)
    stroke = max(6, size // 9)
    w = int(max(f.getlength(l) for l in lines)) + stroke * 2 + 20
    img = _canvas(w, lh * len(lines) + stroke * 2 + 20)
    d = ImageDraw.Draw(img)
    y = stroke + 10
    for l in lines:
        d.text(((w - f.getlength(l)) / 2, y), l, font=f, fill=color, stroke_width=stroke, stroke_fill=BLACK)
        y += lh
    return _shadowed(img, 6, 150, 6)


def pill(text: str, sub: str | None = None, bg=(0, 0, 0, 200), fg=WHITE, accent=YELLOW, size: int = 44) -> Image.Image:
    """A dark rounded label: 'GTA 6' with a small '2026' after it."""
    f = font(size)
    fs = font(int(size * 0.62), bold=False)
    tw = f.getlength(text)
    sw = fs.getlength(sub) + 18 if sub else 0
    h = int(size * 1.6)
    w = int(tw + sw + 56)
    img = _canvas(w + 20, h + 20)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((10, 10, w + 10, h + 10), h // 2, fill=bg)
    asc, desc = f.getmetrics()
    y = 10 + (h - (asc + desc)) / 2
    d.text((10 + 28, y), text, font=f, fill=fg)
    if sub:
        a2, d2 = fs.getmetrics()
        d.text((10 + 28 + tw + 18, 10 + (h - (a2 + d2)) / 2 + 2), sub, font=fs, fill=accent)
    return _shadowed(img, 6, 110, 5)


def attempt_tag(n: int, landed: bool = False) -> Image.Image:
    txt = f"ATTEMPT #{n}"
    return outline_text(txt, 84, 1000, GREEN if landed else WHITE, 1)


def stamp(text: str, color=RED, size: int = 150, angle: float = -8) -> Image.Image:
    """The big verdict: BUSTED / CONFIRMED."""
    f = font(size)
    tw = f.getlength(text)
    asc, desc = f.getmetrics()
    pad = 36
    w, h = int(tw + pad * 2), int(asc + desc + pad * 1.4)
    img = _canvas(w + 24, h + 24)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((12, 12, w + 12, h + 12), 26, fill=color)
    d.rounded_rectangle((24, 24, w, h), 18, outline=(255, 255, 255, 235), width=8)
    d.text((12 + pad, 12 + (h - (asc + desc)) / 2), text, font=f, fill=WHITE)
    img = img.rotate(angle, resample=Image.BICUBIC, expand=True)
    return _shadowed(img, 12, 140, 10)


# ------------------------------------------------------------------ pictures that pop up

def picture_card(src: Path, max_w: int = 760, max_h: int = 620, caption: str | None = None) -> Image.Image:
    im = Image.open(src)
    im = ImageOps.exif_transpose(im).convert("RGBA")
    ar = im.width / max(1, im.height)
    if ar > 2.0:                       # a panorama would come out as a thin strip: keep its middle at 16:9
        nw = int(im.height * 16 / 9)
        x0 = (im.width - nw) // 2
        im = im.crop((x0, 0, x0 + nw, im.height))
    elif ar < 0.6:                     # a very tall picture: keep its middle at 3:4
        nh = int(im.width * 4 / 3)
        y0 = (im.height - nh) // 3
        im = im.crop((0, y0, im.width, y0 + nh))
    s = min(max_w / im.width, max_h / im.height)
    im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)
    border = 12
    cap_h = 0
    fcap = font(34)
    if caption:
        cap_h = 64
    w, h = im.width + border * 2, im.height + border * 2 + cap_h
    card = _canvas(w + 40, h + 40)
    d = ImageDraw.Draw(card)
    d.rounded_rectangle((20, 20, w + 20, h + 20), 26, fill=WHITE)
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, im.width - 1, im.height - 1), 18, fill=255)
    card.paste(im, (20 + border, 20 + border), mask)
    if caption:
        f, lines, _ = _fit_lines(caption.upper(), 34, w - 40, 1, 22)
        d.text((20 + (w - f.getlength(lines[0])) / 2, 20 + border + im.height + 12), lines[0], font=f, fill=INK)
    return _shadowed(card, 14, 140, 12)


POP = [0.55, 0.86, 1.07, 1.02, 1.0]      # five frames at 30 fps: a quick pop with a small overshoot


def pop_frames(img: Image.Image, box: tuple[int, int], scales=POP, anchor: str = "center") -> list[Image.Image]:
    """The same image at growing sizes inside a fixed box (box = the size of the overlay track)."""
    out = []
    bw, bh = box
    for s in scales:
        w, h = max(1, int(img.width * s)), max(1, int(img.height * s))
        fr = _canvas(bw, bh)
        im = img.resize((w, h), Image.LANCZOS)
        x = (bw - w) // 2
        y = (bh - h) // 2 if anchor == "center" else 0
        fr.alpha_composite(im, (max(0, x), max(0, y)) if (x >= 0 and y >= 0) else (0, 0))
        out.append(fr)
    return out


def place(img: Image.Image, box: tuple[int, int], x: int | None = None, y: int | None = None) -> Image.Image:
    """Put an image into a transparent box (centred unless x / y are given)."""
    bw, bh = box
    fr = _canvas(bw, bh)
    px = (bw - img.width) // 2 if x is None else x
    py = (bh - img.height) // 2 if y is None else y
    fr.alpha_composite(img, (max(0, px), max(0, py)))
    return fr


# ------------------------------------------------------------------ the ranking board

def rank_board(labels: dict[int, str], shown: set[int], current: int | None, n: int = 5, title: str | None = None,
               w: int = 1080, h: int = 1920) -> Image.Image:
    """The list on the left side: 1 at the top, n at the bottom; a label appears once its clip has played (or is
    playing). The playing one is highlighted."""
    img = _canvas(w, h)
    d = ImageDraw.Draw(img)
    top = 380 if title else 260
    row = 150
    fnum = font(104)
    flab = font(66)
    for r in range(1, n + 1):
        y = top + (r - 1) * row
        is_cur = current == r
        col = YELLOW if is_cur else WHITE
        d.text((40, y), f"{r}.", font=fnum, fill=col, stroke_width=9, stroke_fill=BLACK)
        if r in shown:
            lab = labels.get(r, "")
            f, lines, _ = _fit_lines(lab.upper(), 66, 860, 1, 30)
            ly = y + (fnum.getmetrics()[0] - f.getmetrics()[0]) // 2 + 6
            d.text((190, ly), lines[0], font=f, fill=col, stroke_width=8, stroke_fill=BLACK)
    if title:
        t = title_card(title, 1000, 62, 2)
        img.alpha_composite(t, ((w - t.width) // 2, 60))
    return _shadowed(img, 5, 140, 5)


def board_frames(board_before: Image.Image, board_after: Image.Image, steps: int = 6) -> list[Image.Image]:
    """A short cross-fade from one board state to the next."""
    out = []
    for i in range(1, steps + 1):
        out.append(Image.blend(board_before, board_after, i / steps))
    return out
