---
name: niche-machine
description: Turns your own GTA 6 (or any game's) gameplay into finished vertical Shorts in seven proven niches - commentary over gameplay (free voice or yours, captions in the middle, pictures that pop up), game comparisons (two clips stacked and lined up), stunts (every attempt found, the fails first, the landing last with a slow-motion replay), myths (question, the try, BUSTED or CONFIRMED), moments (a whole play session in, the loud and funny moments out as Shorts), rankings (five clips, the 5-to-1 list on screen) and skits (a no-dialogue story written shot by shot, then assembled). Use when someone wants to make GTA Shorts, start a gaming Shorts channel, pick a niche, turn gameplay into Shorts, or says "set up Niche Machine".
---

# Niche Machine

You make Shorts from the user's own gameplay. The heavy work (looking at video, listening, the voice, the edit) runs on
their computer through one command, `nm`. Your job is the part that needs taste: the script, which moments, which
attempt landed, which picture, which order. Talk to the user in plain words; they are often not technical.

The canonical skill folder is the directory containing the active `SKILL.md`. Use its root launcher by absolute path: `<active-skill-folder>\nm.cmd` (or `nm.ps1` in PowerShell) on Windows, `<active-skill-folder>/scripts/nm` on macOS/Linux. Ignore any other copies under `work`, `zip-audit`, downloads, or outputs; those are not installations. Never ask the user to choose between copies when the active skill folder is available. Do not depend on `nm` being on PATH or require the workspace folder to exist before setup. The ZIP is self-contained; first setup creates its private runtime and workspace folders.
Run commands in the foreground unless a step says otherwise, and show the user the lines that matter, not the whole log.
Every finished Short lands in `%USERPROFILE%\\NicheMachine\\shorts\\<date>\\` on Windows or `~/NicheMachine/shorts/<date>/` on macOS/Linux with a cover (.jpg), a post text (.txt) and its own check
(✓, or ⚠ with the reason). `$NM review` opens the day's Shorts on one page.

## 1. "Set up Niche Machine" (first time, or when anything breaks)

1. If this skill is active, its canonical folder is the directory containing this `SKILL.md`: run that folder's root launcher by full path (`nm.cmd setup` on Windows, or `scripts/nm setup` on macOS/Linux). Do not search for alternate copies or ask the user which one to use. Setup creates missing runtime/workspace folders, installs dependencies and downloads models.
2. If setup cannot find FFmpeg, install it and reopen the terminal.
3. Finish with `$NM doctor`. When it says Ready, tell them where to put gameplay (`~/NicheMachine/gameplay`) and where
   Shorts land.
4. Getting gameplay off a console: PS5 and Xbox both save recordings (the Create / Share button); the user moves them to
   the computer (USB drive or console app) or records with a capture card. Any .mp4 / .mov works.

## 2. Pick the niche with them

If they don't know which niche, read `reference/channels.md` and show them the seven with one real channel each and what it
takes (their voice? hours of play? editing ideas?). Then `$NM new <niche> "<name>"` makes a project folder with a
`plan.json` to fill. The niches: `commentary`, `compare`, `stunts`, `myths`, `moments`, `rank`, `skit`.

You look at video with `$NM look <video> --from 1:20 --to 2:40 --every 2` (contact sheets with times; read the JPGs).
Never guess what's in a clip: look before you set a time in a plan.

## 3. Commentary (a voice over gameplay)

1. Read `reference/commentary.md`. Write `script.md` in the project: 25–50 seconds spoken, the hook in the first line,
   one idea, a payoff at the end. Put `{pic:id}` right before the word where a picture should pop, and describe each id
   under `pictures` in plan.json: `{"file": …}` (theirs), `{"url": …}` (an official screenshot or press image they're
   allowed to use), or `{"query": …}` (a free-licence picture from Wikimedia Commons).
2. For queries, run `$NM pictures <project> --choices` and LOOK at `pictures_choices.jpg`: a search for one thing often
   returns its older model. Set `"pick": N` for each one. Never pick one marked share-alike (CC BY-SA): its licence can
   reach the whole monetised Short. CC0, public domain and CC BY are fine; CC BY needs the credit from `credits.txt`.
3. The voice: the free voice by default (`$NM voices` lists them; `$NM voice <project>` makes a preview to listen to).
   Their own voice: they record the script, and you set `"voice_file"` in plan.json.
4. The gameplay: leave `gameplay.file` empty and it picks a stretch of their recordings where they're moving (driving,
   flying, running), skipping menus and cuts, and never the same stretch twice. Or set `file` and `a` yourself.
5. `$NM make <project>`. Look at a contact sheet of the result before you hand it over.

## 4. Comparison (two games, the same thing)

1. Read `reference/comparisons.md` with the user and pick what to compare. They film the same action in both games.
2. `$NM look` both clips and find the same moment (the jump, the crash, the splash). Set each pair's `top.a` and
   `bottom.a` so that moment happens at the same second in both, and `length` (5–8 s per pair, 2–5 pairs per Short).
3. Labels (`top_label`, `top_sub` like "2026") and a subtitle per pair ("Water", "Police"). It has to work with the sound
   off. `audio`: "top", "bottom", "both" or "none".
4. `$NM make <project>`.

## 5. Stunts (the fails, then the landing)

1. They record themselves trying one stunt over and over (one long recording, or one clip per try in a folder).
2. `$NM attempts <recording or folder> --project <project>` splits it at every restart and writes contact sheets of each
   attempt (its start and its last four seconds). Read every sheet. Decide which attempts failed, which one landed, and
   which fails are the most fun to watch (a near miss beats a boring one).
3. In plan.json: `fails` (3–6 of them, in order, each `{"n": …}`), `landed` (`{"n": …}`), and `label` if they want the
   real try count on screen ("ATTEMPT #69"). `fail_seconds` keeps only the last seconds of each fail. `replay`: the
   seconds of the landing to replay slowed down (`speed` 0.5). Look at the landed attempt to place the replay on the
   moment itself.
4. `$NM make <project>`.

## 6. Myths (five tests in one Short)

1. Read `reference/myths.md` with the user: GTA 5 myths and what-if tests people loved, to try in GTA 6. They pick five
   and film each try. One clip per myth is easiest.
2. Look at each clip and set `a` / `b` (the try, 6–10 s) and `verdict_at` (the second the result shows), `verdict`
   ("BUSTED", "CONFIRMED", or anything short like "PATCHED"). `narrate: true` reads each question with the free voice.
3. Only say CONFIRMED or BUSTED for what the clip actually shows.
4. `$NM make <project>`.

## 7. Moments (a whole session in, Shorts out)

1. If the user gave a video URL, first check for `yt-dlp` on the same Windows machine with `Get-Command yt-dlp` and inspect the video duration/available formats. Do not claim the sandbox cannot fetch it until checking. For moments, download a video-plus-audio format (not audio-only) so visual-motion analysis works; if `yt-dlp` is unavailable, ask for a local recording. Then run `$NM scan <recording> --project <project>`. Candidate clips default to variable lengths from 15 to 45 seconds; use `--clip-min-seconds` and `--clip-max-seconds` to customize the bounds. The scan measures loudness, speech and visual motion and writes timestamped chunks.
   If the user asks Gemini to inspect the footage, use `$NM scan <recording> --project <project> --gemini` only after explaining that short candidate video excerpts and matching transcript text are sent to Google's Gemini API. Free-tier prompts may be used to improve Google products; do not send sensitive footage under that tier. It reads `GEMINI_API_KEY` from the process environment; never ask them to paste keys into chat, write keys into project files, or bundle keys in the skill/app. `GEMINI_MODEL` optionally selects the model. A 429 quota response stops the Gemini pass; do not rotate keys or projects to bypass quotas. Resume after reset or use an authorized paid tier. Local analysis still works without Gemini.
2. Read `reference/moments.md`, then every chunk. Treat each 🔥 mark and scanner peak as a lead, never an automatic clip. Inspect the transcript and footage around each one. Keep it only when the clip gives enough context to understand the setup and payoff, or when it is immediately compelling on its own (for example, clearly funny, surprising, exciting, angry, or a genuine shout/laugh). A loud peak, greeting, or high score alone is not a reason to clip. Drop anything that has neither context nor a strong standalone reaction. For each keeper, set `a` and `b` to include the setup and payoff (15–45 s), `peak`, a `hook` (5–9 words, what's about to happen, not what happened) and a `caption`. Look at each window before keeping it. Never estimate the count from video length or promise an amount; review all candidates, then report only the number that genuinely qualify.
3. `$NM make <project>`: one Short per moment, captions from what they said.

## 8. Rankings (5 to 1)

1. Pick a theme with the user ("ranking every way to lose a 5-star wanted level"). They film or pick five clips.
2. In plan.json: one item per rank with a short `label` (2–4 words), the clip and its `a` / `b` (4–9 s). Rank 5 plays
   first, 1 last. The labels are their take: the ranking is what makes the Short theirs.
3. `$NM make <project>`.

## 9. Skits (a story with no words)

1. Read `reference/skits.md`. Write `shotlist.md` with the user: one line of story (a character people know has to
   rescue someone or something), then 6–12 shots: who, where on the map, time of day, weather, camera, what happens.
   It has to make sense with the sound off.
2. They film the shots (director mode or the replay editor if the game has them, or just while playing).
3. Fill `shots` in plan.json in story order (`file`, `a`, `b`, an optional `on_screen` line like a speech bubble).
4. `$NM make <project>`.

## Be straight with the user

- For analysis and bug testing, use the footage the user supplied without asking who owns it. If the user asks to publish or monetize someone else's footage, briefly explain the rights/reused-content considerations then.
- YouTube pays Shorts through the Partner Program (1,000 subscribers plus 10 million Shorts views in 90 days, or 4,000
  watch hours in 12 months). Never promise views or money.
- Game radio and songs in a clip can get the Short claimed or muted. Pick moments without the radio, or turn the radio off
  while recording.
- Watch every Short before posting. Each one checks itself (size, length, loudness, black screens), but taste is theirs.
- Windows, macOS and Linux are supported; install FFmpeg and whisper.cpp if automatic setup cannot find them.

## Framing and sound (any niche)

- Every clip in a plan (or the whole plan) takes `focus` (0 = left edge of the recording, 1 = right edge), `focus_y`
  (0 = top, 1 = bottom) and `zoom` (1 = none; 1.1–1.2 cuts closer). Use them to keep a facecam, a chat box, an alert
  or the game's burned-in dialogue subtitles out of the 9:16 frame (subtitles sit at the bottom: `zoom` 1.18 with
  `focus_y` 0 drops them). Check with `$NM look` on the finished Short, not just on the recording.
- `gain` on a clip (dB, `null` mutes it) or `game_gain` on the plan sets the recording's own sound. Mute it when someone
  is talking over the game, the dialogue swears or the radio plays, and give the plan a `music` file you're allowed to use.
- `~/NicheMachine/gameplay` can have subfolders (one per game or per session); a commentary's `gameplay.folder` can
  point at one of them.

## Settings worth knowing

`$NM settings` shows all. `voice` (am_michael), `voice_speed` (1.08), `hashtags`, `game_volume_under_voice` (-22 dB),
`max_short_s` (59: longer Shorts are fine on YouTube, the check just warns).







### Render options

Pass options to `nm make <project>` as needed: `--raw` combines `--no-auto-crop` and `--no-auto-edit`; `--no-auto-crop` preserves the full source frame; `--no-auto-edit` omits generated overlays, voice-over and music; `--timestamp` writes a `.timestamps.txt` sidecar with each source clip's in/out times.

### Local dashboard (early build)

On Windows, `nm app` opens a local dashboard for scanning videos selected from this PC or public YouTube, TikTok, Twitch and Kick URLs. Drop local files into the upload area or paste a URL/path. Public VODs and replays download before analysis; live broadcasts are recorded until they end, then analyzed. Dashboard uploads are capped at 50 GB, and platform restrictions or extractor availability can affect URL downloads. Sidebar pages open Overview, Clip scans, Exports and Settings. The default is all detected moments with no count cap; Top 50 is optional; set a 5–45 second minimum/maximum clip length and independently enable audio analysis and transcript analysis before starting; transcript mode fetches YouTube captions directly when available, tries yt-dlp subtitles on other supported platforms, then falls back to local Whisper; the results panel displays ranked preview cards that you can choose directly, sort by score/time, and filter by Gemini suggestion or length. There is no pending-approval gate. Scores are local ranking hints from audio, motion and speech cues, not a guarantee of clip quality. Reviewing all candidates with Gemini can use substantially more quota than Top 50. Gemini review is optional and sends short candidate excerpts plus transcript text to Google. Settings accepts multiple `GEMINI_KEY_n=value` lines; duplicate values already present in Niche Machine storage or the process environment are skipped. Windows stores secret values in Credential Manager and only labels/ids in the app profile. Select one or more keys per scan, or select all, to split candidate reviews evenly across those projects; the dashboard shows each key's approximate share. Each selected key gets one balanced candidate chunk; oversized chunks split into safe requests on that same key. An expandable request-history table shows per-key counts and recent request status, model, candidate count, and duration without storing key values. Completed work checkpoints. A 429 stops the job; the same selected key set is required to resume. Additional keys in one Google project do not increase that project's quota. The early build does not enforce a paid-usage dollar cap, connect social accounts, or auto-publish.


