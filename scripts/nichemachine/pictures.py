"""Pictures that pop up when the voice names something. Three sources, in this order of preference:
your own file, a link you give it (official screenshots, a press page), or a search on Wikimedia Commons
(free-licence pictures; the licence and author go in credits.txt so you can credit them)."""
from __future__ import annotations

import json
import shutil
import urllib.parse
import urllib.request
from pathlib import Path

from .util import resolve, say, warn

UA = "NicheMachine/1.0 (local video tool; https://commons.wikimedia.org/wiki/Commons:API)"
COMMONS = "https://commons.wikimedia.org/w/api.php"


def _get(url: str, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def commons_search(query: str, limit: int = 6) -> list[dict]:
    q = urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search", "gsrsearch": f"{query} filetype:bitmap",
        "gsrnamespace": 6, "gsrlimit": limit, "prop": "imageinfo", "iiprop": "url|extmetadata|size",
        "iiurlwidth": 1400})
    try:
        d = json.loads(_get(f"{COMMONS}?{q}"))
    except Exception as e:
        warn(f"Wikimedia search failed for {query!r}: {e}")
        return []
    pages = sorted((d.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))
    out = []
    for p in pages:
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata") or {}
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        artist = (meta.get("Artist") or {}).get("value", "")
        import re
        artist = re.sub(r"<[^>]+>", "", artist).strip()
        if ii.get("width", 0) < 400:
            continue
        out.append({"title": p.get("title", ""), "url": ii.get("thumburl") or ii.get("url"), "page": ii.get("descriptionurl"),
                    "license": lic, "author": artist, "w": ii.get("width"), "h": ii.get("height")})
    # share-alike (CC BY-SA) can reach a whole monetised video: those go last, and the choices sheet marks them
    out.sort(key=lambda r: 1 if "SA" in r["license"].upper().replace("-", " ").split() or "BY-SA" in r["license"].upper() else 0)
    return out


PER = 6                  # results shown per query on the choices sheet


def _results(project: Path, query: str) -> list[dict]:
    """The results for a query: the very list the choices sheet showed (so "pick": N is the picture that was
    looked at), or a fresh search when no sheet was made for this query."""
    seen = (_read_json(project / "pictures_choices.json") or {}).get(query)
    if seen:
        return seen
    return commons_search(query, limit=PER + 2)[:PER]


def _read_json(p: Path):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return None


def choices(project: Path, pics: dict, per: int = PER) -> Path | None:
    """For every picture asked for by query: the top results side by side, numbered, so Claude can look and set
    "pick" in plan.json before rendering (a search for one thing often returns its older model). The results are
    kept in pictures_choices.json, and the render fetches from that same list."""
    from io import BytesIO

    from PIL import Image, ImageDraw

    from . import draw
    rows = [(pid, spec) for pid, spec in pics.items() if spec.get("query") and not spec.get("file") and not spec.get("url")]
    if not rows:
        return None
    shown = {}
    tw, th = 300, 200
    sheet = Image.new("RGB", (220 + per * (tw + 10), len(rows) * (th + 40) + 10), (18, 18, 18))
    d = ImageDraw.Draw(sheet)
    for r, (pid, spec) in enumerate(rows):
        y = 10 + r * (th + 40)
        d.text((10, y + 70), pid, font=draw.font(30), fill=(255, 214, 10))
        res = commons_search(spec["query"], limit=per + 2)[:per]
        shown[spec["query"]] = res
        for k, it in enumerate(res):
            try:
                url = it["url"].replace("1400px", "400px")
                im = Image.open(BytesIO(_get(url))).convert("RGB")
                im.thumbnail((tw, th))
                x = 220 + k * (tw + 10)
                sheet.paste(im, (x + (tw - im.width) // 2, y + (th - im.height) // 2))
                sa = "SA" in it["license"].upper()
                d.text((x + 6, y + th + 6), f"pick {k} · {it['license'] or '?'}" + (" · AVOID" if sa else ""),
                       font=draw.font(18, bold=False), fill=(255, 120, 90) if sa else (230, 230, 230))
            except Exception:
                pass
    out = project / "pictures_choices.jpg"
    sheet.save(out, quality=82)
    kept = _read_json(project / "pictures_choices.json") or {}
    kept.update(shown)
    (project / "pictures_choices.json").write_text(json.dumps(kept, indent=1, ensure_ascii=False))
    return out


def fetch(project: Path, pics: dict) -> dict:
    """pics: {"id": {"file": …} | {"url": …} | {"query": …, "pick": 0}}. Writes pictures/<id>.<ext> and credits.txt."""
    folder = project / "pictures"
    folder.mkdir(parents=True, exist_ok=True)
    credits = []
    got = {}
    for pid, spec in pics.items():
        try:
            if spec.get("file"):
                src = resolve(spec["file"], project)
                dst = folder / f"{pid}{src.suffix.lower() or '.jpg'}"
                if src.resolve() != dst.resolve():
                    shutil.copy(src, dst)
                got[pid] = str(dst)
                credits.append(f"{pid}: your file {src.name}")
            elif spec.get("url"):
                data = _get(spec["url"])
                ext = ".png" if data[:4] == b"\x89PNG" else ".webp" if data[8:12] == b"WEBP" else ".jpg"
                dst = folder / f"{pid}{ext}"
                dst.write_bytes(data)
                got[pid] = str(dst)
                credits.append(f"{pid}: {spec['url']}" + (f" ({spec['credit']})" if spec.get("credit") else ""))
            elif spec.get("query"):
                res = _results(project, spec["query"])
                if not res:
                    warn(f"no free picture found for {pid} ({spec['query']!r}); give it a url or a file")
                    continue
                r = res[min(int(spec.get("pick", 0)), len(res) - 1)]
                if "SA" in r["license"].upper().replace("-", " ").split():
                    warn(f"picture {pid} is share-alike ({r['license']}): its licence can reach the whole Short. "
                         f"Pick another one, or give it a url or a file")
                data = _get(r["url"])
                dst = folder / f"{pid}.jpg"
                dst.write_bytes(data)
                got[pid] = str(dst)
                credits.append(f"{pid}: {r['title']} by {r['author'] or 'unknown'}, {r['license']} ({r['page']})")
            say(f"  ✓ picture {pid}")
        except Exception as e:
            warn(f"picture {pid} failed: {e}")
    (project / "credits.txt").write_text("\n".join(credits) + "\n")
    return got
