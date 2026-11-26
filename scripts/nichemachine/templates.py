"""The plan.json each niche starts from. Claude fills it in (SKILL.md says how), then `nm make <project>`."""

GAMEPLAY = "~/NicheMachine/gameplay"

PLANS = {
    "commentary": {
        "mode": "commentary",
        "_how": "Write script.md (reference/commentary.md). Put {pic:id} right before the word where a picture should pop, "
                "and describe each id under pictures: a file, a url, or a query for a free picture. Leave gameplay.file "
                "empty and it finds a stretch of your gameplay where you're moving.",
        "name": "",
        "title_on_screen": None,
        "voice": None, "voice_speed": None, "voice_file": None,
        "pictures": {"example": {"query": "PlayStation 5 console", "caption": None}},
        "picture_hold": 2.6,
        "gameplay": {"folder": GAMEPLAY, "file": None, "a": None, "layout": "crop", "focus": 0.5, "mute": False},
        "music": None,
        "post": {"title": "", "caption": "", "hashtags": None},
    },
    "compare": {
        "mode": "compare",
        "_how": "One pair per thing both games let you do (reference/comparisons.md). Use `nm look` on both clips to find "
                "the same moment, then set each a so the two line up. length = seconds per pair.",
        "name": "",
        "title_on_screen": "GTA 6 vs GTA 5",
        "top_label": "GTA 6", "top_sub": "2026", "bottom_label": "GTA 5", "bottom_sub": "2013",
        "audio": "top",
        "pairs": [{"subtitle": "Water", "length": 6,
                   "top": {"file": "", "a": 0, "focus": 0.5}, "bottom": {"file": "", "a": 0, "focus": 0.5}}],
        "music": None,
        "post": {"title": "", "caption": "", "hashtags": None},
    },
    "stunts": {
        "mode": "stunts",
        "_how": "Run `nm attempts <recording or folder> --project <this folder>` first and look at the sheets. "
                "fails = attempt numbers to show (3 to 6 of the best), landed = the one that worked. "
                "label = the number shown on screen if you want the real count (attempt #69).",
        "name": "",
        "title_on_screen": None,
        "layout": "crop", "focus": 0.5,
        "fail_seconds": 3.5,
        "fails": [{"n": 1, "label": None}],
        "landed": {"n": 2, "label": None},
        "replay": {"a": 0, "b": 0, "speed": 0.5, "zoom": 1.0},
        "music": None,
        "post": {"title": "", "caption": "", "hashtags": None},
    },
    "myths": {
        "mode": "myths",
        "_how": "Pick tests from reference/myths.md, film each one, then fill one entry per myth: the clip, where the try "
                "starts and ends, and the verdict. verdict_at = the second in the clip when the result shows.",
        "name": "",
        "narrate": True, "voice": None, "voice_speed": None,
        "layout": "crop",
        "myths": [{"question": "Can you survive a jump off the tallest building?", "say": None,
                   "file": "", "a": 0, "b": 8, "verdict": "BUSTED", "verdict_at": None, "focus": 0.5}],
        "music": None,
        "post": {"title": "Busting 5 Myths in GTA 6", "caption": "", "hashtags": None},
    },
    "moments": {
        "mode": "moments",
        "_how": "Run `nm scan <recording> --project <this folder>`, read the chunk files, and add one entry per moment "
                "worth a Short (reference/moments.md). Each entry becomes its own Short.",
        "file": "",
        "layout": "crop", "focus": 0.5, "captions": True,
        "moments": [{"a": 0, "b": 30, "peak": None, "hook": "", "caption": "", "why": ""}],
    },
    "rank": {
        "mode": "rank",
        "_how": "One item per rank (5 is played first, 1 last). Keep each clip 4 to 9 seconds. "
                "Add your own take in the labels: the ranking is what makes it yours.",
        "name": "",
        "title_on_screen": "Ranking GTA 6 police chases",
        "layout": "crop",
        "items": [{"rank": 5, "label": "", "file": "", "a": 0, "b": 6, "focus": 0.5}],
        "music": None,
        "post": {"title": "", "caption": "", "hashtags": None},
    },
    "skit": {
        "mode": "skit",
        "_how": "Write shotlist.md first (reference/skits.md): a story with no dialogue, shot by shot. "
                "Film the shots, then fill file / a / b for each.",
        "name": "",
        "title_on_screen": None,
        "layout": "crop",
        "shots": [{"n": 1, "what": "", "file": None, "a": 0, "b": None, "focus": 0.5, "on_screen": None}],
        "music": None,
        "post": {"title": "", "caption": "", "hashtags": None},
    },
}
