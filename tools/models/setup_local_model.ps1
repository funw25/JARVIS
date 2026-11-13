<#
Run this in an ordinary Windows PowerShell session when the Codex runner cannot
write Ollama's per-user configuration directory. The official CLI was already
downloaded and hash-checked by bootstrap_ollama.py. No sign-in is performed.
#>
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$base = Join-Path $repo '.jarvis-ollama'
$ollama = Join-Path $base 'runtime-v0.40.15\ollama.exe'
$python = Join-Path $repo '.jarvis-python-3.14.10\python.exe'
$launcher = Join-Path $repo 'tools\models\ollama_in_project.py'
$model = 'qwen3:4b-instruct-2507-q4_K_M'
if (-not (Test-Path -LiteralPath $ollama)) { throw "verified Ollama CLI missing: $ollama" }
if (-not (Test-Path -LiteralPath $python)) { throw "Python runtime missing: $python" }

& (Join-Path $PSScriptRoot 'start_ollama.ps1')

$ready = $false
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Seconds 1
    try {
        $version = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 2
        if ($version.version) { $ready = $true; break }
    } catch { }
}
if (-not $ready) {
    $recent = Get-Content -LiteralPath (Join-Path $base 'server.stderr.log') -Tail 5 -ErrorAction SilentlyContinue
    throw "Ollama loopback API did not become ready. Recent error: $($recent -join ' | ')"
}

$serverLog = Join-Path $base 'server.stderr.log'
$cloudOff = Select-String -LiteralPath $serverLog -Pattern 'Ollama cloud disabled:\s*true' -Quiet -ErrorAction SilentlyContinue
if (-not $cloudOff) { throw 'could not confirm Ollama cloud is disabled in the server log' }

Write-Host "OLLAMA_LOCAL_API=PASS version=$($version.version)"
Write-Host "Downloading local model $model (about 2.5 GB); no Ollama sign-in is used."
& $python $launcher pull $model
if ($LASTEXITCODE -ne 0) { throw "model pull failed: $model" }

$tags = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 10
$found = $tags.models | Where-Object { $_.name -eq $model } | Select-Object -First 1
if (-not $found -or -not $found.digest -or [long]$found.size -le 0) {
    throw 'downloaded local model was not found with a digest and nonzero size'
}
Write-Host "LOCAL_MODEL_READY=PASS model=$model digest=$($found.digest) size_bytes=$($found.size)"
