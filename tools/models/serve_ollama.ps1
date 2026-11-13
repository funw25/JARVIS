<# Run Ollama with a project-local profile in the current process. #>
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $repo '.jarvis-python-3.14.10\python.exe'
& $python (Join-Path $repo 'tools\models\ollama_in_project.py') serve
exit $LASTEXITCODE
