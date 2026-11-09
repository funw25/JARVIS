<#
.SYNOPSIS
    Rebuild the M1 offline Python environment on a clean Windows machine.

.DESCRIPTION
    Steps:
      1. Verify (or obtain) the official CPython 3.14.10 embeddable zip and check
         its SHA-256.
      2. Extract it to the target directory.
      3. Rewrite python314._pth so the dependency directory is importable.
      4. Install the locked dependency set from the local wheelhouse with
         --require-hashes and --no-index (no network).
      5. Run verify_env_lock.py with the new interpreter.

    No account, no private data and no network access is needed when -ZipPath
    and -Wheelhouse already exist.

.PARAMETER Target
    Directory that will hold the interpreter (default: <repo>\.jarvis-python-3.14.10).

.PARAMETER ZipPath
    Path to python-3.14.10-embed-amd64.zip. Downloaded and hash-checked when missing.

.PARAMETER Wheelhouse
    Directory containing the locked wheels (default: <repo>\.jarvis-wheelhouse).

.PARAMETER DepsDir
    Dependency directory (default: <repo>\.jarvis-python314-deps).
#>
param(
    [string]$Target,
    [string]$ZipPath,
    [string]$Wheelhouse,
    [string]$DepsDir,
    [string]$Python313
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $Target) { $Target = Join-Path $repo '.jarvis-python-3.14.10' }
if (-not $Wheelhouse) { $Wheelhouse = Join-Path $repo '.jarvis-wheelhouse' }
if (-not $DepsDir) { $DepsDir = Join-Path $repo '.jarvis-python314-deps' }
if (-not $ZipPath) { $ZipPath = Join-Path $env:TEMP 'python-3.14.10-embed-amd64.zip' }
if (-not $Python313) { $Python313 = Join-Path $repo '.venv\Scripts\python.exe' }

$expectedHash = '90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159'
$url = 'https://www.python.org/ftp/python/3.14.10/python-3.14.10-embed-amd64.zip'
$lock = Join-Path $repo 'tools\env\requirements-m1.lock'

if (-not (Test-Path -LiteralPath $ZipPath)) {
    Write-Host "downloading $url"
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $ZipPath
}
$actual = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $expectedHash) {
    throw "interpreter zip hash mismatch: expected $expectedHash, got $actual"
}
Write-Host "interpreter zip verified: $actual"

if (Test-Path -LiteralPath $Target) {
    Write-Host "target exists, removing: $Target"
    Remove-Item -LiteralPath $Target -Recurse -Force
}
Expand-Archive -LiteralPath $ZipPath -DestinationPath $Target
$pth = Join-Path $Target 'python314._pth'
$relative = '..\' + (Split-Path -Leaf $DepsDir)
@('python314.zip', '.', '', '# Uncomment to run site.main() automatically', '#import site', $relative) |
    Set-Content -LiteralPath $pth -Encoding ASCII
Write-Host "._pth written with dependency entry: $relative"

if (-not (Test-Path -LiteralPath $Wheelhouse)) { throw "wheelhouse missing: $Wheelhouse" }
if (-not (Test-Path -LiteralPath $Python313)) { throw "a Python with pip is required to install wheels offline: $Python313" }

if (Test-Path -LiteralPath $DepsDir) {
    Write-Host "clearing dependency directory: $DepsDir"
    Remove-Item -LiteralPath $DepsDir -Recurse -Force
}
New-Item -ItemType Directory -Path $DepsDir -Force | Out-Null
& $Python313 -m pip install --disable-pip-version-check --no-cache-dir --no-index `
    --find-links $Wheelhouse --require-hashes --target $DepsDir -r $lock
if ($LASTEXITCODE -ne 0) { throw "offline wheel install failed" }
Write-Host "wheels installed from $Wheelhouse"

$python = Join-Path $Target 'python.exe'
& $python (Join-Path $repo 'backend\verification\verify_env_lock.py')
if ($LASTEXITCODE -ne 0) { throw "environment verification failed" }
Write-Host "M1 environment ready: $Target"
