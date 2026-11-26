# OpenCode Niche Machine integration

This folder contains the OpenCode **video-review / clip-council** skill. It is intentionally separate from the repository-root `SKILL.md`, which documents the Niche Machine creator app and its seven Shorts workflows. Keep those products' instructions in their own files; do not replace or merge the root skill with this integration.

## Install

Copy `skills/niche-machine/` into OpenCode's skills directory as `niche-machine/`. The skill's companion GTA 6 rules are under `skills/niche-machine/references/`.

## Publish updates from the active OpenCode installation

Use `scripts/publish-opencode-skill.ps1` from the repository root. It copies the active skill and companion reference into this scoped integration, checks the staged file list, refuses unrelated checkout changes or a branch behind/diverged from `origin/main`, and pushes without force. Authentication comes from the host's Git Credential Manager login; never paste or store GitHub tokens in files, remotes, or chat.
