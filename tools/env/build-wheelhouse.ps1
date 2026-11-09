<#
.SYNOPSIS
    Rebuild the offline wheelhouse from tools/env/requirements-m1.lock.

.DESCRIPTION
    The lock file is the source of truth. This script downloads exactly the
    pinned versions and checks every wheel against the recorded SHA-256.
    Network access is required here; bootstrap.ps1 and the clean-room test then
    work fully offline.

.PARAMETER Wheelhouse
    Destination directory (default: <repo>\.jarvis-wheelhouse).

.PARAMETER Python313
    A Python with pip used only to fetch wheels (default: <repo>\.venv\Scripts\python.exe).
#>
param(
    [string]$Wheelhouse,
    [string]$Python313
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $Wheelhouse) { $Wheelhouse = Join-Path $repo '.jarvis-wheelhouse' }
if (-not $Python313) { $Python313 = Join-Path $repo '.venv\Scripts\python.exe' }
$lock = Join-Path $repo 'tools\env\requirements-m1.lock'
if (-not (Test-Path -LiteralPath $lock)) { throw "lock file missing: $lock" }
if (-not (Test-Path -LiteralPath $Python313)) { throw "pip-capable interpreter missing: $Python313" }

$pinned = @{}
$download = Join-Path $env:TEMP ('m1-download-' + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.txt')
$lines = Get-Content -LiteralPath $lock | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') }
foreach ($line in $lines) {
    if ($line -notmatch '^([A-Za-z0-9_.\-]+)==([^\s]+)\s+--hash=sha256:([0-9a-f]{64})') {
        throw "unparsable lock line: $line"
    }
    $name = $Matches[1]
    $version = $Matches[2]
    $hash = $Matches[3]
    $pinned[$name.ToLower()] = @{ Version = $version; Hash = $hash }
    Add-Content -LiteralPath $download -Value ("{0}=={1}" -f $name, $version) -Encoding ASCII
}
Write-Host "locked packages: $($pinned.Count)"

New-Item -ItemType Directory -Path $Wheelhouse -Force | Out-Null
& $Python313 -m pip download --disable-pip-version-check --no-cache-dir --dest $Wheelhouse `
    --only-binary=:all: --platform win_amd64 --implementation cp --python-version 3.14 --abi cp314 `
    -r $download
if ($LASTEXITCODE -ne 0) { throw "wheel download failed" }
Remove-Item -LiteralPath $download -Force -ErrorAction SilentlyContinue

$wheels = @(Get-ChildItem -LiteralPath $Wheelhouse -Filter '*.whl')
$problems = New-Object System.Collections.Generic.List[string]
$present = @{}
foreach ($wheel in $wheels) {
    $parts = $wheel.BaseName -split '-'
    $name = ($parts[0] -replace '_', '-').ToLower()
    if (-not $pinned.ContainsKey($name)) {
        $problems.Add("unexpected wheel: $($wheel.Name)")
        continue
    }
    $actual = (Get-FileHash -LiteralPath $wheel.FullName -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $pinned[$name].Hash) {
        $problems.Add("hash mismatch: $($wheel.Name)")
    }
    $present[$name] = $true
}
foreach ($name in $pinned.Keys) {
    if (-not $present.ContainsKey($name)) { $problems.Add("missing wheel: $name") }
}

if ($problems.Count -gt 0) {
    $problems | ForEach-Object { Write-Host "PROBLEM $_" }
    throw "wheelhouse does not match the lock"
}
Write-Host "WHEELHOUSE_REBUILT=PASS wheels=$($wheels.Count)"
