$ErrorActionPreference = 'Stop'
$projectDir = Split-Path $PSScriptRoot -Parent
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'start_local.ps1') -PrepareOnly
if ($LASTEXITCODE -ne 0) { throw 'Ollama start failed.' }
$env:OLLAMA_HOST = '127.0.0.1:11434'
& (Join-Path $projectDir 'runtime/ollama/ollama.exe') pull qwen3:4b
if ($LASTEXITCODE -ne 0) { throw 'Model download failed; run again to resume.' }
