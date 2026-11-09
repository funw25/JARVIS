<# Run the pinned Ollama CLI in the current process for restricted-runner diagnosis. #>
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$base = Join-Path $repo '.jarvis-ollama'
$ollama = Join-Path $base 'runtime-v0.40.15\ollama.exe'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_MODELS = Join-Path $base 'models'
$env:HTTPS_PROXY = 'http://127.0.0.1:7890'
$env:NO_PROXY = '127.0.0.1,localhost'
& $ollama serve
exit $LASTEXITCODE
