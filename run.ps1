#requires -Version 5.1
# run.ps1 -- AAC in ONE command (Windows). Mirror of run.sh. SECURE path by default.
#
# ASCII-ONLY ON PURPOSE: a BOM-less .ps1 is decoded by Windows PowerShell 5.1 with the
# system ANSI code page (Windows-1252), NOT UTF-8. Any non-ASCII byte (accent, em-dash,
# smart quote) then mis-tokenizes and breaks parsing (the classic em-dash 0x94 -> U+201D
# "TerminatorExpectedAtEndOfString"). Keeping this file ASCII makes parsing independent
# of code page and BOM, on both Windows PowerShell 5.1 and PowerShell 7.
#
# Full Docker stack + hardened sandbox overlay, NVIDIA GPU auto-detected (Docker Desktop
# + WSL2), idempotent models, REAL health gate, opens the Console. No bash dependency:
# image models are fetched by scripts/windows/Fetch-ComfyUIModels.ps1, LLM models are
# pulled inside the ollama container via `docker compose exec`.
#
#   run.bat / run.ps1            start, open /console
#   run.bat --down               stop the stack
#   run.bat --logs               follow the logs
#   run.bat --no-open            do not open the browser
#   run.bat --no-models          DEGRADED: skip model (down)load
#   run.bat --no-build           do not rebuild images
#   run.bat --force-download     re-download image models even if present
#   run.bat --no-docker-start    do not auto-start Docker Desktop if it is stopped
#
# Long (--xxx) and PowerShell (-Xxx) option styles are both accepted.

$ErrorActionPreference = "Stop"

# --- Console encoding: deterministic UTF-8 output (belt-and-suspenders; messages are
#     ASCII, but docker/compose can emit UTF-8). Never fatal if the host refuses it.
try {
  [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
  $OutputEncoding           = New-Object System.Text.UTF8Encoding($false)
} catch { }

# =======================================================================================
# FUNCTIONS (defined first so tests can dot-source this file with AAC_RUN_PS1_NOEXEC=1
# and exercise them in isolation, without running the installer).
# =======================================================================================

function Die($m)  { throw [System.Exception]::new($m) }
function Log($m)  { Write-Host ""; Write-Host "== $m ==" -ForegroundColor Cyan }
function Info($m) { Write-Host "   $m" -ForegroundColor DarkGray }
function Warn($m) { Write-Host "[!] $m" -ForegroundColor Yellow }

# Native-command stderr must NOT bubble up as a NativeCommandError under EAP=Stop. We
# isolate EAP=Continue around every docker call and decide solely on $LASTEXITCODE.
function Invoke-DockerQuiet([string[]]$DockerArgs) {
  $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  try { $null = & docker @DockerArgs 2>&1; return $LASTEXITCODE }
  finally { $ErrorActionPreference = $old }
}
function Get-DockerInfoText {
  $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  try { return (& docker info 2>&1 | Out-String) }
  finally { $ErrorActionPreference = $old }
}
function Test-DockerDaemon { return ((Invoke-DockerQuiet @("info")) -eq 0) }

function Find-DockerDesktop {
  $cands = @(
    (Join-Path ${env:ProgramFiles} "Docker\Docker\Docker Desktop.exe"),
    (Join-Path ${env:ProgramW6432} "Docker\Docker\Docker Desktop.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Docker\Docker\Docker Desktop.exe")
  )
  foreach ($c in $cands) { if ($c -and (Test-Path $c)) { return $c } }
  return $null
}

function Ensure-DockerDaemon {
  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Die "Docker CLI absente. Installez Docker Desktop (backend WSL2) : https://www.docker.com/products/docker-desktop"
  }
  if ((Invoke-DockerQuiet @("compose","version")) -ne 0) {
    Die "Plugin 'docker compose' v2 absent. Mettez Docker Desktop a jour."
  }
  if (Test-DockerDaemon) { Info "Docker daemon : actif"; return }

  # CLI present but daemon not responding -> distinguish 'stopped' and act.
  $dd = Find-DockerDesktop
  if (-not $dd) {
    Die "Le daemon Docker ne repond pas et Docker Desktop est introuvable. Demarrez le daemon Docker (ou installez Docker Desktop) puis relancez."
  }
  if ($NoDockerStart) {
    Die "Le daemon Docker ne repond pas. Demarrez Docker Desktop puis relancez (auto-demarrage desactive via --no-docker-start)."
  }
  $wait = 180
  Log "Docker Desktop detecte mais arrete -> demarrage et attente (timeout ${wait}s)"
  try { Start-Process -FilePath $dd | Out-Null }
  catch { Die "Echec du demarrage de Docker Desktop : $($_.Exception.Message). Ouvrez-le manuellement puis relancez." }
  $deadline = (Get-Date).AddSeconds($wait)
  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    if (Test-DockerDaemon) { Info "Docker daemon : pret"; return }
  }
  Die "Le daemon Docker n'est pas pret apres ${wait}s. Ouvrez Docker Desktop manuellement, attendez l'icone verte, puis relancez."
}

function Test-DockerHasNvidia {
  # Override explicite : AAC_GPU=1 force le GPU, AAC_GPU=0 force le CPU.
  if ($env:AAC_GPU -eq "1") { return $true }
  if ($env:AAC_GPU -eq "0") { return $false }
  # Detection PORTABLE. L'ancien `docker info -match nvidia` ne marche que sous Linux natif :
  # Docker Desktop/WSL2 expose le GPU SANS publier le runtime "nvidia" dans `docker info`
  # -> faux negatif -> ComfyUI retombait en CPU et le rendu 2D "final" calait (WSL2 GPU stall).
  # On SONDE donc reellement : un conteneur jetable lance AVEC --gpus all. S'il demarre, le GPU
  # est utilisable (vrai sous Linux natif ET WSL2). busybox est minuscule (~4 Mo, telecharge 1x).
  if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) { return $false }
  return ((Invoke-DockerQuiet @("run","--rm","--gpus","all","busybox","true")) -eq 0)
}

function Invoke-Child([string]$Script, [string[]]$ChildArgs) {
  # Run a child .ps1 in its own PowerShell so its `exit` does not kill run.ps1. The child
  # is a SEPARATE process: its stdout comes back as strings on this function's output
  # stream and would corrupt the return value (commingling text with the exit code). So
  # capture the output into a variable, echo it for the transcript, and return ONLY the
  # integer exit code.
  $out = & powershell -NoProfile -ExecutionPolicy Bypass -File $Script @ChildArgs 2>&1
  $code = $LASTEXITCODE
  if ($null -ne $out) { Write-Host ($out | Out-String).TrimEnd() }
  return $code
}

function Get-ComposeCid([string[]]$Compose, [string]$Service) {
  $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  try { return ((& docker @Compose ps -q $Service 2>$null | Out-String).Trim()) }
  finally { $ErrorActionPreference = $old }
}
function Get-ContainerHealth([string]$Id) {
  if (-not $Id) { return "absent" }
  $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  try { return ((& docker inspect --format '{{.State.Health.Status}}' $Id 2>$null | Out-String).Trim()) }
  finally { $ErrorActionPreference = $old }
}

# Model-name source of truth = scripts/models.manifest (shared with Linux and the
# Fetch-*.ps1 scripts). Parsed here only to verify, never re-typed by hand.
function Get-OllamaModelNames {
  $names = @()
  $manifest = Join-Path $RepoRoot "scripts\models.manifest"
  if (Test-Path $manifest) {
    foreach ($line in (Get-Content -LiteralPath $manifest)) {
      $t = $line.Trim()
      if (-not $t -or $t.StartsWith("#")) { continue }
      $f = $t.Split("|")
      if ($f.Count -ge 3 -and $f[0].Trim() -eq "ollama") { $names += $f[2].Trim() }
    }
  }
  if (-not $names) { $names = @("qwen3:8b","qwen2.5-coder:7b","qwen2.5vl:3b") }
  return $names
}

function Invoke-OllamaPull([string[]]$Compose) {
  $present = ""
  try { $present = (& docker @Compose exec -T ollama ollama list 2>$null | Out-String) } catch { }
  foreach ($m in (Get-OllamaModelNames)) {
    if ($present -match [regex]::Escape($m)) { Info "deja present : $m"; continue }
    Info "pull $m ..."
    & docker @Compose exec -T ollama ollama pull $m
    if ($LASTEXITCODE -ne 0) { Die "echec du pull du modele LLM '$m'." }
  }
}

# Query the backend runtime health and fail fast if a required service is not READY
# (reachable but missing models counts as NOT ready -> no false 'green').
function Assert-RuntimeReady {
  param([bool]$RequireComfy = $true)

  $url = "http://127.0.0.1:8000/health/runtime"
  $resp = $null
  try { $resp = Invoke-RestMethod -Uri $url -TimeoutSec 15 -UseBasicParsing }
  catch { Die "Impossible d'interroger $url : $($_.Exception.Message)" }

  $services = $resp.services
  if (-not $services) { Die "Reponse /health/runtime inattendue (pas de 'services')." }

  $ollama = $services.ollama
  if (-not $ollama.ready) { Die "Ollama n'est pas pret : $($ollama.reason)" }

  $comfy = $services.comfyui
  if ($comfy) {
    if (-not $comfy.reachable) {
      Die "ComfyUI injoignable : $($comfy.reason)"
    }
    if ($comfy.ready) {
      Info "ComfyUI : pret (modeles requis presents)"
    } elseif ($RequireComfy) {
      Die "ComfyUI joignable mais PAS pret (modeles requis manquants) : $($comfy.reason). Relancez sans --no-models, ou avec --force-download."
    } else {
      Warn "ComfyUI joignable mais modeles non installes (mode degrade --no-models) : $($comfy.reason)"
    }
  }
}

# --- Main flow (returns the process exit code; Die throws to the global handler) ---------
function Invoke-Main {
  if ($Action -eq "help") {
    Get-Content -LiteralPath $PSCommandPath | Where-Object { $_ -match '^#' } |
      Select-Object -First 26 | ForEach-Object { Write-Host $_ }
    return 0
  }

  Ensure-DockerDaemon

  # Compose : base + HARDENED sandbox (security) ALWAYS ; GPU overlay if NVIDIA exposed.
  $Compose = @("compose","--project-directory",$DockerDir,
               "-f",(Join-Path $DockerDir "docker-compose.app.yml"),
               "-f",(Join-Path $DockerDir "docker-compose.sandbox.yml"))
  $Gpu = $false
  if (Test-DockerHasNvidia) {
    $Gpu = $true
    $Compose += @("-f",(Join-Path $DockerDir "docker-compose.gpu.yml"))
  }

  if ($Action -eq "down") { Log "Arret de la stack"; & docker @Compose down; return 0 }
  if ($Action -eq "logs") { & docker @Compose logs -f; return 0 }

  Log ("GPU NVIDIA : " + $(if ($Gpu) { "detecte (CUDA)" } else { "non detecte (CPU)" }))

  $searxSettings = Join-Path $DockerDir "searxng\settings.yml"
  if (-not (Test-Path $searxSettings)) {
    Copy-Item (Join-Path $DockerDir "searxng\settings.example.yml") $searxSettings
    Log "SearXNG : settings.yml cree depuis l'exemple (json active)"
  }

  # Image models BEFORE the up (mounted read-only into comfyui). Mandatory unless --no-models.
  if ($Models) {
    Log "Modeles image (ComfyUI) -> $env:COMFYUI_MODELS_DIR"
    $fa = @("-ModelsDir", $env:COMFYUI_MODELS_DIR)
    if ($ForceDownload) { $fa += "-Force" }
    $code = Invoke-Child (Join-Path $ScriptsWin "Fetch-ComfyUIModels.ps1") $fa
    if ($code -ne 0) {
      Die "Echec du telechargement des modeles image (code $code). Voir le journal. Relancez, ou demarrez en mode degrade avec --no-models."
    }
  } else {
    Warn "Mode degrade : modeles image non telecharges (--no-models). La generation d'image echouera tant que les modeles sont absents."
  }

  if ($Build) {
    Log "Build + demarrage de la stack"
    & docker @Compose up -d --build
  } else {
    Log "Demarrage de la stack (sans rebuild : --no-build)"
    & docker @Compose up -d
  }
  if ($LASTEXITCODE -ne 0) { Die "Echec du build/demarrage de la stack (docker compose up). Voir le journal." }

  # LLM models inside the ollama container (idempotent). Mandatory unless --no-models.
  if ($Models) {
    Log "Modeles LLM (Ollama, dans le conteneur)"
    Invoke-OllamaPull $Compose
  } else {
    Warn "Mode degrade : modeles LLM non telecharges (--no-models)."
  }

  # --- REAL health gate (no lying 'ready') : container health first --------------------
  Log "Verification de la sante reelle des services"
  $cidBackend = (Get-ComposeCid $Compose "aac-backend")
  $cidComfy   = (Get-ComposeCid $Compose "comfyui")
  $deadline = (Get-Date).AddSeconds(240); $back = $false; $cf = $false
  while ((Get-Date) -lt $deadline) {
    if ((Get-ContainerHealth $cidBackend) -eq "healthy") { $back = $true }
    if ((Get-ContainerHealth $cidComfy)   -eq "healthy") { $cf = $true }
    if ($back -and $cf) { break }
    Start-Sleep -Seconds 4
  }
  if (-not $back) { Die "backend pas 'healthy' (diagnostic : run.bat --logs)" }
  if (-not $cf)   { Die "comfyui pas 'healthy' (diagnostic : run.bat --logs)" }

  # --- REAL runtime gate : backend /health/runtime distinguishes reachable vs ready ----
  #     (ComfyUI has no published port; it is validated through the backend, which also
  #     checks that the configured checkpoint/upscaler are actually loadable.)
  Assert-RuntimeReady -RequireComfy:$Models

  # --- Ollama actually populated (an empty ollama answering 'ready' is the classic lie) -
  if ($Models) {
    $present = ""
    try { $present = (& docker @Compose exec -T ollama ollama list 2>$null | Out-String) } catch { }
    foreach ($m in (Get-OllamaModelNames)) {
      if ($present -notmatch [regex]::Escape($m)) {
        Die "Ollama repond mais le modele '$m' manque (relancez sans --no-models)."
      }
    }
  }

  Log "OK -- stack saine. Console : http://127.0.0.1:8000/console"
  if ($Open) { Start-Process "http://127.0.0.1:8000/console" }
  return 0
}

# =======================================================================================
# SCRIPT BODY (skipped when dot-sourced for tests via AAC_RUN_PS1_NOEXEC=1).
# =======================================================================================
if ($env:AAC_RUN_PS1_NOEXEC -eq "1") { return }

$RepoRoot   = $PSScriptRoot
$DockerDir  = Join-Path $RepoRoot "docker"
$ScriptsWin = Join-Path $RepoRoot "scripts\windows"

if (-not $env:COMFYUI_MODELS_DIR) { $env:COMFYUI_MODELS_DIR = (Join-Path $DockerDir "models") }
# Absolute host path of outputs -> the Console shows it (copyable) to open the folder.
$env:AAC_HOST_OUTPUTS_DIR = (Join-Path $DockerDir "outputs")

# --- Options (accept both --long and -Switch forms; run.bat forwards %*) ---------------
$Open = $true; $Models = $true; $Build = $true; $Action = "up"
$ForceDownload = $false; $NoDockerStart = $false
foreach ($a in $args) {
  switch -regex ($a) {
    '^(--down|-Down)$'                    { $Action = "down" }
    '^(--logs|-Logs)$'                    { $Action = "logs" }
    '^(--no-open|-NoOpen)$'               { $Open = $false }
    '^(--no-models|--skip-models|-SkipModels)$' { $Models = $false }
    '^(--no-build|-NoBuild)$'             { $Build = $false }
    '^(--force-download|-ForceDownload)$' { $ForceDownload = $true }
    '^(--no-docker-start|-NoDockerStart)$' { $NoDockerStart = $true }
    '^(-h|--help|-Help)$'                 { $Action = "help" }
    default { Write-Host "option inconnue : $a" -ForegroundColor Yellow; exit 2 }
  }
}

# --- Logging: persistent journal in logs/, path always shown -----------------------------
$LogDir = Join-Path $RepoRoot "logs"
try { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null } catch { }
$LogFile = Join-Path $LogDir ("run-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))
$Transcript = $false
try { Start-Transcript -Path $LogFile -Append -ErrorAction Stop | Out-Null; $Transcript = $true } catch { $Transcript = $false }

# --- Global wrapper : transcript + exception handling + clean exit code ------------------
$Exit = 0
try {
  $Exit = Invoke-Main
}
catch {
  Write-Host ""
  Write-Host "ERREUR: $($_.Exception.Message)" -ForegroundColor Red
  $Exit = 1
}
finally {
  if ($Transcript) { try { Stop-Transcript | Out-Null } catch { } }
  Write-Host ""
  Write-Host "Journal : $LogFile" -ForegroundColor DarkGray
}
exit $Exit
