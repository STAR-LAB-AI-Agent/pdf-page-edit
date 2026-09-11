param([switch]$PrepareOnly)
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path $PSScriptRoot -Parent
$runtimeDir = Join-Path $projectDir 'runtime'
$ollamaExe = Join-Path $runtimeDir 'ollama/ollama.exe'
if (-not (Test-Path -LiteralPath $ollamaExe)) { throw 'Ollama not installed in runtime/ollama yet.' }
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_MODELS = Join-Path $runtimeDir 'models'
$env:OLLAMA_CONTEXT_LENGTH = '8192'
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_KEEP_ALIVE = '5m'
$env:OLLAMA_NO_CLOUD = '1'
$env:NO_PROXY = '127.0.0.1,localhost'
try { $null = Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 }
catch {
    Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WorkingDirectory $runtimeDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDir 'ollama.stdout.log') -RedirectStandardError (Join-Path $runtimeDir 'ollama.stderr.log') | Out-Null
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Seconds 1
        try { $null = Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 1; $ready = $true; break } catch {}
    }
    if (-not $ready) { throw 'Ollama did not start. Check runtime/ollama.stderr.log.' }
}
if ($PrepareOnly) { Write-Output 'Ollama ready on 127.0.0.1:11434'; exit 0 }
$models = Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5
if (-not ($models.models | Where-Object name -eq 'qwen3:4b')) { throw 'qwen3:4b is missing. Run the model preparation script first.' }
$pythonExe = Join-Path $projectDir '.venv-nanobot/Scripts/python.exe'
Set-Location -LiteralPath $projectDir
& $pythonExe -X utf8 (Join-Path $projectDir 'nanobot_chat.py')
exit $LASTEXITCODE
