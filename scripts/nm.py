"""Niche Machine: your gameplay in, finished Shorts out, in seven proven GTA niches.

  nm setup | doctor                         install / check what this computer needs
  nm new <niche> "<name>"                   start a project (commentary, compare, stunts, myths, moments, rank, skit)
  nm make <project> [--raw] [--no-auto-crop] [--no-auto-edit] [--timestamp]  render it
  nm look <video> [--from T] [--to T] [--every S]   contact sheet(s) of frames with times, for Claude to look at
  nm moving <folder|video> [--length S]     the stretches where you're actually moving (commentary backgrounds)
  nm attempts <recording|folder> --project <project>   find every attempt at a stunt
  nm scan <recording> --project <project>   listen to a whole session and mark the hot moments
  nm pictures <project>                     fetch the pictures named in plan.json
  nm voice <project>                        make the voice-over now (commentary) and print its length
  nm voices                                 list the free voices
  nm review [day]                           the day's Shorts on one page
  nm settings [key value]                   show or change a setting
  nm app                                   open the local creator dashboard
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import webbrowser

# Windows consoles may use a legacy code page that cannot print the UI glyphs.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nichemachine import util  # noqa: E402
from nichemachine.util import MODES, PROJECTS, read_json, resolve, say, slug, today, write_json  # noqa: E402

ALIASES = {"comparison": "compare", "comparisons": "compare", "stunt": "stunts", "myth": "myths",
           "discoveries": "myths", "discovery": "myths", "moment": "moments", "ranking": "rank", "rankings": "rank",
           "skits": "skit", "story": "skit"}


def _project(p: str) -> Path:
    path = resolve(p)
    if path.exists():
        return path
    cand = sorted(PROJECTS.glob(f"*{p}*"))
    if len(cand) == 1:
        return cand[0]
    raise SystemExit(f"No project {p!r}" + (f" (several match: {', '.join(c.name for c in cand)})" if cand else ""))


def cmd_new(a):
    from nichemachine.templates import PLANS
    mode = ALIASES.get(a.niche.lower(), a.niche.lower())
    if mode not in MODES:
        raise SystemExit(f"Unknown niche {a.niche!r}. Pick one of: {', '.join(MODES)}")
    base = PROJECTS / f"{today()}_{mode}_{slug(a.name, 36)}"
    proj, k = base, 2
    while (proj / "plan.json").exists():          # a project with that name already exists: start a fresh one
        proj = base.with_name(f"{base.name}-{k}")
        k += 1
    proj.mkdir(parents=True, exist_ok=True)
    plan = json.loads(json.dumps(PLANS[mode]))
    if "name" in plan:
        plan["name"] = a.name
    if not (proj / "plan.json").exists():
        write_json(proj / "plan.json", plan)
    if mode == "commentary" and not (proj / "script.md").exists():
        (proj / "script.md").write_text("# Write the voice-over here. {pic:id} before a word pops that picture.\n")
    if mode == "skit" and not (proj / "shotlist.md").exists():
        (proj / "shotlist.md").write_text(f"# {a.name}\n\nThe story in one line:\n\n| # | What happens | Who | Where | Time / weather | Camera |\n|---|---|---|---|---|---|\n")
    say(f"✓ {proj}")
    say(f"  plan: {proj / 'plan.json'}")


def cmd_make(a):
    from nichemachine import modes, review
    proj = _project(a.project)
    plan = read_json(proj / "plan.json")
    if not plan:
        raise SystemExit(f"{proj / 'plan.json'} is missing or not valid JSON")
    mode = plan.get("mode")
    plan["no_auto_crop"] = bool(a.no_auto_crop)
    plan["no_auto_edit"] = bool(a.no_auto_edit)
    plan["timestamp"] = bool(a.timestamp)
    if a.raw:
        plan["no_auto_crop"] = True
        plan["no_auto_edit"] = True
    say(f"Niche Machine · {mode} · {proj.name}")
    made = modes.MAKERS[mode](proj, plan)
    page = review.build()
    write_json(proj / "made.json", made)
    say(f"\n{len(made)} Short(s) made · review: {page}")


def cmd_look(a):
    from nichemachine import media
    v = resolve(a.video)
    d = media.probe(v)["dur"]
    t0 = util.parse_t(a.start) if a.start else 0.0
    t1 = util.parse_t(a.end) if a.end else d
    every = a.every or max(0.5, round((t1 - t0) / 47, 1))
    out = resolve(a.out) if a.out else (util.WORK / "_looks" / f"{slug(v.stem, 30)}_{int(t0)}_{int(t1)}.jpg")
    sheets = media.contact_sheet(v, out, t0, min(t1, d - 0.05), every, label=f"{v.name} · every {every:g} s")
    for s in sheets:
        say(str(s))


def cmd_moving(a):
    from nichemachine import find
    vids = util.videos_in(resolve(a.path))
    for c in find.moving(vids, a.length, n=a.n, avoid_used=not a.all):
        say(f"{Path(c['file']).name}  {util.hms(c['a'])}–{util.hms(c['b'])}  moving score {c['score']}")


def cmd_attempts(a):
    from nichemachine import find
    proj = _project(a.project)
    find.attempts(resolve(a.path), proj / "attempts")


def cmd_scan(a):
    from nichemachine import find
    proj = _project(a.project)
    v = resolve(a.recording)
    info = find.scan(v, proj / "scan", max_candidates=a.max_candidates,
                     clip_min_seconds=a.clip_min_seconds, clip_max_seconds=a.clip_max_seconds)
    if a.gemini:
        from nichemachine import gemini_review, listen
        transcript = read_json(proj / "scan" / "transcript.json", {})
        reviews = gemini_review.review(v, proj / "scan", read_json(proj / "scan" / "candidates.json", []),
                                       listen.segments(transcript), a.model)
        kept = [r["candidate"] for r in reviews if r["keep"]]
        say(f"  Gemini marked {len(kept)} candidate(s) worth human review; this is not an automatic clip approval.")
    plan = read_json(proj / "plan.json", {})
    if plan.get("mode") == "moments" and not plan.get("file"):
        plan["file"] = str(v)
        write_json(proj / "plan.json", plan)


def cmd_pictures(a):
    from nichemachine import pictures
    proj = _project(a.project)
    plan = read_json(proj / "plan.json", {})
    if a.choices:
        out = pictures.choices(proj, plan.get("pictures", {}))
        say(f"look: {out}" if out else "no picture is asked for by query")
        return
    for f in (proj / "pictures").glob("*"):
        f.unlink()
    pictures.fetch(proj, plan.get("pictures", {}))


def cmd_voice(a):
    from nichemachine import media, voice
    proj = _project(a.project)
    plan = read_json(proj / "plan.json", {})
    text = voice.spoken((proj / "script.md").read_text())
    out = proj / "voice_preview.wav"
    voice.synth(text, out, plan.get("voice"), plan.get("voice_speed"))
    say(f"{out}  ({media.probe(out)['dur']:.1f} s)")


def cmd_voices(a):
    from nichemachine import voice
    vs = voice.voices()
    say("American: " + ", ".join(v for v in vs if v[:2] in ("am", "af")))
    say("British:  " + ", ".join(v for v in vs if v[:2] in ("bm", "bf")))
    say(f"Now: {util.settings()['voice']}  ·  change: nm settings voice am_adam")


def cmd_review(a):
    from nichemachine import review
    p = review.build(a.day)
    say(str(p))
    if not a.no_open:
        if sys.platform == "darwin":
            subprocess.run(["open", str(p)])
        else:
            webbrowser.open(p.as_uri())


def cmd_settings(a):
    s = util.settings()
    if a.key:
        v = a.value
        try:
            v = json.loads(v)
        except Exception:
            pass
        s[a.key] = v
        util.save_settings(s)
    for k, v in s.items():
        say(f"{k}: {v}")


def cmd_setup(a, doctor=False):
    from nichemachine import setup
    ok = setup.setup(doctor_only=doctor)
    sys.exit(0 if ok else 1)


def cmd_app(a):
    from nichemachine.app import serve
    serve()


def main():
    ap = argparse.ArgumentParser(prog="nm", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd")
    sp.add_parser("setup")
    sp.add_parser("doctor")
    sp.add_parser("app", help="open the local creator dashboard")
    p = sp.add_parser("new"); p.add_argument("niche"); p.add_argument("name")
    p = sp.add_parser("make"); p.add_argument("project")
    p.add_argument("--no-auto-crop", action="store_true", help="preserve the full source frame inside the vertical output")
    p.add_argument("--no-auto-edit", action="store_true", help="omit generated overlays, voice-over and music")
    p.add_argument("--timestamp", action="store_true", help="write source clip in/out timestamps beside each output")
    p.add_argument("--raw", action="store_true", help="alias for --no-auto-crop --no-auto-edit")
    p = sp.add_parser("look"); p.add_argument("video"); p.add_argument("--from", dest="start"); p.add_argument("--to", dest="end")
    p.add_argument("--every", type=float); p.add_argument("--out")
    p = sp.add_parser("moving"); p.add_argument("path"); p.add_argument("--length", type=float, default=40)
    p.add_argument("-n", type=int, default=5); p.add_argument("--all", action="store_true")
    p = sp.add_parser("attempts"); p.add_argument("path"); p.add_argument("--project", required=True)
    p = sp.add_parser("scan"); p.add_argument("recording"); p.add_argument("--project", required=True)
    p.add_argument("--max-candidates", type=int, default=None, help="optional candidate ceiling from 1 to 50 (default: all detected moments)")
    p.add_argument("--clip-min-seconds", type=float, default=15.0, help="minimum candidate length in seconds (default: 15)")
    p.add_argument("--clip-max-seconds", type=float, default=45.0, help="maximum candidate length in seconds (default: 45)")
    p.add_argument("--gemini", action="store_true", help="ask Gemini to visually review short locally detected candidates")
    p.add_argument("--model", help="Gemini model (defaults to GEMINI_MODEL or gemini-3.8-flash)")
    p = sp.add_parser("pictures"); p.add_argument("project"); p.add_argument("--choices", action="store_true")
    p = sp.add_parser("voice"); p.add_argument("project")
    sp.add_parser("voices")
    p = sp.add_parser("review"); p.add_argument("day", nargs="?"); p.add_argument("--no-open", action="store_true")
    p = sp.add_parser("settings"); p.add_argument("key", nargs="?"); p.add_argument("value", nargs="?")
    a = ap.parse_args()
    if a.cmd in ("setup", "doctor"):
        return cmd_setup(a, doctor=a.cmd == "doctor")
    if a.cmd == "app":
        return cmd_app(a)
    fn = {"new": cmd_new, "make": cmd_make, "look": cmd_look, "moving": cmd_moving, "attempts": cmd_attempts,
          "scan": cmd_scan, "pictures": cmd_pictures, "voice": cmd_voice, "voices": cmd_voices,
          "review": cmd_review, "settings": cmd_settings}.get(a.cmd)
    if not fn:
        ap.print_help()
        return
    fn(a)


if __name__ == "__main__":
    main()



