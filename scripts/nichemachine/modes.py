"""The seven niches. Each one reads its plan.json (written by Claude, following SKILL.md and reference/), builds the
gameplay pieces and the overlays, and hands them to the one renderer."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np

from . import draw, find, listen, media, pictures, short, voice
from .track import Track
from .util import FPS, H, W, read_json, resolve, say, settings, slug, videos_in, warn, write_json

TITLE_S = 2.6          # how long the opening title stays up


def _title_track(proj: Path, title: str | None, until: float = TITLE_S, y: int = 70) -> Track | None:
    if not title:
        return None
    tr = Track("title", proj / "_layers", 0, y, W, 420)
    card = draw.title_card(title, 960, 66, 3)
    tr.add_anim(0.0, until, draw.pop_frames(card, (W, 420), [0.8, 0.96, 1.03, 1.0]),
                out_frames=draw.pop_frames(card, (W, 420), [0.9, 0.6])[:2])
    return tr


def _hash(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:10]


def _voice_words(proj: Path, wav: Path, script_text: str | None) -> list[dict]:
    """Word timings of a voice file, cached next to it."""
    cache = wav.with_suffix(".words.json")
    if cache.exists() and cache.stat().st_mtime >= wav.stat().st_mtime:
        words = read_json(cache, [])
    else:
        w16 = media.extract_wav(wav, proj / "_layers" / f"{wav.stem}_16k.wav")
        words = listen.words(w16, prompt=script_text)
        write_json(cache, words)                  # what was heard; the script's spelling goes on below, every time
    if script_text:
        words = listen.respell(words, script_text)
    return words


def _norm(x: str) -> str:
    return re.sub(r"[^a-z0-9]", "", x.lower())


def _gain(item: dict | None, plan: dict, default: float | None = 0.0) -> float | None:
    """The clip's own sound in dB: "gain" on the clip, else "game_gain" on the plan, else the default.
    null mutes it (a stream with someone talking, a mission full of dialogue, the radio)."""
    if item and "gain" in item:
        return item["gain"]
    if "game_gain" in plan:
        return plan["game_gain"]
    return default


def _add(g: float | None, d: float) -> float | None:
    return None if g is None else float(g) + d


def _frame(*sources: dict | None) -> dict:
    """Where the 9:16 crop sits in a 16:9 recording: focus (0 = left edge, 1 = right edge), focus_y (0 = top,
    1 = bottom) and zoom (1 = no zoom; 1.3 cuts closer, which keeps a facecam or a chat box in a corner out).
    Set per clip or once for the whole plan; the first one that sets a value wins."""
    out = {"focus": 0.5, "zoom": 1.0, "focus_y": 0.5}
    for k in out:
        for s in sources:
            if s and s.get(k) is not None:
                out[k] = float(s[k])
                break
    return out


# ================================================================== 1. commentary

def commentary(proj: Path, plan: dict) -> list[dict]:
    """Voice (free Kokoro or yours) over a stretch of your gameplay where you're moving, captions in the middle,
    and a picture popping up every time the voice names something."""
    cfg = settings()
    script_path = proj / "script.md"
    script = script_path.read_text() if script_path.exists() else plan.get("script", "")
    if not script.strip():
        raise SystemExit(f"Write the script first: {script_path}")
    text = voice.spoken(script)
    # 1. the voice
    if plan.get("voice_file"):
        vfile = resolve(plan["voice_file"], proj)
        say(f"  using your own voice: {vfile.name}")
    else:
        vfile = proj / f"voice_{_hash(text, plan.get('voice', cfg['voice']), plan.get('voice_speed', cfg['voice_speed']))}.wav"
        if not vfile.exists():
            voice.synth(text, vfile, plan.get("voice"), plan.get("voice_speed"))
    vdur = media.probe(vfile)["dur"]
    words = _voice_words(proj, vfile, text)
    lead = 0.15
    for w in words:
        w["s"] += lead
        w["e"] += lead
    L = vdur + lead + 0.6
    # 2. pictures, and when each one pops (the word right after its {pic:id} cue)
    pics_spec = plan.get("pictures", {})
    have = {p.stem: str(p) for p in (proj / "pictures").glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")}
    missing = {k: v for k, v in pics_spec.items() if k not in have}
    if missing:
        have.update(pictures.fetch(proj, missing))
    cues = []                         # (script word index, pic id)
    n_words = 0
    for tok in re.split(r"(\{pic:[^}]*\})", voice.without_notes(script)):
        m = re.fullmatch(r"\{pic:([^}]*)\}", tok)
        if m:
            cues.append((n_words, m.group(1).strip()))
        else:
            n_words += len(tok.split())
    script_words = text.split()
    times = []
    for k, pid in cues:
        if not words:
            break
        guess = int(round(k * len(words) / max(1, len(script_words))))
        target = _norm(script_words[k]) if k < len(script_words) else ""
        best = None
        for d in range(0, 8):
            for j in (guess + d, guess - d):
                if 0 <= j < len(words) and target and _norm(words[j]["w"]) == target:
                    best = j
                    break
            if best is not None:
                break
        j = best if best is not None else min(len(words) - 1, max(0, guess))
        times.append((words[j]["s"], pid))
    # 3. gameplay underneath
    gp = plan.get("gameplay") or {}
    if gp.get("file") and gp.get("a") is not None:
        bg = {"file": str(resolve(gp["file"], proj)), "a": float(gp["a"])}
        find.mark_used(bg["file"], bg["a"])          # so a later automatic pick doesn't reuse it
    else:
        folder = resolve(gp.get("folder") or plan.get("gameplay_folder") or "~/NicheMachine/gameplay", proj)
        # making the same Short again (a new script line, another picture) keeps the stretch it found the first time
        last = read_json(proj / "background.json")
        if (last and Path(last.get("file", "")).exists() and Path(last["file"]).resolve().is_relative_to(folder.resolve())
                and float(last["a"]) + L <= media.probe(Path(last["file"]))["dur"]):
            bg = last
            say(f"  background: {Path(bg['file']).name} from {bg['a']:.0f} s (the same stretch as last time)")
        else:
            vids = videos_in(folder)
            if not vids:
                raise SystemExit(f"No gameplay found in {folder}. Put your recordings there or set gameplay.file in plan.json")
            best = find.moving(vids, L, n=1)
            if not best:
                raise SystemExit("No stretch of gameplay is long enough for this voice-over")
            bg = best[0]
            write_json(proj / "background.json", bg)
            find.mark_used(bg["file"], bg["a"])
            say(f"  background: {Path(bg['file']).name} from {bg['a']:.0f} s (moving score {bg['score']})")
    seg = {"kind": "single", "file": bg["file"], "a": bg["a"], "b": bg["a"] + L, "layout": gp.get("layout", "crop"),
           **_frame(gp),
           "gain": cfg["game_volume_under_voice"] if not gp.get("mute") else None}
    # 4. overlays
    layers = proj / "_layers"
    caps = Track("captions", layers, 0, 960 - draw.CAP_H // 2 + 60, draw.CAP_W, draw.CAP_H)
    for s, e, p in draw.caption_events(words, layers / "cap_png", L):
        caps.add_png(s, e, p)
    pic_tr = Track("pictures", layers, 0, 150, W, 720)
    for i, (t, pid) in enumerate(times):
        if pid not in have:
            warn(f"picture {pid} is missing; skipped")
            continue
        nxt = times[i + 1][0] if i + 1 < len(times) else L
        hold_end = min(nxt - 0.05, t + float(plan.get("picture_hold", 2.6)))
        card = draw.picture_card(Path(have[pid]), 940, 640, pics_spec.get(pid, {}).get("caption"))
        pic_tr.add_anim(t, hold_end, draw.pop_frames(card, (W, 720)),
                        out_frames=draw.pop_frames(card, (W, 720), [0.9, 0.6]) if nxt - hold_end > 0.2 else None)
    tracks = [pic_tr, caps]
    tt = _title_track(proj, plan.get("title_on_screen"))
    if tt:
        tracks.insert(0, tt)
    m = short.render([seg], tracks, plan.get("name") or proj.name, "commentary",
                     meta={"background": bg, "pictures": len(times), "words": len(words), "cover_t": 1.0},
                     voice={"file": str(vfile), "start": lead, "gain": 0},
                     music={"file": str(resolve(plan["music"], proj)), "gain": plan.get("music_gain", -24)} if plan.get("music") else None,
                     work=proj / "_work", post=plan.get("post"), no_auto_crop=plan.get("no_auto_crop", False), no_auto_edit=plan.get("no_auto_edit", False), timestamp=plan.get("timestamp", False))
    return [m]


# ================================================================== 2. comparison

def compare(proj: Path, plan: dict) -> list[dict]:
    """Two games doing the same thing, one on top of the other, lined up on the same moment, each labelled."""
    segs = []
    layers = proj / "_layers"
    lab = Track("labels", layers, 0, 960 - 150, W, 300)
    sub = Track("subtitle", layers, 0, 40, W, 180)
    t = 0.0
    top_pill = draw.pill(plan.get("top_label", "GTA 6"), plan.get("top_sub"), size=66)
    bot_pill = draw.pill(plan.get("bottom_label", "GTA 5"), plan.get("bottom_sub"), size=66)
    for k, pr in enumerate(plan["pairs"]):
        top, bot = dict(pr["top"]), dict(pr["bottom"])
        for part in (top, bot):
            part["file"] = str(resolve(part["file"], proj))
            part["a"] = float(part["a"])
            part["b"] = float(part.get("b", part["a"] + float(pr.get("length", 6))))
        dur = min(top["b"] - top["a"], bot["b"] - bot["a"])
        segs.append({"kind": "stack", "top": top, "bottom": bot, "audio": pr.get("audio", plan.get("audio", "top"))})
        if pr.get("subtitle"):
            s_img = draw.pill(pr["subtitle"].upper(), None, bg=(255, 255, 255, 235), fg=draw.INK, size=60)
            sub.add_anim(t + (TITLE_S if k == 0 and plan.get("title_on_screen") else 0.0), t + dur,
                         draw.pop_frames(s_img, (W, 180), [0.7, 1.05, 1.0]))
        t += dur
    L = t
    both = draw.place(top_pill, (W, 300), 24, 150 - top_pill.height - 6)
    both.alpha_composite(bot_pill, (24, 150 + 6))
    lab.add(0.0, L, both)
    tracks = [lab, sub]
    tt = _title_track(proj, plan.get("title_on_screen"), y=40)
    if tt:
        tracks.append(tt)
    m = short.render(segs, tracks, plan.get("name") or proj.name, "compare",
                     meta={"pairs": len(segs), "cover_t": min(L / 2, 2.0)},
                     music={"file": str(resolve(plan["music"], proj)), "gain": plan.get("music_gain", -20)} if plan.get("music") else None,
                     work=proj / "_work", post=plan.get("post"), no_auto_crop=plan.get("no_auto_crop", False), no_auto_edit=plan.get("no_auto_edit", False), timestamp=plan.get("timestamp", False))
    return [m]


# ================================================================== 3. stunts

def stunts(proj: Path, plan: dict) -> list[dict]:
    """The fails first (each with its attempt number), the one that lands last, then a slow-motion replay."""
    layers = proj / "_layers"
    att = read_json(proj / "attempts" / "attempts.json", [])
    by_n = {a["n"]: a for a in att}
    segs = []
    tag = Track("attempt", layers, 0, 470, W, 260)
    rep = Track("replay", layers, 0, 1500, W, 220)
    t = 0.0
    lay = plan.get("layout", "crop")
    fails = plan.get("fails", [])
    landed = plan["landed"]
    order = [(f, False) for f in fails] + [(landed, True)]
    for item, ok in order:
        if isinstance(item, (int, float)):
            item = {"n": int(item)}
        base = dict(by_n.get(item.get("n"), {}))
        base.update({k: v for k, v in item.items() if v is not None})
        if "file" not in base:
            raise SystemExit(f"attempt {item} isn't in attempts/attempts.json; run nm attempts first or give file/a/b")
        a, b = float(base["a"]), float(base["b"])
        if not ok and "a" not in item and plan.get("fail_seconds"):
            a = max(a, b - float(plan["fail_seconds"]))        # a fail only needs its last few seconds
        label = int(base.get("label", base.get("n", 0)))
        segs.append({"kind": "single", "file": str(resolve(base["file"], proj)), "a": a, "b": b, "layout": lay,
                     **_frame(item, base, plan), "gain": _gain(item, plan)})
        dur = b - a
        img = draw.attempt_tag(label, landed=ok)
        tag.add_anim(t, t + dur, draw.pop_frames(img, (W, 260), [0.7, 1.08, 1.0]))
        t += dur
    r = plan.get("replay")
    if r:
        base = by_n.get(landed if isinstance(landed, int) else landed.get("n"), {})
        rf = str(resolve(r.get("file", base.get("file")), proj))
        speed = float(r.get("speed", 0.5))
        ra, rb = float(r["a"]), float(r["b"])
        segs.append({"kind": "single", "file": rf, "a": ra, "b": rb, "layout": lay, **_frame(r, plan),
                     "speed": speed, "gain": _add(_gain(r, plan), -4)})
        rdur = (rb - ra) / speed
        rep.add_anim(t, t + rdur, draw.pop_frames(draw.pill("REPLAY", f"{speed:g}×", size=76), (W, 220), [0.7, 1.05, 1.0]))
        t += rdur
    tracks = [tag, rep]
    tt = _title_track(proj, plan.get("title_on_screen"), y=40)
    if tt:
        tracks.append(tt)
    m = short.render(segs, tracks, plan.get("name") or proj.name, "stunts",
                     meta={"attempts_shown": len(order), "landed": landed, "cover_t": max(0.5, t - 1.5)},
                     music={"file": str(resolve(plan["music"], proj)), "gain": plan.get("music_gain", -18)} if plan.get("music") else None,
                     work=proj / "_work", post=plan.get("post"), no_auto_crop=plan.get("no_auto_crop", False), no_auto_edit=plan.get("no_auto_edit", False), timestamp=plan.get("timestamp", False))
    return [m]


# ================================================================== 4. myths

def _place_voice(clips: list[tuple[float, Path]], total: float, out: Path) -> Path:
    import soundfile as sf
    sr = 24000
    y = np.zeros(int(sr * (total + 0.5)), dtype=np.float32)
    for t, p in clips:
        x, r = sf.read(str(p), dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        if r != sr:
            idx = np.linspace(0, len(x) - 1, int(len(x) * sr / r))
            x = np.interp(idx, np.arange(len(x)), x).astype(np.float32)
        i = int(t * sr)
        y[i:i + len(x)] += x[: max(0, len(y) - i)]
    sf.write(str(out), y, sr)
    return out


def myths(proj: Path, plan: dict) -> list[dict]:
    """Five myths in one Short: MYTH #n and the question on screen (read out by the free voice if you want), the
    try, then a big BUSTED or CONFIRMED."""
    cfg = settings()
    layers = proj / "_layers"
    q_tr = Track("question", layers, 0, 90, W, 520)
    st_tr = Track("stamp", layers, 0, 760, W, 520)
    segs = []
    t = 0.0
    narr = []
    lay = plan.get("layout", "crop")
    for k, my in enumerate(plan["myths"], 1):
        f = str(resolve(my["file"], proj))
        a, b = float(my["a"]), float(my["b"])
        dur = b - a
        q_len = 0.0
        if plan.get("narrate", True):
            qtext = my.get("say") or my["question"]
            qf = proj / f"q_{_hash(qtext, cfg['voice'])}.wav"
            if not qf.exists():
                voice.synth(qtext, qf, plan.get("voice"), plan.get("voice_speed"))
            q_len = media.probe(qf)["dur"]
            narr.append((t + 0.15, qf))
        # duck the game while the question is read
        split = min(dur - 0.5, q_len + 0.3) if q_len else 0
        if split > 0.5:
            segs.append({"kind": "single", "file": f, "a": a, "b": a + split, "layout": lay, **_frame(my, plan), "gain": _add(_gain(my, plan), -16)})
            segs.append({"kind": "single", "file": f, "a": a + split, "b": b, "layout": lay, **_frame(my, plan), "gain": _gain(my, plan)})
        else:
            segs.append({"kind": "single", "file": f, "a": a, "b": b, "layout": lay, **_frame(my, plan), "gain": _gain(my, plan)})
        head = f"MYTH #{k}"
        card = draw.title_card(my["question"], 980, 58, 3)
        hd = draw.outline_text(head, 70, 900, draw.YELLOW, 1)
        comp = draw._canvas(W, 520)
        comp.alpha_composite(hd, ((W - hd.width) // 2, 0))
        comp.alpha_composite(card, ((W - card.width) // 2, hd.height - 6))
        q_tr.add_anim(t, t + dur, draw.pop_frames(comp, (W, 520), [0.85, 1.03, 1.0]))
        v = (my.get("verdict") or "BUSTED").upper()
        v_at = t + (float(my["verdict_at"]) - a if my.get("verdict_at") is not None else max(dur * 0.62, dur - 2.2))
        col = draw.RED if v.startswith("BUST") else draw.GREEN if v.startswith("CONF") else (240, 160, 20, 255)
        s_img = draw.stamp(v, col, 140)
        st_tr.add_anim(v_at, t + dur, draw.pop_frames(s_img, (W, 520), [1.6, 1.25, 0.95, 1.02, 1.0]))
        t += dur
    L = t
    vo = _place_voice(narr, L, proj / "_layers" / "narration.wav") if narr else None
    tracks = [q_tr, st_tr]
    m = short.render(segs, tracks, plan.get("name") or proj.name, "myths",
                     meta={"myths": len(plan["myths"]), "cover_t": 1.2},
                     voice={"file": str(vo), "start": 0, "gain": 0} if vo else None,
                     music={"file": str(resolve(plan["music"], proj)), "gain": plan.get("music_gain", -22)} if plan.get("music") else None,
                     work=proj / "_work", post=plan.get("post"), no_auto_crop=plan.get("no_auto_crop", False), no_auto_edit=plan.get("no_auto_edit", False), timestamp=plan.get("timestamp", False))
    return [m]


# ================================================================== 5. moments

def moments(proj: Path, plan: dict) -> list[dict]:
    """Each picked moment becomes its own Short: the hook on top, captions from what you said."""
    src = str(resolve(plan["file"], proj))
    lay = plan.get("layout", "crop")
    made = []
    for k, mo in enumerate(plan["moments"], 1):
        a, b = float(mo["a"]), float(mo["b"])
        L = b - a
        layers = proj / "_layers" / f"m{k:02d}"
        wav = media.extract_wav(Path(src), layers / "clip.wav", a, L)
        words = listen.words(wav) if plan.get("captions", True) else []
        tracks = []
        if words:
            caps = Track("captions", layers, 0, 1180, draw.CAP_W, draw.CAP_H)
            for s, e, p in draw.caption_events(words, layers / "cap_png", L):
                caps.add_png(s, e, p)
            tracks.append(caps)
        hook = mo.get("hook")
        if hook:
            tr = Track("hook", layers, 0, 150, W, 420)
            card = draw.title_card(hook, 960, 64, 3)
            tr.add_anim(0.0, min(L, float(mo.get("hook_s", 3.2))), draw.pop_frames(card, (W, 420), [0.8, 0.96, 1.03, 1.0]))
            tracks.append(tr)
        seg = {"kind": "single", "file": src, "a": a, "b": b, "layout": mo.get("layout", lay),
               **_frame(mo, plan), "gain": _gain(mo, plan)}
        made.append(short.render([seg], tracks, mo.get("name") or hook or f"moment {k}", "moments",
                                 meta={"source": src, "a": a, "b": b, "hook": hook, "why": mo.get("why", ""),
                                       "cover_t": float(mo.get("peak", b - 3)) - a if mo.get("peak") else L * 0.6},
                                 work=layers, post={"title": hook, "caption": mo.get("caption", ""),
                                                    "hashtags": mo.get("hashtags"),
                                                    "credit": mo.get("credit") or plan.get("credit")},
                                 no_auto_crop=plan.get("no_auto_crop", False),
                                 no_auto_edit=plan.get("no_auto_edit", False),
                                 timestamp=plan.get("timestamp", False)))
    return made


# ================================================================== 6. ranking

def rank(proj: Path, plan: dict) -> list[dict]:
    """Five clips from worst to best: the list on screen, each label appearing as its clip plays, number one last."""
    items = sorted(plan["items"], key=lambda x: -int(x["rank"]))       # play 5, 4, 3, 2, 1
    n = max(int(x["rank"]) for x in items)
    labels = {int(x["rank"]): x["label"] for x in items}
    layers = proj / "_layers"
    board = Track("board", layers, 0, 0, W, H)
    segs = []
    t = 0.0
    shown: set[int] = set()
    prev = draw.rank_board(labels, set(), None, n, plan.get("title_on_screen"))
    for it in items:
        r = int(it["rank"])
        f = str(resolve(it["file"], proj))
        a, b = float(it["a"]), float(it["b"])
        dur = b - a
        segs.append({"kind": "single", "file": f, "a": a, "b": b, "layout": it.get("layout", plan.get("layout", "crop")),
                     **_frame(it, plan), "gain": _gain(it, plan)})
        shown.add(r)
        now = draw.rank_board(labels, set(shown), r, n, plan.get("title_on_screen"))
        board.add_anim(t, t + dur, draw.board_frames(prev, now, 6))
        prev = now
        t += dur
    L = t
    m = short.render(segs, [board], plan.get("name") or proj.name, "rank",
                     meta={"items": len(items), "cover_t": 1.5},
                     music={"file": str(resolve(plan["music"], proj)), "gain": plan.get("music_gain", -20)} if plan.get("music") else None,
                     work=proj / "_work", post=plan.get("post"), no_auto_crop=plan.get("no_auto_crop", False), no_auto_edit=plan.get("no_auto_edit", False), timestamp=plan.get("timestamp", False))
    return [m]


# ================================================================== 7. skit

def skit(proj: Path, plan: dict) -> list[dict]:
    """The shots you filmed, in story order, trimmed; a title, and an optional line on screen per shot."""
    layers = proj / "_layers"
    segs = []
    line_tr = Track("lines", layers, 0, 1240, W, 420)
    t = 0.0
    for sh in plan["shots"]:
        if not sh.get("file"):
            raise SystemExit(f"shot {sh.get('n')} has no file yet: film it, then set file / a / b in plan.json")
        f = str(resolve(sh["file"], proj))
        a = float(sh.get("a", 0))
        b = float(sh["b"]) if sh.get("b") is not None else media.probe(Path(f))["dur"]
        dur = b - a
        segs.append({"kind": "single", "file": f, "a": a, "b": b, "layout": sh.get("layout", plan.get("layout", "crop")),
                     **_frame(sh, plan), "gain": _gain(sh, plan)})
        if sh.get("on_screen"):
            img = draw.title_card(sh["on_screen"], 900, 56, 2)
            line_tr.add_anim(t + 0.15, t + dur, draw.pop_frames(img, (W, 420), [0.8, 1.04, 1.0]))
        t += dur
    tracks = [line_tr]
    tt = _title_track(proj, plan.get("title_on_screen"))
    if tt:
        tracks.append(tt)
    m = short.render(segs, tracks, plan.get("name") or proj.name, "skit",
                     meta={"shots": len(segs), "cover_t": 1.0},
                     music={"file": str(resolve(plan["music"], proj)), "gain": plan.get("music_gain", -18)} if plan.get("music") else None,
                     work=proj / "_work", post=plan.get("post"), no_auto_crop=plan.get("no_auto_crop", False), no_auto_edit=plan.get("no_auto_edit", False), timestamp=plan.get("timestamp", False))
    return [m]


MAKERS = {"commentary": commentary, "compare": compare, "stunts": stunts, "myths": myths, "moments": moments,
          "rank": rank, "skit": skit}

