<#
.SYNOPSIS
    Prove the M1 environment can be rebuilt on a clean machine, offline.

.DESCRIPTION
    Simulates a fresh Windows host in a temporary directory:
      1. extract the pinned CPython embeddable zip (SHA-256 checked),
      2. write python314._pth with a private dependency directory,
      3. install the locked wheels with --no-index --require-hashes,
      4. run the focused M1 checks and the environment check with that interpreter.

    The repository's own .jarvis-python314-deps is never used. No account, no
    private data and no network access is required once the zip and the
    wheelhouse exist.
#>
param(
    [string]$ZipPath,
    [string]$Wheelhouse,
    [string]$WorkDir
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $ZipPath) { $ZipPath = Join-Path $env:TEMP 'python-3.14.10-embed-amd64.zip' }
if (-not $Wheelhouse) { $Wheelhouse = Join-Path $repo '.jarvis-wheelhouse' }
if (-not $WorkDir) { $WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) ('jarvis-cleanroom-' + [guid]::NewGuid().ToString('N').Substring(0, 8)) }

$expectedHash = '90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159'
$lock = Join-Path $repo 'tools\env\requirements-m1.lock'
$pipPython = Join-Path $repo '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $ZipPath)) { throw "interpreter zip missing: $ZipPath" }
$actual = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $expectedHash) { throw "interpreter hash mismatch: $actual" }
if (-not (Test-Path -LiteralPath $Wheelhouse)) { throw "wheelhouse missing: $Wheelhouse" }
if (-not (Test-Path -LiteralPath $pipPython)) { throw "need a pip-capable interpreter to unpack wheels: $pipPython" }

New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null
$py = Join-Path $WorkDir 'py'
$deps = Join-Path $WorkDir 'deps'
New-Item -ItemType Directory -Path $py, $deps -Force | Out-Null

Write-Host "== clean room: $WorkDir"
Expand-Archive -LiteralPath $ZipPath -DestinationPath $py -Force
@('python314.zip', '.', '', '# Uncomment to run site.main() automatically', '#import site', '..\deps') |
    Set-Content -LiteralPath (Join-Path $py 'python314._pth') -Encoding ASCII

Write-Host "== installing locked wheels offline"
& $pipPython -m pip install --disable-pip-version-check --no-cache-dir --no-index `
    --find-links $Wheelhouse --require-hashes --target $deps `
    --only-binary=:all: --platform win_amd64 --implementation cp --python-version 3.14 --abi cp314 `
    -r $lock
if ($LASTEXITCODE -ne 0) { throw "offline install failed" }

$python = Join-Path $py 'python.exe'
Write-Host "== verifying environment"
& $python (Join-Path $repo 'backend\verification\verify_env_lock.py') --deps $deps
if ($LASTEXITCODE -ne 0) { throw "environment verification failed" }

Write-Host "== running the focused M1 checks"
& $python (Join-Path $repo 'backend\verification\verify_m1.py')
if ($LASTEXITCODE -ne 0) { throw "M1 checks failed in the clean room" }

Write-Host "CLEAN_ROOM_REBUILD=PASS interpreter=$python deps=$deps"
Write-Host "note: the work directory is kept for inspection; delete it when done"
