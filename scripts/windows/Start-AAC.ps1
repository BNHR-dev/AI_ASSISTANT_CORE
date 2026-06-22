# Start-AAC.ps1 - Demarre tous les services AAC puis le backend.
# Usage : double-clic sur Start-AAC.bat

$ErrorActionPreference = 'SilentlyContinue'

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$CoreDir  = Join-Path $RepoRoot 'core'
$VenvPy   = Join-Path $CoreDir '.venv\Scripts\python.exe'

Write-Host ''
Write-Host '  +-======================================-+' -ForegroundColor White
Write-Host '  |   AAC - Demarrage                    |' -ForegroundColor White
Write-Host '  +-======================================-+' -ForegroundColor White
Write-Host ''

# -- Verification bootstrap ----------------------------------------------------
if (-not (Test-Path $VenvPy)) {
    Write-Host '  [!] Installation incomplete - lancer Install-AAC.bat en premier.' -ForegroundColor Red
    Read-Host -Prompt '  Appuyez sur Entree pour fermer'
    exit 1
}

# -- Ollama --------------------------------------------------------------------
$ollamaUp = $false
try {
    Invoke-WebRequest 'http://127.0.0.1:11434/api/tags' -UseBasicParsing -TimeoutSec 2 | Out-Null
    $ollamaUp = $true
} catch {}

if ($ollamaUp) {
    Write-Host '  [OK]  Ollama  ->  http://127.0.0.1:11434' -ForegroundColor Green
} else {
    $ollamaExe = $null
    $cands = @(
        "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
        (Get-Command ollama -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
    )
    $ollamaExe = $cands | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

    if ($ollamaExe) {
        Write-Host '  [->]   Demarrage Ollama...' -ForegroundColor Gray
        Start-Process $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden
        for ($i = 0; $i -lt 15 -and -not $ollamaUp; $i++) {
            Start-Sleep 1
            try {
                Invoke-WebRequest 'http://127.0.0.1:11434/api/tags' -UseBasicParsing -TimeoutSec 2 | Out-Null
                $ollamaUp = $true
            } catch {}
        }
        if ($ollamaUp) { Write-Host '  [OK]  Ollama  ->  http://127.0.0.1:11434' -ForegroundColor Green }
        else           { Write-Host '  [~]   Ollama injoignable apres 15s (le backend demarrera quand meme)' -ForegroundColor Yellow }
    } else {
        Write-Host '  [~]   Ollama introuvable - relancer Install-AAC.bat' -ForegroundColor Yellow
    }
}

# -- SearXNG -------------------------------------------------------------------
try {
    $running = docker ps --filter 'name=searxng' --format '{{.Names}}' 2>$null |
               Select-String 'searxng' -Quiet
    if ($running) {
        Write-Host '  [OK]  SearXNG  ->  http://127.0.0.1:8081' -ForegroundColor Green
    } else {
        $exists = docker ps -a --filter 'name=searxng' --format '{{.Names}}' 2>$null |
                  Select-String 'searxng' -Quiet
        if ($exists) {
            docker start searxng 2>$null | Out-Null
            Write-Host '  [->]   SearXNG redemarre  ->  http://127.0.0.1:8081' -ForegroundColor Gray
        } else {
            Write-Host '  [~]   SearXNG absent - recherche web desactivee' -ForegroundColor Yellow
        }
    }
} catch {
    Write-Host '  [~]   Docker non disponible - SearXNG ignore' -ForegroundColor Yellow
}

# -- Backend Python ------------------------------------------------------------
Write-Host ''
Write-Host '  [->]   Backend AAC sur http://127.0.0.1:8000' -ForegroundColor Cyan
Write-Host ''

Push-Location $CoreDir
try {
    & $VenvPy -m uvicorn app.main:app --host 127.0.0.1 --port 8000
} finally {
    Pop-Location
}
