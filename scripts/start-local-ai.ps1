param(
    [switch]$CpuOnly,
    [switch]$SkipModelPull
)

$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $taskRoot
try {
    if (-not (Test-Path -LiteralPath '.env')) {
        Copy-Item -LiteralPath '.env.example' -Destination '.env'
    }
    $composeArgs = @('compose', '-f', 'docker-compose.yml', '-f', 'compose.ollama.yml')
    if (-not $CpuOnly) {
        $composeArgs += @('-f', 'compose.ollama-gpu.yml')
    }
    & docker @composeArgs config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Compose configuration failed.' }
    & docker @composeArgs up -d --wait --wait-timeout 120 ollama
    if ($LASTEXITCODE -ne 0) { throw 'Ollama startup failed. Check Docker Desktop/GPU access, or use -CpuOnly.' }
    if (-not $SkipModelPull) {
        & docker @composeArgs exec -T ollama ollama pull qwen3:4b
        if ($LASTEXITCODE -ne 0) { throw 'The local model download failed.' }
    }
    & docker @composeArgs up --build -d web worker
    if ($LASTEXITCODE -ne 0) { throw 'Rahasya startup failed.' }
    & docker @composeArgs exec -T worker python -m rahasya brain-check --probe
    if ($LASTEXITCODE -ne 0) { throw 'Local model check failed. Check model assignments in .env and Ollama logs.' }
    Write-Output 'Local AI is ready. Open http://localhost:8501 and select Use local AI agents.'
} finally {
    Pop-Location
}
