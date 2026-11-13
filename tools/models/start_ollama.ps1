<# Start Ollama with its entire per-user state isolated under the project. #>
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$base = Join-Path $repo '.jarvis-ollama'
$python = Join-Path $repo '.jarvis-python-3.14.10\python.exe'
$launcher = Join-Path $repo 'tools\models\ollama_in_project.py'
if (-not (Test-Path -LiteralPath $python)) { throw "Python runtime missing: $python" }
if (-not (Test-Path -LiteralPath $launcher)) { throw "Ollama launcher missing: $launcher" }

$existing = Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue
if ($existing) { throw 'port 11434 already has a listener; inspect it before starting Jarvis Ollama' }

$process = Start-Process -FilePath $python -ArgumentList @($launcher, 'serve') -WorkingDirectory $repo `
    -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $base 'server.stdout.log') `
    -RedirectStandardError (Join-Path $base 'server.stderr.log')
$process.Id | Set-Content -LiteralPath (Join-Path $base 'server.pid') -Encoding ASCII
Start-Sleep -Seconds 1
if ($process.HasExited) {
    $recent = Get-Content -LiteralPath (Join-Path $base 'server.stderr.log') -Tail 5 -ErrorAction SilentlyContinue
    throw "Ollama exited immediately. Recent error: $($recent -join ' | ')"
}
Write-Host "OLLAMA_PROCESS_SPAWNED pid=$($process.Id) host=127.0.0.1:11434 cloud_disabled=1 profile=project"
