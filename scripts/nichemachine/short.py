"""One renderer for every niche: gameplay pieces in order (cropped, fitted or stacked), overlay tracks on top,
gameplay sound, an optional voice and music, mixed to -14 LUFS, checked, with a cover and a post kit."""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import media
from .track import Track
from .util import FPS, H, SHORTS, W, ffmpeg, run, say, settings, slug, today, warn

AFMT = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"


def _atempo(speed: float) -> str:
    parts = []
    s = speed
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    parts.append(f"atempo={s:.4f}")
    return ",".join(parts)


def _cover_crop(label_in: str, label_out: str, src: dict, bw: int, bh: int, focus: float = 0.5,
                zoom: float = 1.0, focus_y: float = 0.5) -> str:
    """Scale the source so it covers bw x bh (times zoom), then crop at the focus point."""
    sw, sh = src["w"], src["h"]
    scale = max(bw / sw, bh / sh) * max(1.0, zoom)
    nw, nh = int(round(sw * scale / 2) * 2), int(round(sh * scale / 2) * 2)
    x = int(max(0, min(nw - bw, (nw - bw) * focus)))
    y = int(max(0, min(nh - bh, (nh - bh) * focus_y)))
    return f"[{label_in}]scale={nw}:{nh}:flags=lanczos,crop={bw}:{bh}:{x}:{y},setsar=1[{label_out}]"


def render(segments: list[dict], tracks: list[Track], out_name: str, mode: str, meta: dict | None = None,
           voice: dict | None = None, music: dict | None = None, work: Path | None = None,
           post: dict | None = None, no_auto_crop: bool = False, no_auto_edit: bool = False,
           timestamp: bool = False) -> dict:
    """segments: [{"kind": "single", "file", "a", "b", "layout": "crop"|"fit", "focus": 0.5, "zoom": 1, "speed": 1,
                   "gain": 0 (dB) | None (mute), "fit_y": None}
                  {"kind": "stack", "top": {...file,a,b,focus}, "bottom": {...}, "audio": "top"|"bottom"|"both"|"none"}]
       voice: {"file", "start": 0, "gain": 0}; music: {"file", "gain": -20}"""
    t_start = time.time()
    if no_auto_edit:
        tracks, voice, music = [], None, None
        segments = [dict(s, speed=1.0) for s in segments]
    if no_auto_crop:
        adjusted = []
        for segment in segments:
            segment = dict(segment)
            if segment.get("kind", "single") == "stack":
                segment["no_auto_crop"] = True
            elif segment.get("layout", "crop") == "crop":
                segment["layout"] = "fit"
            adjusted.append(segment)
        segments = adjusted
    cfg = settings()
    work = work or (SHORTS / "_work" / slug(out_name))
    work.mkdir(parents=True, exist_ok=True)

    # inputs: every source once
    files: list[str] = []
    info: dict[str, dict] = {}

    def idx(f) -> int:
        f = str(f)
        if f not in files:
            files.append(f)
            info[f] = media.probe(Path(f))
        return files.index(f)

    fc: list[str] = []
    vlabels, alabels = [], []
    total = 0.0
    for i, sg in enumerate(segments):
        speed = float(sg.get("speed", 1.0) or 1.0)
        if sg.get("kind", "single") == "stack":
            top, bot = sg["top"], sg["bottom"]
            dur = min(float(top["b"]) - float(top["a"]), float(bot["b"]) - float(bot["a"])) / speed
            parts = []
            for k, part in (("t", top), ("b", bot)):
                n = idx(part["file"])
                src = info[str(part["file"])]
                a = float(part["a"])
                fc.append(f"[{n}:v]trim=start={a:.3f}:end={a + dur * speed:.3f},setpts=(PTS-STARTPTS)/{speed:.4f}"
                          f"[s{i}{k}0]")
                if sg.get("no_auto_crop"):
                    fc.append(f"[s{i}{k}0]scale={W}:{H // 2}:force_original_aspect_ratio=decrease:flags=lanczos,pad={W}:{H // 2}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[s{i}{k}]")
                else:
                    fc.append(_cover_crop(f"s{i}{k}0", f"s{i}{k}", src, W, H // 2, float(part.get("focus", 0.5)),
                                          float(part.get("zoom", 1.0)), float(part.get("focus_y", 0.5))))
                parts.append(f"[s{i}{k}]")
            fc.append(f"{parts[0]}{parts[1]}vstack=inputs=2,fps={FPS},format=yuv420p,setsar=1[v{i}]")
            # sound
            which_a = sg.get("audio", "top")
            srcs = {"top": [top], "bottom": [bot], "both": [top, bot], "none": []}.get(which_a, [top])
            alist = []
            for j, part in enumerate(srcs):
                n = idx(part["file"])
                if not info[str(part["file"])]["audio"]:
                    continue
                a = float(part["a"])
                g = float(part.get("gain", 0) or 0) - (3 if len(srcs) == 2 else 0)
                fc.append(f"[{n}:a]atrim=start={a:.3f}:end={a + dur * speed:.3f},asetpts=PTS-STARTPTS,"
                          f"{_atempo(speed) if speed != 1 else 'anull'},{AFMT},volume={g:.2f}dB[a{i}_{j}]")
                alist.append(f"[a{i}_{j}]")
            if len(alist) == 2:
                fc.append(f"{alist[0]}{alist[1]}amix=inputs=2:normalize=0:duration=first[a{i}r]")
            elif len(alist) == 1:
                fc.append(f"{alist[0]}anull[a{i}r]")
            else:
                fc.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{dur:.3f},{AFMT}[a{i}r]")
        else:
            n = idx(sg["file"])
            src = info[str(sg["file"])]
            a, b = float(sg["a"]), float(sg["b"])
            dur = (b - a) / speed
            head = f"[{n}:v]trim=start={a:.3f}:end={b:.3f},setpts=(PTS-STARTPTS)/{speed:.4f}"
            if sg.get("layout", "crop") == "fit":
                fc.append(f"{head},split=2[g{i}a][g{i}b]")
                fc.append(f"[g{i}a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=38,"
                          f"eq=brightness=-0.14:saturation=0.85[g{i}bg]")
                fc.append(f"[g{i}b]scale={W}:-2:flags=lanczos,setsar=1[g{i}fg]")
                fy = sg.get("fit_y")
                yexpr = "(H-h)/2" if fy is None else str(int(fy))
                fc.append(f"[g{i}bg][g{i}fg]overlay=0:{yexpr},fps={FPS},format=yuv420p,setsar=1[v{i}]")
            else:
                fc.append(f"{head}[g{i}0]")
                fc.append(_cover_crop(f"g{i}0", f"g{i}1", src, W, H, float(sg.get("focus", 0.5)),
                                      float(sg.get("zoom", 1.0)), float(sg.get("focus_y", 0.5))))
                fc.append(f"[g{i}1]fps={FPS},format=yuv420p,setsar=1[v{i}]")
            gain = sg.get("gain", 0)
            if src["audio"] and gain is not None:
                fc.append(f"[{n}:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,"
                          f"{_atempo(speed) if speed != 1 else 'anull'},{AFMT},volume={float(gain):.2f}dB,"
                          f"afade=t=in:d=0.015,afade=t=out:st={max(0, dur - 0.015):.3f}:d=0.015[a{i}r]")
            else:
                fc.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{dur:.3f},{AFMT}[a{i}r]")
        vlabels.append(f"[v{i}]")
        alabels.append(f"[a{i}r]")
        total += dur
    K = len(segments)
    fc.append("".join(f"{v}{a}" for v, a in zip(vlabels, alabels)) + f"concat=n={K}:v=1:a=1[base][game]")
    L = total

    # overlay tracks
    inputs: list[str] = []
    for f in files:
        inputs += ["-i", f]
    n_in = len(files)
    last = "base"
    for k, tr in enumerate(tracks):
        lst = tr.write(L)
        if not lst:
            continue
        inputs += ["-f", "concat", "-safe", "0", "-i", str(lst)]
        fc.append(f"[{n_in}:v]format=rgba[ov{k}];[{last}][ov{k}]overlay=x={tr.x}:y={tr.y}:eof_action=pass[o{k}]")
        last = f"o{k}"
        n_in += 1

    # sound: gameplay + voice + music
    mix = ["[game]"]
    if voice and voice.get("file"):
        inputs += ["-i", str(voice["file"])]
        ms = int(float(voice.get("start", 0)) * 1000)
        fc.append(f"[{n_in}:a]{AFMT},adelay={ms}|{ms},volume={float(voice.get('gain', 0)):.2f}dB[vo]")
        mix.append("[vo]")
        n_in += 1
    if music and music.get("file"):
        inputs += ["-stream_loop", "-1", "-i", str(music["file"])]
        fc.append(f"[{n_in}:a]{AFMT},volume={float(music.get('gain', -20)):.2f}dB,atrim=0:{L:.3f}[mu]")
        mix.append("[mu]")
        n_in += 1
    if len(mix) > 1:
        fc.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first[mixed]")
        amain = "mixed"
    else:
        amain = "game"
    fc.append(f"[{amain}]alimiter=limit=0.89:level=false,aresample=48000[aout]")

    day = SHORTS / today()
    day.mkdir(parents=True, exist_ok=True)
    stem = f"{mode}_{slug(out_name, 40)}"

    def _same_project(prev: Path) -> bool:        # another project with the same name gets its own number
        try:
            w = json.loads(prev.with_suffix(".json").read_text()).get("work")
        except Exception:
            return True
        return w is None or w == str(work)

    again = [p for p in sorted(day.glob(f"*_{stem}.mp4"))
             if p.stem.partition("_")[0].isdigit() and _same_project(p)]
    if again:                                     # making the same Short again replaces it
        name = again[0].stem
    else:
        nums = [int(p.stem.partition("_")[0]) for p in day.glob("*_*.mp4")
                if p.stem.partition("_")[0].isdigit()]
        name = f"{max(nums, default=0) + 1:02d}_{stem}"
    out = day / f"{name}.mp4"
    script = work / "filter.txt"
    script.write_text(";\n".join(fc))
    base_cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y"] + inputs
    tail = ["-map", f"[{last}]", "-map", "[aout]", "-r", str(FPS), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{L:.3f}", str(out)]
    enc_hw = ["-c:v", "h264_videotoolbox", "-b:v", "12M", "-maxrate", "18M", "-bufsize", "24M", "-profile:v", "high"]
    enc_sw = ["-c:v", "libx264", "-preset", "medium", "-crf", "19"]
    say(f"  rendering {name} ({L:.1f} s)…")
    err = ""
    ok = False
    for opt in ("-filter_complex_script", "-/filter_complex"):
        for enc in (enc_hw, enc_sw):
            p = run(base_cmd + [opt, str(script)] + enc + tail, check=False, timeout=3600)
            if p.returncode == 0 and out.exists() and out.stat().st_size > 10000:
                ok = True
                break
            err += (p.stderr or "")[-1200:]
        if ok:
            break
    if not ok:
        raise RuntimeError("ffmpeg failed:\n" + err[-2400:])

    # loudness: -14 LUFS, one more pass on the sound only if the limiter moved it
    lufs = media.loudness(out)
    if lufs > -70 and abs(lufs + 14) > 0.7:
        fix = out.with_suffix(".fix.mp4")
        p3 = run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(out), "-c:v", "copy", "-af",
                  f"volume={-14 - lufs:.2f}dB,alimiter=limit=0.89:level=false", "-c:a", "aac", "-b:a", "192k",
                  "-movflags", "+faststart", str(fix)], check=False)
        if p3.returncode == 0 and fix.exists():
            fix.replace(out)
    qc = check(out, L, cfg)
    cover_t = (meta or {}).get("cover_t", min(L * 0.3, 3.0))
    media.cover(out, cover_t, out.with_suffix(".jpg"))
    kit = post_kit(post or {}, mode, cfg)
    out.with_suffix(".txt").write_text(kit)
    sources = []
    for sg in segments:
        parts = [sg] if sg.get("kind", "single") == "single" else [sg["top"], sg["bottom"]]
        for part in parts:
            sources.append({"file": str(part["file"]), "start": float(part["a"]), "end": float(part["b"])})
    m = {"file": str(out), "name": name, "mode": mode, "length": round(L, 2), "qc": qc, "work": str(work),
         "sources": sources,
         "made": time.strftime("%Y-%m-%d %H:%M"), "seconds_to_make": round(time.time() - t_start, 1)}
    if meta:
        m.update({k: v for k, v in meta.items() if k != "cover_t"})
    out.with_suffix(".json").write_text(json.dumps(m, indent=1, ensure_ascii=False))
    if timestamp:
        def stamp(sec: float) -> str:
            ms = round(sec * 1000)
            h, ms = divmod(ms, 3_600_000); minute, ms = divmod(ms, 60_000); second, ms = divmod(ms, 1000)
            return f"{h:02d}:{minute:02d}:{second:02d}.{ms:03d}"
        lines = [f"{Path(s['file']).name}\t{stamp(s['start'])}\t{stamp(s['end'])}" for s in sources]
        out.with_suffix(".timestamps.txt").write_text("\n".join(lines) + "\n")
    mark = "✓" if qc["ok"] else "⚠ " + "; ".join(qc["issues"])
    say(f"  {mark}  {out}  ({qc['seconds']} s, {qc['lufs']} LUFS, made in {m['seconds_to_make']} s)")
    return m


def check(out: Path, L: float, cfg: dict) -> dict:
    pv = media.probe(out)
    lufs = media.loudness(out)
    blacks = media.black_stretches(out)
    issues = []
    if pv["w"] != W or pv["h"] != H:
        issues.append(f"size {pv['w']}x{pv['h']}")
    if pv["dur"] > 180.5:
        issues.append(f"{pv['dur']:.0f} s is over the 3-minute Shorts limit")
    elif pv["dur"] > float(cfg.get("max_short_s", 59)) + 0.5:
        issues.append(f"{pv['dur']:.0f} s (over {cfg.get('max_short_s', 59)} s: fine for Shorts, long for TikTok/Reels)")
    if pv["dur"] < 6:
        issues.append(f"only {pv['dur']:.1f} s")
    if lufs > -70 and abs(lufs + 14) > 1.5:
        issues.append(f"loudness {lufs:.1f} LUFS")
    if lufs <= -70:
        issues.append("no sound")
    if blacks:
        issues.append(f"{blacks} black stretch(es)")
    return {"ok": not issues, "issues": issues, "lufs": round(lufs, 1), "seconds": round(pv["dur"], 2)}


def post_kit(post: dict, mode: str, cfg: dict) -> str:
    title = post.get("title") or ""
    caption = post.get("caption") or ""
    tags = post.get("hashtags") or cfg.get("hashtags", [])
    credit = post.get("credit") or ""
    lines = [f"TITLE: {title}", "", "CAPTION:", caption.strip(), "", " ".join(tags)]
    if credit:
        lines += ["", f"CREDIT: {credit}"]
    return "\n".join(lines).strip() + "\n"
