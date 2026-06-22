# run.ps1 — AAC en UNE commande (Windows). Miroir de run.sh. Chemin SÉCURISÉ par défaut.
#
# Stack Docker complète + overlay sandbox durci, GPU NVIDIA auto-détecté (Docker Desktop
# + WSL2), modèles idempotents, gate de santé RÉELLE, ouvre la Console.
# Unique prérequis : Docker Desktop (backend WSL2).
#
# >>> À VALIDER au boot Windows (non exécuté côté Linux). Le (télé)chargement des
#     modèles appelle des scripts bash -> nécessite WSL/Git-bash dans le PATH. <<<
#
#   run.bat / run.ps1        démarre, ouvre /console
#   run.bat --down           arrête la stack
#   run.bat --logs           suit les logs
$ErrorActionPreference = "Stop"

$RepoRoot  = $PSScriptRoot
$DockerDir = Join-Path $RepoRoot "docker"
if (-not $env:COMFYUI_MODELS_DIR) { $env:COMFYUI_MODELS_DIR = (Join-Path $DockerDir "models") }

$Open = $true; $Models = $true; $Action = "up"
foreach ($a in $args) {
  switch ($a) {
    "--down"      { $Action = "down" }
    "--logs"      { $Action = "logs" }
    "--no-open"   { $Open = $false }
    "--no-models" { $Models = $false }
    default       { Write-Error "option inconnue : $a"; exit 2 }
  }
}

function Die($m) { Write-Host "ERREUR: $m" -ForegroundColor Red; exit 1 }
function Log($m) { Write-Host "`n== $m ==" -ForegroundColor Cyan }

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Die "Docker absent. Installez Docker Desktop (backend WSL2)." }
docker compose version *> $null; if ($LASTEXITCODE -ne 0) { Die "plugin 'docker compose' v2 absent." }
docker info *> $null;            if ($LASTEXITCODE -ne 0) { Die "le daemon Docker ne répond pas (démarrez Docker Desktop)." }

# Compose : base + sandbox DURCI (sécurité) TOUJOURS ; overlay GPU si NVIDIA exposé.
$Compose = @("compose","--project-directory",$DockerDir,
             "-f",(Join-Path $DockerDir "docker-compose.app.yml"),
             "-f",(Join-Path $DockerDir "docker-compose.sandbox.yml"))
$Gpu = $false
if ((Get-Command nvidia-smi -ErrorAction SilentlyContinue) -and (docker info 2>$null | Select-String -Quiet -Pattern "nvidia")) {
  $Gpu = $true
  $Compose += @("-f",(Join-Path $DockerDir "docker-compose.gpu.yml"))
}

if ($Action -eq "down") { Log "Arrêt de la stack"; docker @Compose down; exit 0 }
if ($Action -eq "logs") { docker @Compose logs -f; exit 0 }

Log ("GPU NVIDIA : " + $(if ($Gpu) { "détecté (CUDA)" } else { "non détecté (CPU)" }))

$searxSettings = Join-Path $DockerDir "searxng\settings.yml"
if (-not (Test-Path $searxSettings)) {
  Copy-Item (Join-Path $DockerDir "searxng\settings.example.yml") $searxSettings
  Log "SearXNG : settings.yml créé depuis l'exemple (json activé)"
}

if ($Models) {
  Log "Modèles image (ComfyUI) -> $env:COMFYUI_MODELS_DIR"
  bash (Join-Path $RepoRoot "scripts/linux/fetch-models.sh")
}

Log "Build + démarrage de la stack"
docker @Compose up -d --build

if ($Models) {
  Log "Modèles LLM (Ollama, dans le conteneur)"
  $env:AAC_OLLAMA_MODE = "docker"
  bash (Join-Path $RepoRoot "scripts/linux/fetch-ollama-models.sh")
  if ($LASTEXITCODE -ne 0) { Die "échec du pull des modèles LLM" }
}

Log "Vérification de la santé réelle des services"
function Health($id) { if ($id) { docker inspect --format '{{.State.Health.Status}}' $id 2>$null } else { "absent" } }
$cidBackend = (docker @Compose ps -q aac-backend) 2>$null
$cidComfy   = (docker @Compose ps -q comfyui)     2>$null
$deadline = (Get-Date).AddSeconds(240); $back = $false; $cf = $false
while ((Get-Date) -lt $deadline) {
  if ((Health $cidBackend) -eq "healthy") { $back = $true }
  if ((Health $cidComfy)   -eq "healthy") { $cf = $true }
  if ($back -and $cf) { break }
  Start-Sleep -Seconds 4
}
if (-not $back) { Die "backend pas 'healthy' (diagnostic : run.bat --logs)" }
if (-not $cf)   { Die "comfyui pas 'healthy' (diagnostic : run.bat --logs)" }

$present = (docker @Compose exec -T ollama ollama list) 2>$null
foreach ($m in @("qwen3:8b","qwen2.5-coder:7b","qwen2.5vl:3b")) {
  if ($present -notmatch [regex]::Escape($m)) { Die "Ollama répond mais le modèle '$m' manque (relancez sans --no-models)." }
}

Log "OK — stack saine. Console : http://127.0.0.1:8000/console"
if ($Open) { Start-Process "http://127.0.0.1:8000/console" }
