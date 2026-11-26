[CmdletBinding()]
param(
    [string]$ActiveSkillPath = (Join-Path $HOME '.config/opencode/skills/niche-machine'),
    [string]$RepositoryPath = (Split-Path -Parent $PSScriptRoot),
    [switch]$NoPush
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath $RepositoryPath).Path
$source = (Resolve-Path -LiteralPath $ActiveSkillPath).Path
$skillFile = Join-Path $source 'SKILL.md'
$referenceFile = Join-Path $source 'references/gta6-clip-grammar.md'
$uiFile = Join-Path $source 'ui/command-builder.html'
$target = Join-Path $repo 'integrations/opencode/skills/niche-machine'
$targetRef = Join-Path $target 'references/gta6-clip-grammar.md'
$targetUi = Join-Path $target 'ui/command-builder.html'

foreach ($required in @($skillFile, $referenceFile, $uiFile)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required active skill file is missing: $required"
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $repo '.git'))) {
    throw "Not a Git repository: $repo"
}
if (-not (Test-Path -LiteralPath (Join-Path $repo 'SKILL.md'))) {
    throw "Expected the separate root product SKILL.md; refusing to publish into an unexpected repository layout."
}

# Never silently include or discard unrelated edits in the publishing checkout.
$status = @(& git -C $repo status --porcelain)
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect repository working-tree status.' }
$allowedWorkingTreeEntries = @(
    '?? integrations/',
    '?? integrations/opencode/skills/niche-machine/ui/',
    '?? integrations/opencode/skills/niche-machine/ui/command-builder.html',
    ' M integrations/opencode/README.md',
    ' M scripts/publish-opencode-skill.ps1',
    '?? scripts/publish-opencode-skill.ps1'
)
$unsafe = @($status | Where-Object { $_ -notin $allowedWorkingTreeEntries })
if ($unsafe.Count -gt 0) {
    throw "Repository has unrelated or out-of-scope changes; refusing to publish: $($unsafe -join '; ')"
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $targetRef) | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $targetUi) | Out-Null
Copy-Item -LiteralPath $skillFile -Destination (Join-Path $target 'SKILL.md') -Force
Copy-Item -LiteralPath $referenceFile -Destination $targetRef -Force
Copy-Item -LiteralPath $uiFile -Destination $targetUi -Force

& git -C $repo add -- integrations/opencode/README.md integrations/opencode/skills/niche-machine/SKILL.md integrations/opencode/skills/niche-machine/references/gta6-clip-grammar.md integrations/opencode/skills/niche-machine/ui/command-builder.html scripts/publish-opencode-skill.ps1
if ($LASTEXITCODE -ne 0) { throw 'Could not stage scoped OpenCode integration files.' }
$staged = @(& git -C $repo diff --cached --name-only)
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect staged changes.' }
$allowed = @(
    'integrations/opencode/README.md',
    'integrations/opencode/skills/niche-machine/SKILL.md',
    'integrations/opencode/skills/niche-machine/references/gta6-clip-grammar.md',
    'integrations/opencode/skills/niche-machine/ui/command-builder.html',
    'scripts/publish-opencode-skill.ps1'
)
$outside = @($staged | Where-Object { $_ -notin $allowed })
if ($outside.Count -gt 0) {
    throw "Refusing to commit files outside the OpenCode integration: $($outside -join ', ')"
}
if ($staged.Count -eq 0) {
    Write-Output 'OpenCode integration is already synchronized; no commit needed.'
    exit 0
}

if (-not $NoPush) {
    $branch = (& git -C $repo branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -ne 'main') {
        throw "Expected the clean publishing checkout on main; got '$branch'."
    }
    # Refuse a non-fast-forward update. No force-pushes and no blind merges.
    & git -C $repo fetch --no-tags origin main
    if ($LASTEXITCODE -ne 0) { throw 'Fetch failed; no commit was published.' }
    & git -C $repo merge-base --is-ancestor origin/main HEAD
    if ($LASTEXITCODE -ne 0) {
        throw 'Local main is behind or diverged from origin/main. Sync/review manually before publishing; force-push is disabled.'
    }
}

& git -C $repo diff --cached --check
if ($LASTEXITCODE -ne 0) { throw 'Whitespace/conflict-marker check failed.' }
& git -C $repo -c user.name='Niche Machine Publisher' -c user.email='noreply@users.noreply.github.com' commit -m 'Update OpenCode video-review integration'
if ($LASTEXITCODE -ne 0) { throw 'Commit failed; changes remain staged for inspection.' }

if ($NoPush) {
    Write-Output 'Committed the OpenCode integration locally (--NoPush); no remote was changed.'
    exit 0
}

& git -C $repo push origin main
if ($LASTEXITCODE -ne 0) { throw 'Push failed. The local commit is preserved; retry after fixing the GitHub authentication or branch issue.' }
Write-Output 'Published the OpenCode video-review integration to origin/main. The root product SKILL.md was not changed.'
