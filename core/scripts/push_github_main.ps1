<#
.SYNOPSIS
    AI_ASSISTANT_CORE — Vérification et push GitHub de main.

.DESCRIPTION
    Vérifie l'état Git local, lance pytest, affiche les commits en attente,
    et propose (ou exécute avec -Apply) git push origin main.

    Dry-run par défaut. Aucun push sans -Apply explicite.

.PARAMETER Apply
    Si présent, exécute réellement git push origin main.
    Sinon, affiche seulement la commande proposée.

.EXAMPLE
    .\scripts\push_github_main.ps1
    # Dry-run : vérifie l'état, lance pytest, affiche ce qui serait poussé.

.EXAMPLE
    .\scripts\push_github_main.ps1 -Apply
    # Même chose, puis exécute git push origin main.
#>

[CmdletBinding()]
param(
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Write-Host "=========================================================="
Write-Host "AI_ASSISTANT_CORE — push_github_main.ps1"
Write-Host "=========================================================="
Write-Host "Mode : $(if ($Apply) { 'APPLY (push réel)' } else { 'DRY-RUN' })"
Write-Host "----------------------------------------------------------"

# -- Résolution de la racine repo ------------------------------------------
# Le script vit dans scripts/ : un niveau au-dessus = racine repo.

$ScriptDir = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($ScriptDir)) {
    Write-Host "ERREUR : PSScriptRoot vide. Lancez depuis un fichier .ps1, pas en copier-coller." -ForegroundColor Red
    exit 2
}

$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $ScriptDir "..") -ErrorAction Stop).Path

$RepoMarkers = @("openai_compat.py", "app", "docker-compose.yml")
$missingMarkers = @($RepoMarkers | Where-Object { -not (Test-Path (Join-Path $RepoRoot $_)) })
if ($missingMarkers.Count -gt 0) {
    Write-Host "ERREUR : racine repo invalide : $RepoRoot" -ForegroundColor Red
    Write-Host "Marqueurs manquants : $($missingMarkers -join ', ')" -ForegroundColor Red
    exit 2
}

Write-Host "RepoRoot : $RepoRoot"
Write-Host "----------------------------------------------------------"

Push-Location $RepoRoot

try {

    # -- 1. Branche courante -----------------------------------------------

    $currentBranch = (& git branch --show-current 2>&1).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($currentBranch)) {
        Write-Host "ERREUR : impossible de lire la branche courante." -ForegroundColor Red
        exit 2
    }
    if ($currentBranch -ne "main") {
        Write-Host "ERREUR : branche courante = '$currentBranch', attendu 'main'." -ForegroundColor Red
        Write-Host "Basculez sur main avant de pousser." -ForegroundColor Red
        exit 2
    }
    Write-Host "Branche : main [OK]"

    # -- 2. État working tree ----------------------------------------------

    $statusLines = @(& git status --short 2>&1)
    $modifiedTracked = @($statusLines | Where-Object { $_ -match '^\s*[MADRC]' })

    if ($modifiedTracked.Count -gt 0) {
        Write-Host ""
        Write-Host "ERREUR : fichiers trackés modifiés :" -ForegroundColor Red
        $modifiedTracked | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
        Write-Host "Committez ou nettoyez avant de pousser." -ForegroundColor Red
        exit 2
    }

    $untrackedLines = @($statusLines | Where-Object { $_ -match '^\?\?' })
    if ($untrackedLines.Count -gt 0) {
        Write-Host "Untracked (non bloquants) :"
        $untrackedLines | ForEach-Object { Write-Host "  $_" }
    }
    Write-Host "Working tree : propre [OK]"

    # -- 3. Commits en attente ---------------------------------------------

    $ahead = @(& git log --oneline origin/main..HEAD 2>&1)
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERREUR : impossible de lire les commits ahead. origin/main est-il connu ?" -ForegroundColor Red
        exit 2
    }

    if ($ahead.Count -eq 0) {
        Write-Host ""
        Write-Host "Aucun commit en avance sur origin/main. Rien à pousser." -ForegroundColor Yellow
        exit 0
    }

    Write-Host ""
    Write-Host "$($ahead.Count) commit(s) à pousser vers origin/main :"
    $ahead | ForEach-Object { Write-Host "  $_" }
    Write-Host ""

    # -- 4. Pytest ---------------------------------------------------------

    Write-Host "Lancement de pytest tests/ ..."
    $pytestOutput = @(& python -m pytest tests/ -q 2>&1)
    $pytestExit = $LASTEXITCODE

    $pytestOutput | ForEach-Object { Write-Host "  $_" }

    if ($pytestExit -ne 0) {
        Write-Host ""
        Write-Host "ERREUR : pytest a échoué (exit=$pytestExit). Push annulé." -ForegroundColor Red
        exit 2
    }
    Write-Host "Pytest : OK [OK]"
    Write-Host "----------------------------------------------------------"

    # -- 5. Push -----------------------------------------------------------

    if (-not $Apply) {
        Write-Host ""
        Write-Host "DRY-RUN — Commande proposée :"
        Write-Host "  git push origin main"
        Write-Host ""
        Write-Host "Relancez avec -Apply pour exécuter."
    }
    else {
        Write-Host ""
        Write-Host "Exécution : git push origin main ..."
        $pushOutput = @(& git push origin main 2>&1)
        $pushExit = $LASTEXITCODE
        $pushOutput | ForEach-Object { Write-Host "  $_" }

        if ($pushExit -ne 0) {
            Write-Host ""
            Write-Host "ERREUR : git push a échoué (exit=$pushExit)." -ForegroundColor Red
            exit 1
        }
        Write-Host ""
        Write-Host "Push OK." -ForegroundColor Green
    }

}
finally {
    Pop-Location
}

Write-Host "=========================================================="
exit 0
