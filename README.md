# Niche Machine

A self-contained Claude Code skill folder for turning gameplay into finished vertical Shorts. You do not need Niche Machine preinstalled: the ZIP contains the launcher and scripts. First setup creates the private Python environment, downloads models and makes the gameplay/project/output folders.

## Windows install
1. Install Python 3.12 from python.org and enable **Add python.exe to PATH**.
2. Extract this ZIP to `%USERPROFILE%\.claude\skills\niche-machine`.
3. Open Command Prompt in that folder and run `nm.cmd setup`. In PowerShell run `./nm.ps1 setup`.
4. Setup installs Python packages, FFmpeg via winget when available, Whisper CLI and the model files. It creates `%USERPROFILE%\.niche-machine` for its private runtime/models and `%USERPROFILE%\NicheMachine` for gameplay, projects and finished Shorts. Neither folder needs to exist before setup.
5. Run `nm.cmd doctor` (or `./nm.ps1 doctor`) and wait for **Ready**. Put recordings in `%USERPROFILE%\NicheMachine\gameplay`.

You can invoke the root launcher from any folder by using its full path, for example:

```powershell
& "$env:USERPROFILE\.claude\skills\niche-machine\nm.cmd" setup
```

Shorts land in `%USERPROFILE%\NicheMachine\shorts\<date>`. Set `NICHE_HOME` or `NICHE_WORK` before setup to choose other data locations. macOS users can launch `scripts/nm`.

## Render options

```text
nm.cmd make <project> --raw --timestamp
```

`--raw` is an alias for `--no-auto-crop --no-auto-edit`: it keeps the full source frame and omits generated overlays, voice-over and music. You can also use either `--no-auto-crop` or `--no-auto-edit` separately. `--timestamp` writes source clip in/out times to a `.timestamps.txt` sidecar beside each Short.

## Optional Gemini visual review

`nm scan <recording> --project <project>` now finds variable-length candidates by default (15–45 seconds); set `--clip-min-seconds` and `--clip-max-seconds` to choose another valid range up to 45 seconds. `nm scan <recording> --project <project> --gemini` sends the short candidate excerpts selected by local audio analysis, plus their transcript text, to Google's Gemini API for a visual/context review. This uploads footage to Google; use it only when that is acceptable for the recording. Set `GEMINI_API_KEY` in the environment (never paste it into chat or save it in a project file); optionally set `GEMINI_MODEL`. The default model is `gemini-3.8-flash`. Gemini's result is a review aid, not automatic approval to clip. A quota/rate-limit response stops processing. The app does not rotate API keys to bypass quota limits: Gemini limits are project-level, so additional keys in one project do not add quota. Under Google's current terms, free-tier prompts may be used to improve Google products; choose a paid tier for content excluded from product improvement. Check Google's current terms, limits and account settings before each app release.

`reference/channels.md` has channels grouped by niche.


## Local creator dashboard (early build)

From the skill folder, run `nm.cmd app` (PowerShell: `./nm.ps1 app`). This opens a Windows dashboard bound to `127.0.0.1`. The sidebar opens Overview, Clip scans, Exports and Settings. Before analysis, set the minimum and maximum candidate length (5–45 seconds), then choose audio analysis and transcript analysis independently. YouTube captions are fetched directly when available; other supported platforms try yt-dlp subtitles, with local Whisper as a fallback. By default, the dashboard keeps all detected moments with no count cap; Top 50 is optional. Results appear as ranked, previewable clip cards that can be selected directly, sorted by score or time, and filtered by Gemini suggestion or clip length. There is no pending-approval gate; choose the clips you want from the list. Scores use local audio, motion and speech cues as ranking hints. Reviewing all candidates with Gemini can use substantially more quota than Top 50.

Settings accepts multiple `GEMINI_KEY_1=value` lines at once, and skips keys already present in Niche Machine's vault or the process `GEMINI_*` environment. On Windows, key secrets are saved in Windows Credential Manager for the current Windows user; only non-secret labels and ids are kept in the app profile. Select one or more keys per scan, or use Select all, to split candidate reviews evenly across those projects; the dashboard shows each key's approximate share. Each selected key gets one balanced candidate chunk; oversized chunks are split into safe requests on that same key. An expandable request-history table shows per-key request counts and recent request status, model, candidate count, and duration; it stores no key values. The same selected key set is required to resume a checkpoint. A 429 stops the job and checkpoints completed chunks; the app does not switch keys after a quota error. Additional keys in one Google project do not increase that project's quota. Add only keys for projects you control. Paid API usage can incur charges; this early build does not enforce a dollar spending cap.

The dashboard accepts local video paths, drag-and-drop uploads, and public video/replay/live-stream URLs from YouTube, TikTok, Twitch and Kick. Stream URLs are retrieved through yt-dlp; live broadcasts are recorded until they end and then analyzed. Local files uploaded through the drop area are streamed to disk (up to 50 GB) instead of being held in memory. Download availability depends on each platform, the source privacy/settings, and yt-dlp extractor support. This does not connect social accounts or publish clips.
