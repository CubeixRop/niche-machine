"""A review page for the day's Shorts: every one playable, its post text one tap from the clipboard."""
from __future__ import annotations

import html
import json
from pathlib import Path

from .util import SHORTS, today

NICHE = {"commentary": "Commentary", "compare": "Comparison", "stunts": "Stunts", "myths": "Myths",
         "moments": "Moments", "rank": "Ranking", "skit": "Skit"}


def build(day: str | None = None) -> Path:
    day = day or today()
    folder = SHORTS / day
    cards = []
    for mp4 in sorted(folder.glob("*.mp4")):
        meta = {}
        j = mp4.with_suffix(".json")
        if j.exists():
            meta = json.loads(j.read_text())
        kit = mp4.with_suffix(".txt").read_text() if mp4.with_suffix(".txt").exists() else ""
        qc = meta.get("qc", {})
        badge = "✓ checked" if qc.get("ok") else "⚠ " + "; ".join(qc.get("issues", []))
        cards.append(f"""
<article>
  <video src="{html.escape(mp4.name)}" controls preload="metadata" playsinline poster="{html.escape(mp4.with_suffix('.jpg').name)}"></video>
  <div class="meta">
    <span class="niche">{NICHE.get(meta.get('mode', ''), meta.get('mode', ''))}</span>
    <span class="len">{meta.get('length', 0):.0f} s</span>
    <span class="qc {'ok' if qc.get('ok') else 'warn'}">{html.escape(badge)}</span>
  </div>
  <pre>{html.escape(kit)}</pre>
  <button onclick="navigator.clipboard.writeText(this.previousElementSibling.textContent);this.textContent='Copied'">Copy post text</button>
</article>""")
    page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Niche Machine · {day}</title>
<style>
:root{{--bg:#111;--card:#1b1b1b;--ink:#f2f2f2;--mute:#9a9a9a;--acc:#ffd60a}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,system-ui,sans-serif}}
header{{padding:24px 20px 8px}} h1{{margin:0;font-size:22px}} header p{{color:var(--mute);margin:4px 0 0}}
main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:18px;padding:16px 20px 40px}}
article{{background:var(--card);border-radius:14px;padding:12px}}
video{{width:100%;aspect-ratio:9/16;border-radius:10px;background:#000}}
.meta{{display:flex;gap:8px;align-items:center;margin:10px 0 6px;flex-wrap:wrap}}
.niche{{background:var(--acc);color:#111;font-weight:700;border-radius:99px;padding:2px 10px}}
.len{{color:var(--mute)}} .qc.ok{{color:#5fd38d}} .qc.warn{{color:#ffb347}}
pre{{white-space:pre-wrap;background:#0d0d0d;border-radius:8px;padding:10px;font:13px/1.4 ui-monospace,monospace;color:#ddd;max-height:180px;overflow:auto}}
button{{background:#2a2a2a;color:var(--ink);border:0;border-radius:8px;padding:8px 12px;cursor:pointer}}
</style></head><body>
<header><h1>Niche Machine · {day}</h1><p>{len(cards)} Short(s). Watch every one before you post it.</p></header>
<main>{''.join(cards)}</main></body></html>"""
    out = folder / "index.html"
    folder.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out
