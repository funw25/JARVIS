<# Start the pinned Ollama CLI as a loopback-only, cloud-disabled local process. #>
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$base = Join-Path $repo '.jarvis-ollama'
$ollama = Join-Path $base 'runtime-v0.40.15\ollama.exe'
$models = Join-Path $base 'models'
if (-not (Test-Path -LiteralPath $ollama)) { throw "Ollama runtime missing: $ollama" }
New-Item -ItemType Directory -Path $models -Force | Out-Null

$existing = Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue
if ($existing) { throw 'port 11434 already has a listener; inspect it before starting Jarvis Ollama' }

$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_MODELS = $models
$env:HTTPS_PROXY = 'http://127.0.0.1:7890'
$env:NO_PROXY = '127.0.0.1,localhost'

$process = Start-Process -FilePath $ollama -ArgumentList 'serve' -WorkingDirectory $base `
    -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $base 'server.stdout.log') `
    -RedirectStandardError (Join-Path $base 'server.stderr.log')
$process.Id | Set-Content -LiteralPath (Join-Path $base 'server.pid') -Encoding ASCII
Start-Sleep -Seconds 1
if ($process.HasExited) {
    $recent = Get-Content -LiteralPath (Join-Path $base 'server.stderr.log') -Tail 5 -ErrorAction SilentlyContinue
    throw "Ollama exited immediately. Recent error: $($recent -join ' | ')"
}
Write-Host "OLLAMA_PROCESS_SPAWNED pid=$($process.Id) host=$env:OLLAMA_HOST cloud_disabled=$env:OLLAMA_NO_CLOUD"
